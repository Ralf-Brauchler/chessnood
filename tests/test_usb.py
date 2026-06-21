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
