"""Async runtime: wires the board, engine, game logic and indicators together.

Responsibilities:
  * forward board readings into the pure :class:`ChessGame`
  * run the engine off the event loop (it's blocking) when it's the computer's turn
  * drive the board LEDs and the status LED
  * handle the "new game" button
  * reload engine settings live when config.yaml changes
  * keep the status file up to date
"""
from __future__ import annotations

import asyncio
import logging

from .boards.base import Board, ConnectionState
from .config import ConfigWatcher
from .engine import Engine
from .game import ChessGame, Reaction
from .indicators import Buttons, StatusIndicator
from .status import StatusFile

log = logging.getLogger(__name__)


class Runner:
    def __init__(self, board: Board, watcher: ConfigWatcher):
        self._board = board
        self._watcher = watcher
        cfg = watcher.current
        self._engine = Engine(cfg.engine)
        self._game = ChessGame(human_color=cfg.game.human_color_bool)
        self._status_led = StatusIndicator(cfg.hardware.status_led_pin)
        self._buttons = Buttons(cfg.hardware)
        self._status = StatusFile(cfg.status_file)
        self._new_game_requested = asyncio.Event()

    async def run(self) -> None:
        self._buttons.on("new_game", self._request_new_game)
        self._status.update(state="starting", skill_level=self._watcher.current.engine.skill_level)
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
            self._status_led.close()
            self._buttons.close()

    def _request_new_game(self) -> None:
        log.info("New game button pressed")
        self._new_game_requested.set()

    async def _handle_new_game(self) -> None:
        while True:
            await self._new_game_requested.wait()
            self._new_game_requested.clear()
            await self._apply(self._game.new_game())

    async def _handle_states(self, states: "asyncio.Queue[ConnectionState]") -> None:
        self._status_led.set_state(self._board.state)
        while True:
            state = await states.get()
            self._status_led.set_state(state)
            self._status.update(connection=state.value)

    async def _handle_readings(self, readings: "asyncio.Queue") -> None:
        while True:
            reading = await readings.get()
            await self._apply(self._game.feed(reading))

    async def _apply(self, reaction: Reaction) -> None:
        """Carry out a game Reaction: LEDs, logging, and engine turns."""
        if reaction.message:
            log.info("%s", reaction.message)
            self._status.update(state=self._game.state.name, last_move=reaction.message)
        if reaction.invalid:
            log.debug("Board reading does not match a legal move (transient)")
        await self._board.set_leds(reaction.leds)

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
