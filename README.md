# chessnood

A headless, senior-friendly chess computer for the **Chessnut Pro** e-board,
designed to run on a **Raspberry Pi 4** with no screen.

The board itself is the display (its LEDs show the computer's move) and a couple
of GPIO buttons are the controls. Everything else — strength, connection state,
debugging — happens over SSH. The Bluetooth connection reconnects silently in the
background, so the player never sees a "connect" button.

> **Status: scaffold.** The full game logic, engine, config, service and CLI work
> today and are tested in simulation. The Chessnut **BLE protocol is implemented
> but not yet verified on real hardware** — see [docs/HARDWARE.md](docs/HARDWARE.md).

## Requirements

- Python 3.11+
- A [Chessnut](https://www.chessnutech.com/) e-board with Bluetooth LE (developed against the **Pro**)
- For deployment: a Raspberry Pi (4 recommended; 1 GB RAM is enough for this headless setup), Raspberry Pi OS
- Optional but recommended: [Stockfish](https://stockfishchess.org/) as the opponent (`apt install stockfish`); without it the engine falls back to random legal moves
- Optional GPIO hardware: one LED (connection status) + 1–2 momentary buttons (new game / resign)

## Try it now (no hardware, no Stockfish needed)

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                 # 13 tests
.venv/bin/chessnood simulate     # plays a full game through the real logic
```

(Without Stockfish installed, the opponent falls back to random legal moves.)

## How it fits together

```
 [Chessnut Pro] --BLE--> boards/ble.py  ---reading--->  game.py (pure state machine)
 [GPIO buttons] -------> indicators.py                       |  detects the move played,
 [status LED]   <------- indicators.py                       |  asks the engine to reply,
                                                             v  lights from/to LEDs
                         runner.py  <---->  engine.py (Stockfish / random fallback)
                              |
                         SSH: config.yaml (live reload), `chessnood status`, journald
```

- `game.py` — pure, I/O-free state machine (fully unit-tested)
- `runner.py` — async glue: board ↔ game ↔ engine ↔ LEDs, auto-reconnect, live config reload
- `boards/` — `mock` (testing) and `ble` (real board); a `usb` backend can be added later
- `engine.py` — Stockfish over UCI, with a random-mover fallback
- `config.py` — YAML config, hot-reloaded between moves

## On the Raspberry Pi

See [docs/SETUP_PI.md](docs/SETUP_PI.md). Short version:

```
./scripts/install_pi.sh
cp config.example.yaml config.yaml   # edit strength, pins, colour
sudo systemctl start chessnood
journalctl -fu chessnood
```

## Next step

Run the [hardware verification checklist](docs/HARDWARE.md) once the board is
reachable (works from any Mac/Linux laptop with Bluetooth — no Pi required) to
confirm the protocol and LED control, then adjust the flagged constants if needed.
```
chessnood scan      # find the board
```

## Credits

The Chessnut BLE protocol implementation is based on the public documentation and
the reverse-engineering work of the community, in particular:

- [chessnutech/EasyLinkSDK](https://github.com/chessnutech/EasyLinkSDK) — the official SDK
- [ecrucru/chessnut-connector](https://github.com/ecrucru/chessnut-connector)
- [rmarabini/chessnutair](https://github.com/rmarabini/chessnutair)
- [staubsauger/ChessnutPy](https://github.com/staubsauger/ChessnutPy)

Chess rules via [python-chess](https://github.com/niklasf/python-chess); opponent via [Stockfish](https://stockfishchess.org/).

## License

[MIT](LICENSE) © 2026 Ralf Brauchler
