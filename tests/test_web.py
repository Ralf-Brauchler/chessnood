"""The read-only web view: it rebuilds the screen from the status file and serves
the same image + Pi health over HTTP, without ever touching the game."""
import json
import threading
import urllib.error
import urllib.request

import chess
import pytest

from chessnood.boards.base import ConnectionState
from chessnood.config import Config
from chessnood.status import StatusFile

web = pytest.importorskip("chessnood.web")  # needs Pillow (display extra)


def _cfg(tmp_path, status: dict | None = None) -> Config:
    sfile = tmp_path / "status.json"
    if status is not None:
        sf = StatusFile(sfile)
        sf.update(**status)
    cfg = Config()
    cfg.status_file = str(sfile)
    cfg.game_state_file = str(tmp_path / "game.json")
    return cfg


def test_model_from_status_rebuilds_the_screen():
    board = chess.Board()
    board.push_uci("e2e4")
    m = web._model_from_status({
        "connection": "connected", "status": "Du bist am Zug",
        "instruction": "Mach deinen Zug.", "fen": board.fen(), "highlight": ["e2", "e4"],
    })
    assert m.connection == ConnectionState.CONNECTED
    assert m.status == "Du bist am Zug"
    assert m.board.fen() == board.fen()
    assert m.highlight == [chess.E2, chess.E4]


def test_model_from_status_handles_missing_and_bad_data():
    assert web._model_from_status(None).connection == ConnectionState.DISCONNECTED
    m = web._model_from_status({"connection": "bogus", "fen": "not-a-fen", "highlight": ["z9"]})
    assert m.connection == ConnectionState.DISCONNECTED   # bad enum -> disconnected
    assert m.board is None                                # bad fen -> no board
    assert m.highlight == []                              # bad square dropped


def test_render_screen_png_is_a_png(tmp_path):
    cfg = _cfg(tmp_path, {"connection": "connected", "status": "Du bist am Zug",
                          "fen": chess.Board().fen()})
    png = web.render_screen_png(cfg)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_snapshot_carries_status_and_health(tmp_path):
    cfg = _cfg(tmp_path, {"connection": "connected", "state": "PLAYER_TURN"})
    snap = web.snapshot(cfg)
    assert snap["status"]["state"] == "PLAYER_TURN"
    assert "hostname" in snap["health"]


def _serve(cfg):
    httpd = web.build_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def test_endpoints_serve_page_image_and_json(tmp_path):
    cfg = _cfg(tmp_path, {"connection": "connected", "state": "PLAYER_TURN",
                          "status": "Du bist am Zug", "fen": chess.Board().fen()})
    httpd, port = _serve(cfg)
    try:
        status, ctype, body = _get(port, "/")
        assert status == 200 and "text/html" in ctype and b"chessnood" in body

        status, ctype, body = _get(port, "/screen.png")
        assert status == 200 and ctype == "image/png" and body[:4] == b"\x89PNG"

        status, ctype, body = _get(port, "/status.json")
        assert status == 200 and "application/json" in ctype
        assert json.loads(body)["status"]["state"] == "PLAYER_TURN"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_missing_status_file_still_serves(tmp_path):
    # service down (no status file): the page and image must still render
    cfg = _cfg(tmp_path, status=None)
    httpd, port = _serve(cfg)
    try:
        assert _get(port, "/screen.png")[0] == 200
        assert web.snapshot(cfg)["status"] is None
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_unknown_path_is_404(tmp_path):
    httpd, port = _serve(_cfg(tmp_path, {"connection": "connected"}))
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(port, "/nope")
        assert exc.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
