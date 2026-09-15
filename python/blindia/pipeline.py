"""High-level, reusable entry point: trigger -> capture -> save.

This is the single call future triggers (the physical button, wired later
through the Bridge) should make. Downstream modules (OCR, product
recognition, audio) consume the `CaptureResult` it returns.
"""
from __future__ import annotations

from datetime import datetime

from arduino.app_utils import Logger

from .capture.service import CameraCaptureService
from .models import CaptureResult
from .storage.image_store import ImageStore

logger = Logger(__name__)


class CapturePipeline:
    """Composes camera capture and image persistence into one call."""

    def __init__(self, camera: CameraCaptureService, store: ImageStore) -> None:
        self._camera = camera
        self._store = store

    def capture(self) -> CaptureResult:
        """Capture one frame and persist it.

        Raises:
            BlindIAError: (or a subclass) if the camera or the disk write
                fails; callers should catch this and report the failure
                over audio instead of crashing the app.
        """
        frame = self._camera.capture_frame()
        path = self._store.save(frame)
        result = CaptureResult(image_path=path, frame=frame, captured_at=datetime.now())
        logger.info(f"Capture complete: {result.image_path.name}")
        return result
