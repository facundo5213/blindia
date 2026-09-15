"""Blind IA -- entry point.

Wires the capture module (camera + storage + trigger) and keeps the camera
open for the app's lifetime.

Trigger source is the physical button (blindia/triggers/button.py), read on
the MCU (sketch/sketch.ino) and relayed over the Bridge. `KeyboardTrigger`
(blindia/triggers/keyboard.py) is still available as a FIFO-based stand-in
for testing without the button.
"""
import time

from arduino.app_utils import App, Logger

from blindia.audio.bluetooth_audio import hablar
from blindia.capture.service import CameraCaptureService
from blindia.config import CAPTURES_DIR, CameraSettings, OcrSettings
from blindia.exceptions import BlindIAError
from blindia.ocr.service import OcrService
from blindia.pipeline import CapturePipeline
from blindia.product.parser import describe_product, parse_product
from blindia.storage.image_store import ImageStore
from blindia.triggers.button import ButtonTrigger

logger = Logger(__name__)

camera_service = CameraCaptureService(CameraSettings())
image_store = ImageStore(CAPTURES_DIR)
pipeline = CapturePipeline(camera_service, image_store)
ocr_service = OcrService(OcrSettings())
trigger = ButtonTrigger()

try:
    camera_service.start()
except BlindIAError as exc:
    logger.error(f"Camera did not start: {exc}")

try:
    ocr_service.start()
except BlindIAError as exc:
    logger.error(f"OCR engine did not start: {exc}")


def run_capture(reason: str) -> None:
    """Run one capture, OCR it, and parse a product out of the text. Called by every trigger source.

    Logs a per-stage timing breakdown (ms) -- temporary instrumentation to
    find where the button-to-voice latency goes; see README "Latencia del
    circuito completo" for how to read it and what's been ruled out.
    """
    t_inicio = time.perf_counter()

    t0 = time.perf_counter()
    try:
        result = pipeline.capture()
        t_captura = (time.perf_counter() - t0) * 1000
        logger.info(f"[{reason}] Capture saved to {result.image_path}")
        logger.info(f"[timing] captura: {t_captura:.0f}ms")
    except BlindIAError as exc:
        logger.error(f"[{reason}] Capture failed: {exc}")
        return

    t0 = time.perf_counter()
    try:
        ocr_result = ocr_service.extract_text(result.frame)
        t_ocr = (time.perf_counter() - t0) * 1000
        logger.info(f"[{reason}] OCR text: {ocr_result.text!r}")
        logger.info(f"[timing] ocr: {t_ocr:.0f}ms")
    except BlindIAError as exc:
        logger.error(f"[{reason}] OCR failed: {exc}")
        return

    t0 = time.perf_counter()
    product = parse_product(ocr_result)
    t_reconocimiento = (time.perf_counter() - t0) * 1000
    logger.info(f"[{reason}] Recognized product: name={product.name!r} price={product.price!r}")
    logger.info(f"[timing] reconocimiento_producto: {t_reconocimiento:.0f}ms")

    t0 = time.perf_counter()
    hablar(describe_product(product))  # bloquea hasta que termina la reproducción (ver bluetooth_audio.py)
    t_hablar = (time.perf_counter() - t0) * 1000
    logger.info(f"[timing] hablar_total (sintesis+IPC+reproduccion): {t_hablar:.0f}ms")

    t_total = (time.perf_counter() - t_inicio) * 1000
    logger.info(f"[timing] TOTAL: {t_total:.0f}ms")


def loop() -> None:
    trigger.wait()
    run_capture("button")


run_capture("startup")  # smoke test: confirm the pipeline works before waiting on triggers
App.run(user_loop=loop)
