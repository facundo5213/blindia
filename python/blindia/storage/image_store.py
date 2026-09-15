"""Persiste frames capturados a disco con nombres de archivo únicos y ordenables."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
from arduino.app_utils import Logger
from arduino.app_utils.image import compress_to_jpeg

from ..exceptions import ImageSaveError

logger = Logger(__name__)


class ImageStore:
    """Guarda frames como archivos JPEG con timestamp bajo un directorio fijo."""

    def __init__(self, directory: Path, jpeg_quality: int = 95) -> None:
        self._directory = directory
        self._jpeg_quality = jpeg_quality
        self._directory.mkdir(parents=True, exist_ok=True)

    def save(self, frame: np.ndarray) -> Path:
        """Codifica `frame` como JPEG y lo escribe bajo un nombre de archivo único.

        El nombre de archivo incluye un timestamp con microsegundos
        (`capture_YYYYmmdd_HHMMSS_ffffff.jpg`) así capturas repetidas nunca
        colisionan y ordenan cronológicamente.

        Returns:
            La ruta completa del archivo guardado.

        Raises:
            ImageSaveError: falló la codificación JPEG o la escritura a disco.
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
