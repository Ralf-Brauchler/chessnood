"""Status LED and buttons, with no-op/console fallbacks when not on a Pi.

GPIO support uses gpiozero and is imported lazily, so this module loads fine on
a Mac. On non-Pi hosts you get logging-only indicators and no physical buttons.
"""
from __future__ import annotations

import logging
from typing import Callable

from .boards.base import ConnectionState
from .config import HardwareConfig

log = logging.getLogger(__name__)


class StatusIndicator:
    """Reflects the Bluetooth connection state on a single LED.

    solid = connected, slow blink = scanning, fast blink = error, off = idle.
    """

    def __init__(self, pin: int | None):
        self._led = None
        if pin is None:
            return
        try:
            from gpiozero import LED

            self._led = LED(pin)
        except Exception as exc:  # noqa: BLE001 - not on a Pi, or no GPIO
            log.info("Status LED disabled (no GPIO on pin %s): %s", pin, exc)

    def set_state(self, state: ConnectionState) -> None:
        log.info("Connection state: %s", state.value)
        if self._led is None:
            return
        if state == ConnectionState.CONNECTED:
            self._led.on()
        elif state == ConnectionState.SCANNING:
            self._led.blink(on_time=0.6, off_time=0.6)
        elif state == ConnectionState.ERROR:
            self._led.blink(on_time=0.15, off_time=0.15)
        else:
            self._led.off()

    def close(self) -> None:
        if self._led is not None:
            self._led.close()


class Buttons:
    """Physical buttons. On non-Pi hosts this is inert (no buttons)."""

    def __init__(self, cfg: HardwareConfig):
        self._buttons: list = []
        self._handlers: dict[str, Callable[[], None]] = {}
        self._wire("new_game", cfg.buttons.new_game_pin)
        self._wire("resign", cfg.buttons.resign_pin)

    def _wire(self, name: str, pin: int | None) -> None:
        if pin is None:
            return
        try:
            from gpiozero import Button

            btn = Button(pin, pull_up=True, bounce_time=0.05)
            btn.when_pressed = lambda n=name: self._fire(n)
            self._buttons.append(btn)
        except Exception as exc:  # noqa: BLE001
            log.info("Button '%s' disabled (no GPIO on pin %s): %s", name, pin, exc)

    def _fire(self, name: str) -> None:
        handler = self._handlers.get(name)
        if handler:
            handler()

    def on(self, name: str, handler: Callable[[], None]) -> None:
        self._handlers[name] = handler

    def close(self) -> None:
        for btn in self._buttons:
            btn.close()
