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
    """

    source: str | int | None = None
    resolution: tuple[int, int] = (1280, 720)
    fps: int = 10
    jpeg_quality: int = 95


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
