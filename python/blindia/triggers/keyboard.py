"""Temporary manual trigger, used until the physical button is wired.

The app runs inside a container with no attached TTY, so reading
`sys.stdin` directly inside the app process never sees a keypress typed on
the host. Instead this trigger blocks on a FIFO (named pipe) under the
app's data directory; opening a FIFO for reading blocks until *something*
writes to it, which is the same "block until an event happens" contract a
GPIO button read will have later.

From a host/adb/SSH shell, fire one capture with:

    echo > ~/ArduinoApps/blindia/data/trigger

or run `scripts/trigger.sh` from the app root.
"""
from __future__ import annotations

import os
from pathlib import Path

from arduino.app_utils import Logger

from .base import Trigger

logger = Logger(__name__)


class KeyboardTrigger(Trigger):
    """Fires a capture each time something is written to a FIFO file."""

    def __init__(self, fifo_path: Path) -> None:
        self._fifo_path = fifo_path
        self._ensure_fifo()

    def _ensure_fifo(self) -> None:
        if self._fifo_path.exists():
            return
        self._fifo_path.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(self._fifo_path)
        logger.info(f"Created trigger FIFO at {self._fifo_path}")

    def wait(self) -> None:
        """Block until a writer opens and writes to the FIFO, then return."""
        # Opening a FIFO for reading blocks until a writer connects; the
        # writer closing (e.g. `echo` returning) then delivers EOF, so this
        # naturally unblocks once per external trigger.
        with self._fifo_path.open("r") as fifo:
            fifo.readline()
        logger.debug("Trigger FIFO fired")
