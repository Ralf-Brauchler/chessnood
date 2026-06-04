"""Chess opponent: Stockfish over UCI, with a random-mover fallback.

If the configured engine binary cannot be started (e.g. Stockfish not installed
yet), we fall back to a legal-random mover so the rest of the system still runs.
This is what lets the whole project work on your Mac before anything is set up.
"""
from __future__ import annotations

import logging
import random

import chess
import chess.engine

from .config import EngineConfig

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, cfg: EngineConfig):
        self._cfg = cfg
        self._engine: chess.engine.SimpleEngine | None = None
        self._open()

    def _open(self) -> None:
        try:
            self._engine = chess.engine.SimpleEngine.popen_uci(self._cfg.path)
            self.configure(self._cfg)
            log.info("Engine started: %s", self._cfg.path)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Could not start engine '%s' (%s). Falling back to random moves.",
                self._cfg.path,
                exc,
            )
            self._engine = None

    def configure(self, cfg: EngineConfig) -> None:
        """Apply (possibly changed) settings. Safe to call between moves."""
        self._cfg = cfg
        if self._engine is None:
            return
        options: dict[str, object] = {
            "Threads": cfg.threads,
            "Hash": cfg.hash_mb,
        }
        if cfg.elo_limit is not None:
            options["UCI_LimitStrength"] = True
            options["UCI_Elo"] = cfg.elo_limit
        else:
            options["UCI_LimitStrength"] = False
            options["Skill Level"] = cfg.skill_level
        for name, value in options.items():
            try:
                self._engine.configure({name: value})
            except Exception as exc:  # noqa: BLE001 - engines vary in options
                log.debug("Engine option %s=%s not applied: %s", name, value, exc)

    def best_move(self, board: chess.Board) -> chess.Move:
        if self._engine is None:
            return random.choice(list(board.legal_moves))
        limit = chess.engine.Limit(time=self._cfg.move_time_ms / 1000.0)
        result = self._engine.play(board, limit)
        assert result.move is not None
        return result.move

    def close(self) -> None:
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:  # noqa: BLE001
                pass
            self._engine = None
