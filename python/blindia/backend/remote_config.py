"""Configuración remota del cliente (velocidad de voz, volumen, nivel de detalle).

Encuesta `GET /api/device/config?since=<version>` al arrancar y cada
`settings.config_poll_interval_segundos` (ver
`iniciar_loop_en_segundo_plano`), siempre en un hilo de fondo -- nunca
bloquea el pipeline del botón (docs/API-DISPOSITIVO.md, punto 8).

Aplica solo lo que ya existe hoy en la app: `volume` y `speech_rate` (al
daemon de audio Bluetooth, que es quien realmente sintetiza y reproduce
la voz -- ver `blindia.audio.bluetooth_audio.aplicar_configuracion_voz`)
y `detail_level` (leído por `main.py` antes de armar el mensaje hablado,
ver `nivel_detalle_actual`). **Ignora `voice` y `confirm_sound` a
propósito**: no hay ninguna función hoy que elija voz del TTS ni que
suene un beep de confirmación, y el pedido fue explícito en no agregar
ninguna de las dos funciones solo para que esos dos campos "hagan algo".

También actualiza `blindia.backend.state` con el campo `linked` de la
respuesta -- es lo que usa `blindia.backend.identifications` para saber
cuándo puede volver a intentar mandar productos después de un 409.
"""
from __future__ import annotations

import threading
import time

from arduino.app_utils import Logger

from ..audio.bluetooth_audio import aplicar_configuracion_voz
from ..config import ApiSettings, REMOTE_CONFIG_VERSION_FILE
from .client import cargar_credenciales, pedir
from .state import establecer_vinculado

logger = Logger(__name__)

_NIVELES_DETALLE_VALIDOS = ("breve", "detallada")
_DETALLE_POR_DEFECTO = "breve"

_lock_detalle = threading.Lock()
_nivel_detalle = _DETALLE_POR_DEFECTO


def nivel_detalle_actual() -> str:
    """Último `detail_level` aplicado, o `"breve"` si todavía no se consultó la configuración remota."""
    with _lock_detalle:
        return _nivel_detalle


def _leer_version_guardada() -> int:
    try:
        return int(REMOTE_CONFIG_VERSION_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _guardar_version(version: int) -> None:
    REMOTE_CONFIG_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    archivo_temporal = REMOTE_CONFIG_VERSION_FILE.with_suffix(".tmp")
    archivo_temporal.write_text(str(version))
    archivo_temporal.replace(REMOTE_CONFIG_VERSION_FILE)


def _aplicar_settings(settings_remotos: dict) -> None:
    """Aplica solo `volume`/`speech_rate` (al daemon de audio) y `detail_level` (acá). Ignora el resto."""
    global _nivel_detalle

    volumen = settings_remotos.get("volume")
    velocidad = settings_remotos.get("speech_rate")
    if volumen is not None or velocidad is not None:
        if not aplicar_configuracion_voz(volumen, velocidad or "normal"):
            logger.warning("[config] No se pudo aplicar volumen/velocidad al daemon de audio Bluetooth.")

    detalle = settings_remotos.get("detail_level")
    if detalle in _NIVELES_DETALLE_VALIDOS:
        with _lock_detalle:
            _nivel_detalle = detalle
        logger.info(f"[config] Nivel de detalle aplicado: {detalle!r}.")


def actualizar_configuracion(settings: ApiSettings) -> bool:
    """Consulta `/config` y aplica lo que cambió. `True` si la consulta salió bien (haya cambiado algo o no)."""
    credenciales = cargar_credenciales()
    if credenciales is None:
        return False

    respuesta = pedir(
        "GET",
        "/api/device/config",
        credenciales=credenciales,
        settings=settings,
        params={"since": _leer_version_guardada()},
    )
    if not respuesta.ok or respuesta.data is None:
        logger.warning(
            f"[config] Consulta de configuración no confirmada (status={respuesta.status}, error={respuesta.error})."
        )
        return False

    datos = respuesta.data
    if "linked" in datos:
        establecer_vinculado(bool(datos["linked"]))

    if not datos.get("changed"):
        logger.debug("[config] Sin cambios de configuración.")
        return True

    _aplicar_settings(datos.get("settings") or {})
    _guardar_version(int(datos["version"]))
    logger.info(f"[config] Configuración actualizada a la versión {datos['version']}.")
    return True


def iniciar_loop_en_segundo_plano(settings: ApiSettings) -> None:
    """Arranca el hilo de fondo: una consulta ya mismo, y después una cada `config_poll_interval_segundos`."""

    def _loop() -> None:
        while True:
            actualizar_configuracion(settings)
            time.sleep(settings.config_poll_interval_segundos)

    threading.Thread(target=_loop, daemon=True, name="blindia-config-remota").start()
