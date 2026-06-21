# chessnood

A senior-friendly chess computer for the **Chessnut Pro** e-board, designed to
run on a **Raspberry Pi 4** with a small **3.5" touchscreen**.

The board's own LEDs are the **primary move display** (the lit squares show the
computer's move — no notation to read). A small touchscreen shows calm,
plain-language status ("Du bist am Zug", "Computer denkt …") and a single big
**Neue Partie** button. Everything else — strength, debugging — happens over SSH.
The Bluetooth connection reconnects silently in the background, so the player
never sees a "connect" button.

> **Status: scaffold.** The full game logic, engine, config, service and CLI work
> today and are tested in simulation. The Chessnut **BLE protocol is implemented
> but not yet verified on real hardware** — see [docs/HARDWARE.md](docs/HARDWARE.md).

## Requirements

- Python 3.11+
- A [Chessnut](https://www.chessnutech.com/) e-board with Bluetooth LE (developed against the **Pro**)
- For deployment: a Raspberry Pi (4 recommended; 1 GB RAM is enough), Raspberry Pi OS
- A 3.5" SPI touchscreen (developed against the **MHS-3.5**) for status + a "Neue Partie" button
- Optional but recommended: [Stockfish](https://stockfishchess.org/) as the opponent (`apt install stockfish`); without it the engine falls back to random legal moves

## Try it now (no hardware, no Stockfish needed)

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                 # 19 tests
.venv/bin/chessnood simulate     # plays a full game through the real logic
.venv/bin/chessnood preview      # render the touchscreen to chessnood-preview.png
```

(Without Stockfish installed, the opponent falls back to random legal moves.)

## How it fits together

```
 [Chessnut Pro] --BLE--> boards/ble.py  ---reading--->  game.py (pure state machine)
 [touchscreen]  <------> display.py                          |  detects the move played,
 ("Neue Partie")                                             |  asks the engine to reply,
                                                             v  lights from/to LEDs on the board
                         runner.py  <---->  engine.py (Stockfish / random fallback)
                              |             (screen mirrors status + a visual board)
                         SSH: config.yaml (live reload), `chessnood status`, journald
```

- `game.py` — pure, I/O-free state machine (fully unit-tested)
- `runner.py` — async glue: board ↔ game ↔ engine ↔ LEDs ↔ screen, auto-reconnect, live config reload
- `boards/` — `mock` (testing) and `ble` (real board); a `usb` backend can be added later
- `display.py` — touchscreen UI: status + "Neue Partie"; pure-Pillow render, framebuffer/console/preview backends
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
