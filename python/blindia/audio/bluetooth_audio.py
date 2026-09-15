"""Cliente del daemon de audio Bluetooth: la puerta de entrada para el resto del sistema.

Módulo independiente: no importa nada de `capture/`, `ocr/`, `product/` ni
`main.py`, y ninguno de esos módulos necesita saber si la voz sale por
Bluetooth o por el parlante de la placa. La única puerta de entrada pensada
para el resto del sistema es `hablar(texto)`.

Toda la lógica real (bluetoothctl, wpctl/pw-dump, espeak-ng) NO vive acá:
vive en `scripts/bluetooth_audio_daemon.py`, un proceso aparte que corre en
el HOST (fuera de cualquier contenedor), porque el contenedor donde corre
`main.py` no tiene acceso a esas herramientas ni al D-Bus del sistema (ver
el README, sección "Audio Bluetooth", para el porqué). Este archivo es
apenas un cliente delgado: manda un pedido por un socket Unix
(`data/bluetooth_audio.sock`, visible dentro del contenedor como
`/app/data/bluetooth_audio.sock` porque la carpeta de la app ya está
bind-mounteada ahí) y devuelve la respuesta.

Decisión de diseño (a diferencia de `capture/` y `ocr/`, que sí definen su
propia jerarquía de excepciones): las funciones de este módulo nunca lanzan
hacia afuera. Loguean y devuelven `bool`/`None` porque este módulo *es* la
capa que anuncia errores por voz para todo el sistema -- un daemon que no
responde es un caso más de fallo silencioso a nivel función (se loguea, no
se relanza), igual que ya era "espeak-ng no instalado" antes de que existiera
el daemon.
"""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time

from arduino.app_utils import Logger

from ..config import APP_ROOT

logger = Logger(__name__)

SOCKET_PATH = APP_ROOT / "data" / "bluetooth_audio.sock"

# Reintentos de conexión con backoff exponencial: el daemon (systemd --user)
# puede no estar listo todavía justo después de un reinicio de la placa,
# porque arranca en paralelo con esta app -- no con un orden garantizado
# entre ambos. 6 intentos con espera 0.2/0.4/0.8/1.6/3.2/6.4s cubren varios
# segundos de margen sin bloquear para siempre si el daemon realmente no
# está corriendo.
_REINTENTOS_CONEXION = 6
_ESPERA_INICIAL_SEGUNDOS = 0.2
_FACTOR_BACKOFF = 2.0
_ESPERA_MAXIMA_SEGUNDOS = 6.0

# Debe cubrir el peor caso del daemon, no solo una frase típica: texto
# extremadamente largo (ver _LARGO_EXTREMO_CARACTERES en el daemon) sin
# resumir implica aviso previo + síntesis completa + reproducción completa +
# un posible reintento por el parlante local -- todo eso puede sumar más de
# un minuto para un texto realmente largo, y no hay que cortarlo del lado
# cliente si el propio daemon ya se tomó el trabajo de no cortarlo.
_TIMEOUT_HABLAR_SEGUNDOS = 180.0
_TIMEOUT_CONSULTA_SEGUNDOS = 5.0  # auriculares_conectados / configurar_salida_default
_TIMEOUT_EMPAREJAR_SEGUNDOS = 90.0  # escaneo + pair + connect, con margen


def _conectar() -> socket.socket | None:
    """Conecta al daemon con reintentos y backoff exponencial, o devuelve None."""
    espera = _ESPERA_INICIAL_SEGUNDOS
    for intento in range(1, _REINTENTOS_CONEXION + 1):
        try:
            conexion = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            conexion.settimeout(_TIMEOUT_CONSULTA_SEGUNDOS)
            conexion.connect(str(SOCKET_PATH))
            return conexion
        except OSError as exc:
            if intento == _REINTENTOS_CONEXION:
                logger.error(
                    f"[bluetooth] No se pudo conectar al daemon de audio Bluetooth tras "
                    f"{_REINTENTOS_CONEXION} intentos ({SOCKET_PATH}): {exc}. "
                    "¿Está corriendo 'systemctl --user status blindia-bluetooth-audio' en la placa?"
                )
                return None
            time.sleep(espera)
            espera = min(espera * _FACTOR_BACKOFF, _ESPERA_MAXIMA_SEGUNDOS)
    return None


def _pedir(comando: dict, *, timeout: float) -> dict | None:
    """Manda `comando` como una línea JSON al daemon y devuelve su respuesta, o None si algo falló.

    Nunca lanza: cualquier problema de conexión/protocolo queda logueado
    acá, para que cada función pública decida su propio valor de reserva
    sin necesitar try/except del lado de quien la llama.
    """
    conexion = _conectar()
    if conexion is None:
        return None
    try:
        conexion.settimeout(timeout)
        conexion.sendall((json.dumps(comando) + "\n").encode("utf-8"))
        conexion.shutdown(socket.SHUT_WR)
        partes = []
        while True:
            fragmento = conexion.recv(4096)
            if not fragmento:
                break
            partes.append(fragmento)
        return json.loads(b"".join(partes).decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(f"[bluetooth] Error hablando con el daemon de audio Bluetooth: {exc}")
        return None
    finally:
        conexion.close()


# =============================================================================
# API pública (uso desde el resto del sistema)
# =============================================================================


def hablar(texto: str) -> None:
    """Sintetiza `texto` por voz, decidiendo sola la salida correcta.

    El resto del sistema llama solo a esta función y no necesita enterarse
    de nada de esto -- ni de Bluetooth, ni del daemon, ni de qué pasa si
    algo falla.
    """
    respuesta = _pedir({"cmd": "hablar", "texto": texto}, timeout=_TIMEOUT_HABLAR_SEGUNDOS)
    if respuesta is None:
        # Único caso realmente silencioso posible: sin daemon no queda
        # ningún camino de audio (ni Bluetooth ni parlante local -- ver
        # README, "Por qué el daemon es un punto único de fallo"). Se deja
        # bien visible en los logs de la app como compensación.
        logger.error(f"[voz] No se pudo anunciar por voz (daemon no disponible): {texto!r}")


def auriculares_conectados() -> bool:
    """True si hay un auricular Bluetooth activo como salida de audio ahora mismo."""
    respuesta = _pedir({"cmd": "auriculares_conectados"}, timeout=_TIMEOUT_CONSULTA_SEGUNDOS)
    return bool(respuesta and respuesta.get("conectado"))


def configurar_salida_default() -> bool:
    """Le pide al daemon que reconfigure la salida de audio por defecto ahora mismo.

    `hablar()` ya hace esto antes de cada síntesis; se expone acá aparte
    solo para chequeos manuales (ver README, "Testing on the real board").
    """
    respuesta = _pedir({"cmd": "configurar_salida_default"}, timeout=_TIMEOUT_CONSULTA_SEGUNDOS)
    return bool(respuesta and respuesta.get("bluetooth"))


def emparejar_auriculares(nombre_objetivo: str | None = None, *, timeout_escaneo: float | None = None) -> bool:
    """Empareja auriculares Bluetooth guiando cada paso por voz (ver el daemon para el detalle del flujo).

    Nunca lanza una excepción: cualquier fallo (incluido que el daemon no
    responda) hace que devuelva False, para que tanto el modo
    `--emparejar` como el futuro hook del botón físico puedan usarla sin
    try/except.
    """
    comando: dict = {"cmd": "emparejar", "nombre_objetivo": nombre_objetivo}
    if timeout_escaneo is not None:
        comando["timeout_escaneo"] = timeout_escaneo

    respuesta = _pedir(comando, timeout=_TIMEOUT_EMPAREJAR_SEGUNDOS)
    if respuesta is None:
        logger.error("[bluetooth] El daemon de audio Bluetooth no respondió al pedido de emparejamiento.")
        return False
    return bool(respuesta.get("emparejado"))


def iniciar_emparejamiento_en_segundo_plano(nombre_objetivo: str | None = None) -> None:
    """Punto de enganche pensado para la pulsación larga del botón físico.

    Corre `emparejar_auriculares` en un hilo aparte para no bloquear el loop
    principal de la app: el escaneo puede tardar decenas de segundos. Todo
    el feedback del proceso ya sale por voz desde el daemon, así que no
    hace falta esperar ni devolver nada acá.
    """
    threading.Thread(target=emparejar_auriculares, args=(nombre_objetivo,), daemon=True).start()


# =============================================================================
# Comando manual de prueba: `python3 -m blindia.audio.bluetooth_audio --emparejar`
# =============================================================================


def _main() -> None:
    parser = argparse.ArgumentParser(description="Utilidades de audio Bluetooth para BlindIA.")
    parser.add_argument("--emparejar", action="store_true", help="Inicia el emparejamiento guiado por voz.")
    parser.add_argument(
        "--nombre",
        default=None,
        help="Filtra el escaneo por nombre del dispositivo (subcadena, sin distinguir mayúsculas).",
    )
    args = parser.parse_args()

    if args.emparejar:
        exito = emparejar_auriculares(nombre_objetivo=args.nombre)
        raise SystemExit(0 if exito else 1)

    parser.print_help()


if __name__ == "__main__":
    _main()
