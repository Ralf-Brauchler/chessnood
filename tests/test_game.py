"""Tests for the pure game state machine and move detection."""
import chess

from chessnood.boards.base import BoardReading
from chessnood.game import ChessGame, Detection, GameState, detect_move


def reading_of(board: chess.Board) -> BoardReading:
    return BoardReading.from_board(board)


def test_detect_simple_move():
    board = chess.Board()
    after = board.copy()
    after.push_uci("e2e4")
    detection, move = detect_move(board, reading_of(after))
    assert detection == Detection.MOVE
    assert move == chess.Move.from_uci("e2e4")


def test_detect_no_change():
    board = chess.Board()
    detection, move = detect_move(board, reading_of(board))
    assert detection == Detection.NONE
    assert move is None


def test_detect_invalid_transient():
    board = chess.Board()
    # a piece lifted (e2 empty) but not yet placed -> matches no legal position
    lifted = dict(board.piece_map())
    del lifted[chess.E2]
    detection, _ = detect_move(board, BoardReading(lifted))
    assert detection == Detection.INVALID


def test_detect_capture():
    board = chess.Board()
    for uci in ("e2e4", "d7d5"):
        board.push_uci(uci)
    after = board.copy()
    after.push_uci("e4d5")  # pawn takes pawn
    detection, move = detect_move(board, reading_of(after))
    assert detection == Detection.MOVE
    assert move == chess.Move.from_uci("e4d5")


def test_game_setup_to_player_turn():
    game = ChessGame(human_color=chess.WHITE)
    assert game.state == GameState.NEED_SETUP
    react = game.feed(reading_of(chess.Board()))
    assert game.state == GameState.PLAYER_TURN
    assert not react.engine_should_move


def test_game_black_human_triggers_engine_first():
    game = ChessGame(human_color=chess.BLACK)
    react = game.feed(reading_of(chess.Board()))
    assert game.state == GameState.ENGINE_THINKING
    assert react.engine_should_move


def test_full_turn_cycle():
    game = ChessGame(human_color=chess.WHITE)
    game.feed(reading_of(chess.Board()))  # -> PLAYER_TURN

    # player plays e4
    b = chess.Board()
    b.push_uci("e2e4")
    react = game.feed(reading_of(b))
    assert react.engine_should_move
    assert game.state == GameState.ENGINE_THINKING

    # engine chooses a reply -> LEDs lit, waiting for execution
    engine_move = chess.Move.from_uci("e7e5")
    react = game.set_engine_move(engine_move)
    assert set(react.leds) == {chess.E7, chess.E5}
    assert game.state == GameState.ENGINE_MOVE_SHOWN

    # player executes the engine move on the board -> back to player's turn
    b.push(engine_move)
    react = game.feed(reading_of(b))
    assert game.state == GameState.PLAYER_TURN
    assert react.leds == []


def test_auto_new_game_when_pieces_reset_to_start():
    game = ChessGame(human_color=chess.WHITE)
    game.feed(reading_of(chess.Board()))               # NEED_SETUP -> PLAYER_TURN
    b = chess.Board()
    b.push_uci("e2e4")
    game.feed(reading_of(b))                            # -> ENGINE_THINKING
    game.set_engine_move(chess.Move.from_uci("e7e5"))   # -> ENGINE_MOVE_SHOWN
    react = game.feed(reading_of(chess.Board()))        # all pieces back to start
    assert game.state == GameState.PLAYER_TURN
    assert game.board.fen() == chess.STARTING_FEN
    assert game.pending_engine_move is None
    assert not react.engine_should_move


def test_no_false_restart_at_first_move():
    game = ChessGame(human_color=chess.WHITE)
    game.feed(reading_of(chess.Board()))               # PLAYER_TURN, no moves yet
    react = game.feed(reading_of(chess.Board()))        # start pos again != restart
    assert game.state == GameState.PLAYER_TURN
    assert not react.engine_should_move


def test_auto_new_game_from_game_over():
    game = ChessGame(human_color=chess.WHITE)
    for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):       # fool's mate
        game.board.push_uci(uci)
    game.state = GameState.GAME_OVER
    game.feed(reading_of(chess.Board()))                # reset pieces -> new game
    assert game.state == GameState.PLAYER_TURN
    assert game.board.fen() == chess.STARTING_FEN


def test_new_game_resets():
    game = ChessGame()
    game.feed(reading_of(chess.Board()))
    b = chess.Board()
    b.push_uci("e2e4")
    game.feed(reading_of(b))
    game.new_game()
    assert game.state == GameState.NEED_SETUP
    assert game.board.fen() == chess.STARTING_FEN
