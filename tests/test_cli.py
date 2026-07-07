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
    import chess as _chess

    from chessnood.status import StatusFile
    sfile = tmp_path / "status.json"
    StatusFile(sfile).update(connection="connected", state="PLAYER_TURN", skill_level=5,
                             fen=_chess.Board().fen())
    cfg = _cfg(tmp_path, f"status_file: {sfile}\n")
    rc = cli.cmd_status(argparse.Namespace(config=cfg))
    assert rc == 0
    out = capsys.readouterr().out
    assert "connected" in out and "PLAYER_TURN" in out
    assert "Board" in out and "R N B Q K B N R" in out   # board rendered from the FEN
    assert "Pi:" in out and "cpu temp" in out             # Pi health section


def test_status_missing_file_still_shows_pi_health(tmp_path, capsys):
    cfg = _cfg(tmp_path, f"status_file: {tmp_path / 'absent.json'}\n")
    rc = cli.cmd_status(argparse.Namespace(config=cfg))
    assert rc == 1
    out, err = capsys.readouterr()
    assert "service running" in err.lower()
    assert "Pi:" in out                                   # health still printed when game is down


def test_preview_writes_png(tmp_path):
    out = tmp_path / "preview.png"
    rc = cli.cmd_preview(argparse.Namespace(out=str(out)))
    assert rc == 0
    assert out.exists() and out.stat().st_size > 0


def test_main_dispatches_simulate(tmp_path):
    cfg = _cfg(tmp_path, "engine:\n  path: /nonexistent/stockfish\n")
    assert cli.main(["-c", cfg, "simulate", "--max-plies", "6"]) == 0


# --- hardware bring-up diagnostics (driven by a fake board, no hardware) -----

import chess

from chessnood.boards import usb


def _empty_board_report() -> bytes:
    b = bytearray(usb.BOARD_DATA_OFFSET + usb.BOARD_DATA_LEN)
    b[0] = usb.REPORT_BOARD
    b[1] = usb.BOARD_DATA_LEN
    return bytes(b)


class FakeDiag:
    """Stand-in for usb.DiagDevice: records writes, replays canned reads."""

    def __init__(self, reads=()):
        self.prefix = True
        self.writes = []
        self._reads = list(reads)

    def write(self, payload):
        self.writes.append(bytes(payload))
        return len(payload)

    def start_realtime(self):
        self.write(usb.CMD_REALTIME)

    def read(self, timeout_ms=100):
        if self._reads:
            return self._reads.pop(0)
        raise KeyboardInterrupt  # ends the (otherwise time-bounded) loop in the test

    def close(self):
        pass


def _patch_open(monkeypatch, diag):
    monkeypatch.setattr(usb, "open_diag", lambda prefix=True: diag)


def test_dump_writes_realtime_and_reports(monkeypatch, capsys):
    fake = FakeDiag(reads=[_empty_board_report(), _empty_board_report()])
    _patch_open(monkeypatch, fake)
    rc = cli.cmd_dump(argparse.Namespace(seconds=60, no_prefix=False))
    assert rc == 0
    assert usb.CMD_REALTIME in fake.writes      # realtime stream was started
    out = capsys.readouterr().out
    assert "type=0x01" in out and "Reports empfangen" in out


def test_watch_decodes_reports(monkeypatch, capsys):
    fake = FakeDiag(reads=[_empty_board_report()])
    _patch_open(monkeypatch, fake)
    rc = cli.cmd_watch(argparse.Namespace(seconds=60, no_prefix=False))
    assert rc == 0
    assert usb.CMD_REALTIME in fake.writes
    assert "(leer)" in capsys.readouterr().out   # empty board decoded


def test_led_lights_then_clears(monkeypatch):
    fake = FakeDiag()
    _patch_open(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda *a: "")  # don't block on Enter
    rc = cli.cmd_led(argparse.Namespace(squares=["a1"], no_prefix=False))
    assert rc == 0
    assert fake.writes[0] == usb.encode_leds([chess.A1])  # lit the square
    assert fake.writes[-1] == usb.encode_leds([])          # then cleared


def test_led_rejects_bad_square(capsys):
    rc = cli.cmd_led(argparse.Namespace(squares=["z9"], no_prefix=False))
    assert rc == 1
    assert "Ungültiges Feld" in capsys.readouterr().err


def test_beep_sends_command(monkeypatch):
    fake = FakeDiag()
    _patch_open(monkeypatch, fake)
    rc = cli.cmd_beep(argparse.Namespace(freq=440, ms=100, no_prefix=False))
    assert rc == 0
    assert fake.writes == [usb.CMD_BEEP + bytes([440 >> 8, 440 & 0xFF, 0, 100])]


def test_diag_open_failure_is_friendly(monkeypatch, capsys):
    def boom(prefix=True):
        raise RuntimeError("no Chessnut USB board found")
    monkeypatch.setattr(usb, "open_diag", boom)
    assert cli.cmd_beep(argparse.Namespace(freq=1000, ms=200, no_prefix=False)) == 1
    assert "Could not open the board" in capsys.readouterr().err


def test_ascii_board_renders_start_position():
    rows = cli._ascii_board(chess.Board().piece_map()).splitlines()
    assert rows[0].startswith("8  r n b q k b n r")
    assert rows[7].startswith("1  R N B Q K B N R")
    assert rows[-1].strip().startswith("a b c d e f g h")
