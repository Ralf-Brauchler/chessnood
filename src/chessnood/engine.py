"""Chess opponent: Stockfish over UCI, with a random-mover fallback.

If the configured engine binary cannot be started (e.g. Stockfish not installed
yet), we fall back to a legal-random mover so the rest of the system still runs.
This is what lets the whole project work on your Mac before anything is set up.
"""
from __future__ import annotations

import logging
import os
import random
import signal
import time

import chess
import chess.engine

from .config import EngineConfig

log = logging.getLogger(__name__)

# After a failed/crashed engine we retry the binary rather than degrading to
# random moves forever -- but not on *every* move, so a permanently broken path
# doesn't spam the log or stall each turn. One retry per this many seconds.
REOPEN_BACKOFF_S = 60.0


class Engine:
    def __init__(self, cfg: EngineConfig):
        self._cfg = cfg
        self._engine: chess.engine.SimpleEngine | None = None
        self._next_retry = 0.0
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
            self._next_retry = time.monotonic() + REOPEN_BACKOFF_S

    def _maybe_reopen(self) -> None:
        """Try to bring a crashed/never-started engine back, at most once per
        backoff window. A transient Stockfish hiccup must not leave the computer
        playing random moves for the rest of the appliance's uptime."""
        if self._engine is None and time.monotonic() >= self._next_retry:
            self._next_retry = time.monotonic() + REOPEN_BACKOFF_S
            log.info("Retrying engine '%s'", self._cfg.path)
            self._open()

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
        self._maybe_reopen()
        if self._engine is not None:
            limit = chess.engine.Limit(time=self._cfg.move_time_ms / 1000.0)
            try:
                result = self._engine.play(board, limit)
                if result.move is not None:
                    return result.move
            except Exception as exc:  # noqa: BLE001 - engine may die mid-game
                # A crashed/terminated engine must never brick the board: drop it
                # and keep playing with the random fallback.
                log.warning("Engine failed mid-game (%s); falling back to random moves", exc)
                self.close()
                self._next_retry = time.monotonic() + REOPEN_BACKOFF_S
        return self.fallback_move(board)

    def fallback_move(self, board: chess.Board) -> chess.Move:
        """A legal random move -- used when no engine is available or one hung."""
        return random.choice(list(board.legal_moves))

    def abandon(self) -> None:
        """Forcibly kill a wedged engine whose move never came back, so a fresh one
        is opened next turn. SIGKILL, not a clean quit, because a hung engine won't
        answer -- and a graceful quit could itself block. Also unblocks the leaked
        best_move thread (its play() then raises EngineTerminatedError)."""
        eng = self._engine
        self._engine = None
        self._next_retry = time.monotonic() + REOPEN_BACKOFF_S
        if eng is None:
            return
        try:
            pid = eng.transport.get_pid()
        except Exception:  # noqa: BLE001 - transport shape varies; best effort
            pid = None
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def close(self) -> None:
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:  # noqa: BLE001
                pass
            self._engine = None
