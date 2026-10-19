#!/usr/bin/env bash
# setup_ui.sh - creates the Python virtual environment for the web UI and installs Flask.
# Usage: scripts/setup_ui.sh        (no sudo needed; needs internet access for pip)
# Creates ui/venv (git-ignored) and installs the pinned packages from ui/requirements.txt.
# Exit status: 0 on success, 1 on any error.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VENV="$ROOT_DIR/ui/venv"
REQ="$ROOT_DIR/ui/requirements.txt"

die() { echo "error: $1" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ]; then
    die "run this as your normal user, not root (the venv would be owned by root)"
fi
command -v python3 >/dev/null 2>&1 || die "python3 not found (sudo apt install python3)"
[ -f "$REQ" ] || die "$REQ not found"

if [ ! -x "$VENV/bin/python" ]; then
    echo "Creating virtual environment in ui/venv ..."
    python3 -m venv "$VENV" || die "could not create the venv (try: sudo apt install python3-venv)"
fi

echo "Installing pinned packages from ui/requirements.txt ..."
"$VENV/bin/python" -m pip install --disable-pip-version-check -r "$REQ" || die "pip install failed"

"$VENV/bin/python" -c "import flask" || die "Flask did not install correctly"
echo "UI environment ready. Start the UI with:  sudo scripts/run_ui.sh [port]"
