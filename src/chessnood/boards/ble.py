"""Real Chessnut board over Bluetooth LE (via bleak).

This backend implements the documented Chessnut protocol (see protocol.py) and
includes an auto-reconnect loop so a dropped connection is re-established
silently in the background -- the player never sees a "connect" button.

⚠️  Not yet verified against a physical Chessnut Pro. The first hardware test
(``chessnood scan`` then a connect) will tell us whether the protocol constants
in protocol.py are correct.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from .base import Board, BoardReading, ConnectionState
from . import protocol

log = logging.getLogger(__name__)

RECONNECT_DELAY_S = 3.0


class BleBoard(Board):
    def __init__(self, address: str | None = None, name_prefix: str = "Chessnut"):
        super().__init__()
        self._address = address
        self._name_prefix = name_prefix
        self._client = None  # bleak.BleakClient
        self._run = False
        self._task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Start the background connect/reconnect loop and return immediately."""
        self._run = True
        self._task = asyncio.create_task(self._maintain())

    async def disconnect(self) -> None:
        self._run = False
        if self._task:
            self._task.cancel()
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001 - best effort on shutdown
                pass
        self._set_state(ConnectionState.DISCONNECTED)

    async def _maintain(self) -> None:
        """Keep a connection alive forever, reconnecting on any drop."""
        while self._run:
            try:
                await self._connect_once()
                # _connect_once returns when the link drops.
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("BLE connection failed: %s", exc)
                self._set_state(ConnectionState.ERROR)
            if self._run:
                await asyncio.sleep(RECONNECT_DELAY_S)

    async def _connect_once(self) -> None:
        from bleak import BleakClient, BleakScanner

        self._set_state(ConnectionState.SCANNING)
        address = self._address
        if address is None:
            log.info("Scanning for a board named '%s*'...", self._name_prefix)
            device = await BleakScanner.find_device_by_filter(
                lambda d, _ad: bool(d.name and d.name.startswith(self._name_prefix)),
                timeout=15.0,
            )
            if device is None:
                raise RuntimeError("no Chessnut board found while scanning")
            address = device.address

        disconnected = asyncio.Event()

        def _on_disconnect(_client) -> None:
            log.warning("Board disconnected")
            self._set_state(ConnectionState.DISCONNECTED)
            disconnected.set()

        async with BleakClient(address, disconnected_callback=_on_disconnect) as client:
            self._client = client

            def _on_notify(_sender, data: bytearray) -> None:
                try:
                    pieces = protocol.decode_board(bytes(data))
                except Exception as exc:  # noqa: BLE001
                    log.debug("Failed to decode board packet: %s", exc)
                    return
                self._emit(BoardReading(pieces))

            await client.start_notify(protocol.READ_CHARACTERISTIC, _on_notify)
            await client.write_gatt_char(
                protocol.WRITE_CHARACTERISTIC, protocol.INIT_REALTIME, response=False
            )
            self._set_state(ConnectionState.CONNECTED)
            log.info("Connected to board at %s", address)
            await disconnected.wait()
            self._client = None

    async def set_leds(self, squares: Iterable[int]) -> None:
        if self._client is None:
            return
        payload = protocol.encode_leds(squares)
        try:
            await self._client.write_gatt_char(
                protocol.WRITE_CHARACTERISTIC, payload, response=False
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("Failed to set LEDs: %s", exc)
