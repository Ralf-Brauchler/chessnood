"""The shared Board plumbing: bounded reading queue with drop-oldest."""
import asyncio

import chess

from chessnood.boards.base import BoardReading, ConnectionState
from chessnood.boards.mock import MockBoard


def _reading(fen_board: chess.Board) -> BoardReading:
    return BoardReading.from_board(fen_board)


def _numbered_reading(n: int) -> BoardReading:
    """A distinct reading tagged by how many pieces it has, so we can tell order."""
    pieces = {sq: chess.Piece(chess.PAWN, chess.WHITE) for sq in range(n)}
    return BoardReading(pieces)


def test_readings_queue_drops_oldest_when_full():
    board = MockBoard()
    q = board.subscribe_readings(maxsize=4)
    for n in range(1, 11):            # emit 10 distinct readings into a size-4 queue
        board._emit(_numbered_reading(n))
    assert q.qsize() == 4             # bounded, never grew past maxsize
    drained = [q.get_nowait() for _ in range(4)]
    assert [len(r.pieces) for r in drained] == [7, 8, 9, 10]  # only the newest survive


def test_emit_delivers_to_all_subscribers():
    board = MockBoard()
    a = board.subscribe_readings()
    b = board.subscribe_readings()
    r = _reading(chess.Board())
    board._emit(r)
    assert a.get_nowait() == r
    assert b.get_nowait() == r


def test_state_change_only_emitted_on_change():
    board = MockBoard()
    q = board.subscribe_state()
    board._set_state(ConnectionState.CONNECTED)
    board._set_state(ConnectionState.CONNECTED)  # no change -> no second event
    assert q.qsize() == 1
