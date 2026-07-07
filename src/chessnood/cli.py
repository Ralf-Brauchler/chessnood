"""Command-line interface.

  chessnood run        run the service against the real board (or mock)
  chessnood simulate   play a full game with no hardware (proves the logic)
  chessnood scan       list attached Chessnut USB boards (first hardware test)
  chessnood status     print what the service, board and Pi are doing (SSH view)
  chessnood web        serve a read-only web page of the same (for remote viewing)
  chessnood preview    render the touchscreen to a PNG to see how it looks

Hardware bring-up (one layer at a time, see docs/HARDWARE.md):
  chessnood dump       open + realtime + hex-dump raw HID reports
  chessnood watch      live ASCII board from the decoded reports (orientation!)
  chessnood led a1 …   light specific squares until Enter (LED mapping)
  chessnood beep       sound one tone
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time

import chess

from .boards import build_board
from .boards.mock import MockBoard
from .config import Config, ConfigWatcher
from .engine import Engine
from .game import ChessGame, GameState
from .logging_setup import setup_logging
from .runner import Runner
from .status import StatusFile


def _squares(squares: list[int]) -> str:
    return ", ".join(chess.square_name(s) for s in squares) or "-"


def cmd_run(args: argparse.Namespace) -> int:
    watcher = ConfigWatcher(args.config)
    setup_logging(watcher.current.log_level)
    board = build_board(watcher.current.board)
    runner = Runner(board, watcher)
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        print("\nStopping.")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    """Play a full game with both sides automated, through the real game logic."""
    cfg = Config.load(args.config)
    setup_logging("warning")  # keep the move list readable
    engine = Engine(cfg.engine)
    game = ChessGame(human_color=chess.WHITE)
    board = MockBoard()

    print("Simulating a game (no hardware). Random human vs. configured engine.\n")
    react = game.feed(board.current)  # start position present -> begin play
    ply = 0
    while game.state != GameState.GAME_OVER and ply < args.max_plies:
        if react.engine_should_move:
            move = engine.best_move(game.board)
            san = game.board.san(move)
            react = game.set_engine_move(move)
            print(f"  computer: {san:7s}  LEDs: {_squares(react.leds)}")
            executed = game.board.copy(stack=False)
            executed.push(move)
            board.set_position(executed)
            react = game.feed(board.current)
            ply += 1
        elif game.state == GameState.PLAYER_TURN:
            move = random.choice(list(game.board.legal_moves))
            print(f"  player:   {game.board.san(move):7s}")
            played = game.board.copy(stack=False)
            played.push(move)
            board.set_position(played)
            react = game.feed(board.current)
            ply += 1
        else:
            break

    print(f"\nFinal: {game.board.result()} after {ply} plies, {game.board.fullmove_number} moves.")
    print(game.board)
    engine.close()
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """List attached Chessnut USB boards. Run this with the board plugged in."""
    try:
        from .boards.usb import list_devices
    except ImportError:
        print("hidapi not installed. Run:  pip install 'chessnood[usb]'", file=sys.stderr)
        return 1
    try:
        boards = list_devices()
    except Exception as exc:  # noqa: BLE001 - hidapi/permission issues
        print(f"Could not enumerate USB devices: {exc}", file=sys.stderr)
        return 1
    if not boards:
        print("No Chessnut USB board found. Is it plugged in and powered on?")
        return 1
    for desc, _pid in boards:
        print(f"  {desc}")
    return 0


def _ascii_board(pieces: dict) -> str:
    """A plain 8x8 board (rank 8 at top, file a at left) of the sensed pieces."""
    rows = []
    for rank in range(7, -1, -1):
        cells = [(pieces[sq].symbol() if (sq := chess.square(f, rank)) in pieces else ".")
                 for f in range(8)]
        rows.append(f"{rank + 1}  " + " ".join(cells))
    rows.append("   " + " ".join("abcdefgh"))
    return "\n".join(rows)


def _diag_open(args: argparse.Namespace):
    """Open the board for a diagnostic command, or print why we can't and None."""
    try:
        from .boards.usb import open_diag
        return open_diag(prefix=not getattr(args, "no_prefix", False))
    except ImportError:
        print("hidapi not installed. Run:  pip install 'chessnood[usb]'", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - no board / permissions
        print(f"Could not open the board: {exc}", file=sys.stderr)
    return None


def cmd_dump(args: argparse.Namespace) -> int:
    """Rungs 2-3: open, start the realtime stream, hex-dump raw HID reports.

    Proves the write path (the realtime command) and that the board streams
    reports at all -- without trusting our decode. Watch the type (byte 0) and
    length (byte 1) against docs/HARDWARE.md."""
    dev = _diag_open(args)
    if dev is None:
        return 1
    print(f"Realtime-Stream (prefix={'an' if dev.prefix else 'aus'}). "
          f"Bewege Figuren; Ctrl-C zum Stoppen.\n")
    n = 0
    try:
        dev.start_realtime()
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            data = dev.read(100)
            if not data:
                continue
            n += 1
            length = data[1] if len(data) > 1 else 0
            print(f"#{n:04d} type=0x{data[0]:02x} len={length}  {data.hex(' ')}")
    except KeyboardInterrupt:
        pass
    finally:
        dev.close()
    print(f"\n{n} Reports empfangen.")
    return 0 if n else 1


def cmd_watch(args: argparse.Namespace) -> int:
    """Rungs 4-5: live ASCII board from the decoded reports.

    Set the start position to check decoding; then put a *single* distinctive
    piece on a known corner (e.g. one king on a1) to check orientation
    unambiguously -- it must show up where you placed it."""
    dev = _diag_open(args)
    if dev is None:
        return 1
    from .boards.usb import (BOARD_DATA_LEN, BOARD_DATA_OFFSET, REPORT_BOARD,
                             decode_board_report)
    print("Live-Brett. Lege Figuren auf; Ctrl-C zum Stoppen.")
    last = None
    try:
        dev.start_realtime()
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            data = dev.read(100)
            if not data or data[0] != REPORT_BOARD or len(data) < BOARD_DATA_OFFSET + BOARD_DATA_LEN:
                continue
            pieces = decode_board_report(data)
            if pieces == last:
                continue
            last = pieces
            occ = ", ".join(f"{chess.square_name(s)}={p.symbol()}"
                            for s, p in sorted(pieces.items())) or "(leer)"
            print("\x1b[2J\x1b[H" + _ascii_board(pieces) + f"\n\nBesetzt: {occ}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        dev.close()
    return 0


def cmd_led(args: argparse.Namespace) -> int:
    """Rungs 6-7: light the given squares until Enter, then clear.

    Light a *single* corner (e.g. `chessnood led a1`) and check that exactly that
    square lights on the board -- this nails the LED mapping/orientation. Use
    --no-prefix to test the other HID report-ID convention."""
    try:
        squares = [chess.parse_square(s.lower()) for s in args.squares]
    except ValueError:
        print(f"Ungültiges Feld in {args.squares} (erwartet z.B. a1 h8).", file=sys.stderr)
        return 1
    dev = _diag_open(args)
    if dev is None:
        return 1
    from .boards.usb import encode_leds
    try:
        dev.write(encode_leds(squares))
        print(f"Leuchtet: {_squares(squares)}  (prefix={'an' if dev.prefix else 'aus'})")
        print("Prüfe am Brett, dann Enter zum Ausschalten …")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        dev.write(encode_leds([]))
    finally:
        dev.close()
    return 0


def cmd_beep(args: argparse.Namespace) -> int:
    """Rung 8: sound one tone."""
    dev = _diag_open(args)
    if dev is None:
        return 1
    from .boards.usb import CMD_BEEP
    f = max(0, min(0xFFFF, args.freq))
    d = max(0, min(0xFFFF, args.ms))
    try:
        dev.write(CMD_BEEP + bytes([f >> 8, f & 0xFF, d >> 8, d & 0xFF]))
        print(f"Beep gesendet: {f} Hz, {d} ms (prefix={'an' if dev.prefix else 'aus'})")
    finally:
        dev.close()
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    """Render the screen in a few states, stacked into one PNG, so you can see
    exactly what your father sees — no Pi or board needed."""
    try:
        from PIL import Image

        from .display import SCREEN_H, SCREEN_W, UiModel, render
    except ImportError:
        print("Pillow not installed. Run:  pip install 'chessnood[display]'", file=sys.stderr)
        return 1
    from .boards.base import ConnectionState

    mid = chess.Board()
    for uci in ("e2e4", "e7e5", "g1f3"):
        mid.push_uci(uci)
    samples = [
        UiModel(ConnectionState.SCANNING, "Suche das Brett …",
                "Schalte das Brett ein und warte kurz.", chess.Board()),
        UiModel(ConnectionState.CONNECTED, "Stelle die Figuren auf",
                "Stelle alle Figuren auf die Grundstellung.", chess.Board()),
        UiModel(ConnectionState.CONNECTED, "Du bist am Zug",
                "Mach deinen Zug auf dem Brett.", mid),
        UiModel(ConnectionState.CONNECTED, "Computer hat gezogen",
                "Die leuchtenden Felder zeigen den Zug. Führe ihn auf dem Brett aus.",
                mid, [chess.G1, chess.F3]),
    ]
    frames = [render(s) for s in samples]
    gap = 12
    sheet = Image.new("RGB", (SCREEN_W, SCREEN_H * len(frames) + gap * (len(frames) - 1)), (0, 0, 0))
    for i, frame in enumerate(frames):
        sheet.paste(frame, (0, i * (SCREEN_H + gap)))
    sheet.save(args.out)
    print(f"Wrote {args.out} — {len(frames)} screens (480x320 each).")
    return 0


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(seconds)
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return (f"{d}d " if d else "") + (f"{h}h " if h or d else "") + f"{m}m"


def _status_board(data: dict, cfg: Config) -> "chess.Board | None":
    """The board the screen is showing: prefer the status FEN, fall back to the
    saved game file (older status files predate the embedded FEN)."""
    import json
    fen = data.get("fen")
    if not fen and cfg.game_state_file:
        try:
            fen = json.loads(open(cfg.game_state_file, encoding="utf-8").read()).get("fen")
        except (OSError, ValueError):
            fen = None
    if not fen:
        return None
    try:
        return chess.Board(fen)
    except ValueError:
        return None


def _print_health() -> None:
    from . import health
    h = health.gather()
    print("Pi:")
    svc = h.get("service") or {}
    print(f"  {'host':13s}: {h.get('hostname')}")
    print(f"  {'service':13s}: {svc.get('active')}"
          + (f" (seit {svc.get('since')})" if svc.get("since") else ""))
    temp = h.get("cpu_temp_c")
    print(f"  {'cpu temp':13s}: {f'{temp} °C' if temp is not None else '-'}")
    thr = h.get("throttled")
    if thr is None:
        power = "-"
    elif thr["ok"]:
        power = "ok"
    elif thr["under_voltage_now"]:
        power = f"UNDERVOLTAGE NOW ({thr['raw']})"
    else:
        power = f"throttling occurred ({thr['raw']})"
    print(f"  {'power':13s}: {power}")
    mem = h.get("memory")
    print(f"  {'memory':13s}: " + (f"{mem['used_pct']}% of {mem['total_mb']} MB" if mem else "-"))
    disk = h.get("disk")
    print(f"  {'disk':13s}: " + (f"{disk['used_pct']}% of {disk['total_gb']} GB" if disk else "-"))
    load = h.get("load")
    print(f"  {'load':13s}: " + ("  ".join(str(x) for x in load) if load else "-"))
    print(f"  {'uptime':13s}: {_fmt_duration(h.get('uptime_s'))}")


def cmd_status(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    try:
        data = StatusFile.read(cfg.status_file)
    except FileNotFoundError:
        print(f"No status file at {cfg.status_file}. Is the service running?", file=sys.stderr)
        print()
        _print_health()      # still show the Pi's health even when the game is down
        return 1
    print("Program:")
    for key in ("connection", "state", "skill_level", "status", "instruction",
                "last_move", "updated"):
        print(f"  {key:13s}: {data.get(key)}")
    bat = data.get("battery")
    if bat:
        charge = " (charging)" if bat.get("charging") else ""
        print(f"  {'battery':13s}: {bat.get('level')}%{charge}")
    board = _status_board(data, cfg)
    if board is not None:
        print("\nBoard (what the screen shows):")
        for line in _ascii_board(board.piece_map()).splitlines():
            print(f"  {line}")
    print()
    _print_health()
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    """Serve a read-only web page showing the screen + the Pi's health."""
    cfg = Config.load(args.config)
    setup_logging(cfg.log_level)
    try:
        from .web import serve
    except ImportError:
        print("Pillow not installed. Run:  pip install 'chessnood[display]'", file=sys.stderr)
        return 1
    host = args.host if args.host is not None else cfg.web.host
    port = args.port if args.port is not None else cfg.web.port
    print(f"chessnood web view on http://{host}:{port}/  (read-only; Ctrl-C to stop)")
    try:
        serve(cfg, host, port)
    except KeyboardInterrupt:
        print("\nStopping.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chessnood", description=__doc__)
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="run the service").set_defaults(func=cmd_run)

    p_sim = sub.add_parser("simulate", help="play a full game without hardware")
    p_sim.add_argument("--max-plies", type=int, default=200)
    p_sim.set_defaults(func=cmd_simulate)

    sub.add_parser("scan", help="list attached Chessnut USB boards").set_defaults(func=cmd_scan)

    # --- hardware bring-up diagnostics (one layer at a time) ---
    def _prefix_flag(p):
        p.add_argument("--no-prefix", action="store_true",
                       help="drop the leading 0x00 HID report-ID byte (try if writes are ignored)")

    p_dump = sub.add_parser("dump", help="hex-dump raw HID reports (open + realtime)")
    p_dump.add_argument("--seconds", type=float, default=20.0, help="how long to stream")
    _prefix_flag(p_dump)
    p_dump.set_defaults(func=cmd_dump)

    p_watch = sub.add_parser("watch", help="live ASCII board from decoded reports")
    p_watch.add_argument("--seconds", type=float, default=120.0, help="how long to watch")
    _prefix_flag(p_watch)
    p_watch.set_defaults(func=cmd_watch)

    p_led = sub.add_parser("led", help="light squares until Enter (e.g. led a1 h8)")
    p_led.add_argument("squares", nargs="+", help="squares to light, e.g. a1 e4 h8")
    _prefix_flag(p_led)
    p_led.set_defaults(func=cmd_led)

    p_beep = sub.add_parser("beep", help="sound one tone")
    p_beep.add_argument("--freq", type=int, default=1000, help="frequency in Hz")
    p_beep.add_argument("--ms", type=int, default=200, help="duration in ms")
    _prefix_flag(p_beep)
    p_beep.set_defaults(func=cmd_beep)

    p_prev = sub.add_parser("preview", help="render the touchscreen to a PNG")
    p_prev.add_argument("--out", default="./chessnood-preview.png")
    p_prev.set_defaults(func=cmd_preview)

    sub.add_parser("status", help="print service, board and Pi status").set_defaults(func=cmd_status)

    p_web = sub.add_parser("web", help="serve a read-only web status page")
    p_web.add_argument("--host", default=None, help="bind address (default from config, 0.0.0.0)")
    p_web.add_argument("--port", type=int, default=None, help="port (default from config, 8080)")
    p_web.set_defaults(func=cmd_web)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
