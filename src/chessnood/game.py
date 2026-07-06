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


def _is_start_setup(pieces: dict[int, chess.Piece]) -> bool:
    """True if the pieces are set up for a new game -- every piece on its home
    RANK with the standard pawn structure -- even if back-rank pieces are in the
    wrong ORDER (most commonly the white king and queen swapped, the classic
    "queen on her colour" mix-up). Recognising this lets a new game start even
    from a slightly-wrong setup; the one-at-a-time guidance then corrects the
    order. Strict enough never to match a real mid-game position: it requires
    exactly the start squares occupied and each rank to hold the start's pieces."""
    start = chess.Board().piece_map()
    if set(pieces) != set(start):
        return False

    def by_rank(pm: dict[int, chess.Piece]) -> dict[int, list[str]]:
        ranks: dict[int, list[str]] = {}
        for sq, piece in pm.items():
            ranks.setdefault(chess.square_rank(sq), []).append(piece.symbol())
        return {r: sorted(v) for r, v in ranks.items()}

    return by_rank(pieces) == by_rank(start)


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

        # Auto new game: once a game has started, putting the pieces back in the
        # initial position is the "new game" signal -- no button or touch needed.
        if self._is_restart_request(reading):
            self.board.reset()
            self.pending_engine_move = None
            if reading.matches(self.board):
                return self._begin_play()          # exact start -> straight into play
            # start-shaped but a back-rank piece is misplaced (e.g. king/queen
            # swapped): go to setup so the guidance walks them to the right squares.
            self.state = GameState.NEED_SETUP
            return Reaction(message="New game: set up the pieces")

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

        The "have we progressed?" test compares the tracked *position* to the
        start, NOT ``move_stack``: a game resumed from disk (``restore``) rebuilds
        the board from FEN with an empty move stack even though it is mid-game, so
        keying off ``move_stack`` would wrongly refuse to restart a resumed game
        (the player sets up the start position and nothing happens).
        """
        if self.state == GameState.ENGINE_THINKING:
            return False
        at_start = self.board.piece_map() == chess.Board().piece_map()
        if self.state != GameState.GAME_OVER and at_start:
            return False
        return _is_start_setup(reading.pieces)

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
    # (src, dst) of the piece currently being cleaned up, threaded back by the
    # runner so the destination lights after the piece is lifted (see _plan_recovery)
    fixing: "tuple[int, int] | None" = None


def _diff_squares(a: chess.Board, b: chess.Board) -> list[int]:
    am, bm = a.piece_map(), b.piece_map()
    return sorted(sq for sq in set(am) | set(bm) if am.get(sq) != bm.get(sq))


def _wrong_squares(sensed: chess.Board, target: chess.Board) -> list[int]:
    """Squares where ``sensed`` has a piece that doesn't belong there (a wrong or
    extra piece that must be taken off before the position is correct)."""
    tm = target.piece_map()
    return sorted(sq for sq, piece in sensed.piece_map().items() if tm.get(sq) != piece)


def _missing_squares(sensed: chess.Board, target: chess.Board) -> list[int]:
    """Squares where ``target`` has a piece that ``sensed`` is missing (empty or
    holding the wrong piece) -- i.e. squares a piece still needs to be placed on."""
    sm = sensed.piece_map()
    return sorted(sq for sq, piece in target.piece_map().items() if sm.get(sq) != piece)


Fixing = tuple[int, int]  # (source, destination) of the piece being corrected


def _plan_recovery(sensed: chess.Board, target: chess.Board,
                   fixing: "Fixing | None"
                   ) -> tuple[list[int], str, bool, "Fixing | None"]:
    """Guide a wrong position back to ``target`` **one whole piece at a time**:
    light the misplaced piece to lift, then -- once lifted -- light the single
    square it belongs on, and only then move on to the next wrong piece.

    Returns ``(highlight, instruction, alert, fixing)``. ``fixing`` is the
    ``(src, dst)`` of the piece currently being corrected; the runner threads it
    back in on the next reading so that after the piece is lifted we light its
    destination -- and ignore any other wrong pieces until it is placed. This is
    what stops "lift everything first, then guess where each goes".

    Only a genuinely wrong piece (or an in-progress correction) engages this. A
    bare lifted piece during normal play has nothing *wrong* on the board, so we
    return an empty highlight and ``fixing=None`` and let the caller treat it as
    ordinary play.
    """
    smap, tmap = sensed.piece_map(), target.piece_map()
    wrong = sorted(sq for sq, piece in smap.items() if tmap.get(sq) != piece)
    missing = sorted(sq for sq, piece in tmap.items() if smap.get(sq) != piece)

    # Continue the piece already being corrected before touching any other.
    if fixing is not None:
        src, dst = fixing
        if src in wrong:                       # not lifted yet -> keep lighting it
            return [src], "Hebe die leuchtende Figur an.", True, fixing
        # Light the destination only if the piece is genuinely in hand. More empty
        # target squares than wrong pieces means a piece is off the board; if the
        # counts are equal the piece was set down on a WRONG square instead, so we
        # fall through and pick that square up first (below).
        if dst in missing and len(missing) > len(wrong):
            return [dst], "Stelle die Figur auf das leuchtende Feld.", False, fixing
        # placed, in hand elsewhere, or done -> fall through

    if wrong:
        src = wrong[0]
        piece = smap[src]
        homes = [m for m in missing if tmap[m] == piece]
        if homes:                              # a misplaced piece: lift it, then place it
            return [src], "Hebe die leuchtende Figur an.", True, (src, homes[0])
        # an extra piece with no home (e.g. a stray/duplicate) -> just take it off
        return [src], "Nimm die Figur vom Brett.", True, None

    # No wrong piece present and no correction in progress -> not a cleanup.
    return [], "", False, None


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


def _needs_all_leds(game: chess.Board, move: chess.Move) -> bool:
    """Castling / en passant / promotion move two pieces or a piece that isn't on
    the from/to line, so they light all involved squares at once (sequencing them
    is a later phase). Everything else -- including a normal capture -- is guided
    one square at a time: lift the mover, then the destination (a piece standing
    on the destination is simply taken off, which is self-evident)."""
    return game.is_castling(move) or game.is_en_passant(move) or bool(move.promotion)


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


def compute_guidance(game: "ChessGame", sensed: chess.Board,
                     fixing: "tuple[int, int] | None" = None) -> Guidance:
    """What to show/say given the game state and the sensed physical position.

    ``fixing`` (tracked by the runner) is the ``(src, dst)`` of the piece being
    cleaned up, so that once it has been lifted we light the square it belongs on
    -- one whole piece at a time -- instead of falling back to "your move" or
    jumping to a different wrong piece. See :func:`_plan_recovery`.
    """
    state, board = game.state, game.board

    if state == GameState.NEED_SETUP:
        start = chess.Board()
        hl, instr, alert, new_fixing = _plan_recovery(sensed, start, fixing)
        if hl:                              # a wrong piece is being corrected
            return Guidance("Figuren aufstellen", instr, hl,
                            target=start, alert=alert, fixing=new_fixing)
        # only missing pieces (still placing them) -> outline what's left to place
        return Guidance("Figuren aufstellen", "Stelle die Figuren in die Grundstellung.",
                        _missing_squares(sensed, start))

    if state == GameState.ENGINE_THINKING:
        return Guidance("Computer denkt …", "Bitte einen Moment warten.")

    if state == GameState.PLAYER_TURN:
        # A bare lifted piece with nothing wrong and no correction in progress is
        # just a move being made -> "your move".
        if fixing is None and _is_lift_of(sensed, board):
            return Guidance("Du bist am Zug", "Mach deinen Zug auf dem Brett.")
        promo = _promotion_square_in_progress(board, sensed)
        if promo is not None:
            return Guidance("Umwandlung",
                            "Ersetze den Bauern auf dem leuchtenden Feld durch eine Dame.",
                            [promo])
        # Wrong pieces on the board: guide the fix one whole piece at a time.
        hl, instr, alert, new_fixing = _plan_recovery(sensed, board, fixing)
        if hl:
            status = "Das passt nicht" if alert else "Fast geschafft"
            return Guidance(status, instr, hl, target=board, alert=alert, fixing=new_fixing)
        return Guidance("Du bist am Zug", "Mach deinen Zug auf dem Brett.")

    if state == GameState.ENGINE_MOVE_SHOWN and game.pending_engine_move is not None:
        move = game.pending_engine_move
        expected = board.copy(stack=False)
        expected.push(move)
        if sensed.piece_map() == expected.piece_map():
            return Guidance("Der Computer hat gezogen", "Der Zug ist ausgeführt.")

        # En passant / castling / promotion: light all involved squares at once
        # (rare and interlocking; sequencing them is a later phase).
        if _needs_all_leds(board, move):
            involved, instr = _engine_move_guidance(board, move)
            executing = _is_lift_of(sensed, board) or _is_lift_of(sensed, expected)
            if executing:
                return Guidance("Der Computer hat gezogen", instr, involved)
            return Guidance("Fast — bitte den leuchtenden Zug ausführen", instr, involved, alert=True)

        # Simple move or normal capture: guide the computer's piece one square at a
        # time -- lift the mover (source lit), then the destination (a piece sitting
        # there is simply taken off). If it's set down on the WRONG square, that
        # square lights until it's lifted, then the correct destination. Seed the
        # (from,to) pairing so this survives a lost fixing state (e.g. a restart).
        seed = fixing if fixing is not None else (move.from_square, move.to_square)
        hl, instr, _plan_alert, new_fixing = _plan_recovery(sensed, expected, seed)
        if not hl:
            return Guidance("Der Computer hat gezogen", "Der Zug ist ausgeführt.")
        executing = _is_lift_of(sensed, board) or _is_lift_of(sensed, expected)
        if executing:                                  # on track -> calm guidance
            return Guidance("Der Computer hat gezogen", instr, hl, fixing=new_fixing)
        return Guidance("Fast — bitte den leuchtenden Zug ausführen", instr, hl,
                        target=expected, alert=True, fixing=new_fixing)

    if state == GameState.GAME_OVER:
        return Guidance(_result_text_for(board),
                        "Für eine neue Partie alle Figuren in die Grundstellung stellen.")

    return Guidance("", "")


def _result_text_for(board: chess.Board) -> str:
    outcome = board.outcome()
    if outcome is None or outcome.winner is None:
        return "Spiel vorbei: Remis"
    return "Spiel vorbei: Weiß gewinnt" if outcome.winner == chess.WHITE else "Spiel vorbei: Schwarz gewinnt"
