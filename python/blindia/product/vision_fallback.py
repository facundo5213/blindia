"""Respaldo remoto de reconocimiento visual.

Cuando el OCR local (RapidOCR) no detecta NINGÚN fragmento de texto en la
imagen, esta placa manda la foto al servidor FastAPI que corre en la PC del
usuario en la red WiFi local (puerto configurable en `blindia.config`,
`VisionFallbackSettings`). Ese servidor internamente le habla a LM Studio
(Gemma) para identificar el producto visualmente -- ese trabajo ya está
resuelto del lado del servidor, este módulo es apenas el cliente HTTP.

Alcance deliberadamente angosto: SOLO se usa cuando el OCR no encontró
ningún texto en absoluto (0 fragmentos). El caso "hay texto pero no se
encontró un precio con $" es un problema distinto, ya resuelto sin llamar a
ningún servidor -- ver `blindia.product.parser` (se lee el texto completo
detectado). No mezclar los dos casos.

Mismo espíritu que `blindia.audio.bluetooth_audio`: nunca lanza una
excepción hacia afuera. Cualquier falla (servidor apagado, timeout, red
caída, respuesta con formato inesperado) se loguea acá y devuelve `None`,
para que quien llama decida el mensaje de reserva sin necesitar
try/except.
"""
from __future__ import annotations

import cv2
import numpy as np
import requests
from arduino.app_utils import Logger

from ..config import VisionFallbackSettings

logger = Logger(__name__)


def identificar_producto_remoto(frame: np.ndarray, settings: VisionFallbackSettings) -> str | None:
    """Manda `frame` (BGR, igual que devuelve la cámara) al servidor remoto.

    Devuelve el `mensaje_voz` ya armado por el servidor, listo para pasarle
    directo a `hablar()` -- el servidor ya hace el trabajo de convertir su
    resultado en una frase hablable, no hace falta reprocesarlo acá. Nunca
    lanza: cualquier problema devuelve `None`.
    """
    ok, jpeg = cv2.imencode(".jpg", frame)
    if not ok:
        logger.error("[vision] No se pudo codificar el frame a JPEG para mandarlo al servidor remoto.")
        return None

    url = f"http://{settings.host}:{settings.port}/analyze"
    try:
        respuesta = requests.post(
            url,
            files={"image": ("captura.jpg", jpeg.tobytes(), "image/jpeg")},
            timeout=settings.timeout_segundos,
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
    except requests.RequestException as exc:
        logger.error(f"[vision] No se pudo contactar al servidor remoto ({url}): {exc}")
        return None
    except ValueError as exc:  # respuesta no es JSON válido
        logger.error(f"[vision] Respuesta del servidor remoto no es JSON válido: {exc}")
        return None

    mensaje = datos.get("mensaje_voz")
    if not mensaje:
        logger.error(f"[vision] Respuesta del servidor remoto sin 'mensaje_voz': {datos!r}")
        return None

    logger.info(
        f"[vision] Servidor remoto: producto={datos.get('producto_detectado')!r} "
        f"(confianza={datos.get('confianza_producto')!r}) "
        f"precio={datos.get('precio_leido')!r} (confianza={datos.get('confianza_precio')!r}) "
        f"tiempo_servidor={datos.get('tiempo_procesamiento_ms')!r}ms"
    )
    return mensaje
