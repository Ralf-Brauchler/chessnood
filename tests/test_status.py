"""The tiny status file backing `chessnood status`."""
import pytest

from chessnood.status import StatusFile


def test_update_writes_and_read_roundtrips(tmp_path):
    p = tmp_path / "status.json"
    sf = StatusFile(p)
    sf.update(connection="connected", state="PLAYER_TURN", skill_level=5)
    data = StatusFile.read(p)
    assert data["connection"] == "connected"
    assert data["state"] == "PLAYER_TURN"
    assert data["skill_level"] == 5
    assert data["updated"] is not None       # stamped on every write


def test_update_merges_fields(tmp_path):
    p = tmp_path / "status.json"
    sf = StatusFile(p)
    sf.update(connection="connected")
    sf.update(last_move="e2e4")               # must not clobber connection
    data = StatusFile.read(p)
    assert data["connection"] == "connected"
    assert data["last_move"] == "e2e4"


def test_update_swallows_unwritable_path(tmp_path):
    # writing into a non-existent directory must not raise (best-effort status)
    sf = StatusFile(tmp_path / "no-such-dir" / "status.json")
    sf.update(state="starting")               # should not raise


def test_read_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        StatusFile.read(tmp_path / "absent.json")
