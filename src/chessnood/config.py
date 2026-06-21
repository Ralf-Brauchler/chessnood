"""Configuration loading with live-reload support.

The running service polls the config file's mtime (see :class:`ConfigWatcher`)
and reloads engine settings between turns, so changing ``skill_level`` over SSH
takes effect on the next move without a restart.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import chess
import yaml


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        data = data or {}
        return cls(
            engine=EngineConfig(**(data.get("engine") or {})),
            board=BoardConfig(**(data.get("board") or {})),
            display=DisplayConfig(**(data.get("display") or {})),
            game=GameConfig(**(data.get("game") or {})),
            log_level=data.get("log_level", "info"),
            status_file=data.get("status_file", "./chessnood-status.json"),
        )

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        """Load config from ``path``. Missing file -> all defaults."""
        if path is None:
            return cls()
        p = Path(path)
        if not p.exists():
            return cls()
        with p.open("r", encoding="utf-8") as fh:
            return cls.from_dict(yaml.safe_load(fh) or {})


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
        """Return (changed, config). Reloads only if the file's mtime changed."""
        if not self.path or not self.path.exists():
            return False, self.current
        mtime = self.path.stat().st_mtime
        if mtime != self._mtime:
            self._mtime = mtime
            self.current = Config.load(self.path)
            return True, self.current
        return False, self.current
