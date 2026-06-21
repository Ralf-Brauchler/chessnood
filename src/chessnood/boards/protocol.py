"""Chessnut BLE protocol: GATT UUIDs, board decoding, LED encoding.

Constants below are cross-checked against multiple community libraries
(chessnutech/EasyLinkSDK, rmarabini/chessnutair, paulvonallwoerden/chessnut-air,
ecrucru/chessnut-connector). The following are CONFIRMED consistent across at
least two independent implementations and so are no longer flagged:
  * the write characteristic 1b7e8272..., read/data characteristic 1b7e8262...
  * the init command 0x21 0x01 0x00
  * the LED command header 0x0A 0x08 + 8 rank bytes, file a = high bit (0x80)
  * the 32-byte board payload after a 2-byte header, low-nibble-first, and the
    piece-code -> symbol map (identical in rmarabini/chessnutair)

Still UNVERIFIED on a physical Chessnut **Pro** specifically (the references are
mostly for the Air, which shares the protocol) and flagged ``# VERIFY``:
  * the exact board square ordering / orientation (rotation, file/rank origin)
  * whether the Pro exposes LED control over BLE at all
Each is isolated as a named constant or a one-line function for an easy fix.
"""
from __future__ import annotations

from typing import Iterable

import chess

# --- GATT UUIDs -----------------------------------------------------------
SERVICE_UUID = "1b7e8261-2877-41c3-b46e-cf057c562023"
READ_CHARACTERISTIC = "1b7e8262-2877-41c3-b46e-cf057c562023"   # notify: board state (confirmed)
WRITE_CHARACTERISTIC = "1b7e8272-2877-41c3-b46e-cf057c562023"  # write: commands/LEDs (confirmed)

# Command that puts the board into real-time streaming mode. (confirmed)
INIT_REALTIME = bytes([0x21, 0x01, 0x00])

# LED command header; followed by 8 bytes (one per rank). (confirmed)
LED_COMMAND = bytes([0x0A, 0x08])

# Board-state notification framing. (confirmed: 2-byte header, 32 data bytes)
DATA_OFFSET = 2
DATA_LEN = 32     # 32 bytes -> 64 squares (2 squares per byte)

# Piece code -> FEN symbol (upper = white). Confirmed identical in
# rmarabini/chessnutair's convertDict.
_CODE_TO_SYMBOL: dict[int, str] = {
    1: "q", 2: "k", 3: "b", 4: "p", 5: "n", 6: "R",
    7: "P", 8: "r", 9: "B", 10: "N", 11: "Q", 12: "K",
}  # confirmed (rmarabini/chessnutair)
PIECE_BY_CODE: dict[int, chess.Piece] = {
    code: chess.Piece.from_symbol(sym) for code, sym in _CODE_TO_SYMBOL.items()
}
CODE_BY_SYMBOL: dict[str, int] = {sym: code for code, sym in _CODE_TO_SYMBOL.items()}


def stream_index_to_square(idx: int) -> int:
    """Map a 0..63 position in the data stream to a python-chess square.

    Assumed stream order: a8, b8, ..., h8, a7, ..., h1 (rank 8 -> 1, file a -> h).
    """
    stream_rank, file = divmod(idx, 8)  # stream_rank 0 == rank 8  # VERIFY
    rank = 7 - stream_rank
    return chess.square(file, rank)


def square_to_stream_index(square: int) -> int:
    file = chess.square_file(square)
    rank = chess.square_rank(square)
    stream_rank = 7 - rank
    return stream_rank * 8 + file


def decode_board(data: bytes) -> dict[int, chess.Piece]:
    """Decode a board-state notification payload into a square -> piece map."""
    body = data[DATA_OFFSET:DATA_OFFSET + DATA_LEN]
    pieces: dict[int, chess.Piece] = {}
    for byte_index, byte in enumerate(body):
        # low nibble = first square, high nibble = second square  # VERIFY
        for nibble, code in ((0, byte & 0x0F), (1, byte >> 4)):
            if code == 0:
                continue
            piece = PIECE_BY_CODE.get(code)
            if piece is None:
                continue
            square = stream_index_to_square(byte_index * 2 + nibble)
            pieces[square] = piece
    return pieces


def encode_board(pieces: dict[int, chess.Piece]) -> bytes:
    """Inverse of :func:`decode_board`. Used by the mock board and tests."""
    nibbles = [0] * (DATA_LEN * 2)
    for square, piece in pieces.items():
        nibbles[square_to_stream_index(square)] = CODE_BY_SYMBOL[piece.symbol()]
    body = bytearray(DATA_LEN)
    for byte_index in range(DATA_LEN):
        low = nibbles[byte_index * 2]
        high = nibbles[byte_index * 2 + 1]
        body[byte_index] = (high << 4) | low
    return bytes(DATA_OFFSET) + bytes(body) + bytes(2)


def encode_leds(squares: Iterable[int]) -> bytes:
    """Build the LED command for the given python-chess squares.

    Layout confirmed against community libraries (paulvonallwoerden/chessnut-air,
    rmarabini/chessnutair): 8 bytes, one per rank with rank 8 first; within a byte
    file a is the high bit (0x80) and file h the low bit (0x01).
    """
    rows = bytearray(8)
    for square in squares:
        stream = square_to_stream_index(square)
        stream_rank, file = divmod(stream, 8)
        rows[stream_rank] |= 1 << (7 - file)  # file a -> bit7 (0x80), file h -> bit0
    return LED_COMMAND + bytes(rows)
