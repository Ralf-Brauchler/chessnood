"""systemd watchdog helper: a safe no-op off systemd, pings when configured."""
import asyncio

from chessnood import watchdog


def test_notify_is_noop_without_socket(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    # must not raise and must report "not sent"
    assert watchdog._notify(b"READY=1") is False
    watchdog.notify_ready()      # no socket -> nothing happens, no error
    watchdog.notify_watchdog()


def test_watchdog_interval_half_of_deadline(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "30000000")  # 30s
    assert watchdog.watchdog_interval_s() == 15.0


def test_watchdog_interval_none_when_unset(monkeypatch):
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert watchdog.watchdog_interval_s() is None


def test_watchdog_interval_handles_garbage(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "not-a-number")
    assert watchdog.watchdog_interval_s() is None


def test_heartbeat_returns_immediately_without_watchdog(monkeypatch):
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    # the task should just end (not loop forever) when no watchdog is configured
    asyncio.run(asyncio.wait_for(watchdog.heartbeat(), timeout=1.0))


def test_heartbeat_pings_then_can_be_cancelled(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "2000000")  # 2s -> ping every 1s
    pings = []
    monkeypatch.setattr(watchdog, "notify_watchdog", lambda: pings.append(1))

    async def go():
        task = asyncio.create_task(watchdog.heartbeat())
        await asyncio.sleep(0.05)   # let it run one immediate ping
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    assert pings, "heartbeat should ping at least once immediately"
