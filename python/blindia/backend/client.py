"""Cliente HTTP de bajo nivel para la API web de administración.

Todo lo que habla HTTP con la web vive acá: lectura de credenciales,
armado de cabeceras, y el pedido en sí con la clasificación de errores de
la tabla del contrato (docs/API-DISPOSITIVO.md, sección 4). Los módulos de
más arriba (`telemetry`, `identifications`, `remote_config`) nunca arman
un `requests.request(...)` por su cuenta -- todos pasan por `pedir()`.

Nunca lanza una excepción hacia afuera: cualquier problema (archivo de
credenciales inexistente, red caída, timeout, respuesta no-JSON) queda
reflejado en el `ApiResponse` devuelto, nunca como excepción -- el uso
diario del anteojo no puede depender de la web (ver
docs/API-DISPOSITIVO.md, punto 8), así que cualquier código que hable con
la API tiene que poder chequear "¿salió bien?" sin try/except.

**Nunca loguear la clave.** Se lee de `DEVICE_CREDENTIALS_FILE` solo para
armar la cabecera `Authorization` -- no aparece en ningún mensaje de log
de este módulo ni de los que lo usan.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests
from arduino.app_utils import Logger

from ..config import ApiSettings, DEVICE_CREDENTIALS_FILE

logger = Logger(__name__)


@dataclass(frozen=True)
class Credenciales:
    serial: str
    clave: str


@dataclass(frozen=True)
class ApiResponse:
    """Resultado de un pedido a la API -- nunca una excepción.

    Attributes:
        status: Código HTTP, o `None` si el pedido ni llegó a tener una
            respuesta (timeout, sin red, DNS, TLS, etc.). El contrato
            trata esto igual que un 5xx: reintentar con espera creciente.
        data: Cuerpo ya parseado como JSON, o `None` si no hubo cuerpo o
            no era JSON válido.
        error: Código corto (`unauthorized`, `rate_limited`,
            `device_not_linked`, `bad_request`, `server_error`,
            `network_error`) para que quien llama decida sin repetir el
            mapeo de códigos HTTP de la tabla del contrato.
    """

    status: int | None
    data: dict[str, Any] | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    @property
    def duplicate(self) -> bool:
        """Para `/identifications`: `True` si la web ya había recibido este `event_id` (200, no 201)."""
        return self.status == 200 and bool((self.data or {}).get("duplicate"))


def cargar_credenciales() -> Credenciales | None:
    """Lee serial + clave de `DEVICE_CREDENTIALS_FILE`, o `None` si el equipo todavía no está vinculado.

    `None` es una condición completamente normal (equipo recién armado,
    todavía no vinculado por USB) -- no se loguea como error, para no
    inundar los logs mientras el anteojo espera a que lo vinculen (ver
    docs/VINCULACION-USB.md).
    """
    try:
        datos = json.loads(DEVICE_CREDENTIALS_FILE.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(f"[api] No se pudo leer {DEVICE_CREDENTIALS_FILE}: {exc}")
        return None

    serial = datos.get("serial")
    clave = datos.get("clave")
    if not serial or not clave:
        logger.error(f"[api] {DEVICE_CREDENTIALS_FILE} existe pero no tiene 'serial'/'clave' válidos.")
        return None
    return Credenciales(serial=serial, clave=clave)


_ERRORES_POR_STATUS = {
    400: "bad_request",
    401: "unauthorized",
    409: "device_not_linked",
    429: "rate_limited",
}


def pedir(
    metodo: str,
    path: str,
    *,
    credenciales: Credenciales,
    settings: ApiSettings,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> ApiResponse:
    """Hace un pedido autenticado a la API y clasifica el resultado. Nunca lanza."""
    url = f"{settings.base_url}{path}"
    headers = {
        "X-Device-Serial": credenciales.serial,
        "Authorization": f"Bearer {credenciales.clave}",
        "Content-Type": "application/json",
    }
    try:
        respuesta = requests.request(
            metodo,
            url,
            headers=headers,
            json=json_body,
            params=params,
            timeout=settings.request_timeout_segundos,
        )
    except requests.RequestException as exc:
        logger.warning(f"[api] {metodo} {path}: error de red ({exc.__class__.__name__}).")
        return ApiResponse(status=None, data=None, error="network_error")

    try:
        datos = respuesta.json() if respuesta.content else None
    except ValueError:
        datos = None

    if respuesta.status_code >= 500:
        logger.warning(f"[api] {metodo} {path}: {respuesta.status_code} (error del servidor).")
        return ApiResponse(status=respuesta.status_code, data=datos, error="server_error")

    error = _ERRORES_POR_STATUS.get(respuesta.status_code)
    if error is not None:
        mensaje = (datos or {}).get("message") if isinstance(datos, dict) else None
        detalle = f" -- {mensaje}" if mensaje else ""
        logger.warning(f"[api] {metodo} {path}: {respuesta.status_code} {error}{detalle}")

    return ApiResponse(status=respuesta.status_code, data=datos, error=error)
