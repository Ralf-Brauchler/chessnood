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
    backend: str = "ble"  # "ble" | "mock"
    address: str | None = None
    name_prefix: str = "Chessnut"


@dataclass
class ButtonsConfig:
    new_game_pin: int | None = 27
    resign_pin: int | None = 22


@dataclass
class HardwareConfig:
    status_led_pin: int | None = 17
    buttons: ButtonsConfig = field(default_factory=ButtonsConfig)


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
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    game: GameConfig = field(default_factory=GameConfig)
    log_level: str = "info"
    status_file: str = "./chessnood-status.json"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        data = data or {}
        buttons = (data.get("hardware") or {}).get("buttons") or {}
        return cls(
            engine=EngineConfig(**(data.get("engine") or {})),
            board=BoardConfig(**(data.get("board") or {})),
            hardware=HardwareConfig(
                status_led_pin=(data.get("hardware") or {}).get("status_led_pin", 17),
                buttons=ButtonsConfig(**buttons),
            ),
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
