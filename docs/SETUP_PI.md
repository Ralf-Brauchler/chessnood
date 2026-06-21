# Raspberry Pi setup

Target: Raspberry Pi 4 (1 GB is enough), Raspberry Pi OS (64-bit) + a 3.5" SPI
touchscreen (MHS-3.5). The **board LEDs are the primary move display**; the screen
shows plain-language status and a big "Neue Partie" touch button; SSH is for setup
and tuning.

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

## 3. The touchscreen (MHS-3.5)

The display mounts on the 40-pin header (SPI). On Raspberry Pi OS Bookworm enable
it with a **device-tree overlay** (not the old `LCD-show` scripts), e.g. add to
`/boot/firmware/config.txt`:

```
dtoverlay=mhs35:rotate=90      # VERIFY the exact overlay name / rotation for your panel
```

After a reboot the screen appears as `/dev/fb1` and the touch panel as an evdev
device. Then in `config.yaml` under `display:` leave `backend: auto` (it picks the
framebuffer automatically). Tune `rotate:` if the image is sideways, and—if needed—
set `touch_device:` and calibrate (the touch mapping in `display.py` is marked
`# VERIFY`).

Preview the look on any machine first, no Pi required:

```
pip install -e '.[display]'
chessnood preview           # writes chessnood-preview.png
```

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
