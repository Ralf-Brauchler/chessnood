"""Runner wiring: connection-state screen, and resilience to a bad save file."""
import asyncio

import chess

from chessnood.boards.base import ConnectionState
from chessnood.boards.mock import MockBoard
from chessnood.config import ConfigWatcher
from chessnood.display import Display, UiModel
from chessnood.game import GameState
from chessnood.runner import Runner


class RecordingDisplay(Display):
    def __init__(self):
        super().__init__()
        self.last: UiModel | None = None

    def update(self, model: UiModel) -> None:
        self.last = model


def _runner(tmp_path, extra=""):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("board:\n  backend: mock\ndisplay:\n  backend: none\n"
                   f"game_state_file: {tmp_path / 'g.json'}\n"
                   f"status_file: {tmp_path / 's.json'}\n" + extra)
    r = Runner(MockBoard(), ConfigWatcher(str(cfg)))
    r._display = RecordingDisplay()
    return r


def test_screen_shows_scanning_message(tmp_path):
    r = _runner(tmp_path)
    r._connection = ConnectionState.SCANNING
    r._refresh_screen()
    assert r._display.last.connection == ConnectionState.SCANNING
    assert "Suche" in r._display.last.status


def test_screen_shows_connection_lost_on_error(tmp_path):
    r = _runner(tmp_path)
    r._connection = ConnectionState.ERROR
    r._refresh_screen()
    assert "verloren" in r._display.last.status.lower()


def test_screen_uses_guidance_when_connected(tmp_path):
    r = _runner(tmp_path)
    r._connection = ConnectionState.CONNECTED
    r._refresh_screen()
    # connected -> the screen reflects the game guidance (NEED_SETUP at start)
    assert r._display.last.status != "Nicht verbunden"


def test_corrupt_save_file_does_not_crash_and_starts_fresh(tmp_path):
    state_file = tmp_path / "g.json"
    state_file.write_text("")            # empty/garbage -> json error on restore
    cfg = tmp_path / "c.yaml"
    cfg.write_text("board:\n  backend: mock\ndisplay:\n  backend: none\n"
                   f"game_state_file: {state_file}\n"
                   f"status_file: {tmp_path / 's.json'}\n")
    r = Runner(MockBoard(), ConfigWatcher(str(cfg)))   # must not raise
    assert r._game.state == GameState.NEED_SETUP
    assert r._game.board.fen() == chess.Board().fen()


def _pieces(board: chess.Board) -> chess.Board:
    b = chess.Board(); b.set_piece_map(board.piece_map()); return b


def test_cleanup_lights_one_led_then_destination(tmp_path):
    """End-to-end: a wrong piece lights alone; once lifted the destination lights;
    once placed back it's 'your move' again -- driven through _apply_guidance with
    the runner threading the ``fixing`` state itself."""
    r = _runner(tmp_path)
    r._game.state = GameState.PLAYER_TURN
    board = r._game.board                                  # start position

    # 1. wrong placement: e2 pawn sits on e5
    wrong = chess.Board()
    pm = wrong.piece_map(); pm[chess.E5] = pm.pop(chess.E2); wrong.set_piece_map(pm)
    r._sensed = _pieces(wrong)
    asyncio.run(r._apply_guidance(beep=False))
    assert r._fixing == (chess.E5, chess.E2)
    assert r._ui.highlight == [chess.E5] and r._ui.alert   # lift the wrong piece

    # 2. wrong piece lifted (in hand): only the destination e2 lights
    lifted = chess.Board()
    pm = lifted.piece_map(); del pm[chess.E2]; lifted.set_piece_map(pm)
    r._sensed = _pieces(lifted)
    asyncio.run(r._apply_guidance(beep=False))
    assert r._fixing == (chess.E5, chess.E2)              # still correcting this piece
    assert r._ui.highlight == [chess.E2] and not r._ui.alert

    # 3. placed back correctly: fully restored -> 'your move', fixing cleared
    r._sensed = _pieces(board)
    asyncio.run(r._apply_guidance(beep=False))
    assert r._fixing is None
    assert r._ui.status == "Du bist am Zug" and r._ui.highlight == []


def test_engine_hang_times_out_and_plays_a_fallback(tmp_path, monkeypatch):
    """A wedged engine (best_move never returns) is abandoned within the hard
    timeout and a fallback move is played, so the turn never freezes."""
    import threading
    from chessnood import runner as runner_mod

    cfg = tmp_path / "c.yaml"
    cfg.write_text("board:\n  backend: mock\n  settle_ms: 0\n"
                   "engine:\n  move_time_ms: 20\ndisplay:\n  backend: none\n"
                   f"game_state_file: {tmp_path / 'g.json'}\n"
                   f"status_file: {tmp_path / 's.json'}\n")
    r = Runner(MockBoard(), ConfigWatcher(str(cfg)))
    r._display = RecordingDisplay()
    r._game.state = GameState.ENGINE_THINKING
    r._game.board.push_uci("e2e4")                     # black (engine) to move
    monkeypatch.setattr(runner_mod, "ENGINE_HARD_TIMEOUT_MARGIN_S", 0.1)

    release = threading.Event()

    class HangEngine:
        abandoned = False
        def best_move(self, board):
            release.wait(0.5)                          # "hangs" (released at test end)
            return next(iter(board.legal_moves))
        def fallback_move(self, board):
            return next(iter(board.legal_moves))
        def abandon(self):
            HangEngine.abandoned = True
            release.set()
        def configure(self, cfg):
            pass
    r._engine = HangEngine()

    asyncio.run(r._do_engine_move())
    release.set()
    assert HangEngine.abandoned                        # the wedged engine was dropped
    assert r._game.state == GameState.ENGINE_MOVE_SHOWN
    assert r._game.pending_engine_move is not None     # a fallback move is shown


def test_engine_move_discarded_if_game_restarted_while_thinking(tmp_path):
    """If the player sets up the start position while the engine is thinking, the
    now-stale engine move is discarded rather than forced onto the new game."""
    r = _runner(tmp_path)
    r._game.state = GameState.ENGINE_THINKING
    r._game.board.push_uci("e2e4")

    game = r._game

    class RestartDuringThink:
        def best_move(self, board):
            game.generation += 1                       # simulate a mid-think restart
            game.state = GameState.PLAYER_TURN
            return next(iter(board.legal_moves))
        def fallback_move(self, board):
            return next(iter(board.legal_moves))
        def configure(self, cfg):
            pass
    r._engine = RestartDuringThink()

    asyncio.run(r._do_engine_move())
    assert r._game.state == GameState.PLAYER_TURN       # not forced to ENGINE_MOVE_SHOWN
    assert r._game.pending_engine_move is None          # the stale move was dropped


def test_cross_squares_is_rank_plus_file():
    from chessnood.runner import _cross_squares
    sq = _cross_squares(chess.D5)
    assert len(sq) == 15                                    # 8 on the file + 8 on the rank - 1 shared
    assert chess.D5 in sq
    assert all(s in sq for s in (chess.D1, chess.D8, chess.A5, chess.H5))
    assert chess.E4 not in sq                               # off the cross


def test_capture_flashes_cross_when_piece_lifted(tmp_path):
    """A computer capture flashes the cross through the target once the capturing
    piece is lifted -- not while it's still down, not for a non-capture, not off."""
    fen = "4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1"              # white Pe4, black pd5
    lifted = chess.Board(fen); pm = lifted.piece_map(); del pm[chess.E4]; lifted.set_piece_map(pm)

    async def run():
        from chessnood.runner import _cross_squares
        r = _runner(tmp_path)
        r._game.board = chess.Board(fen)
        r._game.state = GameState.ENGINE_MOVE_SHOWN
        r._game.pending_engine_move = chess.Move.from_uci("e4d5")   # a capture

        r._sensed = chess.Board(fen)                        # capturing piece still down
        assert r._capture_cross() is None
        r._sensed = _pieces(lifted)                         # piece lifted -> flash
        assert r._capture_cross() == _cross_squares(chess.D5)
        assert r._capture_cross() == _cross_squares(chess.D5)   # persists within the window

        for t in asyncio.all_tasks() - {asyncio.current_task()}:
            t.cancel()                                      # clean up spawned _cross_timer(s)
    asyncio.run(run())


def test_no_cross_for_non_capture_or_when_disabled(tmp_path):
    async def run():
        r = _runner(tmp_path)
        r._game.state = GameState.ENGINE_MOVE_SHOWN
        r._game.board = chess.Board()
        r._game.pending_engine_move = chess.Move.from_uci("e2e4")   # not a capture
        lifted = chess.Board(); pm = lifted.piece_map(); del pm[chess.E2]; lifted.set_piece_map(pm)
        r._sensed = _pieces(lifted)
        assert r._capture_cross() is None                   # non-capture -> no cross

        # a capture, but the signal is switched off
        r._game.board = chess.Board("4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1")
        r._game.pending_engine_move = chess.Move.from_uci("e4d5")
        cap_lifted = chess.Board("4k3/8/8/3p4/8/8/8/4K3 w - - 0 1")   # e4 empty
        r._sensed = _pieces(cap_lifted)
        r._capture_signal = False
        assert r._capture_cross() is None
    asyncio.run(run())


def test_accept_wrong_position_after_the_timeout(tmp_path):
    """When the timer fires on a stuck wrong position, the runner adopts the sensed
    board and it becomes the player's turn (guidance clears)."""
    r = _runner(tmp_path)
    r._connection = ConnectionState.CONNECTED
    r._game.state = GameState.PLAYER_TURN
    r._game.board = chess.Board()                       # tracked = start position
    # sensed = a different, legal position (two pawns moved) -> a wrong state
    sensed = chess.Board(); sensed.push_uci("e2e4"); sensed.push_uci("e7e5"); sensed.push_uci("d2d4")
    r._sensed = _pieces(sensed)
    r._recompute_guidance()
    assert r._ui.alert                                  # it's a wrong position

    asyncio.run(r._accept_wrong_position())
    assert r._game.state == GameState.PLAYER_TURN
    assert r._game.board.turn == chess.WHITE
    assert r._game.board.piece_at(chess.D4) and r._game.board.piece_at(chess.E4)
    assert not r._ui.alert                              # now "your move", no longer wrong


def test_accept_timer_not_armed_when_position_is_right(tmp_path):
    r = _runner(tmp_path)
    r._loop = asyncio.new_event_loop()
    try:
        r._game.state = GameState.PLAYER_TURN
        r._sensed = chess.Board()                       # matches the tracked board
        r._recompute_guidance()
        assert not r._ui.alert
        r._arm_accept_timer()
        assert r._accept_handle is None                 # nothing to accept -> no timer
        # now a wrong position -> a timer is armed
        sensed = chess.Board(); sensed.push_uci("e2e4"); sensed.push_uci("e7e5"); sensed.push_uci("d2d4")
        r._sensed = _pieces(sensed)
        r._recompute_guidance()
        r._arm_accept_timer()
        assert r._accept_handle is not None
        r._accept_handle.cancel()
    finally:
        r._loop.close()


def test_save_game_writes_a_restorable_file(tmp_path):
    r = _runner(tmp_path)
    r._game.board.push_uci("e2e4")
    r._game.state = GameState.PLAYER_TURN
    r._save_game()
    # a fresh runner on the same file picks the position back up
    r2 = Runner(MockBoard(), ConfigWatcher(str(tmp_path / "c.yaml")))
    assert r2._game.board.fen() == r._game.board.fen()
    assert r2._game.state == GameState.PLAYER_TURN
