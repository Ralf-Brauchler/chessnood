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
WRITE_INTERVAL_S = 0.2                      # SDK enforces >=200ms between writes

# Report framing (EasyLinkSDK read thread + toFen).
REPORT_BOARD = 0x01    # readBuf[0] for a board-state report
BOARD_DATA_OFFSET = 2  # readBuf[0]=type, readBuf[1]=len, data starts at 2
BOARD_DATA_LEN = 32    # 32 bytes -> 64 squares (2 per byte)

# Piece code -> FEN symbol; index = code. Verbatim from EasyLinkSDK CHESS_PIECES.
_CHESS_PIECES = "0qkbpnRPrBNQK"

RECONNECT_DELAY_S = 3.0


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


class UsbBoard(Board):
    def __init__(self) -> None:
        super().__init__()
        self._dev = None              # hid.device
        self._lock = threading.Lock()  # serialise device access (read vs write)
        self._run = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_write = 0.0

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
        await asyncio.to_thread(self._write, payload)

    # --- thread side ------------------------------------------------------
    def _maintain(self) -> None:
        while self._run:
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
        self._write(CMD_REALTIME)
        self._post_state(ConnectionState.CONNECTED)
        log.info("Connected to board over USB")

        while self._run:
            with self._lock:
                if self._dev is None:
                    return
                data = self._dev.read(256, 100)  # 100 ms timeout
            if not data:
                continue
            if data[0] == REPORT_BOARD and len(data) >= BOARD_DATA_OFFSET + BOARD_DATA_LEN:
                try:
                    pieces = decode_board_report(bytes(data))
                except Exception as exc:  # noqa: BLE001
                    log.debug("Failed to decode board report: %s", exc)
                    continue
                self._post_reading(BoardReading(pieces))

    def _write(self, payload: bytes) -> None:
        # hidapi expects a leading report-ID byte (0x00 = unnumbered reports). The
        # SDK uses raw C hid_write; on Linux hidraw the 0x00 prefix is the correct
        # equivalent.  # VERIFY on hardware: drop the prefix if writes are ignored.
        with self._lock:
            if self._dev is None:
                return
            since = time.monotonic() - self._last_write
            if since < WRITE_INTERVAL_S:
                time.sleep(WRITE_INTERVAL_S - since)
            try:
                self._dev.write(b"\x00" + payload)
            except Exception as exc:  # noqa: BLE001
                log.debug("USB write failed: %s", exc)
            self._last_write = time.monotonic()

    # --- marshal back onto the event loop ---------------------------------
    def _post_state(self, state: ConnectionState) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._set_state, state)

    def _post_reading(self, reading: BoardReading) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._emit, reading)


def _find_device(hid) -> str | None:
    """Return the HID path of the first Chessnut board, or None."""
    for info in hid.enumerate(VENDOR_ID, 0):
        if (info["product_id"] & PRODUCT_ID_MASK) in PRODUCT_IDS and \
                info.get("usage_page") == USAGE_PAGE:
            return info["path"]
    return None


def list_devices() -> list[tuple[str, int]]:
    """List attached Chessnut boards as (description, product_id). For the CLI."""
    import hid

    found = []
    for info in hid.enumerate(VENDOR_ID, 0):
        if (info["product_id"] & PRODUCT_ID_MASK) in PRODUCT_IDS:
            name = info.get("product_string") or "Chessnut"
            found.append((f"{name} (pid 0x{info['product_id']:04x})", info["product_id"]))
    return found
