"""Estado compartido mínimo entre los módulos de `blindia.backend`, para no acoplarlos entre sí.

Hoy solo guarda si el equipo está vinculado a una cuenta (`linked`, visto
por última vez en `GET /config`): `identifications.py` lo lee para saber
si tiene sentido seguir mandando productos después de un `409
device_not_linked`, y `remote_config.py` es quien lo actualiza en cada
encuesta de configuración exitosa. Evita que esos dos módulos se importen
entre sí solo para compartir este único dato.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()

# `None` = todavía no se sabe (ninguna encuesta de /config exitosa todavía
# en esta corrida) -- distinto de `False`, para no pausar el envío de
# identificaciones sin un 409 o un /config real que lo confirme.
_vinculado: bool | None = None


def establecer_vinculado(valor: bool) -> None:
    global _vinculado
    with _lock:
        _vinculado = valor


def esta_vinculado() -> bool | None:
    with _lock:
        return _vinculado
