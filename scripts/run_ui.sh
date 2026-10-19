#!/usr/bin/env bash
# run_ui.sh - starts the minibox web UI (Flask) on 127.0.0.1.
# Usage: sudo scripts/run_ui.sh [port]      (default port 8080)
# Must run as root because minibox itself needs root. The server prints the URL,
# including a random access token, once at startup. It listens on localhost only.
# Exit status: 1 on a setup problem, otherwise whatever the server returns.

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PY="$ROOT_DIR/ui/venv/bin/python"
PORT=${1:-8080}

die() { echo "error: $1" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "must run as root (minibox needs it): sudo scripts/run_ui.sh $PORT"
case "$PORT" in
    ''|*[!0-9]*) die "port must be a number" ;;
esac
[ -x "$PY" ] || die "ui/venv not found; run 'scripts/setup_ui.sh' first (as your normal user)"
[ -x "$ROOT_DIR/minibox" ] || die "minibox not built; run 'make' first"
[ -x "$ROOT_DIR/rootfs/bin/busybox" ] || die "Alpine rootfs missing; run 'make rootfs' first"

echo "Starting the minibox UI on 127.0.0.1:$PORT (Ctrl-C to stop)."
echo "Open the URL printed below in your browser. Keep it private: it holds the access token."
exec "$PY" "$ROOT_DIR/ui/app.py" --host 127.0.0.1 --port "$PORT"
