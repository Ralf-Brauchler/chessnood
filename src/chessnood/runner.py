"""Async runtime: wires the board, engine, game logic and indicators together.

Responsibilities:
  * forward board readings into the pure :class:`ChessGame`
  * run the engine off the event loop (it's blocking) when it's the computer's turn
  * drive the board LEDs (the primary move indicator) and the status screen
  * handle the "Neue Partie" touch on the screen
  * reload engine settings live when config.yaml changes
  * keep the status file up to date
"""
from __future__ import annotations

import asyncio
import logging

import chess

from .boards.base import Board, ConnectionState
from .config import ConfigWatcher
from .display import UiModel, make_display
from .engine import Engine
from .game import ChessGame, Guidance, Reaction, compute_guidance
from .status import StatusFile

log = logging.getLogger(__name__)


def _board_from_pieces(pieces: dict) -> chess.Board:
    """A board carrying just the sensed piece placement, for rendering."""
    board = chess.Board()
    board.set_piece_map(dict(pieces))
    return board


class Runner:
    def __init__(self, board: Board, watcher: ConfigWatcher):
        self._board = board
        self._watcher = watcher
        cfg = watcher.current
        self._engine = Engine(cfg.engine)
        self._game = ChessGame(human_color=cfg.game.human_color_bool)
        self._display = make_display(cfg.display)
        self._status = StatusFile(cfg.status_file)
        self._settle_s = max(0.0, cfg.board.settle_ms / 1000.0)
        self._new_game_requested = asyncio.Event()
        self._connection = board.state
        self._loop: asyncio.AbstractEventLoop | None = None
        # the last position the board physically sensed (so the screen can show
        # what's actually on the board, including a piece lifted mid-move)
        self._sensed = chess.Board()
        self._ui = Guidance("", "")  # current committed guidance (recomputed on settled readings)

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._display.on_new_game(self._request_new_game)
        self._status.update(state="starting", skill_level=self._watcher.current.engine.skill_level)
        self._ui = compute_guidance(self._game, self._sensed)
        self._refresh_screen()
        readings = self._board.subscribe_readings()
        states = self._board.subscribe_state()
        await self._board.connect()

        tasks = [
            asyncio.create_task(self._handle_states(states)),
            asyncio.create_task(self._handle_readings(readings)),
            asyncio.create_task(self._handle_new_game()),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            await self._board.disconnect()
            self._engine.close()
            self._display.close()

    def _request_new_game(self) -> None:
        """Called from the touch thread; hop back onto the event loop."""
        log.info("New game requested")
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._new_game_requested.set)
        else:
            self._new_game_requested.set()

    # --- screen -----------------------------------------------------------
    def _refresh_screen(self) -> None:
        if self._connection != ConnectionState.CONNECTED:
            status = {
                ConnectionState.SCANNING: "Suche das Brett …",
                ConnectionState.ERROR: "Verbindung verloren",
            }.get(self._connection, "Nicht verbunden")
            self._display.update(UiModel(self._connection, status,
                                         "Schalte das Brett ein und warte kurz.", self._sensed))
            return
        # show the guidance's target position if it has one (e.g. "set it up like
        # this"), otherwise the live physically sensed board
        board = self._ui.target if self._ui.target is not None else self._sensed
        self._display.update(UiModel(self._connection, self._ui.status,
                                     self._ui.instruction, board, self._ui.highlight))

    async def _handle_new_game(self) -> None:
        while True:
            await self._new_game_requested.wait()
            self._new_game_requested.clear()
            await self._apply(self._game.new_game())

    async def _handle_states(self, states: "asyncio.Queue[ConnectionState]") -> None:
        while True:
            state = await states.get()
            self._connection = state
            self._status.update(connection=state.value)
            if state == ConnectionState.CONNECTED:
                self._ui = compute_guidance(self._game, self._sensed)
            self._refresh_screen()

    async def _handle_readings(self, readings: "asyncio.Queue") -> None:
        """Show every reading live, but only *commit* a settled position.

        A move is fed to the game logic only once the board has been stable for
        ``settle_s``. This stops a piece slid across an intermediate square (e.g.
        a pawn passing over e3 on its way to e4, which momentarily reads as the
        legal move e2e3) from being committed as the wrong move -- a brief pass
        isn't stable, only the final resting position is.
        """
        while True:
            reading = await readings.get()
            self._show_sensed(reading)
            # absorb further readings until the board is quiet for settle_s
            while self._settle_s > 0:
                try:
                    reading = await asyncio.wait_for(readings.get(), self._settle_s)
                except asyncio.TimeoutError:
                    break
                self._show_sensed(reading)
            await self._apply(self._game.feed(reading))

    def _show_sensed(self, reading) -> None:
        """Reflect the physically sensed position on screen (live, uncommitted)."""
        self._sensed = _board_from_pieces(reading.pieces)
        self._refresh_screen()

    async def _apply(self, reaction: Reaction) -> None:
        """Carry out a game Reaction: recompute guidance, drive LEDs/screen, run engine."""
        if reaction.message:
            log.info("%s", reaction.message)
            self._status.update(state=self._game.state.name, last_move=reaction.message)
        if reaction.invalid:
            log.debug("Board reading does not match a legal move (transient)")

        # Work out what to show/say and which squares to light, then apply it to
        # the board LEDs (primary move indicator) and the screen together.
        self._ui = compute_guidance(self._game, self._sensed)
        await self._board.set_leds(self._ui.highlight)
        self._refresh_screen()

        if reaction.engine_should_move:
            await self._do_engine_move()

    async def _do_engine_move(self) -> None:
        # Reload settings (e.g. skill_level changed over SSH) before thinking.
        changed, cfg = self._watcher.poll()
        if changed:
            log.info("Config reloaded; skill_level=%s", cfg.engine.skill_level)
            self._engine.configure(cfg.engine)
            self._status.update(skill_level=cfg.engine.skill_level)

        move = await asyncio.to_thread(self._engine.best_move, self._game.board)
        await self._apply(self._game.set_engine_move(move))
