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

## 3. The screen (MHS-3.5, high-speed / ILI9486)

This is the recipe verified on a high-speed ("SPI 125 MHz") MHS-3.5 with
Raspberry Pi OS (Debian Trixie, kernel 6.18). The generic kernel `ili9486` overlay
shows a **white screen** on these panels — they need the vendor `mhs35` overlay
(custom init sequence, `regwidth=16`). Install it:

```
git clone --depth 1 https://github.com/goodtft/LCD-show.git ~/LCD-show
sudo cp ~/LCD-show/usr/mhs35-overlay.dtb /boot/firmware/overlays/mhs35.dtbo
```

Append to `/boot/firmware/config.txt`:

```
dtparam=spi=on
dtoverlay=mhs35:rotate=90
```

Reboot. The screen comes up as **`/dev/fb0`** (480x320, 16bpp). Pi OS Lite ships no
TrueType font, so install one (else umlauts render as boxes):

```
sudo apt install -y fonts-dejavu-core
```

Then in `config.yaml` set `display: { backend: framebuffer, fb_device: /dev/fb0 }`.
Preview the look on any machine first (no Pi needed): `pip install -e '.[display]'`
then `chessnood preview` (writes `chessnood-preview.png`).

**Touch:** the resistive touch panel does **not** work on a mainline kernel (the
ADS7846 PENIRQ never fires — goodtft's patched kernel would be required). We don't
use it: a **new game is started by resetting the pieces to the start position**, so
no touch or button is needed.

**Boot console on the screen (optional):** until the service draws, the Linux text
console is visible on `/dev/fb0`. To hide it from the player, add `fbcon=map:2` to
`/boot/firmware/cmdline.txt` and enable the chessnood service to autostart.

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
