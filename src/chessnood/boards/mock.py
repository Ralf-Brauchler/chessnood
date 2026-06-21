"""In-memory board backend for development and tests (no hardware needed)."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Iterable

import chess

from .base import Board, BoardReading, ConnectionState

log = logging.getLogger(__name__)


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


class SelfPlayBoard(Board):
    """A mock board that plays *itself*, to dry-run the whole stack on real output.

    Plugged into the real :class:`~chessnood.runner.Runner`, it drives a full game
    (random "human" vs the configured engine) so the screen and LED logic show the
    genuine flow -- no physical board needed. It follows the game purely by what
    the Runner asks it to light:

      * ``set_leds([from, to])`` = the engine's move -> execute it on the board.
      * ``set_leds([])`` = the human's turn -> play a random legal move.

    The empty ``set_leds([])`` the Runner emits *right after* a human move (before
    the engine thinks) is swallowed via the ``_just_moved`` flag, so the two cases
    never get confused. On game over it resets to the start position, which the
    Runner treats as a new game -- so it loops forever, ideal as a dev display.
    """

    def __init__(self, human_color: chess.Color = chess.WHITE, move_pause: float = 1.2):
        super().__init__()
        self._board = chess.Board()
        self._human_color = human_color
        self._pause = move_pause
        self._just_moved = False
        self._pending: asyncio.Task | None = None

    async def connect(self) -> None:
        self._set_state(ConnectionState.CONNECTED)
        self._emit(BoardReading.from_board(self._board))

    async def disconnect(self) -> None:
        if self._pending:
            self._pending.cancel()
        self._set_state(ConnectionState.DISCONNECTED)

    async def set_leds(self, squares: Iterable[int]) -> None:
        sq = list(squares)
        if len(sq) == 2:
            self._just_moved = False
            self._after_pause(self._execute_engine_move, sq[0], sq[1])
        elif not sq:
            if self._just_moved:
                self._just_moved = False  # spurious post-human-move clear
                return
            self._after_pause(self._play_human_move)

    def _after_pause(self, fn, *args) -> None:
        if self._pending and not self._pending.done():
            self._pending.cancel()

        async def _run():
            try:
                await asyncio.sleep(self._pause)
            except asyncio.CancelledError:
                return
            fn(*args)

        self._pending = asyncio.create_task(_run())

    def _play_human_move(self) -> None:
        if self._board.is_game_over():
            self._after_pause(self._restart)
            return
        move = random.choice(list(self._board.legal_moves))
        self._board.push(move)
        self._just_moved = True
        self._emit(BoardReading.from_board(self._board))

    def _execute_engine_move(self, frm: int, to: int) -> None:
        move = self._match_move(frm, to)
        if move is not None:
            self._board.push(move)
        self._emit(BoardReading.from_board(self._board))

    def _restart(self) -> None:
        log.info("[demo] game over (%s); starting a new game", self._board.result())
        self._board.reset()
        self._just_moved = False
        self._emit(BoardReading.from_board(self._board))

    def _match_move(self, frm: int, to: int) -> chess.Move | None:
        for m in self._board.legal_moves:
            if m.from_square == frm and m.to_square == to:
                if m.promotion and m.promotion != chess.QUEEN:
                    continue
                return m
        return None
