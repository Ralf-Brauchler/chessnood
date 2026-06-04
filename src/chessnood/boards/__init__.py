"""Board backends."""
from __future__ import annotations

from ..config import BoardConfig
from .base import Board, BoardReading, ConnectionState
from .mock import MockBoard

__all__ = ["Board", "BoardReading", "ConnectionState", "MockBoard", "build_board"]


def build_board(cfg: BoardConfig) -> Board:
    """Construct the board backend named in the config."""
    if cfg.backend == "mock":
        return MockBoard()
    if cfg.backend == "ble":
        from .ble import BleBoard  # imported lazily so bleak stays optional

        return BleBoard(address=cfg.address, name_prefix=cfg.name_prefix)
    raise ValueError(f"unknown board backend: {cfg.backend!r}")
