"""Tests for the USB-HID protocol logic (port of EasyLinkSDK).

These verify self-consistency and the byte/bit layout taken verbatim from the
official SDK. They can't confirm the constants against real hardware -- that's
what `chessnood scan` + a board test is for.
"""
import chess

from chessnood.boards import usb


def _board_report(pieces: dict[int, chess.Piece]) -> bytes:
    """Inverse of decode_board_report, mirroring the SDK byte/nibble layout."""
    data = bytearray(usb.BOARD_DATA_OFFSET + usb.BOARD_DATA_LEN)
    data[0] = usb.REPORT_BOARD
    data[1] = usb.BOARD_DATA_LEN
    code_by_symbol = {sym: i for i, sym in enumerate(usb._CHESS_PIECES)}
    for i in range(8):
        for j in range(7, -1, -1):
            square = chess.square(7 - j, 7 - i)
            piece = pieces.get(square)
            if piece is None:
                continue
            code = code_by_symbol[piece.symbol()]
            idx = (i * 8 + j) // 2 + usb.BOARD_DATA_OFFSET
            if j % 2 == 0:
                data[idx] |= code & 0x0F
            else:
                data[idx] |= code << 4
    return bytes(data)


def test_decode_start_position_roundtrip():
    pieces = chess.Board().piece_map()
    assert usb.decode_board_report(_board_report(pieces)) == pieces


def test_decode_after_moves_roundtrip():
    board = chess.Board()
    for uci in ("e2e4", "c7c5", "g1f3", "d7d6"):
        board.push_uci(uci)
    pieces = board.piece_map()
    assert usb.decode_board_report(_board_report(pieces)) == pieces


def test_decode_empty_board():
    assert usb.decode_board_report(_board_report({})) == {}


def test_led_command_header_and_length():
    payload = usb.encode_leds([chess.A1, chess.H8])
    assert payload[:2] == usb.CMD_LED
    assert len(payload) == 10  # 2 header + 8 rank bytes


def test_led_bit_layout_matches_sdk():
    # EasyLinkSDK: byte 0 = rank 8, file a = high bit (0x80), file h = low bit.
    rows = usb.encode_leds([chess.A1, chess.H8])[2:]
    assert rows[7] == 0x80  # a1: rank 1 -> byte 7, file a -> bit 7
    assert rows[0] == 0x01  # h8: rank 8 -> byte 0, file h -> bit 0
    assert usb.encode_leds([chess.A8])[2] == 0x80  # a8 -> first byte, top bit


def test_led_all_squares_and_empty():
    assert usb.encode_leds([])[2:] == bytes(8)             # nothing lit -> all zero
    assert usb.encode_leds(chess.SQUARES)[2:] == b"\xff" * 8  # every square lit


def test_decode_ignores_empty_and_out_of_range_codes():
    # a report that is all 0xFF: every nibble = 15, which is >= len(_CHESS_PIECES)
    # (an invalid code) and must be skipped, not raise or invent pieces.
    data = bytearray(usb.BOARD_DATA_OFFSET + usb.BOARD_DATA_LEN)
    data[0] = usb.REPORT_BOARD
    data[1] = usb.BOARD_DATA_LEN
    for i in range(usb.BOARD_DATA_OFFSET, len(data)):
        data[i] = 0xFF
    assert usb.decode_board_report(bytes(data)) == {}


def test_decode_battery_level_and_charging():
    assert usb.decode_battery(bytes([usb.REPORT_BATTERY, 0x01, 87, 1])) == {"level": 87, "charging": True}
    assert usb.decode_battery(bytes([usb.REPORT_BATTERY, 0x01, 50, 0])) == {"level": 50, "charging": False}
    # no charging byte -> charging unknown
    assert usb.decode_battery(bytes([usb.REPORT_BATTERY, 0x01, 42])) == {"level": 42, "charging": None}


def test_decode_battery_rejects_invalid():
    assert usb.decode_battery(bytes([usb.REPORT_BATTERY, 0x01, 0, 0])) is None      # 0 = not ready
    assert usb.decode_battery(bytes([usb.REPORT_BATTERY, 0x01, 250, 0])) is None    # >100 implausible
    assert usb.decode_battery(bytes([usb.REPORT_BOARD, 0x01, 80])) is None          # wrong report type
    assert usb.decode_battery(bytes([usb.REPORT_BATTERY])) is None                  # too short


class _FakeHid:
    """Minimal stand-in for the hidapi module."""

    def __init__(self, devices):
        self._devices = devices

    def enumerate(self, vid=0, pid=0):
        return [d for d in self._devices if d["vendor_id"] == vid or vid == 0]


def _pro_device():
    return {"vendor_id": usb.VENDOR_ID, "product_id": 0x8123,
            "usage_page": usb.USAGE_PAGE, "path": b"/dev/hidraw9",
            "product_string": "Chessnut Pro"}


def test_find_device_matches_pro_pid_and_usage_page():
    hid = _FakeHid([_pro_device()])
    assert usb._find_device(hid) == b"/dev/hidraw9"


def test_find_device_prefers_vendor_usage_page():
    # macOS/Windows expose several HID collections; pick the vendor one (0xFF00)
    # over e.g. the board's keyboard interface.
    keyboard = _pro_device(); keyboard["usage_page"] = 0x0001
    keyboard["path"] = b"/dev/hidraw8"
    vendor = _pro_device()  # usage_page = USAGE_PAGE, path /dev/hidraw9
    assert usb._find_device(_FakeHid([keyboard, vendor])) == b"/dev/hidraw9"


def test_find_device_falls_back_when_usage_page_absent():
    # Linux hidraw reports usage_page 0 (verified on a Pro); match on the product
    # id alone rather than dropping the board.
    dev = _pro_device(); dev["usage_page"] = 0x0000
    assert usb._find_device(_FakeHid([dev])) == b"/dev/hidraw9"


def test_find_device_none_when_absent():
    other = {"vendor_id": usb.VENDOR_ID, "product_id": 0x9999,
             "usage_page": usb.USAGE_PAGE, "path": b"/x"}
    assert usb._find_device(_FakeHid([other])) is None


def test_list_devices_uses_injected_hid(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "hid", _FakeHid([_pro_device()]))
    found = usb.list_devices()
    assert len(found) == 1
    desc, pid = found[0]
    assert "Chessnut Pro" in desc and pid == 0x8123


def test_set_leds_skips_redundant_writes():
    import asyncio

    board = usb.UsbBoard()
    writes = []
    board._write = lambda payload: writes.append(payload)  # capture, no hardware

    async def go():
        await board.set_leds([chess.E2, chess.E4])
        await board.set_leds([chess.E2, chess.E4])   # identical -> skipped
        await board.set_leds([chess.D2])             # different -> written

    asyncio.run(go())
    assert len(writes) == 2
    assert writes[0] == usb.encode_leds([chess.E2, chess.E4])
    assert writes[1] == usb.encode_leds([chess.D2])


def test_led_dedup_resets_so_same_squares_resend_after_reconnect():
    import asyncio

    board = usb.UsbBoard()
    writes = []
    board._write = lambda payload: writes.append(payload)

    async def go():
        await board.set_leds([chess.E2])
        board._last_led_payload = None     # what a fresh connection does
        await board.set_leds([chess.E2])   # must go through again (board LEDs were reset)

    asyncio.run(go())
    assert len(writes) == 2


class _FailingDev:
    """A device whose write() raises, but stays 'connected' (no disconnect)."""

    def write(self, data):
        raise OSError("pipe error")

    def close(self):
        pass


def test_failed_led_write_invalidates_dedup_cache():
    # If a write fails while the link stays up, the LED state is unknown -- the
    # next identical set_leds must be re-sent, not skipped by the dedup cache.
    board = usb.UsbBoard()
    board._dev = _FailingDev()
    board._last_led_payload = usb.encode_leds([chess.E2])  # pretend we sent e2
    board._write(usb.encode_leds([chess.E2]))              # fails internally
    assert board._last_led_payload is None                 # cache dropped -> will resend


# --- silent re-arm when the board sleeps -----------------------------------

class _FakeDev:
    """A hid handle stand-in that records close()."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_rearm_stream_swaps_handle_rearms_realtime_and_keeps_leds():
    board = usb.UsbBoard()
    old, new = _FakeDev(), _FakeDev()
    board._dev = old
    board._open_device = lambda: new              # a fresh handle
    writes = []
    board._write = lambda payload: writes.append(payload)
    board._last_led_payload = usb.encode_leds([chess.E2])   # a move is currently lit

    board._rearm_stream()

    assert board._dev is new and old.closed        # swapped to the fresh handle
    assert writes[0] == usb.CMD_REALTIME           # stream re-armed
    assert writes[1] == usb.encode_leds([chess.E2])  # lit move restored on the new handle
    assert board._last_led_payload == usb.encode_leds([chess.E2])  # dedup cache kept


def test_rearm_stream_raises_when_board_is_gone():
    # If the board really fell off the bus, re-arm can't open it and must raise so
    # the caller falls back to a full reconnect (which surfaces DISCONNECTED).
    board = usb.UsbBoard()
    board._dev = _FakeDev()
    board._open_device = _raise_no_board
    import pytest
    with pytest.raises(RuntimeError):
        board._rearm_stream()


# --- self-restart on an unrecoverable disconnect ---------------------------

def _raise_no_board():
    raise RuntimeError("no Chessnut USB board found")


def test_reconnect_exits_for_restart_after_a_lost_link(monkeypatch):
    """Board was connected, then lost and unrecoverable: the maintain loop exits
    (systemd then restarts with a fresh libusb context)."""
    board = usb.UsbBoard()
    board._run = True
    board._connected_once = True                       # we HAD a working link
    monkeypatch.setattr(board, "_connect_once", _raise_no_board)
    clock = {"t": 0.0}
    monkeypatch.setattr(usb.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(usb.time, "sleep", lambda _: clock.__setitem__("t", clock["t"] + 10))

    class _Exit(Exception):
        pass

    def fake_exit(code):
        raise _Exit()
    monkeypatch.setattr(usb.os, "_exit", fake_exit)

    import pytest
    with pytest.raises(_Exit):
        board._maintain()                              # gives up within ~2 retries (>15s)


def test_reconnect_waits_forever_when_never_connected(monkeypatch):
    """Pi booted before the board was plugged in (never connected): keep retrying,
    never exit -- so it can't restart-loop waiting for a board."""
    board = usb.UsbBoard()
    board._run = True
    board._connected_once = False                      # never opened the board
    monkeypatch.setattr(board, "_connect_once", _raise_no_board)
    monkeypatch.setattr(usb.time, "monotonic", lambda: 1e9)   # lots of time passes
    calls = {"n": 0}

    def fake_sleep(_):
        calls["n"] += 1
        if calls["n"] >= 5:
            board._run = False                         # stop the loop after a few tries
    monkeypatch.setattr(usb.time, "sleep", fake_sleep)
    monkeypatch.setattr(usb.os, "_exit",
                        lambda code: (_ for _ in ()).throw(AssertionError("must not exit")))

    board._maintain()                                  # returns cleanly, no exit
    assert calls["n"] == 5
