"""Board abstraction shared by the real (BLE) and mock backends."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

import chess


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    SCANNING = "scanning"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass(frozen=True)
class BoardReading:
    """A snapshot of which piece sits on which square, as the board senses it.

    Squares use python-chess indexing (a1 = 0 .. h8 = 63). ``pieces`` only
    contains occupied squares.
    """

    pieces: dict[int, chess.Piece]

    @classmethod
    def from_board(cls, board: chess.Board) -> "BoardReading":
        return cls(dict(board.piece_map()))

    def matches(self, board: chess.Board) -> bool:
        return self.pieces == board.piece_map()


class Board(ABC):
    """Common interface for an e-board backend."""

    def __init__(self) -> None:
        self._state = ConnectionState.DISCONNECTED
        self._reading_subs: list[asyncio.Queue[BoardReading]] = []
        self._state_subs: list[asyncio.Queue[ConnectionState]] = []

    # --- connection state -------------------------------------------------
    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def battery(self) -> dict | None:
        """Last known battery status {level, charging}, or None if unknown/unsupported."""
        return None

    def _set_state(self, state: ConnectionState) -> None:
        if state != self._state:
            self._state = state
            for q in self._state_subs:
                q.put_nowait(state)

    def subscribe_state(self) -> "asyncio.Queue[ConnectionState]":
        q: asyncio.Queue[ConnectionState] = asyncio.Queue()
        self._state_subs.append(q)
        return q

    # --- readings ---------------------------------------------------------
    def _emit(self, reading: BoardReading) -> None:
        for q in self._reading_subs:
            # Only the latest board state matters; if a consumer falls behind,
            # drop the oldest reading rather than letting the queue grow without
            # bound. (The runner debounces anyway, so a stale reading is useless.)
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(reading)

    def subscribe_readings(self, maxsize: int = 64) -> "asyncio.Queue[BoardReading]":
        q: asyncio.Queue[BoardReading] = asyncio.Queue(maxsize=maxsize)
        self._reading_subs.append(q)
        return q

    # --- lifecycle / IO ---------------------------------------------------
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def set_leds(self, squares: Iterable[int]) -> None:
        """Light the given squares (python-chess indices). Empty = all off."""

    async def clear_leds(self) -> None:
        await self.set_leds([])

    async def beep(self, frequency_hz: int = 1000, duration_ms: int = 150) -> None:
        """Sound a short tone, if the board supports it. No-op by default."""
        return None
