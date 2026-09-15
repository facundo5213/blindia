"""Turns raw OCR text into a structured, best-effort product identification.

This is the MVP's whole product-recognition step: a price label's printed
text (brand + name + price) already identifies the product for the common
case, so there is no visual recognition model here. See the project README
("Product recognition") for the alternatives that were evaluated and
deferred instead of implemented.
"""
from __future__ import annotations

import re

from ..models import OcrResult, RecognizedProduct

# Matches "$1.549,90", "$1549.90", "$5", etc. Kept as the exact printed
# substring (see RecognizedProduct.price) instead of being normalized to a
# number, since thousands/decimal separators are locale-dependent.
_PRICE_PATTERN = re.compile(r"\$\s?\d[\d.,]*")


def parse_product(ocr_result: OcrResult) -> RecognizedProduct:
    """Extract a best-effort product name and price from OCR text.

    Heuristic only: `price` is the first `$`-prefixed number found in the
    text; `name` is what remains after removing price fragments. Either can
    end up `None` if nothing matched.

    When no price is found, `name` ends up being the full OCR text (minus
    nothing, since there's no price to strip) -- read verbatim by
    `describe_product` even if long. A shorter heuristic name was tried here
    (single longest OCR fragment) to cut down `hablar()` time in that case,
    but was reverted: reading everything the OCR actually saw is preferred
    over a guess that can pick a misleading fragment, even at the cost of a
    longer spoken response.
    """
    text = ocr_result.text.strip()

    price_match = _PRICE_PATTERN.search(text)
    price = price_match.group(0).strip() if price_match else None

    name = re.sub(r"\s{2,}", " ", _PRICE_PATTERN.sub("", text)).strip(" -|") or None

    return RecognizedProduct(raw_text=text, name=name, price=price)


def describe_product(product: RecognizedProduct) -> str:
    """Turn a `RecognizedProduct` into a spoken-friendly sentence for `hablar()`.

    Falls back to the raw OCR text when neither `name` nor `price` matched,
    since that's still more useful to the user than staying silent.
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
