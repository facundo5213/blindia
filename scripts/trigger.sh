#!/usr/bin/env bash
# Dispara una captura en la app BlindIA en ejecución -- respaldo temporal
# del pulsador físico. Correr desde cualquier lado; requiere que la app esté arrancada.
set -euo pipefail

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIFO="$APP_ROOT/data/trigger"

if [ ! -p "$FIFO" ]; then
    echo "Trigger FIFO not found at $FIFO -- is the app running?" >&2
    exit 1
fi

echo > "$FIFO"
echo "Trigger sent."
