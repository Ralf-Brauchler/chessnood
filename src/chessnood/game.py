"""Pure game state machine.

This module is deliberately free of any I/O (no Bluetooth, no asyncio, no GPIO)
so it can be unit-tested directly. The async :mod:`chessnood.runner` wires it to
the real board, engine and indicators.

Move detection: because Chessnut boards report the *identity* of the piece on
each square, we can recover the move played by finding the single legal move
whose resulting position matches what the board now senses. Transient states
(a piece lifted mid-move, a capture in progress) match nothing and are ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

import chess

from .boards.base import BoardReading


class GameState(Enum):
    NEED_SETUP = auto()       # waiting for the pieces to be in the start position
    PLAYER_TURN = auto()      # waiting for the human to make a move
    ENGINE_THINKING = auto()  # engine is computing (ignore board noise)
    ENGINE_MOVE_SHOWN = auto()  # LEDs lit; waiting for the human to execute it
    GAME_OVER = auto()


class Detection(Enum):
    NONE = auto()     # board unchanged
    MOVE = auto()     # a legal move was recognised
    INVALID = auto()  # board doesn't correspond to any legal move (transient)


def detect_move(board: chess.Board, reading: BoardReading) -> tuple[Detection, chess.Move | None]:
    if reading.matches(board):
        return Detection.NONE, None
    for move in board.legal_moves:
        board.push(move)
        matched = reading.matches(board)
        board.pop()
        if matched:
            return Detection.MOVE, move
    return Detection.INVALID, None


@dataclass
class Reaction:
    """What the runner should do after feeding a reading."""

    leds: list[int] = field(default_factory=list)
    engine_should_move: bool = False
    message: str | None = None
    invalid: bool = False


class ChessGame:
    def __init__(self, human_color: chess.Color = chess.WHITE):
        self.human_color = human_color
        self.board = chess.Board()
        self.state = GameState.NEED_SETUP
        self.pending_engine_move: chess.Move | None = None

    # --- control ----------------------------------------------------------
    def new_game(self) -> Reaction:
        self.board.reset()
        self.pending_engine_move = None
        self.state = GameState.NEED_SETUP
        return Reaction(leds=[], message="New game: set up the pieces")

    def _begin_play(self) -> Reaction:
        if self.board.turn == self.human_color:
            self.state = GameState.PLAYER_TURN
            return Reaction(message="Your move")
        self.state = GameState.ENGINE_THINKING
        return Reaction(engine_should_move=True, message="Computer to move")

    def set_engine_move(self, move: chess.Move) -> Reaction:
        """Called by the runner once the engine has chosen its move."""
        self.pending_engine_move = move
        self.state = GameState.ENGINE_MOVE_SHOWN
        return Reaction(
            leds=[move.from_square, move.to_square],
            message=f"Computer plays {self.board.san(move)}",
        )

    # --- board input ------------------------------------------------------
    def feed(self, reading: BoardReading) -> Reaction:
        if self.state == GameState.NEED_SETUP:
            if reading.matches(self.board):
                return self._begin_play()
            return Reaction(message="Waiting for start position")

        # Auto new game: once a game has started, putting every piece back in the
        # initial position is the "new game" signal -- no button or touch needed.
        if self._is_restart_request(reading):
            self.board.reset()
            self.pending_engine_move = None
            return self._begin_play()

        if self.state == GameState.PLAYER_TURN:
            return self._handle_player(reading)

        if self.state == GameState.ENGINE_MOVE_SHOWN:
            return self._handle_engine_execution(reading)

        # ENGINE_THINKING / GAME_OVER: ignore other board noise.
        return Reaction()

    def _is_restart_request(self, reading: BoardReading) -> bool:
        """True when the player has reset the board to the start position.

        Only meaningful once play has progressed (or the game is over) -- at the
        very first move the board legitimately *is* the start position, which is
        normal play, not a restart. Ignored while the engine is thinking.
        """
        if self.state == GameState.ENGINE_THINKING:
            return False
        if self.state != GameState.GAME_OVER and not self.board.move_stack:
            return False
        return reading.matches(chess.Board())

    def _handle_player(self, reading: BoardReading) -> Reaction:
        detection, move = detect_move(self.board, reading)
        if detection == Detection.NONE:
            return Reaction()
        if detection == Detection.INVALID:
            return Reaction(invalid=True)
        assert move is not None
        self.board.push(move)
        if self.board.is_game_over():
            self.state = GameState.GAME_OVER
            return Reaction(message=self._result_text())
        self.state = GameState.ENGINE_THINKING
        return Reaction(engine_should_move=True)

    def _handle_engine_execution(self, reading: BoardReading) -> Reaction:
        assert self.pending_engine_move is not None
        expected = self.board.copy(stack=False)
        expected.push(self.pending_engine_move)
        if reading.matches(expected):
            self.board.push(self.pending_engine_move)
            self.pending_engine_move = None
            if self.board.is_game_over():
                self.state = GameState.GAME_OVER
                return Reaction(message=self._result_text())
            self.state = GameState.PLAYER_TURN
            return Reaction(leds=[], message="Your move")
        if reading.matches(self.board):
            # Player hasn't executed the move yet; keep showing the LEDs.
            return Reaction(
                leds=[self.pending_engine_move.from_square, self.pending_engine_move.to_square]
            )
        return Reaction(
            leds=[self.pending_engine_move.from_square, self.pending_engine_move.to_square],
            invalid=True,
        )

    def _result_text(self) -> str:
        return f"Game over: {self.board.result()}"
