"""Cola persistente de identificaciones de producto, con reintentos ante fallas de red.

`encolar_identificacion()` es la única función que debe llamar el
pipeline del botón (`main.py`): escribe el evento a disco y devuelve al
instante -- nunca intenta mandar nada por red en el hilo que la llama
(docs/API-DISPOSITIVO.md, punto 8: el pipeline del botón nunca espera a la
red). El envío real lo hace el hilo de fondo arrancado por
`iniciar_loop_en_segundo_plano()`.

Garantías:

- **`event_id` único y estable ante reintentos**: `<serial>-<contador de
  6 dígitos>`, con el contador persistido en
  `IDENTIFICATION_COUNTER_FILE` -- sobrevive a reinicios de la app, y
  nunca se genera uno nuevo para reintentar un evento: se reenvía tal
  cual quedó guardado en la cola.
- **Cola sin conexión**: hasta `settings.max_eventos_en_cola` eventos
  (el contrato pide "al menos 20") persistidos en
  `IDENTIFICATION_QUEUE_FILE`; si se llena, se descarta el más viejo para
  dejar lugar al más nuevo (se loguea cuando esto pasa).
- **`seconds_ago`**: calculado recién al mandar el evento, a partir de
  cuándo ocurrió realmente (`occurred_at`), no de cuándo se encoló.
- **Reintentos con espera creciente** ante error de red/5xx (tabla de
  errores del contrato); **pausa larga** ante 401 (credenciales mal, no
  insistir cada pocos segundos); **pausa corta** ante 429; y **pausa**
  ante 409 (`device_not_linked`) hasta que `blindia.backend.remote_config`
  confirme que ya se vinculó (ver `blindia.backend.state`).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from arduino.app_utils import Logger

from ..config import ApiSettings, IDENTIFICATION_COUNTER_FILE, IDENTIFICATION_QUEUE_FILE
from .client import cargar_credenciales, pedir
from .state import establecer_vinculado, esta_vinculado

logger = Logger(__name__)

_lock_cola = threading.Lock()
_lock_contador = threading.Lock()

# Ritmo del loop de fondo cuando no hay nada para mandar, o mientras
# espera a que el equipo se vincule (409) -- no es el backoff de un
# reintento puntual, es solo cada cuánto se vuelve a mirar la cola.
_ESPERA_COLA_VACIA_SEGUNDOS = 5.0
_ESPERA_PAUSA_VINCULACION_SEGUNDOS = 30.0


def _siguiente_event_id(serial: str) -> str:
    """Contador persistente: nunca se repite, ni tras un reinicio de la app."""
    with _lock_contador:
        try:
            contador = int(IDENTIFICATION_COUNTER_FILE.read_text().strip())
        except (FileNotFoundError, ValueError):
            contador = 0
        contador += 1
        IDENTIFICATION_COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)
        archivo_temporal = IDENTIFICATION_COUNTER_FILE.with_suffix(".tmp")
        archivo_temporal.write_text(str(contador))
        archivo_temporal.replace(IDENTIFICATION_COUNTER_FILE)
    return f"{serial}-{contador:06d}"


def _leer_cola() -> list[dict[str, Any]]:
    try:
        return json.loads(IDENTIFICATION_QUEUE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _escribir_cola(eventos: list[dict[str, Any]]) -> None:
    IDENTIFICATION_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    archivo_temporal = IDENTIFICATION_QUEUE_FILE.with_suffix(".tmp")
    archivo_temporal.write_text(json.dumps(eventos))
    archivo_temporal.replace(IDENTIFICATION_QUEUE_FILE)


def encolar_identificacion(
    product: str,
    price_ars: int | None,
    confidence: float | None,
    *,
    settings: ApiSettings,
    uad_cost: int = 1,
) -> None:
    """Guarda un evento en la cola persistente y devuelve al instante -- nunca manda nada por red acá.

    Si el equipo todavía no está vinculado (sin credenciales), no hace
    nada: no tiene sentido encolar un evento cuyo `event_id` ni se podría
    construir (depende del serial del equipo vinculado).
    """
    credenciales = cargar_credenciales()
    if credenciales is None:
        return

    evento = {
        "event_id": _siguiente_event_id(credenciales.serial),
        "product": product,
        "price_ars": price_ars,
        "confidence": confidence,
        "uad_cost": uad_cost,
        "occurred_at": time.time(),
    }

    with _lock_cola:
        eventos = _leer_cola()
        eventos.append(evento)
        if len(eventos) > settings.max_eventos_en_cola:
            descartado = eventos.pop(0)
            logger.warning(
                f"[identificaciones] Cola llena ({settings.max_eventos_en_cola}) -- "
                f"se descarta el evento más viejo sin confirmar ({descartado['event_id']!r})."
            )
        _escribir_cola(eventos)

    logger.info(f"[identificaciones] Encolado {evento['event_id']!r} ({product!r}).")


def _cuerpo_pedido(evento: dict[str, Any]) -> dict[str, Any]:
    """Arma el cuerpo JSON del pedido, con `seconds_ago` calculado recién ahora (no al encolar)."""
    cuerpo: dict[str, Any] = {
        "event_id": evento["event_id"],
        "product": evento["product"],
        "seconds_ago": max(0, int(time.time() - evento["occurred_at"])),
    }
    for campo in ("price_ars", "confidence", "uad_cost"):
        if evento.get(campo) is not None:
            cuerpo[campo] = evento[campo]
    return cuerpo


def _quitar_de_cola(event_id: str) -> None:
    with _lock_cola:
        eventos = [e for e in _leer_cola() if e["event_id"] != event_id]
        _escribir_cola(eventos)


def _primero_de_la_cola() -> dict[str, Any] | None:
    with _lock_cola:
        eventos = _leer_cola()
        return eventos[0] if eventos else None


def _intentar_enviar_uno(settings: ApiSettings) -> tuple[str, float | None]:
    """Intenta mandar el evento más viejo de la cola.

    Devuelve `(estado, espera_segundos)`. `estado` es uno de:
    `"exito"`, `"descartado"`, `"vacio"`, `"esperar"` (pausa explícita,
    con `espera_segundos` fijo), o `"reintentar"` (error de red/5xx --
    quien llama decide el backoff creciente, no hay un tiempo fijo acá).
    """
    credenciales = cargar_credenciales()
    if credenciales is None:
        return "esperar", _ESPERA_COLA_VACIA_SEGUNDOS

    if esta_vinculado() is False:
        # Ya se confirmó un 409 antes: no insistir -- es
        # blindia.backend.remote_config quien levanta esta pausa cuando
        # el cliente vincule la cuenta (ver blindia.backend.state).
        return "esperar", _ESPERA_PAUSA_VINCULACION_SEGUNDOS

    evento = _primero_de_la_cola()
    if evento is None:
        return "vacio", _ESPERA_COLA_VACIA_SEGUNDOS

    respuesta = pedir(
        "POST",
        "/api/device/identifications",
        credenciales=credenciales,
        settings=settings,
        json_body=_cuerpo_pedido(evento),
    )

    if respuesta.ok:
        if respuesta.duplicate:
            logger.info(f"[identificaciones] {evento['event_id']!r} ya estaba recibido (duplicado) -- OK.")
        else:
            logger.info(f"[identificaciones] {evento['event_id']!r} confirmado por la web.")
        _quitar_de_cola(evento["event_id"])
        return "exito", None

    if respuesta.error == "bad_request":
        # Bug de este lado (cuerpo inválido) -- reintentar no lo arregla,
        # y dejarlo en la cola solo trabaría a todos los eventos detrás.
        logger.error(f"[identificaciones] {evento['event_id']!r} rechazado (bad_request) -- se descarta.")
        _quitar_de_cola(evento["event_id"])
        return "descartado", None

    if respuesta.error == "unauthorized":
        logger.error("[identificaciones] 401 -- credenciales rechazadas, pausando reintentos.")
        return "esperar", settings.pausa_tras_401_segundos

    if respuesta.error == "device_not_linked":
        logger.warning("[identificaciones] 409 -- el equipo no está vinculado a ninguna cuenta todavía.")
        establecer_vinculado(False)
        return "esperar", _ESPERA_PAUSA_VINCULACION_SEGUNDOS

    if respuesta.error == "rate_limited":
        logger.warning("[identificaciones] 429 -- esperando antes de seguir.")
        return "esperar", settings.pausa_tras_429_segundos

    # server_error o network_error: el mismo evento se reintenta con
    # backoff creciente (lo controla el loop de afuera).
    return "reintentar", None


def iniciar_loop_en_segundo_plano(settings: ApiSettings) -> None:
    """Arranca el hilo de fondo que drena la cola de identificaciones."""

    def _loop() -> None:
        indice_backoff = 0
        while True:
            estado, espera = _intentar_enviar_uno(settings)
            if estado == "reintentar":
                backoff = settings.reintento_backoff_segundos
                time.sleep(backoff[min(indice_backoff, len(backoff) - 1)])
                indice_backoff += 1
                continue

            indice_backoff = 0
            if espera:
                time.sleep(espera)
            # "exito"/"descartado" sin espera: seguir enseguida con el
            # próximo evento de la cola, si hay más.

    threading.Thread(target=_loop, daemon=True, name="blindia-identificaciones").start()
