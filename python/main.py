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
from blindia.backend import identifications, remote_config, telemetry
from blindia.capture.service import CameraCaptureService
from blindia.config import ApiSettings, CAPTURES_DIR, CameraSettings, OcrSettings, VisionFallbackSettings
from blindia.exceptions import BlindIAError
from blindia.ocr.service import OcrService
from blindia.pipeline import CapturePipeline
from blindia.product.parser import describe_product, extraer_price_ars, ocr_tiene_sentido, parse_product
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
api_settings = ApiSettings()
trigger = ButtonTrigger()

try:
    camera_service.start()
except BlindIAError as exc:
    logger.error(f"Camera did not start: {exc}")

try:
    ocr_service.start()
except BlindIAError as exc:
    logger.error(f"OCR engine did not start: {exc}")

# Telemetría, cola de identificaciones y configuración remota corren en
# hilos de fondo propios, nunca en el hilo del pipeline del botón -- el
# uso diario del anteojo no puede depender de la web (ver
# docs/API-DISPOSITIVO.md, punto 8). Si el equipo todavía no está
# vinculado (sin data/device_credentials.json), estos tres simplemente no
# mandan nada hasta que lo vinculen por USB (ver docs/VINCULACION-USB.md).
telemetry.iniciar_loop_en_segundo_plano(api_settings)
identifications.iniciar_loop_en_segundo_plano(api_settings)
remote_config.iniciar_loop_en_segundo_plano(api_settings)


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
        resultado_remoto = identificar_producto_remoto(result.frame, vision_fallback_settings)
        logger.info(f"[timing] vision_fallback: {(time.perf_counter() - t0) * 1000:.0f}ms")

        if resultado_remoto is not None:
            logger.info(f"[{reason}] Identificado por respaldo remoto: {resultado_remoto.mensaje_voz!r}")

            # Solo se reporta a la web cuando el servidor identificó algo
            # concreto -- una lectura fallida (el servidor no pudo ver el
            # producto) no es una identificación real, no se manda.
            # uad_cost=1: a diferencia del camino de OCR local, esta
            # identificación la resolvió el servidor de IA remoto.
            if resultado_remoto.producto:
                identifications.encolar_identificacion(
                    resultado_remoto.producto,
                    resultado_remoto.price_ars,
                    resultado_remoto.confianza,
                    settings=api_settings,
                    uad_cost=1,
                )

            t0 = time.perf_counter()
            hablar(resultado_remoto.mensaje_voz)
            logger.info(f"[timing] hablar_total (sintesis+IPC+reproduccion): {(time.perf_counter() - t0) * 1000:.0f}ms")
            logger.info(f"[timing] TOTAL: {(time.perf_counter() - t_inicio) * 1000:.0f}ms")
            return

        logger.warning(f"[{reason}] Respaldo remoto no disponible o falló -- usando mensaje de reserva.")

    t0 = time.perf_counter()
    product = parse_product(ocr_result)
    t_reconocimiento = (time.perf_counter() - t0) * 1000
    logger.info(f"[{reason}] Recognized product: name={product.name!r} price={product.price!r}")
    logger.info(f"[timing] reconocimiento_producto: {t_reconocimiento:.0f}ms")

    # Encolar es solo escritura local (ver blindia.backend.identifications) --
    # nunca espera a la red, así que no puede atrasar la respuesta hablada.
    # Solo se reporta a la web cuando se identificó algo real (nombre y/o
    # precio) -- una lectura fallida (sin nombre ni precio) no es una
    # identificación, no se manda. uad_cost=0: a diferencia del respaldo
    # remoto, esta identificación la resolvió el OCR local, sin IA.
    if product.name or product.price:
        t0 = time.perf_counter()
        identifications.encolar_identificacion(
            product.name or product.raw_text,
            extraer_price_ars(product.price),
            None,  # sin una señal de confianza propia en este camino (ver blindia.product.parser)
            settings=api_settings,
            uad_cost=0,
        )
        logger.info(f"[timing] encolar_identificacion: {(time.perf_counter() - t0) * 1000:.0f}ms")

    t0 = time.perf_counter()
    mensaje = describe_product(product, detail_level=remote_config.nivel_detalle_actual())
    hablar(mensaje)  # bloquea hasta que termina la reproducción (ver bluetooth_audio.py)
    t_hablar = (time.perf_counter() - t0) * 1000
    logger.info(f"[timing] hablar_total (sintesis+IPC+reproduccion): {t_hablar:.0f}ms")

    t_total = (time.perf_counter() - t_inicio) * 1000
    logger.info(f"[timing] TOTAL: {t_total:.0f}ms")


def loop() -> None:
    trigger.wait()
    run_capture("button")


run_capture("startup")  # smoke test: confirma que el pipeline funciona antes de esperar triggers
App.run(user_loop=loop)
