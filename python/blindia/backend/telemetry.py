"""Telemetría periódica: latido de 'estoy viva' + estado de Bluetooth/firmware.

Corre en un hilo de fondo (ver `iniciar_loop_en_segundo_plano`), nunca en
el hilo del pipeline del botón -- un fallo o demora de red acá nunca debe
poder atrasar una identificación de producto (docs/API-DISPOSITIVO.md,
punto 8).

No se manda `battery_pct`: esta placa no tiene forma de medir batería, y
el contrato es explícito en que el campo es opcional -- omitirlo, nunca
mandarlo en `null`.
"""
from __future__ import annotations

import threading
import time

from arduino.app_utils import Logger

from ..audio.bluetooth_audio import auriculares_conectados
from ..config import ApiSettings, FIRMWARE_VERSION
from .client import cargar_credenciales, pedir

logger = Logger(__name__)


def enviar_telemetria(settings: ApiSettings) -> bool:
    """Manda un latido de telemetría. `True` si la web lo confirmó, `False` en cualquier otro caso.

    Si el equipo todavía no está vinculado (sin `device_credentials.json`)
    no manda nada y devuelve `False` sin loguear error -- es el estado
    normal de un equipo recién armado (ver docs/VINCULACION-USB.md).
    """
    credenciales = cargar_credenciales()
    if credenciales is None:
        return False

    cuerpo = {
        "bluetooth_connected": auriculares_conectados(),
        "firmware": FIRMWARE_VERSION,
    }
    respuesta = pedir(
        "POST", "/api/device/telemetry", credenciales=credenciales, settings=settings, json_body=cuerpo
    )
    if respuesta.ok:
        server_time = (respuesta.data or {}).get("server_time")
        logger.info(f"[telemetria] Latido confirmado por la web (server_time={server_time}).")
        return True

    logger.warning(f"[telemetria] Latido no confirmado (status={respuesta.status}, error={respuesta.error}).")
    return False


def iniciar_loop_en_segundo_plano(settings: ApiSettings) -> None:
    """Arranca el hilo de fondo: un latido ya mismo, y después uno cada `telemetry_interval_segundos`."""

    def _loop() -> None:
        while True:
            enviar_telemetria(settings)
            time.sleep(settings.telemetry_interval_segundos)

    threading.Thread(target=_loop, daemon=True, name="blindia-telemetria").start()
