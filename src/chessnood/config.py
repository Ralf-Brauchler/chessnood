"""Configuration loading with live-reload support.

The running service polls the config file's mtime (see :class:`ConfigWatcher`)
and reloads engine settings between turns, so changing ``skill_level`` over SSH
takes effect on the next move without a restart.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import chess
import yaml

log = logging.getLogger(__name__)


def _known(cls, data: Any) -> dict[str, Any]:
    """Keep only the keys that are real fields of dataclass ``cls``.

    A typo or stale key in config.yaml must NOT crash the appliance (it would
    otherwise raise ``TypeError: unexpected keyword argument``). Unknown keys are
    dropped with a warning so the board still starts with sensible values.
    """
    data = data or {}
    allowed = {f.name for f in fields(cls)}
    unknown = set(data) - allowed
    if unknown:
        log.warning("Ignoring unknown config keys for %s: %s",
                    cls.__name__, ", ".join(sorted(unknown)))
    return {k: v for k, v in data.items() if k in allowed}


@dataclass
class EngineConfig:
    path: str = "stockfish"
    skill_level: int = 5
    move_time_ms: int = 800
    elo_limit: int | None = None
    threads: int = 1
    hash_mb: int = 32


@dataclass
class BoardConfig:
    backend: str = "usb"  # "usb" | "mock"
    settle_ms: int = 1000  # a move is committed only after the board is stable this long
    beeps: bool = True     # short tones on the board for "your turn" / wrong move / game over
    capture_signal: bool = True  # flash a cross through the target when the computer captures
    accept_wrong_after_s: int = 300  # adopt an uncorrected wrong position after this long (0 = never)
    stale_timeout_s: float = 0.0  # >0: reconnect if no board report for this long (0 = off)  # VERIFY


@dataclass
class DisplayConfig:
    """The 3.5" SPI touchscreen (MHS-3.5) used as a status + control panel.

    The board LEDs stay the primary move indicator; this screen shows
    plain-language status and a big "Neue Partie" touch button.
    """

    backend: str = "auto"            # auto | framebuffer | console | preview | none
    fb_device: str = "/dev/fb1"      # SPI TFT framebuffer device  # VERIFY on Pi
    touch_device: str | None = None  # evdev path; None = auto-detect  # VERIFY on Pi
    rotate: int = 0                  # 0 | 90 | 180 | 270  # VERIFY orientation on Pi
    preview_path: str = "./chessnood-screen.png"  # where the "preview" backend writes


@dataclass
class GameConfig:
    human_color: str = "white"  # "white" | "black"

    @property
    def human_color_bool(self) -> chess.Color:
        return chess.WHITE if self.human_color.lower().startswith("w") else chess.BLACK


@dataclass
class Config:
    engine: EngineConfig = field(default_factory=EngineConfig)
    board: BoardConfig = field(default_factory=BoardConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    game: GameConfig = field(default_factory=GameConfig)
    log_level: str = "info"
    status_file: str = "./chessnood-status.json"
    game_state_file: str = "./chessnood-game.json"  # saved so a power blip resumes mid-game

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        data = data or {}
        return cls(
            engine=EngineConfig(**_known(EngineConfig, data.get("engine"))),
            board=BoardConfig(**_known(BoardConfig, data.get("board"))),
            display=DisplayConfig(**_known(DisplayConfig, data.get("display"))),
            game=GameConfig(**_known(GameConfig, data.get("game"))),
            log_level=data.get("log_level", "info"),
            status_file=data.get("status_file", "./chessnood-status.json"),
            game_state_file=data.get("game_state_file", "./chessnood-game.json"),
        )

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        """Load config from ``path``. Missing or unreadable/invalid -> defaults.

        The appliance must always come up with *some* working config: a missing
        file, a YAML syntax error or a half-written file falls back to defaults
        (with a warning) rather than refusing to start.
        """
        if path is None:
            return cls()
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            with p.open("r", encoding="utf-8") as fh:
                return cls.from_dict(yaml.safe_load(fh) or {})
        except (OSError, yaml.YAMLError) as exc:
            log.warning("Could not read config %s (%s); using defaults", p, exc)
            return cls()


class ConfigWatcher:
    """Reloads the config file when it changes on disk."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self._mtime: float | None = None
        self.current = self._read()

    def _read(self) -> Config:
        if self.path and self.path.exists():
            self._mtime = self.path.stat().st_mtime
        return Config.load(self.path)

    def poll(self) -> tuple[bool, Config]:
        """Return (changed, config). Reloads only if the file's mtime changed.

        A failed reload (file vanished or half-written/invalid YAML caught mid-save
        over SSH) must never crash the running service nor silently reset it to
        defaults -- we keep the last good config and try again on the next change.
        """
        if not self.path or not self.path.exists():
            return False, self.current
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return False, self.current
        if mtime == self._mtime:
            return False, self.current
        self._mtime = mtime  # advance first so a broken file isn't retried every poll
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                new = Config.from_dict(yaml.safe_load(fh) or {})
        except (OSError, yaml.YAMLError) as exc:
            log.warning("Config reload failed (%s); keeping current settings", exc)
            return False, self.current
        self.current = new
        return True, self.current
