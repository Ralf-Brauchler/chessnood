# Raspberry Pi setup

Target: Raspberry Pi 4 (1 GB is enough), Raspberry Pi OS (64-bit) + a 3.5" SPI
screen (MHS-3.5). The board connects to the Pi by **USB cable**. The **board LEDs
are the primary move display**; the screen shows plain-language status. A new game
starts by resetting the pieces to the start position (no button/touch); SSH is for
setup and tuning.

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

**Boot console on the screen:** the service redraws the screen on a short heartbeat,
so the chessnood UI comes up on its own within a couple of seconds of the service
starting and reasserts itself over the Linux login console — no cmdline change is
required. To also suppress the brief flash of boot/login text before the service is
up, add `fbcon=map:2` to `/boot/firmware/cmdline.txt` (optional).

## 4. The board (USB)

Connect the Chessnut Pro to a Pi USB-A port with a USB-A-to-USB-C cable. The board
is a USB-HID peripheral (the Pi is the host); it stays powered over the same cable,
so no charging step and no pairing. Install the udev rule for non-root access:

```
sudo cp scripts/99-chessnut.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

Then `chessnood scan` should list the board (e.g. `Chessnut Pro (pid 0x8100)`).
If the Pi reports USB undervoltage (`vcgencmd get_throttled` != 0x0), use a good
3A Pi PSU or a powered USB hub.

## 5. Day-to-day (over SSH)

```
journalctl -fu chessnood        # live logs incl. connection state
chessnood status                # quick snapshot (connection, state, skill)
nano config.yaml                # change skill_level / move_time — applied next move, no restart
chessnood scan                  # list attached Chessnut USB boards
sudo systemctl restart chessnood
systemctl list-timers chessnood-update   # when the next auto-update check runs
```

## 6. Adjusting strength

In `config.yaml` under `engine:`:
- `skill_level: 0..20` — quickest knob; lower is weaker.
- or `elo_limit: 1200` — cap by approximate Elo (overrides skill_level).
- `move_time_ms` — lower = snappier, higher = stronger.

Changes are picked up automatically at the start of the computer's next move.

**From the board (no SSH):** with the pieces in the start position, lift your king
and set it on any empty square — the **file** picks the strength (a = level 1 … h =
level 8) and the screen shows the chosen level. Put the king back on its home square
to start playing at that strength. This writes `skill_level` into `config.yaml`, so
it sticks across restarts. (Note: an active `elo_limit` would override it — leave it
unset to use this.)

## 7. A Pi at a remote site (e.g. a relative's house)

Once the box lives on someone else's network you can't reach it by `.local` (mDNS
is LAN-only) and their router NATs it away. Two complementary mechanisms cover this;
set up **both**.

**Deploy from a git checkout, not rsync.** So the Pi can update itself, install it
with `git clone` rather than copying files:

```
git clone https://github.com/<you>/chessnood.git ~/chessnood
cd ~/chessnood && ./scripts/install_pi.sh
```

### Hands-off updates (self-update timer)

`install_pi.sh` installs `chessnood-update.timer`, which every ~30 min runs a
`git pull` of the branch the Pi has checked out and restarts the services **only if
it moved**. It talks *out* to GitHub, so it needs no VPN, no port forwarding, and
nothing exposed. Which branch a Pi follows is just the branch it's on:

- **Test Pi at home:** stay on `master`.
- **Pi at the remote site:** put it on a `release` branch (`git checkout release`).
- Promote a tested change with `git push origin master:release`; the remote Pi
  picks it up on its next tick. Never push straight to `release` untested — a broken
  commit lands unattended where nobody can fix it. `systemctl restart` keeps the
  service alive, but a commit that crashes on start would loop.

Check it: `systemctl list-timers chessnood-update` and `journalctl -u chessnood-update`.

### Remote access (Tailscale)

For SSH / the web view / `journalctl` from anywhere, put the Pi on a [Tailscale](https://tailscale.com)
tailnet (free; punches through NAT, gives a stable name). On the Pi **and** your
machine:

```
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # opens a login URL -- authenticate to your account
```

Then reach it by its tailnet name from anywhere: `ssh <you>@chessnood-vater`,
`http://chessnood-vater:8080/`. Keep the web view **inside** the tailnet (do not
port-forward it: it has no authentication).
