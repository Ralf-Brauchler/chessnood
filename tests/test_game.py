"""Tests for the pure game state machine and move detection."""
import chess

from chessnood.boards.base import BoardReading
from chessnood.game import (
    ChessGame,
    Detection,
    GameState,
    detect_move,
    detect_strength_selection,
)


def reading_of(board: chess.Board) -> BoardReading:
    return BoardReading.from_board(board)


def _king_on(square: int, color: chess.Color = chess.WHITE) -> dict:
    """Start position with ``color``'s king lifted onto ``square``."""
    home = chess.E1 if color == chess.WHITE else chess.E8
    pm = chess.Board().piece_map()
    del pm[home]
    pm[square] = chess.Piece(chess.KING, color)
    return pm


def test_strength_selection_maps_files_a_to_h_to_1_to_8():
    game = ChessGame(human_color=chess.WHITE)
    files = [chess.A3, chess.B4, chess.C3, chess.D5, chess.E4, chess.F6, chess.G3, chess.H5]
    for expected, square in enumerate(files, start=1):
        react = game.feed(BoardReading(_king_on(square)))
        assert react.select_skill == expected
        assert react.engine_should_move is False
        assert game.state == GameState.NEED_SETUP     # a gesture never starts play


def test_strength_selection_ignores_rank_only_file_counts():
    game = ChessGame(human_color=chess.WHITE)
    assert game.feed(BoardReading(_king_on(chess.C3))).select_skill == 3
    assert game.feed(BoardReading(_king_on(chess.C6))).select_skill == 3


def test_no_selection_when_king_home_begins_play():
    game = ChessGame(human_color=chess.WHITE)
    react = game.feed(reading_of(chess.Board()))
    assert react.select_skill is None
    assert game.state == GameState.PLAYER_TURN


def test_no_selection_once_a_move_has_been_played():
    game = ChessGame(human_color=chess.WHITE)
    game.feed(reading_of(chess.Board()))              # begin play
    board = chess.Board(); board.push_uci("e2e4")
    game.board = board
    # even a king-on-empty pattern must not be read as a selection mid-game
    assert detect_strength_selection(board, _king_on(chess.C3)) is None


def test_no_selection_when_another_piece_also_moved():
    board = chess.Board()
    pieces = _king_on(chess.C3)
    pieces[chess.E5] = pieces.pop(chess.E2)           # also nudged a pawn
    assert detect_strength_selection(board, pieces) is None


def test_black_king_gesture_makes_human_play_black_and_engine_opens():
    game = ChessGame(human_color=chess.WHITE)
    game.feed(reading_of(chess.Board()))              # normally begins as White
    # lift the BLACK king onto the c-file: play black at level 3
    react = game.feed(BoardReading(_king_on(chess.C5, chess.BLACK)))
    assert react.select_skill == 3
    assert react.select_color == chess.BLACK
    assert game.human_color == chess.BLACK
    # king back home -> the computer (White) opens
    react2 = game.feed(reading_of(chess.Board()))
    assert react2.engine_should_move
    assert game.state == GameState.ENGINE_THINKING


def test_white_king_gesture_switches_side_back_to_white():
    game = ChessGame(human_color=chess.BLACK)
    react = game.feed(BoardReading(_king_on(chess.B3, chess.WHITE)))
    assert react.select_skill == 2 and react.select_color == chess.WHITE
    assert game.human_color == chess.WHITE
    react2 = game.feed(reading_of(chess.Board()))    # king home -> human (White) to move
    assert not react2.engine_should_move
    assert game.state == GameState.PLAYER_TURN


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


def _kq_swapped_start() -> chess.Board:
    """The start position with the white king and queen swapped (d1/e1) -- the
    classic setup mix-up."""
    b = chess.Board()
    pm = b.piece_map()
    pm[chess.D1], pm[chess.E1] = pm[chess.E1], pm[chess.D1]
    b.set_piece_map(pm)
    return b


def test_new_game_recognised_with_king_queen_swapped():
    """Mid-game, setting up a start-shaped position with the king and queen
    swapped must still start a new game (into setup, so the guidance fixes the
    order) -- not stay stuck because it isn't an *exact* start position."""
    game = ChessGame(human_color=chess.WHITE)
    game.feed(reading_of(chess.Board()))
    b = chess.Board(); b.push_uci("e2e4"); game.feed(reading_of(b))
    game.set_engine_move(chess.Move.from_uci("e7e5"))   # ENGINE_MOVE_SHOWN, mid-game
    react = game.feed(reading_of(_kq_swapped_start()))  # start shape, K/Q swapped
    assert game.state == GameState.NEED_SETUP           # new game, guiding the fix
    assert game.board.fen() == chess.STARTING_FEN       # internal board reset to standard
    assert game.pending_engine_move is None
    # once the exact start is set up, play begins
    react = game.feed(reading_of(chess.Board()))
    assert game.state == GameState.PLAYER_TURN


def test_restart_recognised_while_engine_thinking():
    """The start position must reset the game even during 'Computer denkt', so the
    player is never stuck through a long/hung computer turn. Generation bumps so
    the runner discards the stale engine move."""
    game = ChessGame(human_color=chess.WHITE)
    game.feed(reading_of(chess.Board()))               # -> PLAYER_TURN
    b = chess.Board(); b.push_uci("e2e4")
    game.feed(reading_of(b))                            # -> ENGINE_THINKING
    assert game.state == GameState.ENGINE_THINKING
    gen = game.generation
    game.feed(reading_of(chess.Board()))                # start position mid-think
    assert game.state == GameState.PLAYER_TURN          # new game began
    assert game.generation != gen                       # -> runner discards the move


def test_accept_position_adopts_a_valid_board_with_human_to_move():
    g = ChessGame(human_color=chess.WHITE)
    g.feed(reading_of(chess.Board()))                  # PLAYER_TURN
    # a legal but non-single-move position (two white pawns advanced)
    sensed = chess.Board(); sensed.push_uci("e2e4"); sensed.push_uci("e7e5"); sensed.push_uci("d2d4")
    gen = g.generation
    react = g.accept_position(sensed)
    assert react.message                               # adopted
    assert g.state == GameState.PLAYER_TURN
    assert g.board.turn == chess.WHITE                 # the player moves next
    assert g.board.piece_at(chess.D4) and g.board.piece_at(chess.E4)
    assert g.generation != gen                         # bumped (discards any pending)


def test_accept_position_refuses_an_illegal_board():
    g = ChessGame(human_color=chess.WHITE)
    sensed = chess.Board(None)
    sensed.set_piece_map({chess.E4: chess.Piece(chess.PAWN, chess.WHITE)})  # no kings
    react = g.accept_position(sensed)
    assert not react.message                           # not adopted
    assert g.board.piece_at(chess.E4) is None          # game board unchanged


def test_is_start_setup_rejects_a_midgame_position():
    from chessnood.game import _is_start_setup
    b = chess.Board()
    for uci in ("e2e4", "e7e5", "g1f3"):
        b.push_uci(uci)
    assert not _is_start_setup(b.piece_map())
    assert _is_start_setup(chess.Board().piece_map())
    assert _is_start_setup(_kq_swapped_start().piece_map())


def test_no_false_restart_at_first_move():
    game = ChessGame(human_color=chess.WHITE)
    game.feed(reading_of(chess.Board()))               # PLAYER_TURN, no moves yet
    react = game.feed(reading_of(chess.Board()))        # start pos again != restart
    assert game.state == GameState.PLAYER_TURN
    assert not react.engine_should_move


def test_auto_new_game_after_resume_from_disk():
    """After a reboot, a game restored from disk (empty move_stack, mid-position)
    must still accept the start position as a new-game request -- regression for
    the `not move_stack` guard that left a resumed game with no way out."""
    saved = ChessGame(human_color=chess.WHITE)
    saved.feed(reading_of(chess.Board()))              # NEED_SETUP -> PLAYER_TURN
    b = chess.Board()
    b.push_uci("e2e4")
    saved.feed(reading_of(b))                          # -> ENGINE_THINKING
    saved.set_engine_move(chess.Move.from_uci("c7c5")) # -> ENGINE_MOVE_SHOWN
    snap = saved.snapshot()

    # Simulate the reboot: a fresh game object restored from the snapshot. This
    # rebuilds the board from FEN, so move_stack is empty though we're mid-game.
    resumed = ChessGame()
    resumed.restore(snap)
    assert not resumed.board.move_stack
    assert resumed.state == GameState.ENGINE_MOVE_SHOWN

    react = resumed.feed(reading_of(chess.Board()))     # set up the start position
    assert resumed.state == GameState.PLAYER_TURN
    assert resumed.board.fen() == chess.STARTING_FEN
    assert resumed.pending_engine_move is None
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


def test_lifted_piece_is_a_transient_then_move_detected():
    """A 'piece lifted' reading is INVALID (ignored); the completed move is detected."""
    game = ChessGame(human_color=chess.WHITE)
    game.feed(reading_of(chess.Board()))  # -> PLAYER_TURN

    # 1) lift the e2 pawn (e2 empty) -> no legal move matches -> transient
    lifted = dict(chess.Board().piece_map())
    del lifted[chess.E2]
    react = game.feed(BoardReading(lifted))
    assert react.invalid
    assert not react.engine_should_move
    assert game.state == GameState.PLAYER_TURN

    # 2) place it on e4 -> completed move detected
    after = chess.Board()
    after.push_uci("e2e4")
    react = game.feed(reading_of(after))
    assert react.engine_should_move
    assert game.state == GameState.ENGINE_THINKING


def test_slide_over_intermediate_is_not_committed(tmp_path):
    """Sliding a pawn over e3 to e4 must commit e2e4, not the transient e2e3."""
    import asyncio
    import chess as _chess
    from chessnood.boards.mock import MockBoard
    from chessnood.config import ConfigWatcher
    from chessnood.runner import Runner

    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"board:\n  backend: mock\n  settle_ms: 80\ndisplay:\n  backend: none\n"
                   f"game_state_file: {tmp_path / 'game.json'}\n"
                   f"status_file: {tmp_path / 'status.json'}\n")

    async def _run():
        board = MockBoard()
        watcher = ConfigWatcher(str(cfg))
        runner = Runner(board, watcher)
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.2)            # start position settles -> PLAYER_TURN
        over = _chess.Board(); over.push_uci("e2e3")
        board.set_position(over)            # pawn momentarily on e3 (a legal move!)
        await asyncio.sleep(0.02)           # < settle window
        final = _chess.Board(); final.push_uci("e2e4")
        board.set_position(final)           # ... continues to e4
        await asyncio.sleep(0.25)           # > settle -> commit
        b = runner._game.board
        task.cancel()
        return b

    b = asyncio.run(_run())
    assert b.piece_at(chess.E4) is not None
    assert b.piece_at(chess.E3) is None
    assert b.move_stack and b.move_stack[0] == chess.Move.from_uci("e2e4")


def test_snapshot_restore_roundtrip():
    g = ChessGame(human_color=chess.BLACK)
    g.board.push_uci("e2e4")
    g.state = GameState.ENGINE_MOVE_SHOWN
    g.pending_engine_move = chess.Move.from_uci("e7e5")
    snap = g.snapshot()

    g2 = ChessGame()
    g2.restore(snap)
    assert g2.board.fen() == g.board.fen()
    assert g2.state == GameState.ENGINE_MOVE_SHOWN
    assert g2.pending_engine_move == chess.Move.from_uci("e7e5")
    assert g2.human_color == chess.BLACK


def test_runner_resumes_saved_game(tmp_path):
    import json
    from chessnood.boards.mock import MockBoard
    from chessnood.config import ConfigWatcher
    from chessnood.runner import Runner

    b = chess.Board()
    b.push_uci("e2e4"); b.push_uci("e7e5")
    state_file = tmp_path / "game.json"
    state_file.write_text(json.dumps({
        "fen": b.fen(), "state": "PLAYER_TURN", "pending": None, "human_color": "white"
    }))
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"board:\n  backend: mock\ndisplay:\n  backend: none\n"
                   f"game_state_file: {state_file}\n"
                   f"status_file: {tmp_path / 'status.json'}\n")
    runner = Runner(MockBoard(), ConfigWatcher(str(cfg)))
    assert runner._game.board.fen() == b.fen()
    assert runner._game.state == GameState.PLAYER_TURN
