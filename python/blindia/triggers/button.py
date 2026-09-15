"""Trigger de pulsador físico, conectado vía el Router Bridge.

El sketch del MCU (`sketch/sketch.ino`) hace debounce del digitalRead()
crudo en D2 y llama a `Bridge.notify("boton_presionado")` una vez por
pulsación válida -- nunca una vez por rebote de contacto. Este trigger solo
espera sobre un threading.Event seteado por ese handler, el mismo contrato
de bloquear-hasta-el-evento que sigue cualquier otra implementación de
Trigger.
"""
from __future__ import annotations

import threading

from arduino.app_utils import Bridge, Logger

from .base import Trigger

logger = Logger(__name__)


class ButtonTrigger(Trigger):
    """Dispara una captura cada vez que el MCU reporta una pulsación de botón ya con debounce."""

    def __init__(self) -> None:
        self._event = threading.Event()
        Bridge.provide("boton_presionado", self._on_press)

    def _on_press(self) -> None:
        logger.debug("Button press notified by MCU")
        self._event.set()

    def wait(self) -> None:
        """Bloquea hasta que el MCU notifica una pulsación, y entonces retorna."""
        self._event.wait()
        self._event.clear()
