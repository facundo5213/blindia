"""Blind IA -- punto de entrada.

Conecta el módulo de captura (cámara + almacenamiento + trigger) y mantiene
la cámara abierta durante toda la vida de la app.

La fuente del trigger es el pulsador físico (blindia/triggers/button.py),
leído en el MCU (sketch/sketch.ino) y transmitido por el Bridge.
`KeyboardTrigger` (blindia/triggers/keyboard.py) sigue disponible como
respaldo basado en FIFO para testing sin el botón.
"""
import time

from arduino.app_utils import App, Logger

from blindia.audio.bluetooth_audio import hablar
from blindia.capture.service import CameraCaptureService
from blindia.config import CAPTURES_DIR, CameraSettings, OcrSettings, VisionFallbackSettings
from blindia.exceptions import BlindIAError
from blindia.ocr.service import OcrService
from blindia.pipeline import CapturePipeline
from blindia.product.parser import describe_product, ocr_tiene_sentido, parse_product
from blindia.product.vision_fallback import identificar_producto_remoto
from blindia.storage.image_store import ImageStore
from blindia.triggers.button import ButtonTrigger

logger = Logger(__name__)

camera_service = CameraCaptureService(CameraSettings())
image_store = ImageStore(CAPTURES_DIR)
pipeline = CapturePipeline(camera_service, image_store)
ocr_settings = OcrSettings()
ocr_service = OcrService(ocr_settings)
vision_fallback_settings = VisionFallbackSettings()
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
    """Corre una captura, le hace OCR, y parsea un producto del texto. La llama cada fuente de trigger.

    Loguea un desglose de tiempos por etapa (ms) -- instrumentación
    temporal para encontrar a dónde se va la latencia entre el botón y la
    voz.
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

    if not ocr_tiene_sentido(
        ocr_result.text,
        min_letras=ocr_settings.min_letras_para_tener_sentido,
        max_ratio_transiciones=ocr_settings.max_ratio_transiciones_capitalizacion,
    ):
        # El OCR no encontró nada de texto, o encontró muy poco como para
        # identificar un producto (ver `ocr_tiene_sentido`) -- no es el
        # caso "hay texto real pero no hay precio", que lo resuelve
        # parse_product más abajo sin llamar a ningún servidor. Acá sí vale
        # la pena el viaje de red al servidor de reconocimiento visual
        # remoto -- ver docs/DEVELOPMENT.md, "Reconocimiento de producto".
        t0 = time.perf_counter()
        mensaje_remoto = identificar_producto_remoto(result.frame, vision_fallback_settings)
        logger.info(f"[timing] vision_fallback: {(time.perf_counter() - t0) * 1000:.0f}ms")

        if mensaje_remoto is not None:
            logger.info(f"[{reason}] Identificado por respaldo remoto: {mensaje_remoto!r}")
            t0 = time.perf_counter()
            hablar(mensaje_remoto)
            logger.info(f"[timing] hablar_total (sintesis+IPC+reproduccion): {(time.perf_counter() - t0) * 1000:.0f}ms")
            logger.info(f"[timing] TOTAL: {(time.perf_counter() - t_inicio) * 1000:.0f}ms")
            return

        logger.warning(f"[{reason}] Respaldo remoto no disponible o falló -- usando mensaje de reserva.")

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


run_capture("startup")  # smoke test: confirma que el pipeline funciona antes de esperar triggers
App.run(user_loop=loop)
