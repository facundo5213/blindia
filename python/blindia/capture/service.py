"""Robust ownership of the camera peripheral.

Kept open for the app's lifetime rather than reopened on every capture, so a
future button press only pays the cost of `capture_frame()`, not a full
camera re-init.
"""
from __future__ import annotations

import numpy as np
from arduino.app_peripherals.camera import Camera, CameraOpenError, CameraReadError
from arduino.app_utils import Logger

from ..config import CameraSettings
from ..exceptions import CameraUnavailableError, CaptureFailedError

logger = Logger(__name__)


class CameraCaptureService:
    """Owns a `Camera` peripheral and exposes a single on-demand capture call."""

    def __init__(self, settings: CameraSettings) -> None:
        self._settings = settings
        self._camera: Camera | None = None

    @property
    def is_started(self) -> bool:
        """Whether the camera has been successfully started."""
        return self._camera is not None and self._camera.is_started()

    def start(self) -> None:
        """Open and start the camera. Safe to call once at app startup.

        Raises:
            CameraUnavailableError: the camera is missing, disconnected, or
                already claimed by another process.
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
            # Covers "no camera found" raised at construction time, before start().
            logger.error(f"Unexpected error while opening the camera: {exc}")
            raise CameraUnavailableError("No se detecto ninguna camara conectada.") from exc

        self._camera = camera
        logger.info(
            f"Camera started (source={self._settings.source!r}, "
            f"resolution={self._settings.resolution}, fps={self._settings.fps})"
        )

    def stop(self) -> None:
        """Stop and release the camera. Safe to call even if never started."""
        if self._camera is None:
            return
        self._camera.stop()
        self._camera = None
        logger.info("Camera stopped")

    def capture_frame(self) -> np.ndarray:
        """Capture a single raw frame on demand (e.g. on a button press).

        Returns:
            The captured frame as a BGR numpy array.

        Raises:
            CameraUnavailableError: called before a successful `start()`.
            CaptureFailedError: the camera failed to deliver a frame.
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
