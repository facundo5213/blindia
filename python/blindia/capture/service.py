"""Dueño robusto del periférico de cámara.

Se mantiene abierta durante toda la vida de la app en vez de reabrirla en
cada captura, así un apretón del botón solo paga el costo de
`capture_frame()`, no una reinicialización completa de la cámara.
"""
from __future__ import annotations

import numpy as np
from arduino.app_peripherals.camera import Camera, CameraOpenError, CameraReadError
from arduino.app_utils import Logger

from ..config import CameraSettings
from ..exceptions import CameraUnavailableError, CaptureFailedError

logger = Logger(__name__)


class CameraCaptureService:
    """Es dueño de un periférico `Camera` y expone una sola llamada de captura bajo demanda."""

    def __init__(self, settings: CameraSettings) -> None:
        self._settings = settings
        self._camera: Camera | None = None

    @property
    def is_started(self) -> bool:
        """Si la cámara se arrancó con éxito."""
        return self._camera is not None and self._camera.is_started()

    def start(self) -> None:
        """Abre y arranca la cámara. Seguro de llamar una sola vez al arrancar la app.

        Raises:
            CameraUnavailableError: la cámara falta, está desconectada, o ya
                está tomada por otro proceso.
        """
        if self._camera is not None:
            logger.debug("Camera already started, ignoring duplicate start()")
            return

        kwargs = {"resolution": self._settings.resolution, "fps": self._settings.fps}
        try:
            camera = (
                Camera(self._settings.source, **kwargs)
                if self._settings.source is not None
                else Camera(**kwargs)
            )
            camera.start()
        except CameraOpenError as exc:
            logger.error(f"Camera failed to open (source={self._settings.source!r}): {exc}")
            raise CameraUnavailableError(
                "No se pudo abrir la camara. Verifica que este conectada y que "
                "ninguna otra app la este usando."
            ) from exc
        except Exception as exc:
            # Cubre "no camera found" lanzado en tiempo de construcción, antes de start().
            logger.error(f"Unexpected error while opening the camera: {exc}")
            raise CameraUnavailableError("No se detecto ninguna camara conectada.") from exc

        self._camera = camera
        logger.info(
            f"Camera started (source={self._settings.source!r}, "
            f"resolution={self._settings.resolution}, fps={self._settings.fps})"
        )

    def stop(self) -> None:
        """Detiene y libera la cámara. Seguro de llamar aunque nunca se haya arrancado."""
        if self._camera is None:
            return
        self._camera.stop()
        self._camera = None
        logger.info("Camera stopped")

    def capture_frame(self) -> np.ndarray:
        """Captura un solo frame crudo bajo demanda (ej. en un apretón de botón).

        Returns:
            El frame capturado como un array numpy BGR.

        Raises:
            CameraUnavailableError: se llamó antes de un `start()` exitoso.
            CaptureFailedError: la cámara falló al entregar un frame.
        """
        if self._camera is None:
            raise CameraUnavailableError("La camara no fue inicializada (llama a start() primero).")

        try:
            frame = self._camera.capture()
        except CameraReadError as exc:
            logger.error(f"Failed to read a frame: {exc}")
            raise CaptureFailedError("Fallo la lectura de un frame de la camara.") from exc

        if frame is None:
            logger.warning("Camera returned no frame")
            raise CaptureFailedError("La camara no devolvio ningun frame.")

        return frame

    def __enter__(self) -> "CameraCaptureService":
        self.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.stop()
