#!/usr/bin/env bash
# One-shot setup on a fresh Raspberry Pi OS. Run from the project directory.
set -euo pipefail

echo ">> Installing system packages..."
sudo apt-get update
# python3-dev + build-essential: build the 'hidapi'/'evdev' wheels (in .[pi])
# libhidapi-hidraw0: runtime lib for USB-HID; fonts-dejavu-core: screen umlauts
sudo apt-get install -y python3-venv python3-pip python3-dev build-essential \
    libhidapi-hidraw0 libhidapi-dev fonts-dejavu-core stockfish

echo ">> Creating virtualenv and installing chessnood..."
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[pi]'   # includes hidapi (USB board), Pillow, evdev

echo ">> Installing config (edit config.yaml afterwards)..."
[ -f config.yaml ] || cp config.example.yaml config.yaml

echo ">> Installing udev rule for USB board access..."
sudo cp scripts/99-chessnut.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger

echo ">> Installing systemd service (rendered for user '$(whoami)' at $PWD)..."
# Fill in the deploying user and project dir, so it works regardless of username
# (the Pi user here is not 'pi'). __USER__/__DIR__ are placeholders in the unit.
sed -e "s|__USER__|$(whoami)|g" -e "s|__DIR__|$PWD|g" \
    systemd/chessnood.service | sudo tee /etc/systemd/system/chessnood.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable chessnood.service

echo
echo ">> NOTE: set up the 3.5\" screen overlay separately -- see docs/SETUP_PI.md"
echo "   (install the goodtft mhs35 overlay; set display.fb_device: /dev/fb0)."
echo
echo "Done. Useful commands:"
echo "  sudo systemctl start chessnood     # start now"
echo "  journalctl -fu chessnood           # live logs"
echo "  .venv/bin/chessnood status         # current state"
echo "  .venv/bin/chessnood scan           # find the board"
