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
