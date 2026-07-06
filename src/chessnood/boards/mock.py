"""In-memory board backend for development and tests (no hardware needed)."""
from __future__ import annotations

from typing import Iterable

import chess

from .base import Board, BoardReading, ConnectionState


class MockBoard(Board):
    """A fake board you drive from code.

    ``set_position`` simulates a human physically arranging the pieces into a
    given position and emits the matching reading. ``led_squares`` records what
    the application asked to light up, so a test or the ``simulate`` command can
    inspect it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.current = BoardReading.from_board(chess.Board())
        self.led_squares: set[int] = set()

    async def connect(self) -> None:
        self._set_state(ConnectionState.CONNECTED)
        self._emit(self.current)

    async def disconnect(self) -> None:
        self._set_state(ConnectionState.DISCONNECTED)

    async def set_leds(self, squares: Iterable[int]) -> None:
        self.led_squares = set(squares)

    # --- test/simulation helpers -----------------------------------------
    def set_position(self, board: chess.Board) -> None:
        """Pretend the physical pieces now match ``board``; emit a reading."""
        self.current = BoardReading.from_board(board)
        self._emit(self.current)

    def set_reading(self, reading: BoardReading) -> None:
        self.current = reading
        self._emit(reading)
