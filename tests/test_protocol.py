"""Tests for the byte-level protocol logic.

These verify the *self-consistency* of the encode/decode logic (nibble splitting,
square ordering, LED bit-packing). They cannot confirm the constants match real
Chessnut hardware -- that's what `chessnood scan` + a board test is for.
"""
import chess

from chessnood.boards import protocol as p


def test_board_roundtrip_start_position():
    pieces = chess.Board().piece_map()
    decoded = p.decode_board(p.encode_board(pieces))
    assert decoded == pieces


def test_board_roundtrip_after_moves():
    board = chess.Board()
    for uci in ("e2e4", "c7c5", "g1f3", "d7d6"):
        board.push_uci(uci)
    pieces = board.piece_map()
    assert p.decode_board(p.encode_board(pieces)) == pieces


def test_empty_board_decodes_empty():
    assert p.decode_board(p.encode_board({})) == {}


def test_stream_index_is_a_bijection():
    seen = {p.stream_index_to_square(i) for i in range(64)}
    assert seen == set(range(64))


def test_led_encoding_sets_one_bit_per_square():
    payload = p.encode_leds([chess.A1, chess.H8])
    assert payload[:2] == p.LED_COMMAND
    rows = payload[2:]
    assert len(rows) == 8
    # exactly two bits set across all rank bytes
    assert sum(bin(b).count("1") for b in rows) == 2
