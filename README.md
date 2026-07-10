# chessnood

A senior-friendly chess computer for the **Chessnut Pro** e-board, designed to
run on a **Raspberry Pi 4** with a small **3.5" status screen**.

The board's own LEDs are the **primary move display** (the lit squares show the
computer's move — no notation to read). A small screen shows calm, plain-language
status ("Du bist am Zug", "Computer denkt …"). To start a fresh game the player
simply puts all the pieces back in the starting position — no buttons to find.
Everything else — strength, debugging — happens over SSH. The board connects to
the Pi by **USB cable** — no pairing, ever, and it stays powered over the same
cable, so the player never sees a "connect" button.

> **Status: scaffold.** The full game logic, engine, config, service and CLI work
> today and are tested in simulation. The board talks to the Pi over **USB-HID**
> (a port of the official EasyLinkSDK, which uses USB for both reading the
> position and lighting the LEDs); this is **not yet verified on a physical
> Chessnut Pro** — see [docs/HARDWARE.md](docs/HARDWARE.md).

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

Strength and colour can be set **from the board**: in the start position, lift a
king onto an empty square — the file (a–h) picks the strength (level 1–8) and the
king's colour picks the side you play; put the king back to start. They can also be
changed in `config.yaml` over SSH (live, no restart). Other settings live in
`config.yaml`.

A one-page, printable player's guide (German) is at
[docs/anleitung.html](docs/anleitung.html).

## Requirements

- Python 3.11+
- A [Chessnut](https://www.chessnutech.com/) e-board connected by **USB** (developed against the **Pro**), plus a USB-A-to-USB-C cable to the Pi
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
 [Chessnut Pro] --USB--> boards/usb.py  ---reading--->  game.py (pure state machine)
 [3.5" screen]  <------- display.py                          |  detects the move played,
                                                             |  asks the engine to reply,
                                                             v  lights from/to LEDs on the board
                         runner.py  <---->  engine.py (Stockfish / random fallback)
                              |             (screen mirrors status + a visual board)
                         SSH: config.yaml (live reload), `chessnood status`, journald
```

- `game.py` — pure, I/O-free state machine (fully unit-tested); a new game starts when the pieces are reset to the start position
- `runner.py` — async glue: board ↔ game ↔ engine ↔ LEDs ↔ screen, auto-reconnect, live config reload
- `boards/` — `mock` (testing) and `usb` (real board, USB-HID); the git history holds a removed `ble` backend if a wireless path is ever wanted
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

## Checking on it from afar

The player never touches the Pi, so the maintainer watches it remotely — over
SSH or a browser. Both show the same three things: the **program** (what state
the game is in), the **board** (the position the screen is showing), and the
**Pi** (temperature, power/undervoltage, disk, uptime).

```
chessnood status        # one-screen summary over SSH: program + board + Pi health
```

`install_pi.sh` also installs a **read-only web page** (`chessnood-web.service`,
port 8080) that shows the very same screen image the player sees, plus the Pi's
health, auto-refreshing:

```
http://<pi>:8080/       # e.g. http://chessnoot.local:8080/
```

It is a separate, read-only process — it only *reads* the status file the game
writes, so it can never disturb a game in progress (if the game hangs, the page
just shows a stale timestamp). There is **no login and no way to control the
game from it**, so keep it on a private network. The easy way to reach it (and
SSH) from anywhere without opening any router port is [Tailscale](https://tailscale.com/):
install it on the Pi and your laptop/phone, and `chessnoot` is reachable from
everywhere as if on the same LAN.

## Next step

Run the [hardware verification checklist](docs/HARDWARE.md) once the board is
plugged in (works from any Mac/Linux laptop — no Pi required) to confirm reading
the position and LED control, then adjust any flagged constants if needed.
```
chessnood scan      # list attached Chessnut USB boards
```

## Credits

The Chessnut USB-HID implementation in `boards/usb.py` is a port of the official
SDK; the protocol was also cross-checked against community libraries:

- [chessnutech/EasyLinkSDK](https://github.com/chessnutech/EasyLinkSDK) — the official SDK; our USB device IDs, commands, board decode and LED layout are ported from it
- [rmarabini/chessnutair](https://github.com/rmarabini/chessnutair) — confirmed the init/LED commands and piece-code map
- [paulvonallwoerden/chessnut-air](https://github.com/paulvonallwoerden/chessnut-air) — confirmed the LED bit/byte layout
- [ecrucru/chessnut-connector](https://github.com/ecrucru/chessnut-connector), [staubsauger/ChessnutPy](https://github.com/staubsauger/ChessnutPy)

Chess rules via [python-chess](https://github.com/niklasf/python-chess); opponent via [Stockfish](https://stockfishchess.org/).

## License

[MIT](LICENSE) © 2026 Ralf Brauchler
