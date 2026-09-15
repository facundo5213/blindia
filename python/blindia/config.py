"""Central configuration for the Blind IA app.

All modules (capture, and later OCR/recognition/audio) should read their
settings from here instead of hardcoding values, so the whole app stays
tunable from one place.
"""
from dataclasses import dataclass
from pathlib import Path

# .../blindia/python/blindia/config.py -> parents[2] is the app root (.../blindia)
APP_ROOT: Path = Path(__file__).resolve().parents[2]

# Where captured photos are saved. Lives under the app root so it is
# persisted on the host via the app's bind mount (see .cache/app-compose.yaml).
CAPTURES_DIR: Path = APP_ROOT / "data" / "captures"

# FIFO used by the temporary keyboard trigger (blindia.triggers.keyboard).
# Write anything to this path from the host to fire one capture.
TRIGGER_FIFO_PATH: Path = APP_ROOT / "data" / "trigger"


@dataclass(frozen=True)
class CameraSettings:
    """Configuration for the camera peripheral.

    Attributes:
        source: Camera identifier forwarded to `Camera(...)` (e.g. "usb:0",
            "/dev/video2", a plain index). `None` lets the platform
            auto-select the first camera found (USB has priority over CSI),
            which is the right default for a single generic USB webcam.
        resolution: Capture resolution as (width, height). Chosen higher
            than the platform default (640x480) since price tags need
            enough detail for OCR later.
        fps: Frames per second the camera stream is configured for. 10 is
            the max the detected webcam (Logitech C525) supports at 720p;
            asking for more just logs a harmless warning.
        jpeg_quality: JPEG quality (0-100) used when persisting captures.
    """

    source: str | int | None = None
    resolution: tuple[int, int] = (1280, 720)
    fps: int = 10
    jpeg_quality: int = 95


@dataclass(frozen=True)
class OcrSettings:
    """Configuration for the OCR engine (blindia.ocr.service.OcrService).

    Attributes:
        use_cls: Whether to run the text-orientation classifier before
            recognition. Disabled by default: price labels are captured
            upright, and skipping this stage measurably cuts latency on
            this board's CPU (~2-3s/image either way, no NPU available).
        min_text_score: Recognition-confidence threshold (0-1) below which a
            text fragment is dropped, to filter out low-confidence noise
            picked up from the background of a shelf photo.
    """

    use_cls: bool = False
    min_text_score: float = 0.5
