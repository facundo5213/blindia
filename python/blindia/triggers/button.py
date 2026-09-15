"""Physical button trigger, wired via the Router Bridge.

The MCU sketch (`sketch/sketch.ino`) debounces the raw digitalRead() on D2
and calls `Bridge.notify("boton_presionado")` once per valid press -- never
once per contact bounce. This trigger just waits on a threading.Event set by
that handler, the same block-until-event contract every other Trigger
implementation follows.
"""
from __future__ import annotations

import threading

from arduino.app_utils import Bridge, Logger

from .base import Trigger

logger = Logger(__name__)


class ButtonTrigger(Trigger):
    """Fires a capture each time the MCU reports a debounced button press."""

    def __init__(self) -> None:
        self._event = threading.Event()
        Bridge.provide("boton_presionado", self._on_press)

    def _on_press(self) -> None:
        logger.debug("Button press notified by MCU")
        self._event.set()

    def wait(self) -> None:
        """Block until the MCU notifies a press, then return."""
        self._event.wait()
        self._event.clear()
