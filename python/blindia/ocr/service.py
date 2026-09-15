"""Dueño robusto del motor de OCR.

Se mantiene cargado durante toda la vida de la app en vez de recargarlo en
cada captura, así una captura solo paga el costo de la inferencia (~2-3s en
la CPU de esta placa), no una carga completa del modelo (RapidOCR /
ONNXRuntime, pesos PP-OCRv5 mobile -- ver `blindia.config.OcrSettings` para
el porqué de elegirlos por sobre PaddleOCR nativo (crashea con el set de
instrucciones SIMD de esta CPU) y Tesseract (sin forma soportada de
instalarlo como paquete de sistema en el contenedor de esta app).
"""
from __future__ import annotations

import numpy as np
from arduino.app_utils import Logger

from ..config import OcrSettings
from ..exceptions import OcrFailedError, OcrUnavailableError
from ..models import OcrResult

logger = Logger(__name__)


class OcrService:
    """Es dueño del motor RapidOCR y expone una sola llamada de extracción de texto."""

    def __init__(self, settings: OcrSettings) -> None:
        self._settings = settings
        self._engine = None

    @property
    def is_started(self) -> bool:
        """Si los modelos de OCR se cargaron con éxito."""
        return self._engine is not None

    def start(self) -> None:
        """Carga los modelos de OCR. Seguro de llamar una sola vez al arrancar la app.

        Cargar los modelos ONNX tarda unos segundos, así que esto debería
        correr una sola vez por adelantado en vez de en la primera captura.

        Raises:
            OcrUnavailableError: el motor de OCR o sus modelos fallaron al cargar.
        """
        if self._engine is not None:
            logger.debug("OCR engine already started, ignoring duplicate start()")
            return

        try:
            from rapidocr import RapidOCR
            from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

            engine = RapidOCR(
                params={
                    "Det.ocr_version": OCRVersion.PPOCRV5,
                    "Det.model_type": ModelType.MOBILE,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.model_type": ModelType.MOBILE,
                    "Rec.lang_type": LangRec.LATIN,
                }
            )
        except Exception as exc:
            logger.error(f"Failed to load OCR models: {exc}")
            raise OcrUnavailableError("No se pudo cargar el motor de OCR.") from exc

        self._engine = engine
        logger.info("OCR engine started (RapidOCR, PP-OCRv5 mobile det + latin mobile rec)")

    def stop(self) -> None:
        """Libera el motor de OCR. Seguro de llamar aunque nunca se haya arrancado."""
        self._engine = None

    def extract_text(self, frame: np.ndarray) -> OcrResult:
        """Corre OCR sobre `frame` y devuelve el texto reconocido.

        Args:
            frame: array numpy BGR (ej. `CaptureResult.frame`).

        Raises:
            OcrUnavailableError: se llamó antes de un `start()` exitoso.
            OcrFailedError: el motor de OCR lanzó una excepción al procesar el frame.
        """
        if self._engine is None:
            raise OcrUnavailableError(
                "El motor de OCR no fue inicializado (llama a start() primero)."
            )

        try:
            rgb_frame = frame[:, :, ::-1]  # los frames de la cámara son BGR; RapidOCR espera RGB
            result = self._engine(rgb_frame, use_cls=self._settings.use_cls)
        except Exception as exc:
            logger.error(f"OCR inference failed: {exc}")
            raise OcrFailedError("Fallo el reconocimiento de texto en la imagen.") from exc

        txts = result.txts or ()
        scores = result.scores or ()
        fragments = tuple(
            txt for txt, score in zip(txts, scores) if score >= self._settings.min_text_score
        )
        text = " ".join(fragments)

        logger.info(f"OCR complete: {len(fragments)} fragment(s)")
        return OcrResult(text=text, fragments=fragments)
