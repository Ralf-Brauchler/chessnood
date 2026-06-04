"""Tiny status file so `chessnood status` can report what the service is doing."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class StatusFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, Any] = {
            "connection": "disconnected",
            "state": "starting",
            "skill_level": None,
            "last_move": None,
            "updated": None,
        }

    def update(self, **fields: Any) -> None:
        self._data.update(fields)
        self._data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def read(path: str | Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))
