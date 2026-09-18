"""Configuración central de la app Blind IA.

Todos los módulos (captura, y luego OCR/reconocimiento/audio) deberían leer
su configuración de acá en vez de hardcodear valores, para que toda la app
se pueda ajustar desde un solo lugar.
"""
from dataclasses import dataclass
from pathlib import Path

# .../blindia/python/blindia/config.py -> parents[2] es la raíz de la app (.../blindia)
APP_ROOT: Path = Path(__file__).resolve().parents[2]

# Dónde se guardan las fotos capturadas. Vive bajo la raíz de la app para
# que quede persistido en el host vía el bind mount de la app (ver
# .cache/app-compose.yaml).
CAPTURES_DIR: Path = APP_ROOT / "data" / "captures"

# FIFO usado por el trigger temporal de teclado (blindia.triggers.keyboard).
# Escribir cualquier cosa a esta ruta desde el host dispara una captura.
TRIGGER_FIFO_PATH: Path = APP_ROOT / "data" / "trigger"


@dataclass(frozen=True)
class CameraSettings:
    """Configuración del periférico de cámara.

    Attributes:
        source: Identificador de cámara pasado a `Camera(...)` (ej.
            "usb:0", "/dev/video2", un índice simple). `None` deja que la
            plataforma auto-seleccione la primera cámara encontrada (USB
            tiene prioridad sobre CSI), que es el default correcto para
            una sola webcam USB genérica.
        resolution: Resolución de captura como (ancho, alto). Elegida más
            alta que el default de la plataforma (640x480) ya que las
            etiquetas de precio necesitan suficiente detalle para el OCR
            después.
        fps: Cuadros por segundo con los que se configura el stream de la
            cámara. 10 es el máximo que soporta la webcam detectada
            (Logitech C525) a 720p; pedir más solo loguea una advertencia
            inofensiva.
        jpeg_quality: Calidad JPEG (0-100) usada al persistir las capturas.
        frames_de_calentamiento: Cuántos frames descartar (leer y tirar)
            justo antes del frame real que se guarda en cada captura. La
            cámara queda abierta entre apretones del botón, a veces varios
            segundos inactiva -- el driver V4L2/UVC puede devolver en el
            primer `read()` un frame viejo, de antes del período inactivo,
            aunque `CAP_PROP_BUFFERSIZE` esté en 1 (no todos los drivers lo
            respetan). Ver docs/DEVELOPMENT.md, "Bug: anuncia el producto
            anterior", para el diagnóstico completo.
    """

    source: str | int | None = None
    resolution: tuple[int, int] = (1280, 720)
    fps: int = 10
    jpeg_quality: int = 95
    frames_de_calentamiento: int = 2


@dataclass(frozen=True)
class OcrSettings:
    """Configuración del motor de OCR (blindia.ocr.service.OcrService).

    Attributes:
        use_cls: Si correr el clasificador de orientación de texto antes
            del reconocimiento. Deshabilitado por defecto: las etiquetas de
            precio se capturan derechas, y saltear esta etapa reduce
            medible la latencia en la CPU de esta placa (~2-3s/imagen de
            cualquier manera, sin NPU disponible).
        min_text_score: Umbral de confianza de reconocimiento (0-1) por
            debajo del cual se descarta un fragmento de texto, para filtrar
            ruido de baja confianza recogido del fondo de una foto de
            góndola.
    """

    use_cls: bool = False
    min_text_score: float = 0.5

    # Umbral usado por `blindia.product.parser.ocr_tiene_sentido()` para
    # decidir si el texto detectado alcanza para identificar un producto,
    # o si conviene ir directo al respaldo remoto de reconocimiento visual
    # (ver `VisionFallbackSettings`). Se mide en letras alfabéticas, no en
    # caracteres totales, para no penalizar detecciones cortas pero reales
    # (ej. "adidas", 6 letras) igual que ruido sin letras (ej. "01", "-",
    # 0 letras) -- ver docs/DEVELOPMENT.md, "Reconocimiento de producto",
    # para los datos reales de esta placa que calibraron este número.
    min_letras_para_tener_sentido: int = 4

    # Segundo filtro de `ocr_tiene_sentido()`: por encima de este ratio de
    # transiciones mayúscula/minúscula entre letras consecutivas, el texto
    # se considera ruido (ej. "sDpIpD" leído sobre una textura) aunque
    # tenga letras de sobra. Texto real de producto nunca alterna case
    # letra por letra -- ver docs/DEVELOPMENT.md, "Reconocimiento de
    # producto", para los 8 casos que calibraron este número (incluye por
    # qué se descartó un chequeo de ratio de vocales en su lugar).
    max_ratio_transiciones_capitalizacion: float = 0.5


@dataclass(frozen=True)
class VisionFallbackSettings:
    """Configuración del respaldo remoto de reconocimiento visual.

    Se usa SOLO cuando el OCR local (RapidOCR) no detecta ningún fragmento
    de texto en absoluto -- ver `blindia.product.vision_fallback` y
    docs/DEVELOPMENT.md, sección "Reconocimiento de producto", para el
    porqué de esta arquitectura (servidor propio en la PC del usuario en
    vez de un respaldo en la nube).

    Attributes:
        host: IP de la PC del usuario en la red WiFi local. Dinámica (no
            fija) -- hay que actualizar este valor a mano cada vez que
            cambie tras reconectarse a la red.
        port: Puerto del servidor FastAPI (no el de LM Studio -- ese es
            interno al servidor, esta app nunca le habla directo).
        timeout_segundos: Timeout corto a propósito: si el servidor no
            está prendido o tarda demasiado, el pipeline tiene que caer al
            mensaje de reserva sin colgarse.
    """

    host: str = "192.168.0.211"  # ACTUALIZAR: IP actual de la PC en la red WiFi
    port: int = 8000
    timeout_segundos: float = 8.0
