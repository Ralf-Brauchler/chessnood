"""Command-line interface.

  chessnood run        run the service against the real board (or mock)
  chessnood demo       dry-run the whole stack on the real screen (self-playing)
  chessnood simulate   play a full game with no hardware (proves the logic)
  chessnood scan       list attached Chessnut USB boards (first hardware test)
  chessnood status     print what a running service is doing
  chessnood preview    render the touchscreen to a PNG to see how it looks
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys

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


def cmd_demo(args: argparse.Namespace) -> int:
    """Dry-run the whole stack: the real Runner + display, driven by a
    self-playing board. Shows the genuine flow on screen, no hardware needed."""
    from .boards.mock import SelfPlayBoard

    watcher = ConfigWatcher(args.config)
    setup_logging(watcher.current.log_level)
    board = SelfPlayBoard(human_color=watcher.current.game.human_color_bool,
                          move_pause=args.pause, mistake_chance=args.mistakes)
    runner = Runner(board, watcher)
    print(f"Demo: self-playing through the real UI (pause {args.pause}s). Ctrl-C to stop.")
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


def cmd_status(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config)
    try:
        data = StatusFile.read(cfg.status_file)
    except FileNotFoundError:
        print(f"No status file at {cfg.status_file}. Is the service running?", file=sys.stderr)
        return 1
    for key in ("connection", "state", "skill_level", "last_move", "updated"):
        print(f"  {key:11s}: {data.get(key)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chessnood", description=__doc__)
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="run the service").set_defaults(func=cmd_run)

    p_demo = sub.add_parser("demo", help="self-playing dry-run on the real screen")
    p_demo.add_argument("--pause", type=float, default=1.2, help="seconds between moves")
    p_demo.add_argument("--mistakes", type=float, default=0.3, metavar="P",
                        help="probability [0..1] a move is fumbled first to show "
                             "the recovery UI (0 = always play correctly)")
    p_demo.set_defaults(func=cmd_demo)

    p_sim = sub.add_parser("simulate", help="play a full game without hardware")
    p_sim.add_argument("--max-plies", type=int, default=200)
    p_sim.set_defaults(func=cmd_simulate)

    sub.add_parser("scan", help="list attached Chessnut USB boards").set_defaults(func=cmd_scan)

    p_prev = sub.add_parser("preview", help="render the touchscreen to a PNG")
    p_prev.add_argument("--out", default="./chessnood-preview.png")
    p_prev.set_defaults(func=cmd_preview)

    sub.add_parser("status", help="print running service status").set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
