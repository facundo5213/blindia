#!/usr/bin/env python3
"""Daemon de vinculación por USB: habla el protocolo de docs/VINCULACION-USB.md.

Corre en el HOST (no en el contenedor -- `/dev/ttyGS1` es un device node del
sistema, no algo que cruce el bind mount de la app), escuchando en el
segundo puerto CDC-ACM (`acm.GS1`/`/dev/ttyGS1`) que agrega
`scripts/usb_vinculacion_gadget_add.sh` vía el drop-in de systemd
`scripts/systemd/adbd-usb-vinculacion.conf` -- NO el puerto `acm.GS0`
(`/dev/ttyGS0`), que ya usa el monitor del MCU (`arduino-router-serial.service`).

Mismo patrón que `bluetooth_audio_daemon.py` / `server_ip_resolver.py`:
proceso aparte en el host, instalado como `systemd --user`, para que
arranque solo al bootear la placa.

Protocolo (texto plano, un comando por línea, ver docs/VINCULACION-USB.md
para la especificación completa):

    PING                        -> PONG blindia
    GET_SERIAL                  -> SERIAL <serial de hardware>
    GET_STATUS                  -> LINKED | NOT_LINKED
    SET_CREDENTIALS <json>      -> OK | ERROR <motivo>
    SET_PAIRING <codigo>        -> OK | ERROR <motivo>     (extensión v1.1, opcional)
    GET_PAIRING                 -> PAIRING <codigo> | ERROR sin codigo  (idem)

El puerto puede no existir todavía al arrancar el daemon (el gadget solo
se arma si hay un cable USB conectado a un host), y puede desaparecer y
volver a aparecer (desconectar/reconectar el cable) -- el daemon reintenta
abrirlo en un loop en vez de morir.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import termios
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from blindia.config import DEVICE_CREDENTIALS_FILE, DEVICE_PAIRING_FILE  # noqa: E402

logger = logging.getLogger("usb_vinculacion_daemon")

_PUERTO = "/dev/ttyGS1"
_REINTENTO_SEGUNDOS = 2.0
_SERIAL_HARDWARE_PATH = Path("/sys/devices/soc0/serial_number")

# Extensión v1.1: 8 caracteres alfanuméricos en mayúsculas (ver
# docs/VINCULACION-USB.md, "Extensión v1.1").
_CODIGO_PAIRING_RE = re.compile(r"^[A-Z0-9]{8}$")


def _leer_serial_hardware() -> str:
    """Mismo identificador que ya usa el propio gadget USB como serialnumber (ver adbd-usb-gadget)."""
    return _SERIAL_HARDWARE_PATH.read_text().strip()


def _escribir_credenciales(serial: str, clave: str) -> None:
    """Escribe serial+clave de forma atómica (write + rename), igual patrón que server_ip_resolver.py."""
    DEVICE_CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    archivo_temporal = DEVICE_CREDENTIALS_FILE.with_suffix(".tmp")
    archivo_temporal.write_text(json.dumps({"serial": serial, "clave": clave}))
    archivo_temporal.replace(DEVICE_CREDENTIALS_FILE)


def _escribir_pairing(codigo: str) -> None:
    """Escribe el código de enlace de forma atómica -- mismo patrón y mismos permisos que la clave."""
    DEVICE_PAIRING_FILE.parent.mkdir(parents=True, exist_ok=True)
    archivo_temporal = DEVICE_PAIRING_FILE.with_suffix(".tmp")
    archivo_temporal.write_text(codigo)
    archivo_temporal.replace(DEVICE_PAIRING_FILE)


def _atender_comando(linea: str) -> str:
    """Procesa una línea de comando y devuelve la línea de respuesta (sin el \\n final)."""
    partes = linea.strip().split(" ", 1)
    comando = partes[0] if partes else ""
    resto = partes[1] if len(partes) > 1 else ""

    if comando == "PING":
        return "PONG blindia"

    if comando == "GET_SERIAL":
        try:
            return f"SERIAL {_leer_serial_hardware()}"
        except OSError as exc:
            logger.error(f"No se pudo leer el serial de hardware: {exc}")
            return "ERROR no se pudo leer el serial de hardware"

    if comando == "GET_STATUS":
        return "LINKED" if DEVICE_CREDENTIALS_FILE.exists() else "NOT_LINKED"

    if comando == "SET_CREDENTIALS":
        try:
            datos = json.loads(resto)
        except json.JSONDecodeError as exc:
            return f"ERROR JSON invalido: {exc}"

        serial_recibido = datos.get("serial")
        clave = datos.get("clave")
        if not serial_recibido or not clave:
            return "ERROR faltan campos 'serial' o 'clave'"

        try:
            serial_real = _leer_serial_hardware()
        except OSError as exc:
            logger.error(f"No se pudo leer el serial de hardware: {exc}")
            return "ERROR no se pudo leer el serial de hardware"

        if serial_recibido != serial_real:
            return f"ERROR el serial recibido ({serial_recibido!r}) no coincide con el de hardware ({serial_real!r})"

        try:
            _escribir_credenciales(serial_real, clave)
        except OSError as exc:
            logger.error(f"No se pudieron escribir las credenciales: {exc}")
            return "ERROR no se pudieron escribir las credenciales"

        logger.info("Credenciales de vinculacion escritas correctamente.")
        return "OK"

    # Extensión v1.1 (opcional, ver docs/VINCULACION-USB.md) -- no toca ni
    # depende de PING/GET_SERIAL/GET_STATUS/SET_CREDENTIALS de ninguna
    # manera: el código de enlace vive en su propio archivo, nunca se usa
    # para hablar con la API (sigue siendo serial + clave).
    if comando == "SET_PAIRING":
        codigo = resto.strip()
        if not _CODIGO_PAIRING_RE.fullmatch(codigo):
            return "ERROR codigo invalido: debe ser de 8 caracteres alfanumericos en mayusculas"

        try:
            _escribir_pairing(codigo)
        except OSError as exc:
            logger.error(f"No se pudo escribir el codigo de enlace: {exc}")
            return "ERROR no se pudo escribir el codigo de enlace"

        logger.info("Codigo de enlace guardado correctamente.")
        return "OK"

    if comando == "GET_PAIRING":
        if not DEVICE_PAIRING_FILE.exists():
            return "ERROR sin codigo"
        return f"PAIRING {DEVICE_PAIRING_FILE.read_text().strip()}"

    # Comando desconocido: nunca cierra el puerto ni reinicia nada -- solo
    # devuelve este error y sigue atendiendo pedidos (ver "Comportamiento y
    # casos borde" de la extensión v1.1, que exige exactamente esto para
    # firmware viejo frente a comandos nuevos que todavía no conoce).
    return f"ERROR comando desconocido: {comando!r}"


def _atender_puerto(fd: int) -> None:
    """Lee líneas del puerto ya abierto y despacha comandos hasta que se corte la conexión."""
    buffer = b""
    while True:
        fragmento = os.read(fd, 256)
        if not fragmento:  # EOF: el host cerró el puerto (desconectó el cable)
            return
        buffer += fragmento
        while b"\n" in buffer:
            linea, buffer = buffer.split(b"\n", 1)
            texto = linea.decode("utf-8", errors="replace").strip()
            if not texto:
                continue
            logger.debug(f"-> {texto}")
            respuesta = _atender_comando(texto)
            logger.debug(f"<- {respuesta}")
            os.write(fd, (respuesta + "\n").encode("utf-8"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info(f"Escuchando en {_PUERTO} (protocolo de vinculacion USB)")

    while True:
        if not os.path.exists(_PUERTO):
            time.sleep(_REINTENTO_SEGUNDOS)
            continue

        fd = None
        try:
            # O_NOCTTY: no convertir este tty en la terminal de control del
            # proceso (evita hangups/señales inesperadas).
            fd = os.open(_PUERTO, os.O_RDWR | os.O_NOCTTY)
            tty.setraw(fd)  # sin eco, sin traducción CR/LF, byte a byte
            # Descarta cualquier byte que haya llegado ANTES de que el modo
            # raw quedara aplicado -- confirmado en vivo: sin esto, la
            # primera línea que lee el cliente puede ser un eco de lo que
            # él mismo mandó, en vez de la respuesta real (ver
            # docs/VINCULACION-USB.md).
            termios.tcflush(fd, termios.TCIFLUSH)
            logger.info(f"{_PUERTO} conectado -- atendiendo pedidos.")
            _atender_puerto(fd)
            logger.info(f"{_PUERTO} desconectado -- esperando reconexion.")
        except OSError as exc:
            logger.warning(f"No se pudo abrir/leer {_PUERTO}: {exc}")
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            time.sleep(_REINTENTO_SEGUNDOS)


if __name__ == "__main__":
    main()
