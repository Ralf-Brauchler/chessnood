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

    # --- persistence (survive a power loss mid-game) ----------------------
    def snapshot(self) -> dict:
        return {
            "fen": self.board.fen(),
            "state": self.state.name,
            "pending": self.pending_engine_move.uci() if self.pending_engine_move else None,
            "human_color": "white" if self.human_color == chess.WHITE else "black",
        }

    def restore(self, data: dict) -> None:
        self.board = chess.Board(data["fen"])
        self.state = GameState[data["state"]]
        pending = data.get("pending")
        self.pending_engine_move = chess.Move.from_uci(pending) if pending else None
        self.human_color = chess.WHITE if data.get("human_color", "white") == "white" else chess.BLACK


# --- on-screen / on-board guidance ---------------------------------------
#
# Pure, I/O-free: given the game state and the physically sensed position, work
# out what to tell the player (plain German, never coordinates), which squares
# to highlight (screen + board LEDs), an optional target position to display,
# and whether something needs correcting (for a beep). The runner calls this on
# every settled reading so the board is always self-explanatory and never just
# silently waits.


@dataclass
class Guidance:
    status: str                                  # short headline
    instruction: str                             # one plain-language line
    highlight: list[int] = field(default_factory=list)  # squares to light
    target: chess.Board | None = None            # position to show (else: sensed)
    alert: bool = False                          # something is wrong / needs fixing


def _diff_squares(a: chess.Board, b: chess.Board) -> list[int]:
    am, bm = a.piece_map(), b.piece_map()
    return sorted(sq for sq in set(am) | set(bm) if am.get(sq) != bm.get(sq))


def _is_lift_of(sensed: chess.Board, reference: chess.Board) -> bool:
    """True if ``sensed`` is ``reference`` with only pieces removed (lifted in
    hand) -- i.e. a move in progress, not a wrong placement."""
    ref = reference.piece_map()
    return all(ref.get(sq) == piece for sq, piece in sensed.piece_map().items())


def _castling_rook_squares(move: chess.Move) -> tuple[int, int]:
    rank = chess.square_rank(move.to_square)
    if chess.square_file(move.to_square) == 6:      # king to g-file: kingside
        return chess.square(7, rank), chess.square(5, rank)   # rook h -> f
    return chess.square(0, rank), chess.square(3, rank)       # rook a -> d (queenside)


def _promotion_square_in_progress(game: chess.Board, sensed: chess.Board) -> int | None:
    """If the player has pushed a pawn onto the promotion rank but not yet
    swapped it for a piece, return that square so we can tell them to place a queen."""
    back_rank = 7 if game.turn == chess.WHITE else 0
    pawn = chess.Piece(chess.PAWN, game.turn)
    for sq, piece in sensed.piece_map().items():
        if chess.square_rank(sq) == back_rank and piece == pawn:
            for mv in game.legal_moves:
                if mv.promotion and mv.to_square == sq:
                    return sq
    return None


def _is_simple_move(game: chess.Board, move: chess.Move) -> bool:
    """A plain move of one piece to an empty square -- no capture, castling, en
    passant or promotion. Only these are guided one square at a time; the rest
    light all involved squares at once (sequencing them is a later phase)."""
    return not (game.is_castling(move) or game.is_en_passant(move)
                or move.promotion or game.is_capture(move))


def _engine_move_guidance(game: chess.Board, move: chess.Move) -> tuple[list[int], str]:
    """Squares to light and a plain instruction for executing the engine's move."""
    involved = [move.from_square, move.to_square]
    if game.is_castling(move):
        involved += list(_castling_rook_squares(move))
        return involved, "Rochade: König und Turm auf die leuchtenden Felder ziehen."
    if game.is_en_passant(move):
        captured = chess.square(chess.square_file(move.to_square),
                                chess.square_rank(move.from_square))
        involved.append(captured)
        return involved, "En passant: Bauer ziehen und den markierten gegnerischen Bauern entfernen."
    if move.promotion:
        return involved, "Der Computer verwandelt: stelle eine Dame auf das leuchtende Feld."
    if game.is_capture(move):
        return involved, "Schlag: gegnerische Figur entfernen und die Figur auf das leuchtende Feld stellen."
    return involved, "Führe den leuchtenden Zug aus."


def compute_guidance(game: "ChessGame", sensed: chess.Board) -> Guidance:
    """What to show/say given the game state and the sensed physical position."""
    state, board = game.state, game.board

    if state == GameState.NEED_SETUP:
        start = chess.Board()
        diff = _diff_squares(sensed, start)
        # only missing pieces (still placing them) is not an error; a wrong piece is
        alert = not _is_lift_of(sensed, start)
        return Guidance("Figuren aufstellen",
                        "Stelle die markierten Felder auf die Grundstellung.",
                        diff, target=start if alert else None, alert=alert)

    if state == GameState.ENGINE_THINKING:
        return Guidance("Computer denkt …", "Bitte einen Moment warten.")

    if state == GameState.PLAYER_TURN:
        if _is_lift_of(sensed, board):   # equal, or a piece lifted mid-move
            return Guidance("Du bist am Zug", "Mach deinen Zug auf dem Brett.")
        promo = _promotion_square_in_progress(board, sensed)
        if promo is not None:
            return Guidance("Umwandlung",
                            "Ersetze den Bauern auf dem leuchtenden Feld durch eine Dame.",
                            [promo])
        return Guidance("Das passt nicht",
                        "Stelle die markierten Felder zurück und mache einen erlaubten Zug.",
                        _diff_squares(sensed, board), target=board, alert=True)

    if state == GameState.ENGINE_MOVE_SHOWN and game.pending_engine_move is not None:
        move = game.pending_engine_move
        expected = board.copy(stack=False)
        expected.push(move)
        done = sensed.piece_map() == expected.piece_map()
        executing = _is_lift_of(sensed, board) or _is_lift_of(sensed, expected)

        # Special/complex moves keep lighting all involved squares at once.
        if not _is_simple_move(board, move):
            involved, instr = _engine_move_guidance(board, move)
            if done or executing:
                return Guidance("Der Computer hat gezogen", instr, involved)
            return Guidance("Fast — bitte den leuchtenden Zug ausführen", instr, involved, alert=True)

        # Simple move: guide one square at a time -- light the source, and once it
        # has been lifted, light the destination. Less to interpret than two lit
        # squares at once (which is "from", which is "to").
        if done:
            return Guidance("Der Computer hat gezogen", "Führe den leuchtenden Zug aus.")
        if not executing:                              # a piece sits on a wrong square
            return Guidance("Fast — bitte den leuchtenden Zug ausführen",
                            "Stelle die Figur auf das leuchtende Feld.", [move.to_square], alert=True)
        if move.from_square in sensed.piece_map():     # source still on the board
            return Guidance("Der Computer hat gezogen", "Hebe die leuchtende Figur an.",
                            [move.from_square])
        return Guidance("Der Computer hat gezogen", "Stelle die Figur auf das leuchtende Feld.",
                        [move.to_square])

    if state == GameState.GAME_OVER:
        return Guidance(_result_text_for(board),
                        "Für eine neue Partie alle Figuren in die Grundstellung stellen.")

    return Guidance("", "")


def _result_text_for(board: chess.Board) -> str:
    outcome = board.outcome()
    if outcome is None or outcome.winner is None:
        return "Spiel vorbei: Remis"
    return "Spiel vorbei: Weiß gewinnt" if outcome.winner == chess.WHITE else "Spiel vorbei: Schwarz gewinnt"
