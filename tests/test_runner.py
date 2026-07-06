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
                   f"game_state_file: {tmp_path / 'g.json'}\n" + extra)
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
                   f"game_state_file: {state_file}\n")
    r = Runner(MockBoard(), ConfigWatcher(str(cfg)))   # must not raise
    assert r._game.state == GameState.NEED_SETUP
    assert r._game.board.fen() == chess.Board().fen()


def _pieces(board: chess.Board) -> chess.Board:
    b = chess.Board(); b.set_piece_map(board.piece_map()); return b


def test_cleanup_lights_one_led_then_destination(tmp_path):
    """End-to-end: a wrong piece lights alone; once lifted the destination lights;
    once placed back it's 'your move' again -- driven through _apply_guidance."""
    r = _runner(tmp_path)
    r._game.state = GameState.PLAYER_TURN
    board = r._game.board                                  # start position

    # 1. wrong placement: e2 pawn sits on e5
    wrong = chess.Board()
    pm = wrong.piece_map(); pm[chess.E5] = pm.pop(chess.E2); wrong.set_piece_map(pm)
    r._sensed = _pieces(wrong)
    asyncio.run(r._apply_guidance(beep=False))
    assert r._restoring is True
    assert r._ui.highlight == [chess.E5] and r._ui.alert   # take the wrong piece off

    # 2. wrong piece lifted (in hand): only the destination e2 lights
    lifted = chess.Board()
    pm = lifted.piece_map(); del pm[chess.E2]; lifted.set_piece_map(pm)
    r._sensed = _pieces(lifted)
    asyncio.run(r._apply_guidance(beep=False))
    assert r._restoring is True                            # still cleaning up
    assert r._ui.highlight == [chess.E2] and not r._ui.alert

    # 3. placed back correctly: fully restored -> 'your move', restoring cleared
    r._sensed = _pieces(board)
    asyncio.run(r._apply_guidance(beep=False))
    assert r._restoring is False
    assert r._ui.status == "Du bist am Zug" and r._ui.highlight == []


def test_save_game_writes_a_restorable_file(tmp_path):
    r = _runner(tmp_path)
    r._game.board.push_uci("e2e4")
    r._game.state = GameState.PLAYER_TURN
    r._save_game()
    # a fresh runner on the same file picks the position back up
    r2 = Runner(MockBoard(), ConfigWatcher(str(tmp_path / "c.yaml")))
    assert r2._game.board.fen() == r._game.board.fen()
    assert r2._game.state == GameState.PLAYER_TURN
