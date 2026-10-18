#!/usr/bin/env python3
"""Resolutor de mDNS para el servidor de reconocimiento visual remoto.

Por qué existe: `main.py` corre dentro de un contenedor Docker cuyo
resolver de DNS está aislado del host -- confirmado en vivo que
`docker exec ... getent hosts blindia-server.local` falla aunque el mismo
comando funciona perfecto corriendo directo en el host (el `/etc/nsswitch.conf`
del contenedor no tiene `mdns4`, y su `/etc/resolv.conf` apunta al DNS
embebido de Docker, que reenvía a los DNS del ISP, nunca al resolver mDNS
del host -- y aunque resolviera DNS normal, mDNS necesita multicast en la
red local, que no cruza el bridge de Docker sin configuración de red
especial). Ver docs/DEVELOPMENT.md, "Reconocimiento de producto", para el
diagnóstico completo.

Este script corre directamente en el sistema operativo del host (mismo
patrón que `bluetooth_audio_daemon.py`, aunque separado de ese porque el
alcance es distinto -- resolución de nombres, no audio/Bluetooth), resuelve
`VisionFallbackSettings.host` cada `_INTERVALO_SEGUNDOS`, y escribe la IP
resultante en `SERVER_IP_FILE`. Esa carpeta ya está bind-mounteada dentro
del contenedor como `/app/data`, así que `blindia/product/vision_fallback.py`
solo necesita leer ese archivo, sin resolver nada él mismo.

Si la resolución falla (PC apagada, fuera de la red, etc.) el archivo NO se
toca -- mejor una IP vieja que ninguna, ya que `vision_fallback.py` de
cualquier forma tiene su propio timeout corto y cae al mensaje de reserva
si esa IP ya no es válida.

Se instala como servicio `systemd --user` (ver
`scripts/systemd/blindia-server-ip-resolver.service`) para que arranque
solo al bootear la placa, igual que el daemon de audio Bluetooth.
"""
from __future__ import annotations

import logging
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from blindia.config import SERVER_IP_FILE, VisionFallbackSettings  # noqa: E402

logger = logging.getLogger("server_ip_resolver")

_INTERVALO_SEGUNDOS = 30.0


def _resolver_ip(nombre_mdns: str) -> str | None:
    """Resuelve `nombre_mdns` a una IP, o `None` si falla. Nunca lanza."""
    try:
        return socket.gethostbyname(nombre_mdns)
    except OSError as exc:
        logger.warning(f"No se pudo resolver {nombre_mdns!r}: {exc}")
        return None


def _escribir_ip(ip: str) -> None:
    """Escribe `ip` en SERVER_IP_FILE de forma atómica (write + rename)."""
    SERVER_IP_FILE.parent.mkdir(parents=True, exist_ok=True)
    archivo_temporal = SERVER_IP_FILE.with_suffix(".tmp")
    archivo_temporal.write_text(ip + "\n")
    archivo_temporal.replace(SERVER_IP_FILE)  # rename es atómico en POSIX


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    nombre_mdns = VisionFallbackSettings().host
    logger.info(f"Resolviendo {nombre_mdns!r} cada {_INTERVALO_SEGUNDOS:.0f}s -> {SERVER_IP_FILE}")

    ultima_ip: str | None = None
    while True:
        ip = _resolver_ip(nombre_mdns)
        if ip is not None and ip != ultima_ip:
            _escribir_ip(ip)
            logger.info(f"IP de {nombre_mdns!r} actualizada: {ip}")
            ultima_ip = ip
        elif ip is None:
            logger.debug(f"Resolución falló -- se deja el último valor conocido ({ultima_ip!r}) sin tocar.")
        time.sleep(_INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    main()
