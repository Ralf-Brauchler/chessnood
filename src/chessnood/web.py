"""A tiny read-only web view of the appliance: the screen + the Pi's health.

Runs as its **own** process/service (`chessnood web`, unit `chessnood-web`). It
only *reads* the status file the game already writes atomically and re-renders
the very same screen with :func:`display.render`, so it can never destabilise the
game -- if the game hangs, the page simply shows a stale ``updated`` time. There
is deliberately no way to control the game from here; to intervene, use SSH.

No authentication: intended for a private network (e.g. Tailscale or the home
LAN). Do not expose it to the open internet.
"""
from __future__ import annotations

import io
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import chess

from . import health
from .boards.base import ConnectionState
from .config import Config
from .display import UiModel, render
from .status import StatusFile

log = logging.getLogger(__name__)


def _read_status(cfg: Config) -> dict | None:
    try:
        return StatusFile.read(cfg.status_file)
    except (FileNotFoundError, ValueError, OSError):
        return None


def _model_from_status(data: dict | None) -> UiModel:
    """Rebuild the screen's UiModel from a status snapshot (best-effort)."""
    if not data:
        return UiModel(ConnectionState.DISCONNECTED, "Kein Status",
                       "Der Dienst läuft nicht (keine Statusdatei).")
    try:
        conn = ConnectionState(data.get("connection") or "disconnected")
    except ValueError:
        conn = ConnectionState.DISCONNECTED
    board = None
    fen = data.get("fen")
    if fen:
        try:
            board = chess.Board(fen)
        except ValueError:
            board = None
    highlight = []
    for name in data.get("highlight") or []:
        try:
            highlight.append(chess.parse_square(name))
        except ValueError:
            pass
    return UiModel(conn, data.get("status") or "", data.get("instruction") or "",
                   board, highlight)


def render_screen_png(cfg: Config) -> bytes:
    """Render the current screen to PNG bytes, exactly as the panel shows it."""
    img = render(_model_from_status(_read_status(cfg)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def snapshot(cfg: Config) -> dict:
    """Everything the page (or a script) needs: program status + machine health."""
    return {"status": _read_status(cfg), "health": health.gather()}


_PAGE = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>chessnood</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; background: #101418; color: #ecf0f2;
         font-family: system-ui, sans-serif; }}
  main {{ max-width: 560px; margin: 0 auto; padding: 20px; }}
  h1 {{ font-size: 20px; font-weight: 600; margin: 0 0 4px; }}
  .sub {{ color: #9ea6a8; font-size: 13px; margin-bottom: 16px; }}
  img {{ width: 100%; max-width: 480px; height: auto; display: block;
        border-radius: 8px; image-rendering: auto; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 14px; }}
  th {{ text-align: left; color: #9ea6a8; font-weight: 600; padding: 8px 0 4px;
       border-bottom: 1px solid #2a3138; }}
  td {{ padding: 3px 0; vertical-align: top; }}
  td.k {{ color: #9ea6a8; width: 40%; }}
  .warn {{ color: #ffb454; }} .bad {{ color: #ff6b6b; }} .ok {{ color: #6bd08a; }}
  .stale {{ color: #ff6b6b; }}
</style></head>
<body><main>
  <h1>chessnood</h1>
  <div class="sub" id="host">…</div>
  <img id="screen" alt="Bildschirm" src="/screen.png">
  <table id="prog"><tr><th colspan="2">Programm</th></tr></table>
  <table id="pi"><tr><th colspan="2">Pi</th></tr></table>
<script>
const REFRESH = {refresh_ms};
function row(k, v, cls) {{
  return '<tr><td class="k">' + k + '</td><td class="' + (cls||'') + '">' + v + '</td></tr>';
}}
function fmtDur(s) {{
  if (s == null) return '–';
  s = Math.floor(s); const d = Math.floor(s/86400), h = Math.floor(s%86400/3600), m = Math.floor(s%3600/60);
  return (d? d+'d ':'') + (h? h+'h ':'') + m + 'm';
}}
function ageSecs(ts) {{
  if (!ts) return null;
  const t = Date.parse(ts.replace(' ', 'T')); return isNaN(t)? null : (Date.now()-t)/1000;
}}
async function tick() {{
  try {{
    const r = await fetch('/status.json', {{cache: 'no-store'}});
    const d = await r.json();
    const s = d.status || {{}}, h = d.health || {{}};
    document.getElementById('host').textContent = (h.hostname || 'chessnood') + ' · ' +
      ((h.service && h.service.active) || 'Dienst?');
    const age = ageSecs(s.updated);
    let upd = s.updated || '–';
    if (age != null && age > 90) upd = '<span class="stale">' + upd + ' (' + fmtDur(age) + ' alt)</span>';
    let bat = '–', batCls = '';
    if (s.battery && s.battery.level != null) {{
      bat = s.battery.level + '%' + (s.battery.charging ? ' (lädt)' : '');
      batCls = s.battery.level <= 15 ? 'bad' : s.battery.level <= 30 ? 'warn' : 'ok';
    }}
    document.getElementById('prog').innerHTML =
      '<tr><th colspan="2">Programm</th></tr>' +
      row('Verbindung', s.connection || '–', s.connection === 'connected' ? 'ok' : 'warn') +
      row('Batterie', bat, batCls) +
      row('Zustand', s.state || '–') +
      row('Anzeige', s.status || '–') +
      row('Hinweis', s.instruction || '–') +
      row('Stärke', s.skill_level == null ? '–' : s.skill_level) +
      row('Letzter Zug', s.last_move || '–') +
      row('Aktualisiert', upd);
    let piRows = '<tr><th colspan="2">Pi</th></tr>';
    if (h.cpu_temp_c != null)
      piRows += row('CPU-Temp', h.cpu_temp_c + ' °C', h.cpu_temp_c >= 80 ? 'bad' : h.cpu_temp_c >= 70 ? 'warn' : 'ok');
    if (h.throttled)
      piRows += row('Stromversorgung',
        h.throttled.ok ? 'ok' : (h.throttled.under_voltage_now ? 'Unterspannung JETZT' : 'Throttling aufgetreten'),
        h.throttled.ok ? 'ok' : 'bad');
    if (h.memory) piRows += row('RAM', h.memory.used_pct + '% von ' + h.memory.total_mb + ' MB');
    if (h.disk) piRows += row('Disk', h.disk.used_pct + '% von ' + h.disk.total_gb + ' GB',
        h.disk.used_pct >= 90 ? 'bad' : '');
    if (h.load) piRows += row('Last', h.load.join('  '));
    if (h.uptime_s != null) piRows += row('Uptime', fmtDur(h.uptime_s));
    if (h.service && h.service.since) piRows += row('Dienst seit', h.service.since);
    document.getElementById('pi').innerHTML = piRows;
  }} catch (e) {{ /* keep the last good view; try again next tick */ }}
  document.getElementById('screen').src = '/screen.png?t=' + Date.now();
}}
tick(); setInterval(tick, REFRESH);
</script>
</main></body></html>"""


def _make_handler(cfg: Config):
    class Handler(BaseHTTPRequestHandler):
        server_version = "chessnood"

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            path = urlparse(self.path).path
            try:
                if path == "/":
                    body = _PAGE.format(refresh_ms=int(cfg.web.refresh_s * 1000)).encode("utf-8")
                    self._send(200, body, "text/html; charset=utf-8")
                elif path == "/screen.png":
                    self._send(200, render_screen_png(cfg), "image/png")
                elif path == "/status.json":
                    body = json.dumps(snapshot(cfg)).encode("utf-8")
                    self._send(200, body, "application/json")
                else:
                    self._send(404, b"not found", "text/plain")
            except Exception:  # noqa: BLE001 - a view error must not kill the server
                log.exception("Error handling %s", path)
                self._send(500, b"internal error", "text/plain")

        do_HEAD = do_GET

        def log_message(self, fmt: str, *args) -> None:
            log.debug("web %s - " + fmt, self.address_string(), *args)

    return Handler


def build_server(cfg: Config, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _make_handler(cfg))


def serve(cfg: Config, host: str, port: int) -> None:
    httpd = build_server(cfg, host, port)
    log.info("chessnood web view on http://%s:%d/ (read-only)", host, port)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
