# chessnood

A senior-friendly chess computer for the **Chessnut Pro** e-board, designed to
run on a **Raspberry Pi 4** with a small **3.5" status screen**.

The board's own LEDs are the **primary move display** (the lit squares show the
computer's move — no notation to read). A small screen shows calm, plain-language
status ("Du bist am Zug", "Computer denkt …"). To start a fresh game the player
simply puts all the pieces back in the starting position — no buttons to find.
Everything else — strength, debugging — happens over SSH. The Bluetooth connection
reconnects silently in the background, so the player never sees a "connect" button.

> **Status: scaffold.** The full game logic, engine, config, service and CLI work
> today and are tested in simulation. The Chessnut **BLE protocol is implemented
> but not yet verified on real hardware** — see [docs/HARDWARE.md](docs/HARDWARE.md).

## How the player uses it (the whole interaction)

The player never touches the Pi — no buttons, no menus, no SSH. The complete
interaction is at the board:

1. **Switch on.** The screen shows *"Stelle die Figuren auf"* until the pieces
   are in the standard starting position.
2. **Play.** When it's the computer's turn, the two squares of its move light up
   (board LEDs, mirrored on the screen) — move that piece. Make your own moves
   normally; the board senses them.
3. **New game.** Just put every piece back in the starting position — a fresh
   game begins automatically. (So "reset the board" *is* "new game"; there is no
   separate off/quit.)

Strength, colour and other settings are fixed in `config.yaml` and only changed
by the maintainer over SSH (live, no restart). Whether the player is White or
Black is set there too — as Black, the computer moves first right after setup.

## Requirements

- Python 3.11+
- A [Chessnut](https://www.chessnutech.com/) e-board with Bluetooth LE (developed against the **Pro**)
- For deployment: a Raspberry Pi (4 recommended; 1 GB RAM is enough), Raspberry Pi OS
- A 3.5" SPI screen (developed against the **MHS-3.5**, ILI9486) for plain-language status
- Optional but recommended: [Stockfish](https://stockfishchess.org/) as the opponent (`apt install stockfish`); without it the engine falls back to random legal moves

## Try it now (no hardware, no Stockfish needed)

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                 # 22 tests
.venv/bin/chessnood simulate     # plays a full game through the real logic
.venv/bin/chessnood preview      # render the status screen to chessnood-preview.png
```

(Without Stockfish installed, the opponent falls back to random legal moves.)

## How it fits together

```
 [Chessnut Pro] --BLE--> boards/ble.py  ---reading--->  game.py (pure state machine)
 [3.5" screen]  <------- display.py                          |  detects the move played,
                                                             |  asks the engine to reply,
                                                             v  lights from/to LEDs on the board
                         runner.py  <---->  engine.py (Stockfish / random fallback)
                              |             (screen mirrors status + a visual board)
                         SSH: config.yaml (live reload), `chessnood status`, journald
```

- `game.py` — pure, I/O-free state machine (fully unit-tested); a new game starts when the pieces are reset to the start position
- `runner.py` — async glue: board ↔ game ↔ engine ↔ LEDs ↔ screen, auto-reconnect, live config reload
- `boards/` — `mock` (testing) and `ble` (real board); a `usb` backend can be added later
- `display.py` — status screen: plain-language state + a visual board; pure-Pillow render, framebuffer/console/preview backends
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
- [rmarabini/chessnutair](https://github.com/rmarabini/chessnutair) — confirmed our UUIDs, init/LED commands and piece-code map
- [paulvonallwoerden/chessnut-air](https://github.com/paulvonallwoerden/chessnut-air) — confirmed the LED bit/byte layout
- [staubsauger/ChessnutPy](https://github.com/staubsauger/ChessnutPy)

Chess rules via [python-chess](https://github.com/niklasf/python-chess); opponent via [Stockfish](https://stockfishchess.org/).

## License

[MIT](LICENSE) © 2026 Ralf Brauchler
