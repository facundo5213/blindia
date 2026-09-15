"""Trigger manual temporal, usado como respaldo del pulsador físico para testing.

La app corre dentro de un contenedor sin TTY adjunta, así que leer
`sys.stdin` directamente dentro del proceso de la app nunca ve una tecla
tipeada en el host. En cambio este trigger bloquea sobre un FIFO (pipe con
nombre) bajo el directorio de datos de la app; abrir un FIFO para lectura
bloquea hasta que *algo* le escribe, que es el mismo contrato de "bloquear
hasta que pase un evento" que tiene la lectura de un botón GPIO.

Desde una shell host/adb/SSH, disparar una captura con:

    echo > ~/ArduinoApps/blindia/data/trigger

o correr `scripts/trigger.sh` desde la raíz de la app.
"""
from __future__ import annotations

import os
from pathlib import Path

from arduino.app_utils import Logger

from .base import Trigger

logger = Logger(__name__)


class KeyboardTrigger(Trigger):
    """Dispara una captura cada vez que se escribe algo a un archivo FIFO."""

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
        """Bloquea hasta que un escritor abre y escribe al FIFO, y entonces retorna."""
        # Abrir un FIFO para lectura bloquea hasta que se conecta un
        # escritor; cuando el escritor cierra (ej. que `echo` retorne) eso
        # entrega EOF, así que esto se desbloquea naturalmente una vez por
        # cada trigger externo.
        with self._fifo_path.open("r") as fifo:
            fifo.readline()
        logger.debug("Trigger FIFO fired")
