"""Minimal systemd ``sd_notify`` support (stdlib only, no python-systemd dep).

For an unattended appliance ``Restart=always`` only covers a *crash* -- if the
asyncio loop wedges (e.g. a backend blocks forever), the process stays alive and
systemd never restarts it. With ``Type=notify`` + ``WatchdogSec`` the service is
expected to ping ``WATCHDOG=1`` periodically; miss the deadline and systemd kills
and restarts it. The runner drives this from its event loop, so a hung loop stops
pinging and gets recovered.

All a no-op when ``$NOTIFY_SOCKET`` is unset (i.e. not run under systemd, e.g. on
a dev Mac), so importing/calling this is always safe.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

log = logging.getLogger(__name__)


def _notify(message: bytes) -> bool:
    """Send a datagram to the systemd notify socket. True if it was sent."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    # An abstract socket address starts with '@' (maps to a leading NUL byte).
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(message)
        return True
    except OSError as exc:
        log.debug("sd_notify failed: %s", exc)
        return False


def notify_ready() -> None:
    """Tell systemd the service has finished starting (required by Type=notify)."""
    _notify(b"READY=1")


def notify_watchdog() -> None:
    """Pet the watchdog. No-op if not running under systemd."""
    _notify(b"WATCHDOG=1")


def watchdog_interval_s() -> float | None:
    """Half of ``WatchdogSec`` (the recommended ping period), or None if unset.

    systemd exports the deadline in microseconds via ``WATCHDOG_USEC``; pinging at
    half that leaves comfortable margin.
    """
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec:
        return None
    try:
        return max(1.0, int(usec) / 1_000_000.0 / 2.0)
    except ValueError:
        return None


async def heartbeat() -> None:
    """Background task: ping the watchdog every half-deadline, forever.

    Returns immediately (so the task just ends) when no watchdog is configured.
    """
    interval = watchdog_interval_s()
    if interval is None:
        return
    log.info("systemd watchdog active; pinging every %.1fs", interval)
    while True:
        notify_watchdog()
        await asyncio.sleep(interval)
