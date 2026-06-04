"""Command-line interface.

  chessnood run        run the service against the real board (or mock)
  chessnood simulate   play a full game with no hardware (proves the logic)
  chessnood scan       list nearby BLE devices (first hardware test)
  chessnood status     print what a running service is doing
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
    """List nearby BLE devices. Run this with the board on to find it."""
    try:
        from bleak import BleakScanner
    except ImportError:
        print("bleak not installed. Run:  pip install 'chessnood[ble]'", file=sys.stderr)
        return 1

    async def _scan() -> int:
        print(f"Scanning for {args.timeout}s ...\n")
        devices = await BleakScanner.discover(timeout=args.timeout)
        if not devices:
            print("No BLE devices found.")
            return 1
        for d in sorted(devices, key=lambda x: (x.name or "~")):
            mark = "  <-- likely Chessnut" if (d.name or "").lower().startswith("chessnut") else ""
            print(f"  {d.address}  {d.name or '(no name)'}{mark}")
        return 0

    return asyncio.run(_scan())


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

    p_sim = sub.add_parser("simulate", help="play a full game without hardware")
    p_sim.add_argument("--max-plies", type=int, default=200)
    p_sim.set_defaults(func=cmd_simulate)

    p_scan = sub.add_parser("scan", help="list nearby BLE devices")
    p_scan.add_argument("--timeout", type=float, default=10.0)
    p_scan.set_defaults(func=cmd_scan)

    sub.add_parser("status", help="print running service status").set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
