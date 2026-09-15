"""Estructuras de datos compartidas entre los módulos de Blind IA."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np


@dataclass(frozen=True, eq=False)
class CaptureResult:
    """Resultado de una sola captura de imagen bajo demanda.

    Lleva tanto la ruta del archivo guardado (para módulos que leen de
    disco) como el frame en memoria (para que el módulo de OCR lo pueda
    reusar sin una lectura de disco redundante en el mismo proceso).
    """

    image_path: Path
    frame: np.ndarray
    captured_at: datetime


@dataclass(frozen=True, eq=False)
class OcrResult:
    """Resultado de correr OCR sobre un frame.

    `fragments` guarda los cuadros de texto individuales que encontró el
    motor (en orden de lectura, de arriba hacia abajo); `text` es la unión
    de esos fragmentos con espacios, como comodidad para cuando quien llama
    (ej. el módulo de audio) solo quiere un string.
    """

    text: str
    fragments: tuple[str, ...]


@dataclass(frozen=True, eq=False)
class RecognizedProduct:
    """Identificación de producto best-effort, parseada de `OcrResult.text`.

    `name` y `price` se extraen heurísticamente (ver
    `blindia.product.parser`) y pueden quedar en `None` si no matcheó nada.
    `raw_text` siempre se conserva para que quien llama (ej. el módulo de
    audio) pueda caer a leerlo textual cuando el parseo no encuentra nada.

    `price` se guarda como la subcadena impresa exacta (ej. "$1.549,90"),
    no parseada a un número: los separadores de miles/decimales dependen
    del locale, y leer mal un monto es peor que no parsearlo.
    """

    raw_text: str
    name: str | None
    price: str | None
