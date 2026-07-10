#!/usr/bin/env bash
# One-shot setup on a fresh Raspberry Pi OS. Run from the project directory.
set -euo pipefail

echo ">> Installing system packages..."
sudo apt-get update
# python3-dev + build-essential: build the 'hidapi' wheel (in .[pi])
# libhidapi-hidraw0: runtime lib for USB-HID; fonts-dejavu-core: screen umlauts
sudo apt-get install -y python3-venv python3-pip python3-dev build-essential \
    libhidapi-hidraw0 libhidapi-dev fonts-dejavu-core stockfish

echo ">> Creating virtualenv and installing chessnood..."
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[pi]'   # includes hidapi (USB board) + Pillow (screen)

echo ">> Installing config (edit config.yaml afterwards)..."
[ -f config.yaml ] || cp config.example.yaml config.yaml

echo ">> Installing udev rule for USB board access..."
sudo cp scripts/99-chessnut.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger

echo ">> Installing systemd services (rendered for user '$(whoami)' at $PWD)..."
# Fill in the deploying user and project dir, so it works regardless of username
# (the Pi user here is not 'pi'). __USER__/__DIR__ are placeholders in the units.
for unit in systemd/chessnood.service systemd/chessnood-web.service \
            systemd/chessnood-update.service systemd/chessnood-update.timer; do
    sed -e "s|__USER__|$(whoami)|g" -e "s|__DIR__|$PWD|g" \
        "$unit" | sudo tee "/etc/systemd/system/$(basename "$unit")" >/dev/null
done
chmod +x scripts/chessnood-update.sh
sudo systemctl daemon-reload
sudo systemctl enable chessnood.service
sudo systemctl enable chessnood-web.service     # read-only status page on :8080
# Self-update only makes sense from a git checkout (not an rsync copy). Enable the
# timer when this is one, so a Pi at a remote site keeps itself up to date.
if git -C "$PWD" rev-parse --git-dir >/dev/null 2>&1; then
    sudo systemctl enable chessnood-update.timer  # hourly-ish `git pull` + restart
else
    echo ">> NOTE: not a git checkout -- skipping the self-update timer."
    echo "   For a remote site, deploy with 'git clone' so it can update itself."
fi

echo
echo ">> NOTE: set up the 3.5\" screen overlay separately -- see docs/SETUP_PI.md"
echo "   (install the goodtft mhs35 overlay; set display.fb_device: /dev/fb0)."
echo
echo "Done. Useful commands:"
echo "  sudo systemctl start chessnood chessnood-web   # start now (game + web page)"
echo "  journalctl -fu chessnood           # live logs"
echo "  .venv/bin/chessnood status         # service + board + Pi health over SSH"
echo "  http://$(hostname).local:8080/     # read-only web view (screen + health)"
echo "  .venv/bin/chessnood scan           # find the board"
