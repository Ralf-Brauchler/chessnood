"""CLI commands: simulate a full game, status, preview, and dispatch."""
import argparse

from chessnood import cli


def _cfg(tmp_path, body=""):
    p = tmp_path / "c.yaml"
    p.write_text(body)
    return str(p)


def test_simulate_plays_without_hardware_or_stockfish(tmp_path, capsys):
    # nonexistent engine -> random fallback, so this runs anywhere
    cfg = _cfg(tmp_path, "engine:\n  path: /nonexistent/stockfish\n")
    rc = cli.cmd_simulate(argparse.Namespace(config=cfg, max_plies=12))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Final:" in out


def test_status_missing_file_returns_error(tmp_path, capsys):
    cfg = _cfg(tmp_path, f"status_file: {tmp_path / 'absent.json'}\n")
    rc = cli.cmd_status(argparse.Namespace(config=cfg))
    assert rc == 1
    assert "service running" in capsys.readouterr().err.lower()


def test_status_reads_existing_file(tmp_path, capsys):
    from chessnood.status import StatusFile
    sfile = tmp_path / "status.json"
    StatusFile(sfile).update(connection="connected", state="PLAYER_TURN", skill_level=5)
    cfg = _cfg(tmp_path, f"status_file: {sfile}\n")
    rc = cli.cmd_status(argparse.Namespace(config=cfg))
    assert rc == 0
    out = capsys.readouterr().out
    assert "connected" in out and "PLAYER_TURN" in out


def test_preview_writes_png(tmp_path):
    out = tmp_path / "preview.png"
    rc = cli.cmd_preview(argparse.Namespace(out=str(out)))
    assert rc == 0
    assert out.exists() and out.stat().st_size > 0


def test_main_dispatches_simulate(tmp_path):
    cfg = _cfg(tmp_path, "engine:\n  path: /nonexistent/stockfish\n")
    assert cli.main(["-c", cfg, "simulate", "--max-plies", "6"]) == 0
