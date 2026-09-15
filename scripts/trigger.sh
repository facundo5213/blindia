#!/usr/bin/env bash
# Fires one capture on the running BlindIA app -- temporary stand-in for the
# physical button. Run from anywhere; requires the app to be started.
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIFO="$APP_ROOT/data/trigger"

if [ ! -p "$FIFO" ]; then
    echo "Trigger FIFO not found at $FIFO -- is the app running?" >&2
    exit 1
fi

echo > "$FIFO"
echo "Trigger sent."
