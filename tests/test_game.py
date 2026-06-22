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


def test_selfplay_board_drives_a_game_via_runner(tmp_path):
    """The SelfPlayBoard + real Runner should advance a game without hardware."""
    import asyncio
    import chess as _chess
    from chessnood.boards.mock import SelfPlayBoard
    from chessnood.config import ConfigWatcher
    from chessnood.runner import Runner

    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"board:\n  backend: mock\n  settle_ms: 0\ndisplay:\n  backend: none\n"
                   f"game_state_file: {tmp_path / 'game.json'}\n")

    async def _drive():
        board = SelfPlayBoard(human_color=_chess.WHITE, move_pause=0.0, transient_pause=0.0)
        watcher = ConfigWatcher(str(cfg))
        runner = Runner(board, watcher)
        task = asyncio.create_task(runner.run())
        # let the self-play loop make a handful of plies
        for _ in range(50):
            await asyncio.sleep(0)
            if len(board._board.move_stack) >= 4:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        return len(board._board.move_stack)

    plies = asyncio.run(_drive())
    assert plies >= 4


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


def test_selfplay_capture_emits_transient_then_final(tmp_path):
    """SelfPlayBoard plays a capture as lift(s) -> final, both whole positions."""
    import asyncio
    import chess as _chess
    from chessnood.boards.base import BoardReading as _BR
    from chessnood.boards.mock import SelfPlayBoard

    async def _run():
        board = SelfPlayBoard(move_pause=0.0, transient_pause=0.0)
        readings = board.subscribe_readings()
        b = _chess.Board()
        for uci in ("e2e4", "d7d5"):  # set up a capture: exd5
            b.push_uci(uci)
        board._board = b.copy()
        cap = _chess.Move.from_uci("e4d5")
        await board._play_as_sequence(cap)
        out = []
        while not readings.empty():
            out.append(readings.get_nowait())
        return out

    out = asyncio.run(_run())
    assert len(out) == 2  # transient then final
    assert isinstance(out[0], _BR)
    # transient: the capturing pawn has been lifted off e4 (and d5 not yet ours)
    assert chess.E4 not in out[0].pieces
    # final: our pawn now stands on d5
    assert out[-1].pieces[chess.D5] == chess.Piece.from_symbol("P")


def test_selfplay_fumble_emits_a_wrong_position_then_corrects(tmp_path):
    """With mistakes enabled, a move is played as lift -> wrong placement -> final,
    and the wrong placement reads as INVALID (which triggers the recovery UI)."""
    import asyncio
    import chess as _chess
    from chessnood.boards.mock import SelfPlayBoard
    from chessnood.game import Detection as _Det, detect_move as _detect

    async def _run():
        # mistake_chance=1.0 -> always fumble; tiny pauses keep the test fast
        board = SelfPlayBoard(move_pause=0.0, transient_pause=0.0,
                              mistake_chance=1.0, mistake_pause=0.0)
        readings = board.subscribe_readings()
        pre = board._board.copy()
        await board._play_as_sequence(_chess.Move.from_uci("e2e4"))
        out = []
        while not readings.empty():
            out.append(readings.get_nowait())
        return pre, out

    pre, out = asyncio.run(_run())
    assert len(out) == 3  # lift -> fumble -> final
    # the fumble reads as INVALID on the pre-move board (no legal move matches it)
    assert _detect(pre, out[1])[0] == _Det.INVALID
    # ...and the final reading is the real, completed move
    expected = pre.copy()
    expected.push_uci("e2e4")
    assert out[-1].pieces == expected.piece_map()


def test_selfplay_fumble_disabled_by_default(tmp_path):
    """Default (mistake_chance=0) plays cleanly: lift -> final, no wrong placement."""
    import asyncio
    import chess as _chess
    from chessnood.boards.mock import SelfPlayBoard

    async def _run():
        board = SelfPlayBoard(move_pause=0.0, transient_pause=0.0)
        readings = board.subscribe_readings()
        await board._play_as_sequence(_chess.Move.from_uci("e2e4"))
        out = []
        while not readings.empty():
            out.append(readings.get_nowait())
        return out

    assert len(asyncio.run(_run())) == 2  # transient then final, no fumble


def test_slide_over_intermediate_is_not_committed(tmp_path):
    """Sliding a pawn over e3 to e4 must commit e2e4, not the transient e2e3."""
    import asyncio
    import chess as _chess
    from chessnood.boards.mock import MockBoard
    from chessnood.config import ConfigWatcher
    from chessnood.runner import Runner

    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"board:\n  backend: mock\n  settle_ms: 80\ndisplay:\n  backend: none\n"
                   f"game_state_file: {tmp_path / 'game.json'}\n")

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
                   f"game_state_file: {state_file}\n")
    runner = Runner(MockBoard(), ConfigWatcher(str(cfg)))
    assert runner._game.board.fen() == b.fen()
    assert runner._game.state == GameState.PLAYER_TURN
