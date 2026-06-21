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
from .game import ChessGame, GameState, Reaction
from .status import StatusFile

log = logging.getLogger(__name__)

# Plain-language, coordinate-free screen text per game phase (the player does
# not read algebraic notation; the lit board LEDs show the actual move).
_STATUS_TEXT = {
    GameState.NEED_SETUP: ("Stelle die Figuren auf", "Stelle alle Figuren auf die Grundstellung."),
    GameState.PLAYER_TURN: ("Du bist am Zug", "Mach deinen Zug auf dem Brett."),
    GameState.ENGINE_THINKING: ("Computer denkt …", "Bitte einen Moment warten."),
    GameState.ENGINE_MOVE_SHOWN: ("Computer hat gezogen",
                                  "Die leuchtenden Felder zeigen den Zug. Führe ihn auf dem Brett aus."),
    GameState.GAME_OVER: ("Spiel vorbei", "Für eine neue Partie alle Figuren wieder aufstellen."),
}


class Runner:
    def __init__(self, board: Board, watcher: ConfigWatcher):
        self._board = board
        self._watcher = watcher
        cfg = watcher.current
        self._engine = Engine(cfg.engine)
        self._game = ChessGame(human_color=cfg.game.human_color_bool)
        self._display = make_display(cfg.display)
        self._status = StatusFile(cfg.status_file)
        self._new_game_requested = asyncio.Event()
        self._connection = board.state
        self._loop: asyncio.AbstractEventLoop | None = None

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._display.on_new_game(self._request_new_game)
        self._status.update(state="starting", skill_level=self._watcher.current.engine.skill_level)
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
                                         "Schalte das Brett ein und warte kurz.", self._game.board))
            return
        status, instruction = _STATUS_TEXT.get(self._game.state, ("", ""))
        if self._game.state == GameState.GAME_OVER:
            status = self._game_over_text()
        highlight = []
        if self._game.state == GameState.ENGINE_MOVE_SHOWN and self._game.pending_engine_move:
            move = self._game.pending_engine_move
            highlight = [move.from_square, move.to_square]
        self._display.update(UiModel(self._connection, status, instruction,
                                     self._game.board, highlight))

    def _game_over_text(self) -> str:
        outcome = self._game.board.outcome()
        if outcome is None or outcome.winner is None:
            return "Remis"
        return "Weiß gewinnt" if outcome.winner == chess.WHITE else "Schwarz gewinnt"

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
            self._refresh_screen()

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
        await self._board.set_leds(reaction.leds)  # board LEDs = primary move indicator
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
