"""Crash-safe writes: the saved game must survive the power being yanked."""
import pytest

from chessnood.atomicio import atomic_write_text


def test_writes_content(tmp_path):
    p = tmp_path / "game.json"
    atomic_write_text(p, "hello")
    assert p.read_text() == "hello"


def test_overwrites_existing(tmp_path):
    p = tmp_path / "game.json"
    p.write_text("old")
    atomic_write_text(p, "new")
    assert p.read_text() == "new"


def test_leaves_no_temp_file_behind(tmp_path):
    p = tmp_path / "game.json"
    atomic_write_text(p, "x")
    # the only file in the dir is the target -- no stray .game.json.tmp
    assert [f.name for f in tmp_path.iterdir()] == ["game.json"]


def test_failed_write_keeps_old_file_and_cleans_temp(tmp_path, monkeypatch):
    import os
    p = tmp_path / "game.json"
    p.write_text("good")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(p, "half-written")

    assert p.read_text() == "good"                       # old file intact
    assert [f.name for f in tmp_path.iterdir()] == ["game.json"]  # temp cleaned up
