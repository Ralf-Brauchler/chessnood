# Raspberry Pi setup

Target: Raspberry Pi 4 (1 GB is enough for this headless setup), Raspberry Pi OS
Lite (64-bit). No screen — board LEDs are the display, GPIO buttons + SSH are the
controls.

## 1. Flash the SD card

Use Raspberry Pi Imager. In the settings (gear icon) **before** writing:
- set a hostname (e.g. `chessnood`)
- **enable SSH** and set a username/password
- configure Wi-Fi

Boot the Pi and `ssh pi@chessnood.local`.

## 2. Install

```
git clone <your-repo> chessnood     # or copy the project over
cd chessnood
./scripts/install_pi.sh
cp config.example.yaml config.yaml  # then edit (see below)
sudo systemctl start chessnood
```

## 3. Wiring

**Status LED** (Bluetooth state): LED + ~330 Ω resistor between BCM 17 and GND.
- solid = connected, slow blink = scanning, fast blink = error.

**Buttons**: a momentary button between the pin and GND (internal pull-ups are used).
- New game → BCM 27
- Resign (optional) → BCM 22

Pins are configurable under `hardware:` in `config.yaml`. Set a pin to `null` to disable it.

## 4. Day-to-day (over SSH)

```
journalctl -fu chessnood        # live logs incl. connection state
chessnood status                # quick snapshot (connection, state, skill)
nano config.yaml                # change skill_level / move_time — applied next move, no restart
chessnood scan                  # list BLE devices if the board won't connect
sudo systemctl restart chessnood
```

## 5. Adjusting strength

In `config.yaml` under `engine:`:
- `skill_level: 0..20` — quickest knob; lower is weaker.
- or `elo_limit: 1200` — cap by approximate Elo (overrides skill_level).
- `move_time_ms` — lower = snappier, higher = stronger.

Changes are picked up automatically at the start of the computer's next move.
