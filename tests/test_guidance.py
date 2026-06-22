"""Tests for the on-screen / on-board guidance (self-healing + special moves)."""
import chess

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


def test_lifted_piece_is_not_an_error():
    g = _game(chess.STARTING_FEN, GameState.PLAYER_TURN)
    sensed = chess.Board()
    pm = sensed.piece_map(); del pm[chess.E2]      # pawn in hand
    sensed.set_piece_map(pm)
    gd = compute_guidance(g, sensed)
    assert gd.status == "Du bist am Zug" and not gd.alert


def test_wrong_placement_triggers_fix_guidance():
    g = _game(chess.STARTING_FEN, GameState.PLAYER_TURN)
    sensed = chess.Board()
    pm = sensed.piece_map()
    pm[chess.E5] = pm.pop(chess.E2)                # pawn teleported to a non-legal spot
    sensed.set_piece_map(pm)
    gd = compute_guidance(g, sensed)
    assert gd.alert
    assert gd.target is not None                    # shows the position to restore
    assert chess.E5 in gd.highlight and chess.E2 in gd.highlight


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
