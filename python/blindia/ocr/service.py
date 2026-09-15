"""Robust ownership of the OCR engine.

Kept loaded for the app's lifetime rather than reloaded on every capture, so
a capture only pays the cost of inference (~2-3s on this board's CPU), not a
full model load (RapidOCR / ONNXRuntime, PP-OCRv5 mobile weights -- see
`blindia.config.OcrSettings` for why these were chosen over native PaddleOCR
(crashes on this CPU's SIMD feature set) and Tesseract (no supported
system-package install path in this app's container).
"""
from __future__ import annotations

import numpy as np
from arduino.app_utils import Logger

from ..config import OcrSettings
from ..exceptions import OcrFailedError, OcrUnavailableError
from ..models import OcrResult

logger = Logger(__name__)


class OcrService:
    """Owns the RapidOCR engine and exposes a single text-extraction call."""

    def __init__(self, settings: OcrSettings) -> None:
        self._settings = settings
        self._engine = None

    @property
    def is_started(self) -> bool:
        """Whether the OCR models have been successfully loaded."""
        return self._engine is not None

    def start(self) -> None:
        """Load the OCR models. Safe to call once at app startup.

        Loading the ONNX models takes a few seconds, so this should run once
        up front rather than on the first capture.

        Raises:
            OcrUnavailableError: the OCR engine or its models failed to load.
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
        """Release the OCR engine. Safe to call even if never started."""
        self._engine = None

    def extract_text(self, frame: np.ndarray) -> OcrResult:
        """Run OCR on `frame` and return the recognized text.

        Args:
            frame: BGR numpy array (e.g. `CaptureResult.frame`).

        Raises:
            OcrUnavailableError: called before a successful `start()`.
            OcrFailedError: the OCR engine raised while processing the frame.
        """
        if self._engine is None:
            raise OcrUnavailableError(
                "El motor de OCR no fue inicializado (llama a start() primero)."
            )

        try:
            rgb_frame = frame[:, :, ::-1]  # camera frames are BGR; RapidOCR expects RGB
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
