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
    if cfg.backend == "usb":
        from .usb import UsbBoard  # imported lazily so hidapi stays optional

        return UsbBoard(stale_timeout_s=cfg.stale_timeout_s, keepalive_s=cfg.keepalive_s)
    raise ValueError(f"unknown board backend: {cfg.backend!r}")
