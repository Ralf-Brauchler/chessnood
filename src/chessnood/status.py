"""Tiny status file so `chessnood status` can report what the service is doing."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .atomicio import atomic_write_text


class StatusFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, Any] = {
            "connection": "disconnected",
            "state": "starting",
            "skill_level": None,
            "last_move": None,
            # A snapshot of what the screen currently shows, so a remote view (SSH
            # `chessnood status` or the web page) can reproduce it without the board.
            "status": None,        # short headline, e.g. "Du bist am Zug"
            "instruction": None,   # one-line plain-language guidance
            "fen": None,           # the position the screen is showing
            "highlight": [],       # squares the board LEDs are lighting (names, e.g. "g1")
            "battery": None,       # {"level": 1-100, "charging": bool|None} from the board
            "updated": None,
        }

    def update(self, **fields: Any) -> None:
        self._data.update(fields)
        self._data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            atomic_write_text(self.path, json.dumps(self._data, indent=2))
        except OSError:
            pass

    @staticmethod
    def read(path: str | Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))
