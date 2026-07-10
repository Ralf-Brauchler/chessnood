"""Async runtime: wires the board, engine, game logic and indicators together.

Responsibilities:
  * forward board readings into the pure :class:`ChessGame`
  * run the engine off the event loop (it's blocking) when it's the computer's turn
  * drive the board LEDs (the primary move indicator) and the status screen
  * reload engine settings live when config.yaml changes
  * keep the status file up to date
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import chess

from .atomicio import atomic_write_text
from .boards.base import Board, ConnectionState
from .config import ConfigWatcher
from .display import UiModel, make_display
from .engine import Engine
from .game import ChessGame, GameState, Guidance, Reaction, compute_guidance
from .status import StatusFile
from . import watchdog

log = logging.getLogger(__name__)

# Hard wall-clock budget for an engine move = its think time + this margin (for
# process start-up / UCI round-trips). Past it we treat the engine as wedged.
ENGINE_HARD_TIMEOUT_MARGIN_S = 5.0

# Capture signal: flash the cross this long, then a brief dark gap, then light the
# destination -- so the captured-piece LED reads as a distinct new "on".
CAPTURE_CROSS_S = 2.0
CAPTURE_CROSS_OFF_S = 0.4

# Refresh the status file this often even when nothing changes, so a remote viewer
# (web page / `chessnood status`) sees a live 'updated' time and a current battery
# level during a long idle (when no move re-publishes it). Cheap: one small atomic
# write per interval.
STATUS_REFRESH_S = 60.0

# Re-assert the screen this often. The service draws on events (moves, connection
# changes), but between boot and the first move nothing would redraw -- so the
# Linux login console stays visible on the framebuffer. A frequent, cheap repaint
# (just replays the last packed frame) makes the UI appear within seconds of boot
# and self-heal if anything else writes to the framebuffer.
SCREEN_REFRESH_S = 2.0


def _board_from_pieces(pieces: dict) -> chess.Board:
    """A board carrying just the sensed piece placement, for rendering."""
    board = chess.Board()
    board.set_piece_map(dict(pieces))
    return board


def _cross_squares(square: int) -> list[int]:
    """The full rank and file through ``square`` -- a '+' cross of lit LEDs used as
    the capture signal, centred on the square where the capture happens."""
    f, r = chess.square_file(square), chess.square_rank(square)
    return sorted({chess.square(f, rr) for rr in range(8)}
                  | {chess.square(ff, r) for ff in range(8)})


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
        self._connection = board.state
        self._loop: asyncio.AbstractEventLoop | None = None
        # the last position the board physically sensed (so the screen can show
        # what's actually on the board, including a piece lifted mid-move)
        self._sensed = chess.Board()
        self._ui = Guidance("", "")  # current committed guidance (recomputed on settled readings)
        self._beeps = cfg.board.beeps
        self._prev_state = self._game.state
        self._prev_alert = False
        # (src, dst) of the piece currently being cleaned up, threaded through
        # compute_guidance so that after a wrong piece is lifted we light the one
        # square it belongs on -- one whole piece at a time. None when not fixing.
        self._fixing: tuple[int, int] | None = None
        # Capture signal: when the computer's move is a capture, briefly flash a
        # cross through the target square as the capturing piece is lifted -- a
        # clear "a piece is being taken here" cue before the destination lights.
        self._capture_signal = cfg.board.capture_signal
        self._cross_until = 0.0                     # board shows the cross until this time
        self._crossed_move: chess.Move | None = None  # capture already flashed for
        self._cross_square: int | None = None
        # Escape hatch: if a wrong position sits uncorrected this long, adopt it and
        # let the player carry on (0 = never). A one-shot timer, re-armed on every
        # committed reading while wrong, cancelled once the board is right again.
        self._accept_after_s = cfg.board.accept_wrong_after_s
        self._accept_handle: asyncio.TimerHandle | None = None
        self._game_file = Path(cfg.game_state_file) if cfg.game_state_file else None
        self._load_game()

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._recompute_guidance()
        self._publish_status(state="starting", skill_level=self._watcher.current.engine.skill_level)
        self._refresh_screen()
        readings = self._board.subscribe_readings()
        states = self._board.subscribe_state()
        await self._board.connect()

        # If power was lost while the engine owed a move, finish that move now.
        if self._game.state == GameState.ENGINE_THINKING:
            asyncio.create_task(self._do_engine_move())

        # Tell systemd we're up; a hung loop then stops petting the watchdog and
        # gets restarted (see Type=notify + WatchdogSec in the service unit).
        watchdog.notify_ready()

        tasks = [
            asyncio.create_task(self._handle_states(states)),
            asyncio.create_task(self._handle_readings(readings)),
            asyncio.create_task(self._status_heartbeat()),
            asyncio.create_task(self._screen_heartbeat()),
            asyncio.create_task(watchdog.heartbeat()),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            await self._board.disconnect()
            self._engine.close()
            self._display.close()

    # --- persistence ------------------------------------------------------
    def _load_game(self) -> None:
        if not self._game_file or not self._game_file.exists():
            return
        try:
            self._game.restore(json.loads(self._game_file.read_text(encoding="utf-8")))
            log.info("Resumed saved game (%s, %s)", self._game.state.name, self._game.board.fen())
        except (OSError, ValueError, KeyError) as exc:
            log.warning("Could not restore saved game: %s", exc)

    def _save_game(self) -> None:
        if not self._game_file:
            return
        try:
            atomic_write_text(self._game_file, json.dumps(self._game.snapshot(), indent=2))
        except OSError:
            pass

    # --- screen -----------------------------------------------------------
    def _current_model(self) -> UiModel:
        """The single UiModel that both the screen and the status snapshot use."""
        detail = self._strength_text()
        if self._connection != ConnectionState.CONNECTED:
            status = {
                ConnectionState.SCANNING: "Suche das Brett …",
                ConnectionState.ERROR: "Verbindung verloren",
            }.get(self._connection, "Nicht verbunden")
            return UiModel(self._connection, status,
                           "Schalte das Brett ein und warte kurz.", self._sensed,
                           detail=detail)
        # show the guidance's target position if it has one (e.g. "set it up like
        # this"), otherwise the live physically sensed board
        board = self._ui.target if self._ui.target is not None else self._sensed
        return UiModel(self._connection, self._ui.status,
                       self._ui.instruction, board, self._ui.highlight, detail=detail)

    def _strength_text(self) -> str:
        """A short, always-on line describing the current engine strength, so the
        setting is visible on the screen from boot (tuned over SSH between games)."""
        eng = self._watcher.current.engine
        if eng.elo_limit:
            return f"Computer: ca. {eng.elo_limit} Elo"
        return f"Computer: Stufe {eng.skill_level}"

    def _refresh_screen(self) -> None:
        self._display.update(self._current_model())

    def _publish_status(self, **extra) -> None:
        """Write the status file: the current screen snapshot plus any extra fields.

        Called only where the status file is already written (transitions, move
        commits) -- never per board reading -- so the SD card isn't hammered. The
        screen snapshot lets a remote viewer reproduce exactly what's displayed.
        """
        model = self._current_model()
        fields = {
            "connection": model.connection.value,
            "status": model.status,
            "instruction": model.instruction,
            "fen": model.board.fen() if model.board is not None else None,
            "highlight": [chess.square_name(s) for s in model.highlight],
            "detail": model.detail,
            "battery": self._board.battery,
        }
        fields.update(extra)
        self._status.update(**fields)

    async def _status_heartbeat(self) -> None:
        """Periodically republish the status so a remote viewer sees a live
        'updated' time and current battery even through a long idle (no moves)."""
        while True:
            await asyncio.sleep(STATUS_REFRESH_S)
            self._publish_status()

    async def _screen_heartbeat(self) -> None:
        """Periodically re-assert the screen so the UI shows from boot and isn't
        left buried under the Linux login console until the first move."""
        while True:
            await asyncio.sleep(SCREEN_REFRESH_S)
            self._display.repaint()

    async def _handle_states(self, states: "asyncio.Queue[ConnectionState]") -> None:
        while True:
            state = await states.get()
            self._connection = state
            if state == ConnectionState.CONNECTED:
                self._recompute_guidance()
            self._publish_status()
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
            await self._show_sensed(reading)
            # absorb further readings until the board is quiet for settle_s
            while self._settle_s > 0:
                try:
                    reading = await asyncio.wait_for(readings.get(), self._settle_s)
                except asyncio.TimeoutError:
                    break
                await self._show_sensed(reading)
            await self._apply(self._game.feed(reading))

    async def _show_sensed(self, reading) -> None:
        """Reflect the physically sensed position on screen (live, uncommitted).

        While the player is executing the computer's move we also advance the
        guidance live -- so the LEDs follow the piece (source lit -> on lift ->
        destination lit) without waiting for the board to settle. Other states
        stay settle-gated to avoid flicker (e.g. a pawn sliding over a square)."""
        self._sensed = _board_from_pieces(reading.pieces)
        if self._game.state == GameState.ENGINE_MOVE_SHOWN:
            await self._apply_guidance(beep=False)
        else:
            self._refresh_screen()

    async def _apply(self, reaction: Reaction) -> None:
        """Carry out a game Reaction: recompute guidance, drive LEDs/screen, run engine."""
        if reaction.message:
            log.info("%s", reaction.message)
        if reaction.invalid:
            log.debug("Board reading does not match a legal move (transient)")

        # Work out what to show/say and which squares to light, then apply it to
        # the board LEDs (primary move indicator) and the screen together.
        await self._apply_guidance(beep=True)
        if reaction.message:
            self._publish_status(state=self._game.state.name, last_move=reaction.message)
        self._save_game()
        self._arm_accept_timer()

        if reaction.engine_should_move:
            await self._do_engine_move()

    def _arm_accept_timer(self) -> None:
        """(Re)start the 'accept a stuck wrong position' countdown while the board
        is in a wrong state; cancel it once things are right again. Re-armed on
        each committed reading, so it fires only after the position has sat wrong
        and untouched for the whole window."""
        if self._accept_handle is not None:
            self._accept_handle.cancel()
            self._accept_handle = None
        wrong = self._ui.alert and self._game.state in (
            GameState.PLAYER_TURN, GameState.ENGINE_MOVE_SHOWN)
        if self._accept_after_s > 0 and wrong and self._loop is not None:
            self._accept_handle = self._loop.call_later(
                self._accept_after_s, lambda: asyncio.create_task(self._accept_wrong_position()))

    async def _accept_wrong_position(self) -> None:
        """Timer fired: the wrong position has stood untouched too long. Adopt it
        (if it's a legal position) and let the player carry on."""
        self._accept_handle = None
        if not self._ui.alert or self._connection != ConnectionState.CONNECTED:
            return
        reaction = self._game.accept_position(self._sensed)
        if reaction.message:
            log.info("Wrong position uncorrected for %ss; accepting the board and "
                     "continuing", self._accept_after_s)
            await self._apply(reaction)
        else:
            log.info("Wrong position uncorrected but not a legal position; still waiting")

    def _recompute_guidance(self) -> None:
        """Recompute guidance for the sensed position, threading the cleanup
        ``fixing`` state so a correction is guided one whole piece at a time."""
        self._ui = compute_guidance(self._game, self._sensed, fixing=self._fixing)
        self._fixing = self._ui.fixing

    async def _apply_guidance(self, *, beep: bool) -> None:
        """Recompute guidance for the sensed position and drive LEDs + screen."""
        self._recompute_guidance()
        cross = self._capture_cross()          # capture signal overrides the LEDs briefly
        await self._board.set_leds(cross if cross is not None else self._ui.highlight)
        if beep:
            await self._beep_on_transition()
        self._refresh_screen()

    def _capture_cross(self) -> list[int] | None:
        """When the computer's move is a capture, flash a cross through the target
        square as the capturing piece is lifted. Returns the cross squares while
        it's showing, else None (normal guidance). Display-only: the game logic is
        untouched. A timer (`_cross_timer`) ends the flash and lights the
        destination, because the board streams only on change -- while the player
        holds the lifted piece no reading would arrive to end it."""
        if not self._capture_signal:
            return None
        if time.monotonic() < self._cross_until:            # still flashing
            return _cross_squares(self._cross_square)
        move = self._game.pending_engine_move
        if (self._game.state == GameState.ENGINE_MOVE_SHOWN and move is not None
                and move is not self._crossed_move
                and self._game.board.is_capture(move)
                and not self._game.board.is_en_passant(move)
                and move.from_square not in self._sensed.piece_map()):  # piece lifted
            self._crossed_move = move          # flash once per move
            self._cross_square = move.to_square
            self._cross_until = time.monotonic() + CAPTURE_CROSS_S
            asyncio.create_task(self._cross_timer(move))
            return _cross_squares(move.to_square)
        return None

    async def _cross_timer(self, move: chess.Move) -> None:
        """Hold the capture cross, then a brief dark gap, then hand back to normal
        guidance (the destination LED) -- even if no board reading arrives."""
        try:
            await asyncio.sleep(CAPTURE_CROSS_S)
            if self._game.pending_engine_move is not move or self._crossed_move is not move:
                return                          # move completed/changed -> leave it
            await self._board.set_leds([])      # "wieder aus": a distinct dark gap
            await asyncio.sleep(CAPTURE_CROSS_OFF_S)
            self._cross_until = 0.0             # end the flash window
            if self._game.pending_engine_move is move:
                await self._apply_guidance(beep=False)   # -> the destination LED
        except asyncio.CancelledError:
            pass

    async def _beep_on_transition(self) -> None:
        """A short tone only when something newly needs the player's attention."""
        if self._beeps:
            state = self._game.state
            if self._ui.alert and not self._prev_alert:
                await self._board.beep(350, 220)            # something is wrong
            elif state == GameState.ENGINE_MOVE_SHOWN and self._prev_state != state:
                await self._board.beep(900, 120)            # your turn to play the move
            elif state == GameState.GAME_OVER and self._prev_state != state:
                await self._board.beep(600, 400)            # game over
        self._prev_state = self._game.state
        self._prev_alert = self._ui.alert

    async def _do_engine_move(self) -> None:
        # Reload settings (e.g. skill_level changed over SSH) before thinking.
        changed, cfg = self._watcher.poll()
        if changed:
            log.info("Config reloaded; skill_level=%s", cfg.engine.skill_level)
            self._engine.configure(cfg.engine)
            self._publish_status(skill_level=cfg.engine.skill_level)

        # Think on a COPY so a concurrent restart (board.reset in feed) can't race
        # the engine thread. Cap the wall-clock so a wedged engine never freezes
        # the turn on "Computer denkt" forever: kill it and play a fallback move.
        gen = self._game.generation
        timeout_s = cfg.engine.move_time_ms / 1000.0 + ENGINE_HARD_TIMEOUT_MARGIN_S
        try:
            move = await asyncio.wait_for(
                asyncio.to_thread(self._engine.best_move, self._game.board.copy()),
                timeout=timeout_s)
        except asyncio.TimeoutError:
            log.warning("Engine did not answer within %.1fs; abandoning it and "
                        "playing a fallback move", timeout_s)
            self._engine.abandon()
            move = self._engine.fallback_move(self._game.board)

        # If the player set up the start position while we were thinking, a new
        # game has begun -- drop this now-stale move rather than forcing it.
        if self._game.generation != gen:
            log.info("New game started while the engine was thinking; move discarded")
            return
        await self._apply(self._game.set_engine_move(move))
