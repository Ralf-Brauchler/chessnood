"""Beep cues fire only on the right state transitions."""
import asyncio

import chess

from chessnood.boards.mock import MockBoard
from chessnood.config import ConfigWatcher
from chessnood.game import GameState
from chessnood.runner import Runner


class BeepBoard(MockBoard):
    def __init__(self):
        super().__init__()
        self.beeps = []

    async def beep(self, frequency_hz=1000, duration_ms=150):
        self.beeps.append((frequency_hz, duration_ms))


def _runner(tmp_path, beeps=True):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"board:\n  backend: mock\n  beeps: {str(beeps).lower()}\n"
                   f"display:\n  backend: none\ngame_state_file: {tmp_path / 'g.json'}\n"
                   f"status_file: {tmp_path / 's.json'}\n")
    return Runner(BeepBoard(), ConfigWatcher(str(cfg)))


def test_beep_on_engine_move_shown(tmp_path):
    r = _runner(tmp_path)
    r._game.board = chess.Board()
    r._game.board.push_uci("e2e4")
    r._sensed = chess.Board(r._game.board.fen())
    # entering ENGINE_MOVE_SHOWN should sound the "your turn" tone (900 Hz)
    asyncio.run(r._apply(r._game.set_engine_move(chess.Move.from_uci("e7e5"))))
    assert any(freq == 900 for freq, _ in r._board.beeps)


def test_beep_can_be_disabled(tmp_path):
    r = _runner(tmp_path, beeps=False)
    r._game.board = chess.Board()
    asyncio.run(r._apply(r._game.set_engine_move(chess.Move.from_uci("e2e4"))))
    assert r._board.beeps == []


def test_beep_alert_when_something_is_wrong(tmp_path):
    r = _runner(tmp_path)
    r._game.state = GameState.PLAYER_TURN
    # a piece on a wrong square -> "Das passt nicht" (alert) -> low warning tone
    wrong = chess.Board()
    pm = wrong.piece_map(); pm[chess.A4] = pm.pop(chess.A2)
    wrong.set_piece_map(pm)
    r._sensed = wrong
    asyncio.run(r._apply_guidance(beep=True))
    assert any(freq == 350 for freq, _ in r._board.beeps)


def test_beep_on_game_over(tmp_path):
    r = _runner(tmp_path)
    r._game.state = GameState.GAME_OVER
    r._sensed = chess.Board()
    asyncio.run(r._apply_guidance(beep=True))
    assert any(freq == 600 for freq, _ in r._board.beeps)


def test_alert_beep_fires_once_not_every_reading(tmp_path):
    """The warning tone sounds on the transition into 'wrong', not repeatedly."""
    r = _runner(tmp_path)
    r._game.state = GameState.PLAYER_TURN
    wrong = chess.Board()
    pm = wrong.piece_map(); pm[chess.A4] = pm.pop(chess.A2)
    wrong.set_piece_map(pm)
    r._sensed = wrong
    asyncio.run(r._apply_guidance(beep=True))
    asyncio.run(r._apply_guidance(beep=True))  # still wrong, no new transition
    assert sum(1 for freq, _ in r._board.beeps if freq == 350) == 1
