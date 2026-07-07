"""Best-effort machine health, so a remote maintainer can see how the Pi is doing.

Every probe is wrapped so a missing file, an absent tool (running on a Mac in
dev) or a permission error yields ``None`` rather than raising -- this is a
read-only status view and must never itself fall over. Values are plain
JSON-friendly types so the same dict feeds both ``chessnood status`` (SSH) and
the web view.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
from typing import Any

# The game service we report on (the web view runs as its own unit).
SERVICE = "chessnood"


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _run(cmd: list[str]) -> str | None:
    """Run a short command, returning stdout stripped, or None if it can't run."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (out.stdout or "").strip()
    return text or None


def cpu_temp_c() -> float | None:
    """CPU temperature in °C from the thermal zone (milli-°C), or None off a Pi."""
    raw = _read("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(raw) / 1000.0, 1) if raw is not None else None
    except ValueError:
        return None


# Bits of vcgencmd's throttled word we care about (undervoltage is the classic
# "USB-powered board pulls too much" failure -- see docs/HARDWARE.md).
_THROTTLE_BITS = {
    "under_voltage_now": 0,
    "freq_capped_now": 1,
    "throttled_now": 2,
    "under_voltage_occurred": 16,
    "freq_capped_occurred": 17,
    "throttled_occurred": 18,
}


def decode_throttled(word: int) -> dict[str, Any]:
    flags = {name: bool(word & (1 << bit)) for name, bit in _THROTTLE_BITS.items()}
    flags["raw"] = f"0x{word:x}"
    flags["ok"] = word == 0
    return flags


def throttled() -> dict[str, Any] | None:
    """Decode ``vcgencmd get_throttled`` (undervoltage/throttling), None off a Pi."""
    out = _run(["vcgencmd", "get_throttled"])   # e.g. "throttled=0x0"
    if not out or "=" not in out:
        return None
    try:
        return decode_throttled(int(out.split("=", 1)[1].strip(), 16))
    except ValueError:
        return None


def uptime_s() -> float | None:
    raw = _read("/proc/uptime")
    try:
        return round(float(raw.split()[0]), 1) if raw else None
    except (ValueError, IndexError):
        return None


def loadavg() -> list[float] | None:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        return None


def memory() -> dict[str, float] | None:
    """Total / available RAM in MB and used-percent, from /proc/meminfo."""
    raw = _read("/proc/meminfo")
    if not raw:
        return None
    vals: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].rstrip(":") in ("MemTotal", "MemAvailable"):
            try:
                vals[parts[0].rstrip(":")] = int(parts[1])  # kB
            except ValueError:
                pass
    total, avail = vals.get("MemTotal"), vals.get("MemAvailable")
    if not total:
        return None
    used_pct = round((total - (avail or 0)) / total * 100, 1)
    return {"total_mb": round(total / 1024, 1), "used_pct": used_pct}


def disk() -> dict[str, float] | None:
    try:
        u = shutil.disk_usage("/")
    except OSError:
        return None
    return {"total_gb": round(u.total / 1e9, 1),
            "used_pct": round(u.used / u.total * 100, 1)}


def service() -> dict[str, str | None]:
    """State of the game service via systemctl (active/inactive/failed), best-effort."""
    active = _run(["systemctl", "is-active", SERVICE])          # nonzero exit when inactive
    since = _run(["systemctl", "show", SERVICE, "-p", "ActiveEnterTimestamp", "--value"])
    return {"active": active, "since": since or None}


def gather() -> dict[str, Any]:
    """One best-effort snapshot of machine health for the status/web views."""
    return {
        "hostname": socket.gethostname(),
        "service": service(),
        "cpu_temp_c": cpu_temp_c(),
        "throttled": throttled(),
        "uptime_s": uptime_s(),
        "load": loadavg(),
        "memory": memory(),
        "disk": disk(),
    }
