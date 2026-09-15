"""Persists captured frames to disk with unique, sortable filenames."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
from arduino.app_utils import Logger
from arduino.app_utils.image import compress_to_jpeg

from ..exceptions import ImageSaveError

logger = Logger(__name__)


class ImageStore:
    """Saves frames as timestamped JPEG files under a fixed directory."""

    def __init__(self, directory: Path, jpeg_quality: int = 95) -> None:
        self._directory = directory
        self._jpeg_quality = jpeg_quality
        self._directory.mkdir(parents=True, exist_ok=True)

    def save(self, frame: np.ndarray) -> Path:
        """Encode `frame` as JPEG and write it under a unique filename.

        The filename embeds a microsecond timestamp (`capture_YYYYmmdd_HHMMSS_ffffff.jpg`)
        so repeated captures never collide and sort chronologically.

        Returns:
            The full path of the saved file.

        Raises:
            ImageSaveError: JPEG encoding or the disk write failed.
        """
        filename = f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        path = self._directory / filename

        jpeg = compress_to_jpeg(frame=frame, quality=self._jpeg_quality)
        if jpeg is None:
            logger.error(f"JPEG compression returned no data for {filename}")
            raise ImageSaveError(f"No se pudo comprimir la imagen a JPEG ({filename}).")

        try:
            path.write_bytes(jpeg.tobytes())
        except OSError as exc:
            logger.error(f"Failed to write {path}: {exc}")
            raise ImageSaveError(f"No se pudo guardar la imagen en {path}.") from exc

        logger.info(f"Saved capture to {path}")
        return path
