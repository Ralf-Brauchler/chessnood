#!/usr/bin/env bash
# One-shot setup on a fresh Raspberry Pi OS. Run from the project directory.
set -euo pipefail

echo ">> Installing system packages..."
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip stockfish bluez

echo ">> Creating virtualenv and installing chessnood..."
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[ble,pi]'

echo ">> Installing config (edit config.yaml afterwards)..."
[ -f config.yaml ] || cp config.example.yaml config.yaml

echo ">> Installing systemd service..."
sudo cp systemd/chessnood.service /etc/systemd/system/chessnood.service
sudo systemctl daemon-reload
sudo systemctl enable chessnood.service

echo
echo "Done. Useful commands:"
echo "  sudo systemctl start chessnood     # start now"
echo "  journalctl -fu chessnood           # live logs"
echo "  .venv/bin/chessnood status         # current state"
echo "  .venv/bin/chessnood scan           # find the board"
