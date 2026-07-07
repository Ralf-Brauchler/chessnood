"""Real Chessnut board over USB-HID (via hidapi).

This is the **primary** transport. It is a faithful port of the official
``chessnutech/EasyLinkSDK`` (C), which talks to the board — including the
Chessnut **Pro** — over USB-HID and is the only path the vendor confirms for
reading the position *and* lighting the board LEDs (their docs note BLE "seems
not to work yet"). Reading and LED control are therefore officially supported
here, unlike over BLE.

USB-HID also removes the father's original pain entirely: no pairing, ever — the
board is just a wired peripheral the Pi enumerates like a keyboard.

Protocol constants below are taken verbatim from EasyLinkSDK (sdk/EasyLink.cpp).
The only things that may still need a tweak on real hardware are flagged
``# VERIFY`` (notably the HID report-ID prefix convention, which differs by OS).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Iterable

import chess

from .base import Board, BoardReading, ConnectionState

log = logging.getLogger(__name__)

# --- device identity (EasyLinkSDK: DEVICE_VID / DEVICE_PIDS / usage page) ---
VENDOR_ID = 0x2D80
PRODUCT_ID_MASK = 0xFF00
PRODUCT_IDS = (0x8000, 0x8100, 0x8200, 0x8300, 0x8400, 0x8500, 0x8600)  # Pro = 0x81xx
USAGE_PAGE = 0xFF00

# --- commands (EasyLinkSDK) ------------------------------------------------
CMD_REALTIME = bytes([0x21, 0x01, 0x00])   # switch to real-time board streaming
CMD_LED = bytes([0x0A, 0x08])              # + 8 rank bytes
CMD_BEEP = bytes([0x0B, 0x04])             # + freq (2 bytes) + duration ms (2 bytes)
WRITE_INTERVAL_S = 0.2                      # SDK enforces >=200ms between writes
# The Pro auto-clears its LEDs shortly after a write (verified on hardware: a
# single write never stays visibly lit; re-sending every ~0.25s holds it solid).
# The read loop refreshes the current pattern this often to keep it lit through a
# long think.  # VERIFY interval on hardware if the LEDs flicker.
LED_REFRESH_S = 0.25

# Report framing (EasyLinkSDK read thread + toFen).
REPORT_BOARD = 0x01    # readBuf[0] for a board-state report
BOARD_DATA_OFFSET = 2  # readBuf[0]=type, readBuf[1]=len, data starts at 2
BOARD_DATA_LEN = 32    # 32 bytes -> 64 squares (2 per byte)

# Piece code -> FEN symbol; index = code. Verbatim from EasyLinkSDK CHESS_PIECES.
_CHESS_PIECES = "0qkbpnRPrBNQK"

RECONNECT_DELAY_S = 3.0
# If the board was connected, is then lost, and can't be re-opened for this long,
# the process exits so systemd restarts it. A surprise USB removal can poison the
# long-lived libusb/hidapi context so the *running* process never re-enumerates
# the board even after it comes back -- but a *fresh* process reconnects fine
# (verified on the Pi). Only triggers after a real connection was lost, so a Pi
# that merely booted before the board is plugged in waits patiently and never
# restart-loops.
RESTART_AFTER_LOST_S = 15.0


def decode_board_report(report: bytes) -> dict[int, chess.Piece]:
    """Decode a 0x01 board report into {square: piece}.

    Faithful port of EasyLinkSDK ``ChessLink::toFen``: iterate i = rank (0 = rank
    8, top) and j = 7..0; the byte is ``data[(i*8+j)//2 + 2]`` and the nibble is
    the low nibble when j is even, the high nibble when j is odd. Within a rank
    j = 7 is file a, so the chess file is ``7 - j``.
    """
    pieces: dict[int, chess.Piece] = {}
    for i in range(8):
        for j in range(7, -1, -1):
            byte = report[(i * 8 + j) // 2 + BOARD_DATA_OFFSET]
            code = (byte & 0x0F) if (j % 2 == 0) else (byte >> 4)
            if code == 0 or code >= len(_CHESS_PIECES):
                continue
            square = chess.square(7 - j, 7 - i)  # file = 7-j, rank = 7-i
            pieces[square] = chess.Piece.from_symbol(_CHESS_PIECES[code])
    return pieces


def encode_leds(squares: Iterable[int]) -> bytes:
    """Build the LED command: header + 8 rank bytes.

    Matches EasyLinkSDK: byte 0 is rank 8, and within a byte file a is the high
    bit (0x80), file h the low bit (0x01).
    """
    rows = bytearray(8)
    for square in squares:
        rank = chess.square_rank(square)  # 0 = rank 1
        file = chess.square_file(square)  # 0 = file a
        rows[7 - rank] |= 1 << (7 - file)
    return CMD_LED + bytes(rows)


_LED_OFF = CMD_LED + bytes(8)  # all ranks 0 -> every LED off


class UsbBoard(Board):
    def __init__(self, stale_timeout_s: float = 0.0) -> None:
        super().__init__()
        self._dev = None              # hid.device
        self._lock = threading.Lock()        # guards the device handle (held briefly)
        self._write_lock = threading.Lock()  # serialises writers + the >=200ms throttle
        self._run = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_write = 0.0
        self._last_led_payload: bytes | None = None
        self._connected_once = False  # have we ever opened the board in this process?
        self._link_up = False         # did the current _connect_once reach CONNECTED?
        # If > 0, force a reconnect after this many seconds without a board report
        # (catches a board whose firmware/USB wedged but stays enumerated, so the
        # app would otherwise sit on a dead "connected" link forever). Off by
        # default until we confirm the Pro streams reports continuously in
        # realtime mode -- otherwise a long think would falsely trip it.  # VERIFY
        self._stale_timeout_s = stale_timeout_s

    async def connect(self) -> None:
        """Start the background read/reconnect loop and return immediately."""
        self._loop = asyncio.get_running_loop()
        self._run = True
        self._thread = threading.Thread(target=self._maintain, daemon=True)
        self._thread.start()

    async def disconnect(self) -> None:
        self._run = False
        with self._lock:
            if self._dev is not None:
                try:
                    self._dev.close()
                except Exception:  # noqa: BLE001 - best effort on shutdown
                    pass
                self._dev = None
        self._post_state(ConnectionState.DISCONNECTED)

    async def set_leds(self, squares: Iterable[int]) -> None:
        payload = encode_leds(squares)
        # Skip redundant writes: during a guided move set_leds fires on every
        # reading, mostly with the same squares. Re-sending wastes the 200ms
        # write budget and starves reads for no change on the board.
        if payload == self._last_led_payload:
            return
        self._last_led_payload = payload
        await asyncio.to_thread(self._write, payload)

    async def beep(self, frequency_hz: int = 1000, duration_ms: int = 150) -> None:
        f = max(0, min(0xFFFF, int(frequency_hz)))
        d = max(0, min(0xFFFF, int(duration_ms)))
        payload = CMD_BEEP + bytes([f >> 8, f & 0xFF, d >> 8, d & 0xFF])
        await asyncio.to_thread(self._write, payload)

    # --- thread side ------------------------------------------------------
    def _maintain(self) -> None:
        lost_since: float | None = None
        while self._run:
            self._link_up = False
            try:
                self._connect_once()
            except Exception as exc:  # noqa: BLE001
                log.warning("USB connection failed: %s", exc)
                self._post_state(ConnectionState.ERROR)
            with self._lock:
                if self._dev is not None:
                    try:
                        self._dev.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._dev = None
            # Give up (and let systemd restart us with a fresh libusb context) only
            # if we HAD a link and can't get it back for a while -- see
            # RESTART_AFTER_LOST_S. A board that was never connected (Pi booted
            # first) just waits, so this can't restart-loop.
            if self._link_up:
                lost_since = None
            elif self._connected_once and self._run:
                now = time.monotonic()
                lost_since = lost_since if lost_since is not None else now
                if now - lost_since >= RESTART_AFTER_LOST_S:
                    log.error("Board lost and unrecoverable for %.0fs; exiting for a "
                              "clean restart", RESTART_AFTER_LOST_S)
                    os._exit(1)
            if self._run:
                self._post_state(ConnectionState.DISCONNECTED)
                time.sleep(RECONNECT_DELAY_S)

    def _connect_once(self) -> None:
        import hid

        path = _find_device(hid)
        if path is None:
            raise RuntimeError("no Chessnut USB board found")
        dev = hid.device()
        dev.open_path(path)
        dev.set_nonblocking(False)
        with self._lock:
            self._dev = dev
        # A fresh link: the board's LEDs are off and the dedup cache is stale, so
        # let the next set_leds through unconditionally.
        self._last_led_payload = None
        self._write(CMD_REALTIME)
        self._connected_once = True
        self._link_up = True
        self._post_state(ConnectionState.CONNECTED)
        log.info("Connected to board over USB")

        last_rx = time.monotonic()
        last_led_refresh = 0.0
        last_pieces: dict[int, chess.Piece] | None = None
        while self._run:
            # Re-send the current LED pattern periodically: the board auto-clears
            # its LEDs, so a lit square would otherwise fade during a long think.
            # Skipped when nothing is lit (payload None/unknown or all-off).
            led = self._last_led_payload
            if led and led != _LED_OFF and \
                    time.monotonic() - last_led_refresh >= LED_REFRESH_S:
                self._write(led)
                last_led_refresh = time.monotonic()
            with self._lock:
                if self._dev is None:
                    return
                data = self._dev.read(256, 100)  # 100 ms timeout
            if not data:
                if self._stale_timeout_s and time.monotonic() - last_rx > self._stale_timeout_s:
                    log.warning("No board report for %.0fs; reconnecting", self._stale_timeout_s)
                    return  # _maintain reconnects
                continue
            last_rx = time.monotonic()
            if data[0] == REPORT_BOARD and len(data) >= BOARD_DATA_OFFSET + BOARD_DATA_LEN:
                try:
                    pieces = decode_board_report(bytes(data))
                except Exception as exc:  # noqa: BLE001
                    log.debug("Failed to decode board report: %s", exc)
                    continue
                # The Pro streams the board state continuously (~5-10x/s), not
                # only on change. Post only when the position actually changes, so
                # the runner's "stable for settle_s" debounce sees a real gap and
                # commits the move -- otherwise the endless stream never settles
                # and nothing is ever fed to the game.
                if pieces != last_pieces:
                    last_pieces = pieces
                    self._post_reading(BoardReading(pieces))

    def _write(self, payload: bytes) -> None:
        # hidapi expects a leading report-ID byte (0x00 = unnumbered reports). The
        # SDK uses raw C hid_write; on Linux hidraw the 0x00 prefix is the correct
        # equivalent.  # VERIFY on hardware: drop the prefix if writes are ignored.
        #
        # _write_lock serialises writers and the >=200ms inter-write throttle; the
        # throttle sleep is held *outside* the device lock so it can't block the
        # read thread (which only needs the device lock, held briefly).
        with self._write_lock:
            since = time.monotonic() - self._last_write
            if since < WRITE_INTERVAL_S:
                time.sleep(WRITE_INTERVAL_S - since)
            with self._lock:
                if self._dev is None:
                    return
                try:
                    self._dev.write(b"\x00" + payload)
                except Exception as exc:  # noqa: BLE001
                    log.debug("USB write failed: %s", exc)
                    # The board's LED state is now unknown -- drop the dedup cache
                    # so the next set_leds is resent rather than wrongly skipped.
                    self._last_led_payload = None
            self._last_write = time.monotonic()

    # --- marshal back onto the event loop ---------------------------------
    def _post_state(self, state: ConnectionState) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._set_state, state)

    def _post_reading(self, reading: BoardReading) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._emit, reading)


class DiagDevice:
    """A thin synchronous wrapper for step-by-step hardware bring-up.

    Deliberately bypasses the async :class:`UsbBoard` (no read thread, no
    reconnect, no game logic) so each diagnostic command tests exactly one layer
    of the interface. ``prefix`` toggles the leading ``0x00`` HID report-ID byte
    that ``UsbBoard._write`` adds -- the one convention flagged ``# VERIFY``.
    """

    def __init__(self, dev, prefix: bool = True):
        self._dev = dev
        self.prefix = prefix

    def write(self, payload: bytes) -> int:
        data = (b"\x00" if self.prefix else b"") + payload
        return self._dev.write(data)

    def read(self, timeout_ms: int = 100) -> bytes:
        return bytes(self._dev.read(256, timeout_ms))

    def start_realtime(self) -> None:
        self.write(CMD_REALTIME)

    def close(self) -> None:
        try:
            self._dev.close()
        except Exception:  # noqa: BLE001 - best effort
            pass


def open_diag(prefix: bool = True) -> DiagDevice:
    """Open the first attached Chessnut board for diagnostics.

    Raises ``RuntimeError`` if no board is found (the CLI turns that into a
    friendly message). ``import hid`` here so the dependency stays optional.
    """
    import hid

    path = _find_device(hid)
    if path is None:
        raise RuntimeError("no Chessnut USB board found (is it plugged in and on?)")
    dev = hid.device()
    dev.open_path(path)
    dev.set_nonblocking(False)
    return DiagDevice(dev, prefix=prefix)


def _find_device(hid) -> str | None:
    """Return the HID path of the first Chessnut board, or None.

    On macOS/Windows hidapi reports the vendor usage page (0xFF00), which picks
    the control interface out of the board's several HID collections. On Linux
    the hidraw backend reports usage_page 0 (verified on a Pro: a single
    interface, usage_page 0x0000 -- see docs/HARDWARE.md rung 1), so there we
    can only match on the product id, the same filter ``scan`` uses.
    """
    candidates = [
        info for info in hid.enumerate(VENDOR_ID, 0)
        if (info["product_id"] & PRODUCT_ID_MASK) in PRODUCT_IDS
    ]
    if not candidates:
        return None
    # Prefer the vendor interface when the platform exposes usage pages;
    # otherwise (Linux) fall back to the sole product match.
    for info in candidates:
        if info.get("usage_page") == USAGE_PAGE:
            return info["path"]
    return candidates[0]["path"]


def list_devices() -> list[tuple[str, int]]:
    """List attached Chessnut boards as (description, product_id). For the CLI."""
    import hid

    found = []
    for info in hid.enumerate(VENDOR_ID, 0):
        if (info["product_id"] & PRODUCT_ID_MASK) in PRODUCT_IDS:
            name = info.get("product_string") or "Chessnut"
            found.append((f"{name} (pid 0x{info['product_id']:04x})", info["product_id"]))
    return found
