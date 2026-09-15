"""Excepciones compartidas por el pipeline de captura de Blind IA.

Quien llama (ej. el manejador del pulsador físico) puede atrapar la base
común `BlindIAError` para reportar cualquier falla de captura por audio sin
necesitar saber qué etapa del pipeline falló.
"""


class BlindIAError(Exception):
    """Clase base para todos los errores de la app Blind IA."""


class CameraUnavailableError(BlindIAError):
    """No se pudo abrir la cámara: no está conectada, o ya está en uso."""


class CaptureFailedError(BlindIAError):
    """La cámara estaba abierta pero no llegó a entregar un frame usable."""


class ImageSaveError(BlindIAError):
    """No se pudo persistir a disco un frame capturado."""


class OcrUnavailableError(BlindIAError):
    """El motor de OCR falló al cargar, o se usó antes de `start()`."""


class OcrFailedError(BlindIAError):
    """El motor de OCR estaba cargado pero falló al procesar una imagen."""
