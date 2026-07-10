"""Tests for the on-screen / on-board guidance (self-healing + special moves)."""
import chess

from chessnood.boards.base import BoardReading
from chessnood.game import ChessGame, GameState, compute_guidance


def _game(fen: str, state: GameState, pending: str | None = None) -> ChessGame:
    g = ChessGame()
    g.board = chess.Board(fen)
    g.state = state
    if pending:
        g.pending_engine_move = chess.Move.from_uci(pending)
    return g


def _sensed(fen: str) -> chess.Board:
    return chess.Board(fen)


def test_player_turn_normal():
    g = _game(chess.STARTING_FEN, GameState.PLAYER_TURN)
    gd = compute_guidance(g, chess.Board())
    assert gd.status == "Du bist am Zug"
    assert not gd.alert and gd.highlight == []


def test_strength_selection_shows_level_and_lights_king():
    g = _game(chess.STARTING_FEN, GameState.PLAYER_TURN)
    sensed = chess.Board()
    pm = sensed.piece_map()
    del pm[chess.E1]
    pm[chess.F4] = chess.Piece(chess.KING, chess.WHITE)   # file f -> level 6
    sensed.set_piece_map(pm)
    gd = compute_guidance(g, sensed)
    assert gd.status == "Spielstärke wählen"
    assert "6" in gd.instruction
    assert gd.highlight == [chess.F4]
    assert not gd.alert


def test_lifted_piece_is_not_an_error():
    g = _game(chess.STARTING_FEN, GameState.PLAYER_TURN)
    sensed = chess.Board()
    pm = sensed.piece_map(); del pm[chess.E2]      # pawn in hand
    sensed.set_piece_map(pm)
    gd = compute_guidance(g, sensed)
    assert gd.status == "Du bist am Zug" and not gd.alert


def _with_piece_moved(frm: int, to: int) -> chess.Board:
    b = chess.Board()
    pm = b.piece_map(); pm[to] = pm.pop(frm); b.set_piece_map(pm)
    return b


def test_wrong_placement_lights_one_square_at_a_time():
    """One misplaced piece: light it to lift, then light where it belongs."""
    g = _game(chess.STARTING_FEN, GameState.PLAYER_TURN)
    sensed = _with_piece_moved(chess.E2, chess.E5)   # pawn teleported to a non-legal spot
    # step 1: only the wrong piece lights (lift it) -- NOT its destination too
    gd = compute_guidance(g, sensed, fixing=None)
    assert gd.alert and gd.target is not None
    assert gd.highlight == [chess.E5]
    assert gd.fixing == (chess.E5, chess.E2)         # remembers where it goes
    # step 2: wrong piece lifted (in hand) -> now the destination e2 lights
    lifted = chess.Board()
    pm = lifted.piece_map(); del pm[chess.E2]; lifted.set_piece_map(pm)
    gd = compute_guidance(g, lifted, fixing=gd.fixing)
    assert gd.highlight == [chess.E2] and not gd.alert
    # step 3: placed back -> fully correct -> your move, no LED, fixing cleared
    gd = compute_guidance(g, chess.Board(), fixing=gd.fixing)
    assert gd.status == "Du bist am Zug" and gd.highlight == [] and gd.fixing is None


def test_bare_lift_without_fixing_is_your_move():
    """A lifted piece during normal play (nothing wrong) is not a cleanup."""
    g = _game(chess.STARTING_FEN, GameState.PLAYER_TURN)
    lifted = chess.Board()
    pm = lifted.piece_map(); del pm[chess.E2]; lifted.set_piece_map(pm)
    gd = compute_guidance(g, lifted, fixing=None)
    assert gd.status == "Du bist am Zug" and gd.highlight == []


def test_two_wrong_pieces_fixed_one_whole_piece_at_a_time():
    """The core fix: correct one piece fully (lift THEN place) before the next --
    never 'lift everything first'."""
    g = _game(chess.STARTING_FEN, GameState.PLAYER_TURN)
    sensed = chess.Board()
    pm = sensed.piece_map()
    pm[chess.E5] = pm.pop(chess.E2)
    pm[chess.D5] = pm.pop(chess.D2)                  # two pawns misplaced (d5 < e5)
    sensed.set_piece_map(pm)

    fixing = None
    # step 1: lift the first (lowest) wrong piece -- exactly one LED
    gd = compute_guidance(g, sensed, fixing=fixing); fixing = gd.fixing
    assert gd.highlight == [chess.D5] and gd.fixing == (chess.D5, chess.D2)
    # step 2: d5 lifted, e5 STILL wrong -> we place d5's piece, not lift e5
    pm = sensed.piece_map(); del pm[chess.D5]; s2 = chess.Board(); s2.set_piece_map(pm)
    gd = compute_guidance(g, s2, fixing=fixing); fixing = gd.fixing
    assert gd.highlight == [chess.D2] and not gd.alert   # place it, ignore e5 for now
    # step 3: d-pawn placed back; now the second piece starts
    pm = s2.piece_map(); pm[chess.D2] = chess.Piece(chess.PAWN, chess.WHITE)
    s3 = chess.Board(); s3.set_piece_map(pm)
    gd = compute_guidance(g, s3, fixing=fixing); fixing = gd.fixing
    assert gd.highlight == [chess.E5] and gd.fixing == (chess.E5, chess.E2)
    # step 4: e5 lifted -> place on e2
    pm = s3.piece_map(); del pm[chess.E5]; s4 = chess.Board(); s4.set_piece_map(pm)
    gd = compute_guidance(g, s4, fixing=fixing); fixing = gd.fixing
    assert gd.highlight == [chess.E2]
    # step 5: fully restored
    gd = compute_guidance(g, chess.Board(), fixing=fixing)
    assert gd.highlight == [] and gd.fixing is None


def test_promotion_in_progress_guidance():
    # white pawn on e7 can promote on e8 (empty); player pushed it but left a pawn
    g = _game("7k/4P3/8/8/8/8/8/4K3 w - - 0 1", GameState.PLAYER_TURN)
    sensed = g.board.copy()
    pm = sensed.piece_map(); pm[chess.E8] = pm.pop(chess.E7)  # pawn now on e8
    sensed.set_piece_map(pm)
    gd = compute_guidance(g, sensed)
    assert gd.status == "Umwandlung" and chess.E8 in gd.highlight


def test_setup_wrong_piece_alerts():
    g = _game(chess.STARTING_FEN, GameState.NEED_SETUP)
    sensed = chess.Board()
    pm = sensed.piece_map(); pm[chess.E4] = chess.Piece(chess.QUEEN, chess.WHITE)
    sensed.set_piece_map(pm)
    gd = compute_guidance(g, sensed)
    assert gd.alert and chess.E4 in gd.highlight


def test_setup_still_placing_is_not_alert():
    g = _game(chess.STARTING_FEN, GameState.NEED_SETUP)
    sensed = chess.Board()
    pm = sensed.piece_map(); del pm[chess.A1]      # one rook not yet placed
    sensed.set_piece_map(pm)
    gd = compute_guidance(g, sensed)
    assert not gd.alert and chess.A1 in gd.highlight


def test_engine_simple_move_guides_source_then_destination():
    g = _game(chess.STARTING_FEN, GameState.ENGINE_MOVE_SHOWN, pending="e2e4")
    # step 1: source still on the board -> light only the source (pick it up)
    gd = compute_guidance(g, chess.Board())
    assert gd.highlight == [chess.E2]
    assert "Hebe" in gd.instruction and not gd.alert
    # step 2: source lifted (e2 empty) -> light only the destination (place it)
    lifted = chess.Board()
    pm = lifted.piece_map(); del pm[chess.E2]; lifted.set_piece_map(pm)
    gd = compute_guidance(g, lifted)
    assert gd.highlight == [chess.E4]
    assert "leuchtende Feld" in gd.instruction and not gd.alert


def test_engine_move_wrong_square_goes_into_correction():
    """Executing the computer's move, if the piece is set on the WRONG square, that
    square lights (alert) until it's lifted, then the correct destination lights."""
    g = _game(chess.STARTING_FEN, GameState.ENGINE_MOVE_SHOWN, pending="e2e4")

    # correct piece placed on the wrong square e3 (destination is e4)
    wrong = chess.Board()
    pm = wrong.piece_map(); pm[chess.E3] = pm.pop(chess.E2); wrong.set_piece_map(pm)
    gd = compute_guidance(g, wrong, fixing=None)
    assert gd.alert and gd.highlight == [chess.E3]        # light the wrong square
    assert "Hebe" in gd.instruction
    assert gd.fixing == (chess.E3, chess.E4)

    # lifted off the wrong square -> now the real destination e4 lights, calm
    lifted = chess.Board()
    pm = lifted.piece_map(); del pm[chess.E2]; lifted.set_piece_map(pm)
    gd = compute_guidance(g, lifted, fixing=gd.fixing)
    assert not gd.alert and gd.highlight == [chess.E4]
    assert "leuchtende Feld" in gd.instruction


def test_engine_capture_guides_source_then_destination():
    """A capture is guided like a simple move: lift the mover (source), then the
    destination lights -- the piece standing there is simply taken off. One LED
    each, never source+destination together."""
    fen = "4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1"   # white Pe4, black pd5
    g = _game(fen, GameState.ENGINE_MOVE_SHOWN, pending="e4d5")
    board = chess.Board(fen)

    # step 1: light the source e4 -- lift the computer's (capturing) piece
    gd = compute_guidance(g, board, fixing=None)
    assert gd.highlight == [chess.E4] and "Hebe" in gd.instruction and not gd.alert

    # step 2: mover lifted, enemy still on d5 -> the destination d5 lights
    s1 = board.copy(); pm = s1.piece_map(); del pm[chess.E4]; s1.set_piece_map(pm)
    gd = compute_guidance(g, s1, fixing=gd.fixing)
    assert gd.highlight == [chess.D5] and not gd.alert

    # step 3: enemy taken off d5 -> d5 still lit, now to place the piece
    s2 = s1.copy(); pm = s2.piece_map(); del pm[chess.D5]; s2.set_piece_map(pm)
    gd = compute_guidance(g, s2, fixing=gd.fixing)
    assert gd.highlight == [chess.D5] and "leuchtende Feld" in gd.instruction

    # done: pawn now on d5, e4 empty
    expected = board.copy(); expected.push_uci("e4d5")
    gd = compute_guidance(g, expected, fixing=gd.fixing)
    assert gd.highlight == [] and gd.status == "Der Computer hat gezogen"


def test_engine_castling_lights_king_and_rook():
    fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
    g = _game(fen, GameState.ENGINE_MOVE_SHOWN, pending="e1g1")
    gd = compute_guidance(g, _sensed(fen))
    assert {chess.E1, chess.G1, chess.H1, chess.F1} <= set(gd.highlight)
    assert "Rochade" in gd.instruction


def test_engine_en_passant_lights_captured_pawn():
    fen = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"
    g = _game(fen, GameState.ENGINE_MOVE_SHOWN, pending="e5d6")
    gd = compute_guidance(g, _sensed(fen))
    assert chess.D5 in gd.highlight  # the pawn to remove
    assert "passant" in gd.instruction.lower()


def test_engine_move_wrong_execution_alerts():
    g = _game(chess.STARTING_FEN, GameState.ENGINE_MOVE_SHOWN, pending="e2e4")
    wrong = chess.Board()
    pm = wrong.piece_map(); pm[chess.A4] = pm.pop(chess.A2)  # moved the wrong pawn
    wrong.set_piece_map(pm)
    gd = compute_guidance(g, wrong)
    assert gd.alert


# --- player-side castling / en passant done one piece at a time ----------
# The player nearly always moves the king first when castling (touch-move rule),
# leaving a half-done position that matches no single legal move. It must NOT read
# as a wrong position (no alarm, no "put the king back"): guide the rook instead.

_CASTLE_FEN = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"


def _king_moved(fen: str, frm: int, to: int) -> chess.Board:
    b = chess.Board(fen)
    pm = b.piece_map(); pm[to] = pm.pop(frm); b.set_piece_map(pm)
    return b


def test_player_castling_king_first_is_not_wrong():
    g = _game(_CASTLE_FEN, GameState.PLAYER_TURN)
    # king already on g1, rook still on h1 -- half-done kingside castling
    sensed = _king_moved(_CASTLE_FEN, chess.E1, chess.G1)
    gd = compute_guidance(g, sensed, fixing=None)
    assert not gd.alert                                   # no "das passt nicht"
    assert chess.E1 not in gd.highlight                   # never asks to undo the king
    assert {chess.H1, chess.F1} == set(gd.highlight)      # light the rook to move
    assert "Turm" in gd.instruction


def test_player_castling_king_first_does_not_commit_or_alarm():
    """feed() must not flag the half-done castling as invalid, nor commit a move."""
    g = _game(_CASTLE_FEN, GameState.PLAYER_TURN)
    sensed = _king_moved(_CASTLE_FEN, chess.E1, chess.G1)
    reaction = g.feed(BoardReading.from_board(sensed))
    assert not reaction.invalid                           # no alarm beep
    assert g.state == GameState.PLAYER_TURN                # nothing committed yet
    # completing it (rook to f1) is recognised as the castling move
    done = chess.Board(_CASTLE_FEN); done.push_uci("e1g1")
    reaction = g.feed(BoardReading.from_board(done))
    assert g.board.peek() == chess.Move.from_uci("e1g1")
    assert g.state == GameState.ENGINE_THINKING


def test_player_en_passant_pawn_first_is_not_wrong():
    fen = "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1"             # white Pe5, black pd5, ep on d6
    g = _game(fen, GameState.PLAYER_TURN)
    # white pawn advanced to d6 but the captured black pawn still on d5
    sensed = chess.Board(fen)
    pm = sensed.piece_map(); pm[chess.D6] = pm.pop(chess.E5); sensed.set_piece_map(pm)
    gd = compute_guidance(g, sensed, fixing=None)
    assert not gd.alert
    assert gd.highlight == [chess.D5]                     # light the pawn to remove
    reaction = g.feed(BoardReading.from_board(sensed))
    assert not reaction.invalid


def test_real_wrong_position_still_alarms():
    """The partial-move escape must not swallow a genuinely wrong placement."""
    g = _game(_CASTLE_FEN, GameState.PLAYER_TURN)
    sensed = _king_moved(_CASTLE_FEN, chess.A1, chess.A5)  # rook wandered off
    gd = compute_guidance(g, sensed, fixing=None)
    assert gd.alert
