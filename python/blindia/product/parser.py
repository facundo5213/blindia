"""Convierte texto crudo de OCR en una identificación de producto estructurada, best-effort.

Esta es toda la etapa de reconocimiento de producto del MVP: el texto
impreso de una etiqueta de precio (marca + nombre + precio) ya identifica
el producto en el caso común, así que no hay ningún modelo de
reconocimiento visual acá. Ver el README del proyecto ("Reconocimiento de
producto") para las alternativas que se evaluaron y se dejaron para más
adelante en vez de implementarlas.
"""
from __future__ import annotations

import re

from ..models import OcrResult, RecognizedProduct

# Matchea "$1.549,90", "$1549.90", "$5", etc. Se guarda como la subcadena
# impresa exacta (ver RecognizedProduct.price) en vez de normalizarla a un
# número, ya que los separadores de miles/decimales dependen del locale.
_PRICE_PATTERN = re.compile(r"\$\s?\d[\d.,]*")


def _ratio_transiciones_capitalizacion(texto: str) -> float:
    """Fracción de pares de letras consecutivas donde cambia mayúscula/minúscula.

    Texto real de producto (marca, ingredientes) está en MAYÚSCULA,
    Título, o minúscula -- nunca alternando letra por letra. Un ratio alto
    acá es la firma de un misread de OCR sobre una textura/patrón (ver
    docs/DEVELOPMENT.md, "Reconocimiento de producto", para el caso real
    que motivó esto: "sDpIpD" leído sobre la cáscara de una fruta).
    """
    letras = [c for c in texto if c.isalpha()]
    if len(letras) < 2:
        return 0.0
    transiciones = sum(1 for a, b in zip(letras, letras[1:]) if a.isupper() != b.isupper())
    return transiciones / (len(letras) - 1)


def ocr_tiene_sentido(texto: str, *, min_letras: int, max_ratio_transiciones: float) -> bool:
    """Decide si el texto detectado por OCR alcanza para identificar un producto.

    Un precio válido (`$...`) siempre "tiene sentido" sin importar cuán
    corto sea el resto del texto -- ej. `"$450"` son 4 caracteres pero es
    perfectamente útil. Si no hay precio, se aplican dos filtros:

    1. Cantidad de letras alfabéticas (no caracteres totales: dígitos/
       guiones/puntuación no cuentan) contra `min_letras`.
    2. Ratio de transiciones de capitalización entre letras consecutivas
       (ver `_ratio_transiciones_capitalizacion`): más del 50% marca texto
       sin sentido, sin importar cuántas letras tenga.

    Por qué estas dos señales y no score de confianza ni ratio de vocales
    (las dos alternativas probadas y descartadas con datos reales de esta
    placa): el score no separa señal de ruido (texto de fondo irrelevante
    salió con confianza tan alta como texto real de producto), y el ratio
    de vocales daba falsos positivos con marcas reales de supermercado
    ("KRAFT" 20% vocales, "McDonald's" 22%, ambas por debajo de cualquier
    corte razonable). Ver docs/DEVELOPMENT.md, "Reconocimiento de
    producto", para el proceso completo de validación (8 casos).
    """
    if _PRICE_PATTERN.search(texto):
        return True
    letras = sum(1 for c in texto if c.isalpha())
    if letras < min_letras:
        return False
    return _ratio_transiciones_capitalizacion(texto) <= max_ratio_transiciones


def parse_product(ocr_result: OcrResult) -> RecognizedProduct:
    """Extrae un nombre de producto y precio best-effort del texto de OCR.

    Solo heurística: `price` es el primer número con prefijo `$` encontrado
    en el texto; `name` es lo que queda después de sacar los fragmentos de
    precio. Cualquiera de los dos puede quedar en `None` si no matcheó nada.

    Cuando no se encuentra un precio, `name` termina siendo el texto
    completo del OCR (sin sacar nada, ya que no hay precio que remover) --
    leído textual por `describe_product` aunque sea largo. Se probó acá un
    nombre heurístico más corto (el fragmento individual más largo del
    OCR) para bajar el tiempo de `hablar()` en ese caso, pero se revirtió:
    leer todo lo que el OCR realmente vio se prefiere por sobre una
    adivinanza que puede elegir un fragmento engañoso, aunque cueste una
    respuesta hablada más larga.
    """
    text = ocr_result.text.strip()

    price_match = _PRICE_PATTERN.search(text)
    price = price_match.group(0).strip() if price_match else None

    name = re.sub(r"\s{2,}", " ", _PRICE_PATTERN.sub("", text)).strip(" -|") or None

    return RecognizedProduct(raw_text=text, name=name, price=price)


def describe_product(product: RecognizedProduct) -> str:
    """Convierte un `RecognizedProduct` en una frase apta para hablar con `hablar()`.

    Cae al texto crudo del OCR cuando no matcheó ni `name` ni `price`, ya
    que eso sigue siendo más útil para el usuario que quedarse en silencio.
    """
    if product.name and product.price:
        return f"{product.name}, {product.price}"
    if product.name:
        return product.name
    if product.price:
        return f"Precio: {product.price}"
    if product.raw_text:
        return f"No se identificó el producto. Texto detectado: {product.raw_text}"
    return "No se detectó texto en la imagen."
