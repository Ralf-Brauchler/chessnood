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
    genuine flow -- no physical board needed.

    It mimics a *real* e-board faithfully: every move is emitted as a **sequence of
    whole-board snapshots**, not as a move. First an intermediate reading with the
    moving piece(s) lifted off (which matches no legal move -> the game treats it as
    a transient and ignores it), then the final position. This exercises the same
    transient handling that physical piece moves trigger.

    Turns are driven from the board itself; the Runner's ``set_leds([from, to])`` is
    used only to learn which move the engine chose (the only thing the board can't
    know on its own). On game over it resets to the start position, which the Runner
    treats as a new game -- so it loops forever, ideal as a dev display.
    """

    def __init__(self, human_color: chess.Color = chess.WHITE,
                 move_pause: float = 1.2, transient_pause: float = 0.5,
                 mistake_chance: float = 0.0, mistake_pause: float = 2.0):
        super().__init__()
        self._board = chess.Board()
        self._human_color = human_color
        self._pause = move_pause
        self._transient_pause = transient_pause
        # How often a move is "fumbled" first (piece placed on a wrong square) so
        # the self-healing guidance ("Das passt nicht" / "Fast …") shows live. The
        # wrong position is held ``mistake_pause`` seconds -- it must exceed the
        # runner's settle window so the bad reading actually commits and alerts,
        # then the move is corrected.
        self._mistake_chance = mistake_chance
        self._mistake_pause = mistake_pause
        self._run = False
        self._task: asyncio.Task | None = None
        self._lit: tuple[int, int] | None = None  # last 2-square set_leds (engine move)

    async def connect(self) -> None:
        self._set_state(ConnectionState.CONNECTED)
        self._emit(BoardReading.from_board(self._board))  # start position
        self._run = True
        self._task = asyncio.create_task(self._drive())

    async def disconnect(self) -> None:
        self._run = False
        if self._task:
            self._task.cancel()
        self._set_state(ConnectionState.DISCONNECTED)

    async def set_leds(self, squares: Iterable[int]) -> None:
        sq = list(squares)
        # The engine move's from/to are always the first two lit squares (extra
        # squares may follow for castling/en passant). One square = a fix hint.
        self._lit = (sq[0], sq[1]) if len(sq) >= 2 else None

    async def _drive(self) -> None:
        try:
            # let the Runner consume the start position before we start moving
            await asyncio.sleep(0.05)
            while self._run:
                if self._board.is_game_over():
                    log.info("[demo] game over (%s); new game", self._board.result())
                    await asyncio.sleep(self._pause * 1.5)
                    self._board.reset()
                    self._emit(BoardReading.from_board(self._board))  # -> new game
                    await asyncio.sleep(self._pause)
                    continue

                await asyncio.sleep(self._pause)
                if self._board.turn == self._human_color:
                    move = random.choice(list(self._board.legal_moves))
                else:
                    move = await self._await_engine_move()
                    if not self._run:
                        break
                    if move is None:  # stalled (e.g. engine underpromotion); restart
                        log.info("[demo] engine move didn't resolve; starting a new game")
                        self._board.reset()
                        self._emit(BoardReading.from_board(self._board))
                        continue
                await self._play_as_sequence(move)
        except asyncio.CancelledError:
            pass

    async def _await_engine_move(self) -> chess.Move | None:
        """Wait until the Runner lights a move that is legal on the current board.

        Polling on legality (rather than an event) sidesteps races: a stale lit
        move from the previous turn won't be legal now, so it's simply skipped
        until the Runner lights the genuine new engine move. Returns None if no
        legal lit move appears within a timeout -- the LED command can't express
        an underpromotion piece, so such a move would never match; the caller
        then just restarts the game.
        """
        deadline = max(10.0, self._pause * 8)
        waited = 0.0
        while self._run and waited < deadline:
            if self._lit is not None:
                move = self._match_move(self._lit[0], self._lit[1])
                if move is not None:
                    self._lit = None
                    return move
            await asyncio.sleep(0.01)
            waited += 0.01
        return None

    async def _play_as_sequence(self, move: chess.Move) -> None:
        """Emit 'pieces lifted' (a transient), maybe a fumble, then the final position."""
        pre = self._board.copy(stack=False)         # position before the move (to test legality)
        before = dict(self._board.piece_map())
        self._board.push(move)
        after = dict(self._board.piece_map())
        changed = {sq for sq in set(before) | set(after) if before.get(sq) != after.get(sq)}
        lifted = {sq: p for sq, p in before.items() if sq not in changed}
        self._emit(BoardReading(lifted))            # transient: ignored by the game
        await asyncio.sleep(self._transient_pause)
        fumble = self._maybe_fumble(pre, before, move)
        if fumble is not None:
            log.info("[demo] fumbling: piece placed on a wrong square (shows the recovery UI)")
            self._emit(BoardReading(fumble))        # held > settle -> alert, then corrected
            await asyncio.sleep(self._mistake_pause)
        self._emit(BoardReading(after))             # the completed move

    def _maybe_fumble(self, pre: chess.Board, before: dict[int, chess.Piece],
                      move: chess.Move) -> dict[int, chess.Piece] | None:
        """Sometimes return a *wrong* placement: the moving piece on an empty square
        that is not its real destination, chosen so the game reads it as INVALID
        (no legal move matches it) -- which is exactly what triggers the guidance.

        Returns ``None`` to play the move cleanly (no fumble this time)."""
        if self._mistake_chance <= 0 or random.random() >= self._mistake_chance:
            return None
        piece = before.get(move.from_square)
        if piece is None:                           # shouldn't happen for a legal move
            return None
        # imported lazily to avoid an import cycle (game <- boards.base <- boards/__init__)
        from ..game import Detection, detect_move

        empties = [sq for sq in chess.SQUARES if sq not in before and sq != move.to_square]
        random.shuffle(empties)
        for wrong in empties:
            candidate = dict(before)
            del candidate[move.from_square]
            candidate[wrong] = piece
            detection, _ = detect_move(pre, BoardReading(candidate))
            if detection == Detection.INVALID:
                return candidate
        return None

    def _match_move(self, frm: int, to: int) -> chess.Move | None:
        for m in self._board.legal_moves:
            if m.from_square == frm and m.to_square == to:
                if m.promotion and m.promotion != chess.QUEEN:
                    continue
                return m
        return None
