# chessnood

A senior-friendly chess computer for the **Chessnut Pro** e-board, running headless
on a **Raspberry Pi 4** with a small **3.5" status screen**.

It was built for one specific player — the author's father — who wants to sit down
at a real board and play, with **no notation to read, no menus, no buttons, and no
computer to operate**. The board's own LEDs are the **primary move display**: when
the computer moves, the two squares of that move light up, and you slide the piece.
A calm 3.5" screen shows the state in plain German ("Du bist am Zug", "Computer
denkt …") and never shows coordinates. The board connects to the Pi by a plain
**USB cable** — no Bluetooth, no pairing, and it stays powered over the same cable,
so there is never a "connect" step.

> **Status: in service.** The full stack — game logic, engine, screen, config,
> systemd services and CLI — is implemented, unit-tested (156 tests), and **verified
> end-to-end on a physical Chessnut Pro over USB**. The board is decoded and its LEDs
> driven via USB-HID (a port of the official EasyLinkSDK). The appliance is deployed
> on a Raspberry Pi and ready to ship.

---

## For the player: the whole interaction

The player never touches the Pi — no keyboard, no menus, no SSH. Everything happens
at the board. (A one-page, printable German guide is at
**[docs/anleitung.html](docs/anleitung.html)**.)

1. **Switch on.** Within a second or two of power-up the screen shows the board and
   the current state on its own — you do **not** have to make a move first for the
   interface to appear. A green dot means the board is connected; the bottom line
   shows the current strength ("Computer: Stufe 3").

2. **Your move.** Just play it on the board — lift the piece, put it on its square.
   The board senses it; the screen says "Du bist am Zug" while it's your turn.

3. **The computer's move.** It thinks briefly ("Computer denkt …"), then **two
   squares light up** on the board — lift the piece from the lit square and set it on
   the other. If the destination is occupied, that piece is captured (take it off);
   captures first flash a short **cross of LEDs** through the target so the take is
   obvious. A soft beep signals it's your turn to play the shown move.

4. **If a piece ends up wrong.** The board is **self-healing**: if something doesn't
   match a legal position, the screen says "Das passt nicht", a low beep sounds, and
   the board lights the one square that needs fixing — guiding you back **one whole
   piece at a time** rather than just flagging an error. If a wrong position is left
   untouched for a while, the appliance can adopt it and let play continue.

5. **New game.** Put every piece back in the **starting position** — a fresh game
   begins automatically. "Reset the board" *is* "new game"; there is no off/quit.

6. **Choose strength and side — from the board.** In the starting position, lift a
   **king** onto any empty square:
   - the **file** (column a–h, left→right) sets the strength — **level 1** (easiest)
     to **level 8**;
   - the **king's colour** sets the side you play — the **white** king means you play
     White (computer answers as Black); the **black** king means you play Black (the
     **computer opens with a white move**).

   The screen shows e.g. "Stufe 3, du spielst Weiß". Put the king back on its home
   square and the game starts at that setting. The chosen strength is written into
   `config.yaml`, so it survives a restart.

7. **Switch off.** Just pull the power — nothing to shut down, and you can't break
   anything. A game in progress is saved atomically and resumes on the next power-up.

### What the screen and beeps mean

| Screen says | Meaning |
|---|---|
| `Du bist am Zug` | Your turn — make your move on the board. |
| `Computer denkt …` | Wait a moment. |
| `Der Computer hat gezogen` | Execute the lit move on the board. |
| `Spielstärke wählen` | A king is on an empty square — picking strength/side. |
| `Das passt nicht` | A piece is misplaced — follow the lit square. |
| `Suche das Brett …` | Board/power not seen yet — check the cable, wait. |

| Beep | Meaning |
|---|---|
| High tone | The computer has moved — play the lit move. |
| Low tone | Something's wrong — look at the lit square. |
| Long tone | The game is over. |

---

## Requirements

- **Python 3.11+**
- A [Chessnut](https://www.chessnutech.com/) e-board over **USB** (developed and
  verified against the **Pro**) plus a USB-A-to-USB-C cable to the Pi
- For deployment: a **Raspberry Pi** (4 recommended; 1 GB RAM is enough), Raspberry
  Pi OS (64-bit, tested on Debian Trixie)
- A **3.5" SPI screen** (developed against the **MHS-3.5**, ILI9486) for the status
  panel
- Recommended: **[Stockfish](https://stockfishchess.org/)** as the opponent
  (`apt install stockfish`); without it the engine falls back to random legal moves

## Try it now (no hardware, no Stockfish)

```
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                 # 156 tests, all pure-Python (no board, no engine)
.venv/bin/chessnood simulate     # play a full game through the real game logic
.venv/bin/chessnood preview      # render the status screen to chessnood-preview.png
```

The engine, screen and board all have hardware-free fallbacks, so the whole flow is
exercisable on any Mac/Linux machine.

## How it fits together

```
 [Chessnut Pro] --USB--> boards/usb.py  --reading-->  game.py  (pure state machine)
 [3.5" screen]  <------- display.py                      |  recovers the move played,
                                                         |  asks the engine to reply,
                                                         v  lights from/to LEDs + screen
                          runner.py  <---->  engine.py  (Stockfish / random fallback)
                               |
                     config.yaml (hot reload) · status file · game save · web view
```

- **`game.py`** — a pure, I/O-free state machine (no asyncio, no USB), fully
  unit-tested. Move detection works from the *piece identities* the board reports:
  the played move is the single legal move whose resulting position matches the
  board. Also home to the strength/side **gesture detection** and the plain-language
  **guidance** (what to say, which squares to light, how to walk a correction back).
- **`runner.py`** — the async glue tying board ↔ game ↔ engine ↔ LEDs ↔ screen:
  debounces board readings, runs the (blocking) engine off the event loop with a hard
  timeout, drives LEDs and screen together, persists state, and hot-reloads config.
- **`boards/`** — `mock` (for tests) and `usb` (the real board over USB-HID). The git
  history keeps a removed `ble` backend if a wireless path is ever wanted.
- **`display.py`** — the status screen: plain-language state plus a visual highlighted
  board; pure-Pillow rendering with `framebuffer` / `console` / `preview` backends. A
  periodic repaint keeps the UI asserted from boot over the Linux login console.
- **`engine.py`** — Stockfish over UCI (skill level or Elo cap), with a random-mover
  fallback and automatic re-open if Stockfish ever dies.
- **`config.py`** — YAML config, hot-reloaded between moves; also the targeted,
  comment-preserving writer for the board-set strength.
- **`web.py`** — a separate, read-only web status page (see below).

## On the Raspberry Pi

Full walkthrough (screen overlay, udev rule, service install): see
**[docs/SETUP_PI.md](docs/SETUP_PI.md)**. Short version, from a `git clone` on the Pi:

```
./scripts/install_pi.sh                 # venv, deps, udev rule, systemd services
cp config.example.yaml config.yaml      # set strength, colour, screen device
sudo systemctl start chessnood chessnood-web
journalctl -fu chessnood                # live logs
```

`install_pi.sh` installs and enables two services — `chessnood` (the game) and
`chessnood-web` (the read-only status page) — so both start on boot.

### Networking (so it comes online anywhere)

The Pi uses **NetworkManager** and can hold **several known Wi-Fi networks** at once,
joining whichever is in range — so it can be configured at home and still come up on
its own at its destination:

```
sudo nmcli connection add type wifi con-name home ifname wlan0 \
     ssid "SSID" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "PASSWORD" \
     connection.autoconnect yes
```

Ethernet (`eth0`) is DHCP + autoconnect, so a **cable into the router is a zero-config
fallback** if Wi-Fi ever fails.

### Remote access and updates (Tailscale)

Because the player never touches the Pi — and, in the shipping case, it can't be
reached by `.local` (mDNS is LAN-only) behind someone else's router — put the Pi
**and your laptop** on a [Tailscale](https://tailscale.com/) tailnet. It punches
through NAT, needs no open ports, reconnects on every boot, and gives a stable name,
so `ssh ralf@chessnoot` and `http://chessnoot:8080/` work from anywhere.

Updates are done **manually and observed over Tailscale** — deliberately, so a bad
change can never brick a single, un-fixable-in-person device unattended:

```
ssh ralf@chessnoot
cd ~/chessnood && git pull && sudo systemctl restart chessnood chessnood-web
journalctl -fu chessnood        # watch it come back up before you walk away
```

Roll back instantly if an update misbehaves:

```
git -C ~/chessnood reset --hard HEAD@{1} && sudo systemctl restart chessnood chessnood-web
```

`sudo systemctl start chessnood-update.service` does the same `git pull` + restart in
one shot. A `chessnood-update.timer` for fully hands-off updates is installed but
**left disabled by design**; enable it only if you accept unattended updates without a
hardware test (`sudo systemctl enable --now chessnood-update.timer`).

## Watching it from afar

The maintainer watches the appliance remotely — over SSH or a browser — without ever
disturbing the game. Both surfaces show the same three things: the **program** (what
state the game is in), the **board** (the position on the screen), and the **Pi**
(temperature, power/undervoltage, disk, memory, uptime).

```
chessnood status        # one-screen summary over SSH: program + board + Pi health
```

`chessnood-web.service` serves a **read-only web page** (port 8080) rendering the
exact screen image the player sees, plus the Pi's health, auto-refreshing:

```
http://<pi>:8080/       # e.g. http://chessnoot:8080/  (keep it inside the tailnet)
```

It is a separate process that only *reads* the status file the game writes, so it can
never disturb a game in progress — if the game hangs, the page just shows a stale
timestamp. There is **no login and no way to control the game from it**, so it must
stay on a private network (the tailnet); never port-forward it.

## Built to survive a living room

The appliance is designed to run untended, be switched off by yanking the power, and
recover on its own:

- **Power loss mid-game** — the position and whose-turn are saved atomically after
  every move and restored on the next boot; a half-written file falls back cleanly.
- **A hung loop** — `systemd`'s watchdog (`Type=notify` + `WatchdogSec`) restarts the
  service if the event loop stops pinging.
- **A wedged engine** — a hard wall-clock timeout abandons Stockfish and plays a
  fallback move, so a turn never freezes on "Computer denkt".
- **The board sleeping** — the realtime stream is silently re-armed and a periodic
  keep-alive plus battery poll keep the link awake; connection loss auto-reconnects.
- **A wrong position** — guidance walks the fix one piece at a time, and an escape
  hatch adopts an uncorrected-but-legal position after a timeout so play can continue.
- **Config typos** — an unknown or malformed `config.yaml` never crashes the service;
  it keeps the last good settings and warns.

## CLI reference

```
chessnood run          # run the appliance (what the systemd service starts)
chessnood simulate     # play a full game through the game logic, no hardware
chessnood preview      # render the status screen to a PNG
chessnood status       # program + board + Pi health snapshot
chessnood web          # serve the read-only status page
chessnood scan         # list attached Chessnut USB boards
# low-level board tools:
chessnood dump         # hex-dump raw HID reports
chessnood watch        # live ASCII board from decoded reports
chessnood led a1 h8    # light squares until Enter
chessnood beep         # sound one tone
```

## Repository layout

```
src/chessnood/   game.py · runner.py · engine.py · display.py · config.py · web.py
                 boards/ (usb, mock) · status.py · health.py · watchdog.py · cli.py
scripts/         install_pi.sh · chessnood-update.sh · 99-chessnut.rules
systemd/         chessnood.service · chessnood-web.service · chessnood-update.{service,timer}
docs/            SETUP_PI.md · HARDWARE.md · IDEAS.md · anleitung.html (player's guide)
tests/           156 tests over the pure logic, guidance, config, display and runner
```

## Credits

The Chessnut USB-HID implementation in `boards/usb.py` is a port of the official SDK;
the protocol was cross-checked against community libraries:

- [chessnutech/EasyLinkSDK](https://github.com/chessnutech/EasyLinkSDK) — the official SDK; our USB device IDs, commands, board decode and LED layout are ported from it
- [rmarabini/chessnutair](https://github.com/rmarabini/chessnutair) — confirmed the init/LED commands and piece-code map
- [paulvonallwoerden/chessnut-air](https://github.com/paulvonallwoerden/chessnut-air) — confirmed the LED bit/byte layout
- [ecrucru/chessnut-connector](https://github.com/ecrucru/chessnut-connector), [staubsauger/ChessnutPy](https://github.com/staubsauger/ChessnutPy)

Chess rules via [python-chess](https://github.com/niklasf/python-chess); opponent via [Stockfish](https://stockfishchess.org/).

## License

[MIT](LICENSE) © 2026 Ralf Brauchler
