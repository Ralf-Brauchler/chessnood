#!/usr/bin/env bash
# Self-update: pull the tracked branch from origin and restart the services if it
# moved. Outbound-only (talks to GitHub, nothing listens) -> works behind any home
# router with no VPN and no port forwarding. Run by chessnood-update.service (as
# root) on a timer; all output goes to the journal (journalctl -u chessnood-update).
#
# Which branch a Pi follows is simply the branch it has checked out: keep the test
# Pi on `master` and the one at the remote site on `release`, and promote to
# `release` only after you have tried a build on the test Pi.
set -euo pipefail

DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
USER_NAME="${2:-$(stat -c %U "$DIR")}"

# Run git and pip as the repo owner (not root) so file ownership stays correct and
# git never trips over "dubious ownership".
as_user() { runuser -u "$USER_NAME" -- "$@"; }
git_r() { as_user git -C "$DIR" "$@"; }

BRANCH="$(git_r rev-parse --abbrev-ref HEAD)"
git_r fetch --quiet origin "$BRANCH"
LOCAL="$(git_r rev-parse HEAD)"
REMOTE="$(git_r rev-parse "origin/$BRANCH")"

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "Up to date on $BRANCH (${LOCAL:0:8})"
    exit 0
fi

echo "Updating $BRANCH: ${LOCAL:0:8} -> ${REMOTE:0:8}"
# The editable install picks up src/ changes for free; only reinstall when the
# dependency set actually changed (rare) to keep updates fast and offline-friendly.
DEPS_CHANGED="$(git_r diff --name-only "$LOCAL" "$REMOTE" -- pyproject.toml)"
git_r reset --hard "$REMOTE"
if [ -n "$DEPS_CHANGED" ]; then
    echo "pyproject.toml changed; reinstalling dependencies"
    as_user bash -c "cd '$DIR' && .venv/bin/pip install -e '.[pi]'"
fi
systemctl restart chessnood chessnood-web
echo "Updated to ${REMOTE:0:8} and restarted"
