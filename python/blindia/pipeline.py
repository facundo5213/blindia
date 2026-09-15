"""Punto de entrada reutilizable de alto nivel: trigger -> captura -> guardado.

Esta es la única llamada que deben hacer los triggers (el pulsador físico,
vía el Bridge). Los módulos siguientes (OCR, reconocimiento de producto,
audio) consumen el `CaptureResult` que devuelve.
"""
from __future__ import annotations

from datetime import datetime

from arduino.app_utils import Logger

from .capture.service import CameraCaptureService
from .models import CaptureResult
from .storage.image_store import ImageStore

logger = Logger(__name__)


class CapturePipeline:
    """Compone la captura de cámara y la persistencia de imagen en una sola llamada."""

    def __init__(self, camera: CameraCaptureService, store: ImageStore) -> None:
        self._camera = camera
        self._store = store

    def capture(self) -> CaptureResult:
        """Captura un frame y lo persiste.

        Raises:
            BlindIAError: (o una subclase) si falla la cámara o la
                escritura a disco; quien llama debería atrapar esto y
                reportar la falla por audio en vez de tirar abajo la app.
        """
        frame = self._camera.capture_frame()
        path = self._store.save(frame)
        result = CaptureResult(image_path=path, frame=frame, captured_at=datetime.now())
        logger.info(f"Capture complete: {result.image_path.name}")
        return result
