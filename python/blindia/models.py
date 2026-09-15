"""Shared data structures passed between Blind IA modules."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np


@dataclass(frozen=True, eq=False)
class CaptureResult:
    """Result of a single on-demand image capture.

    Carries both the saved file path (for modules that read from disk) and
    the in-memory frame (so the OCR module can reuse it without a redundant
    disk read in the same process).
    """

    image_path: Path
    frame: np.ndarray
    captured_at: datetime


@dataclass(frozen=True, eq=False)
class OcrResult:
    """Result of running OCR on one frame.

    `fragments` keeps the individual text boxes the engine found (reading
    order, top to bottom); `text` is them joined with spaces for convenience
    when a caller (e.g. the future audio module) just wants one string.
    """

    text: str
    fragments: tuple[str, ...]


@dataclass(frozen=True, eq=False)
class RecognizedProduct:
    """Best-effort product identification, parsed from `OcrResult.text`.

    `name` and `price` are heuristically extracted (see
    `blindia.product.parser`) and may be `None` if nothing matched.
    `raw_text` is always kept so a caller (e.g. the future audio module) can
    fall back to reading it verbatim when parsing comes up empty.

    `price` is kept as the exact printed substring (e.g. "$1.549,90"),
    not parsed into a number: thousands/decimal separators are
    locale-dependent, and misreading an amount is worse than not parsing it.
    """

    raw_text: str
    name: str | None
    price: str | None
