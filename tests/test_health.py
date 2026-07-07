"""Best-effort machine health: never raises, decodes the throttled word."""
from chessnood import health


def test_gather_returns_expected_shape_without_raising():
    h = health.gather()
    for key in ("hostname", "service", "cpu_temp_c", "throttled",
                "uptime_s", "load", "memory", "disk"):
        assert key in h
    assert isinstance(h["hostname"], str) and h["hostname"]
    assert set(h["service"]) == {"active", "since"}


def test_decode_throttled_all_clear():
    d = health.decode_throttled(0x0)
    assert d["ok"] is True
    assert d["raw"] == "0x0"
    assert not any(d[k] for k in
                   ("under_voltage_now", "throttled_now", "under_voltage_occurred"))


def test_decode_throttled_undervoltage_now_and_past():
    # bit 0 = undervoltage now, bit 16 = undervoltage has occurred
    d = health.decode_throttled(0x10001)
    assert d["ok"] is False
    assert d["under_voltage_now"] is True
    assert d["under_voltage_occurred"] is True
    assert d["throttled_now"] is False


def test_probes_return_none_when_source_absent(monkeypatch):
    # simulate a non-Pi: no thermal file, no vcgencmd
    monkeypatch.setattr(health, "_read", lambda path: None)
    monkeypatch.setattr(health, "_run", lambda cmd: None)
    assert health.cpu_temp_c() is None
    assert health.throttled() is None
    assert health.uptime_s() is None
    assert health.service() == {"active": None, "since": None}
