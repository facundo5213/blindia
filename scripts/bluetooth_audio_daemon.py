#!/usr/bin/env python3
"""Daemon de audio Bluetooth de BlindIA: corre en el HOST, fuera de cualquier contenedor.

Por qué existe: `main.py` corre dentro de un contenedor Docker
(`ghcr.io/arduino/app-bricks/python-apps-base`) que no tiene acceso a
`bluetoothctl`, `wpctl` ni `espeak-ng`, ni al socket D-Bus del sistema donde
vive `bluetoothd` -- se confirmó en vivo que `arduino-app-cli` no ofrece,
en esta versión, ninguna forma de darle a un contenedor de app ese acceso
(ni por `app.yaml`, ni por bricks, ni por el mecanismo de "compose
overrides" -- ver el README, sección "Audio Bluetooth", para el detalle de
lo que se probó). Este script tiene toda la lógica real (la misma que tenía
antes `blindia/audio/bluetooth_audio.py`, sin cambios de comportamiento) y
corre directamente en el sistema operativo del host, donde esas
herramientas sí están disponibles.

Se comunica con el contenedor por un socket Unix en
`data/bluetooth_audio.sock`. No hace falta ningún montaje nuevo para eso:
la carpeta de la app entera ya está bind-mounteada dentro del contenedor
como `/app` (el mismo mecanismo que ya usa el FIFO de
`blindia/triggers/keyboard.py`), así que un socket creado acá en
`data/bluetooth_audio.sock` aparece solo, del otro lado, como
`/app/data/bluetooth_audio.sock`.

Se instala como servicio `systemd --user` (ver
`scripts/systemd/blindia-bluetooth-audio.service` y el README) para que
arranque solo al bootear la placa y se reinicie solo si se cae --
imprescindible acá porque, a diferencia del contenedor, este proceso es el
ÚNICO camino de audio que tiene la app: si no corre, ni la salida por
Bluetooth ni el respaldo al parlante local funcionan (`espeak-ng` tampoco
está instalado dentro del contenedor).
"""
from __future__ import annotations

import json
import logging
import queue
import re
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

# `blindia.config` no depende de nada específico del framework de Arduino
# (solo dataclasses/pathlib), así que este script -- que corre con el
# python3 de `.venv-piper` (ver más abajo), no con el venv del contenedor --
# puede importarlo igual, para no duplicar dónde vive `data/` en dos lugares
# distintos.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from blindia.config import APP_ROOT  # noqa: E402

logger = logging.getLogger("bluetooth_audio_daemon")

SOCKET_PATH = APP_ROOT / "data" / "bluetooth_audio.sock"

# --- Configuración del motor de voz -----------------------------------------

_ESPEAK_BIN = "espeak-ng"
_VOZ = "es"  # voz en español (Castilian); ver README si hace falta otra variante
_PALABRAS_POR_MINUTO = 140  # bastante más lento que el default (175) para claridad
_PAUSA_ENTRE_PALABRAS = 6  # unidades de 10ms; un poco más de aire entre palabras

# Piso del timeout para una frase típica; para texto largo se escala con una
# estimación conservadora de segundos por caracter (más lenta que el ritmo
# real a 140wpm, a propósito, para no cortar un texto legítimamente largo --
# ver _LARGO_EXTREMO_CARACTERES y el mismo criterio en _PIPER_TIMEOUT_SEGUNDOS).
_TIMEOUT_SINTESIS_SEGUNDOS = 30.0
_SEGUNDOS_POR_CARACTER_ESPEAK = 0.15

# --- Configuración del motor de voz principal (Piper), con espeak-ng como
#     respaldo si Piper no está disponible o falla -- ver README, sección
#     "Voz: Piper TTS", para el porqué del modelo y del venv separado.

try:
    from piper import PiperVoice, SynthesisConfig
except ImportError:  # el venv no tiene piper-tts instalado, o el daemon está
    PiperVoice = None  # corriendo con otro intérprete -- se cae a espeak-ng
    SynthesisConfig = None

_PIPER_MODELO = APP_ROOT / "data" / "piper" / "es_ES-davefx-medium.onnx"
_PIPER_LENGTH_SCALE = 1.35  # >1.0 = más lento que el ritmo nativo del modelo
_PIPER_SENTENCE_SILENCE = 0.3  # segundos de silencio al final de cada frase

# Piso del timeout de REPRODUCCIÓN (no de síntesis, que no tiene timeout --
# ver _reproducir_con_piper): alcanza de sobra para una frase típica ya con
# el modelo precargado (~1-3s). Para texto largo (ver _LARGO_EXTREMO_CARACTERES
# más abajo) el timeout real usado es más alto, calculado a partir de la
# duración real del WAV sintetizado -- nunca un tope fijo que pueda cortar
# un audio legítimamente largo a mitad de camino (eso pasó con un texto de
# ~500 caracteres: se cortó a los 20s con el audio todavía sonando).
_PIPER_TIMEOUT_SEGUNDOS = 20.0
_MARGEN_TIMEOUT_REPRODUCCION_SEGUNDOS = 5.0  # margen sobre la duración real del clip
_TIMEOUT_REPRODUCCION_MAXIMO_SEGUNDOS = 300.0  # tope absoluto: audio realmente colgado, no solo largo

# Por encima de este largo, el texto ya no es lo que mide una etiqueta de
# producto real (es más bien un envase entero o ruido de OCR mezclado con
# el entorno) -- no se resume ni se descarta (ver README: se probó y se
# revirtió a pedido), pero sí se avisa por voz ANTES de arrancar la síntesis
# larga, para que nunca parezca que el sistema no respondió nada.
_LARGO_EXTREMO_CARACTERES = 300
_AVISO_TEXTO_LARGO = "Detecté mucho texto, puede tardar un momento."

# Objeto `PiperVoice` cacheado: cargar el modelo tarda ~11-15s (una sola vez
# al arrancar el daemon), pero sintetizar con el modelo ya en memoria tarda
# ~1-3s por frase típica -- por eso se carga una sola vez acá, nunca por
# request. Ver `_voz_piper()`.
_voz_piper_cache: "PiperVoice | None" = None
_voz_piper_intentado = False

# --- Configuración del emparejamiento Bluetooth -----------------------------

_TIMEOUT_ESCANEO_SEGUNDOS = 20.0
_TIMEOUT_PASO_SEGUNDOS = 15.0

# Cadenas (en minúsculas) que indican que un dispositivo escaneado ofrece
# algún perfil de audio, para no emparejar por error con un celular o mouse
# que también esté visible durante el escaneo.
_UUIDS_AUDIO = ("audio sink", "audio source", "handsfree")

# bluetoothctl colorea su salida con códigos ANSI (ej. "[\x1b[0;92mNEW\x1b[0m]")
# incluso cuando stdout no es una TTY, así que hay que limpiarlos antes de
# aplicar cualquiera de los regex de abajo o nunca matchean nada.
_PATRON_ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

_PATRON_DISPOSITIVO_NUEVO = re.compile(r"\[NEW\] Device ([0-9A-F:]{17}) (.+)")
_PATRON_PAIR_OK = re.compile(r"Pairing successful")
_PATRON_PAIR_FAIL = re.compile(
    r"Failed to pair|AuthenticationFailed|AuthenticationCanceled|AuthenticationRejected|AuthenticationTimeout"
)
_PATRON_CONNECT_OK = re.compile(r"Connection successful")
_PATRON_CONNECT_FAIL = re.compile(r"Failed to connect")

# Serializa la reproducción real (para que dos pedidos simultáneos -- ej. un
# `hablar()` de main.py justo cuando el propio emparejamiento está anunciando
# un paso -- no hablen encimados) y el proceso de emparejamiento (para que
# dos pulsaciones largas del botón no abran dos sesiones de bluetoothctl a
# la vez). Cada uno es independiente: emparejar puede seguir angunciando
# pasos por voz sin esperar a que otro hilo termine de hablar algo distinto.
_LOCK_REPRODUCCION = threading.Lock()
_LOCK_EMPAREJAMIENTO = threading.Lock()


# =============================================================================
# Salida de voz
# =============================================================================


def hablar(texto: str) -> None:
    """Sintetiza `texto` por voz, decidiendo sola la salida correcta.

    Motor de voz: intenta Piper primero (mejor calidad); si no está
    disponible o falla, cae a espeak-ng -- ver README, sección "Voz: Piper
    TTS". Salida de audio: si hay auriculares Bluetooth conectados los usa
    (y los deja como salida de audio por defecto); si no, cae al parlante
    local. Nunca lanza una excepción: cualquier error queda registrado en
    el log del daemon (`journalctl --user -u blindia-bluetooth-audio`).
    """
    t0 = time.monotonic()
    usando_bluetooth = configurar_salida_default()
    logger.info(f"[timing] configurar_salida_default: {(time.monotonic() - t0) * 1000:.0f}ms")
    logger.info(f"[voz] ({'Bluetooth' if usando_bluetooth else 'parlante local'}) {texto}")

    if len(texto) > _LARGO_EXTREMO_CARACTERES:
        logger.warning(f"[voz] Texto largo ({len(texto)} caracteres) -- aviso previo antes de sintetizarlo entero.")
        _reproducir(_AVISO_TEXTO_LARGO, vigilar_desconexion=usando_bluetooth)

    if _reproducir(texto, vigilar_desconexion=usando_bluetooth):
        return

    if usando_bluetooth:
        logger.error("[voz] Falló la reproducción por Bluetooth; reintentando por el parlante local.")
        configurar_salida_default()  # los auriculares ya no están: esto deja el parlante local como default
        if _reproducir(f"Se perdió la conexión con los auriculares. {texto}", vigilar_desconexion=False):
            return

    # Ninguno de los intentos anteriores llegó a sonar -- esto NUNCA debe
    # quedar en silencio total (pasó justo con un texto extremadamente largo
    # que agotó el timeout de entonces). Último recurso: espeak-ng directo
    # con un mensaje corto y genérico, sin reintentar el texto original --
    # si algo en él está haciendo fallar la reproducción, repetirlo solo
    # repetiría el fallo.
    logger.error("[voz] No se pudo reproducir nada -- anunciando el error por voz como último recurso.")
    _reproducir_con_espeak("Ocurrió un error reproduciendo el audio.", vigilar_desconexion=False)


def _reproducir(texto: str, *, vigilar_desconexion: bool) -> bool:
    """Reproduce `texto`: intenta Piper, cae a espeak-ng si no está disponible o falla."""
    with _LOCK_REPRODUCCION:
        resultado_piper = _reproducir_con_piper(texto, vigilar_desconexion=vigilar_desconexion)
        if resultado_piper is not None:
            return resultado_piper
        return _reproducir_con_espeak(texto, vigilar_desconexion=vigilar_desconexion)


def _voz_piper() -> "PiperVoice | None":
    """Carga y cachea el modelo de Piper -- una sola vez de verdad por vida del daemon.

    Cargar el modelo tarda ~11-15s; por eso se cachea acá y no se repite en
    cada `hablar()` (ahí la síntesis, con el modelo ya en memoria, tarda
    ~1-3s por frase típica). Si ya se intentó cargar y falló, no se
    reintenta en cada llamada -- solo se vuelve a intentar reiniciando el
    daemon (evita que un modelo roto agregue latencia a cada `hablar()`
    intentando y fallando la carga una y otra vez).
    """
    global _voz_piper_cache, _voz_piper_intentado
    if _voz_piper_cache is not None:
        return _voz_piper_cache
    if _voz_piper_intentado:
        return None
    _voz_piper_intentado = True

    if PiperVoice is None:
        logger.warning(
            "[voz] piper-tts no está instalado en este intérprete -- usando espeak-ng. "
            "Ver README, sección 'Voz: Piper TTS', para instalarlo."
        )
        return None
    if not _PIPER_MODELO.exists():
        logger.warning(f"[voz] Modelo de Piper no encontrado en {_PIPER_MODELO} -- usando espeak-ng.")
        return None

    try:
        logger.info(f"[voz] Cargando modelo de Piper ({_PIPER_MODELO.name})...")
        _voz_piper_cache = PiperVoice.load(str(_PIPER_MODELO))
        logger.info("[voz] Modelo de Piper cargado y listo.")
    except Exception as exc:  # noqa: BLE001 -- cualquier falla acá cae a espeak-ng, nunca debe tirar el daemon
        logger.error(f"[voz] No se pudo cargar el modelo de Piper: {exc} -- usando espeak-ng.")
        return None
    return _voz_piper_cache


def _reproducir_con_piper(texto: str, *, vigilar_desconexion: bool) -> bool | None:
    """Sintetiza con Piper (en el propio proceso) y reproduce el WAV vía PulseAudio.

    Devuelve `None` si Piper no está disponible o la síntesis en sí falló
    (el llamador debe probar con espeak-ng en su lugar); `True`/`False` si
    sí llegó a reproducir algo, igual que `_reproducir_con_espeak`.

    La reproducción corre en un subproceso aparte (`pasimple.play_wav` vía
    `-c`, no la librería en el hilo actual) a propósito: así se puede matar
    igual que al proceso de espeak-ng si los auriculares se desconectan a
    mitad de camino (`_vigilar_desconexion` ya sabe hacer eso con cualquier
    `subprocess.Popen`); una llamada bloqueante de pasimple en este mismo
    hilo no se podría interrumpir limpiamente desde el hilo vigía.
    """
    t_voz = time.monotonic()
    voz = _voz_piper()  # cacheado -- ver docstring de _voz_piper(); el timing separa esto de la síntesis en sí
    logger.info(f"[timing] obtener_voz_piper (cache hit esperado): {(time.monotonic() - t_voz) * 1000:.0f}ms")
    if voz is None:
        return None

    wav_path = None
    t0 = time.monotonic()
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name
        with wave.open(wav_path, "wb") as wav_file:
            voz.synthesize_wav(texto, wav_file, syn_config=SynthesisConfig(length_scale=_PIPER_LENGTH_SCALE))
        logger.info(f"[timing] sintesis_piper: {(time.monotonic() - t0) * 1000:.0f}ms")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[voz] Síntesis con Piper falló: {exc} -- usando espeak-ng.")
        if wav_path is not None:
            Path(wav_path).unlink(missing_ok=True)
        return None

    with wave.open(wav_path, "rb") as wav_leido:
        duracion_clip_ms = wav_leido.getnframes() / wav_leido.getframerate() * 1000
    logger.info(f"[timing] duracion_clip_wav (audio real, sin overhead): {duracion_clip_ms:.0f}ms")

    t0 = time.monotonic()  # arranque de la reproducción -- el próximo [timing] es su duración
    try:
        proceso = subprocess.Popen(
            [sys.executable, "-c", "import sys, pasimple; pasimple.play_wav(sys.argv[1])", wav_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        logger.error(f"[voz] No se pudo reproducir el WAV de Piper (pasimple): {exc} -- usando espeak-ng.")
        Path(wav_path).unlink(missing_ok=True)
        return None

    cortado = threading.Event()
    vigia = None
    if vigilar_desconexion:
        vigia = threading.Thread(target=_vigilar_desconexion, args=(proceso, cortado), daemon=True)
        vigia.start()

    # Timeout dinámico: piso _PIPER_TIMEOUT_SEGUNDOS para frases típicas,
    # pero nunca menor que la duración real del clip + margen -- un tope fijo
    # cortaba audio legítimamente largo a mitad de camino (ver constantes).
    timeout_reproduccion = min(
        max(_PIPER_TIMEOUT_SEGUNDOS, duracion_clip_ms / 1000 + _MARGEN_TIMEOUT_REPRODUCCION_SEGUNDOS),
        _TIMEOUT_REPRODUCCION_MAXIMO_SEGUNDOS,
    )
    try:
        _, stderr = proceso.communicate(timeout=timeout_reproduccion)
        logger.info(f"[timing] reproduccion_audio: {(time.monotonic() - t0) * 1000:.0f}ms")
    except subprocess.TimeoutExpired:
        proceso.kill()
        proceso.communicate()
        logger.error(f"[voz] Reproducción de Piper colgada (timeout) para: {texto!r}")
        return False
    finally:
        if vigia is not None:
            vigia.join(timeout=1.0)
        Path(wav_path).unlink(missing_ok=True)

    if cortado.is_set():
        logger.error("[voz] Reproducción interrumpida: los auriculares se desconectaron a mitad de camino.")
        return False
    if proceso.returncode != 0:
        logger.error(f"[voz] Reproducción de Piper devolvió código {proceso.returncode}: {stderr.strip()}")
        return False
    return True


def _reproducir_con_espeak(texto: str, *, vigilar_desconexion: bool) -> bool:
    """Corre `espeak-ng` de forma bloqueante (motor de respaldo). Devuelve False si hubo que cortarlo."""
    if shutil.which(_ESPEAK_BIN) is None:
        logger.error(
            "[voz] espeak-ng no está instalado en el host -- no se puede anunciar "
            f"nada por voz: {texto!r}. Instalalo (ver README, sección Instalación)."
        )
        return False

    try:
        proceso = subprocess.Popen(
            [
                _ESPEAK_BIN,
                "-v", _VOZ,
                "-s", str(_PALABRAS_POR_MINUTO),
                "-g", str(_PAUSA_ENTRE_PALABRAS),
                texto,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        logger.error(f"[voz] No se pudo ejecutar espeak-ng: {exc}")
        return False

    cortado = threading.Event()
    vigia = None
    if vigilar_desconexion:
        vigia = threading.Thread(target=_vigilar_desconexion, args=(proceso, cortado), daemon=True)
        vigia.start()

    timeout_espeak = min(
        max(_TIMEOUT_SINTESIS_SEGUNDOS, len(texto) * _SEGUNDOS_POR_CARACTER_ESPEAK),
        _TIMEOUT_REPRODUCCION_MAXIMO_SEGUNDOS,
    )
    try:
        _, stderr = proceso.communicate(timeout=timeout_espeak)
    except subprocess.TimeoutExpired:
        proceso.kill()
        proceso.communicate()
        logger.error(f"[voz] Síntesis de voz colgada (timeout) para: {texto!r}")
        return False
    finally:
        if vigia is not None:
            vigia.join(timeout=1.0)

    if cortado.is_set():
        logger.error("[voz] Reproducción interrumpida: los auriculares se desconectaron a mitad de camino.")
        return False
    if proceso.returncode != 0:
        logger.error(f"[voz] espeak-ng devolvió código {proceso.returncode}: {stderr.strip()}")
        return False
    return True


def _vigilar_desconexion(proceso: subprocess.Popen, cortado: threading.Event) -> None:
    while proceso.poll() is None:
        if not auriculares_conectados():
            cortado.set()
            proceso.terminate()
            return
        time.sleep(0.5)


# =============================================================================
# Detección de auriculares y ruteo de audio (PipeWire/WirePlumber)
# =============================================================================


def auriculares_conectados() -> bool:
    """True si hay un auricular Bluetooth activo como salida de audio ahora."""
    return _nodo_sink_bluetooth() is not None


def configurar_salida_default() -> bool:
    """Configura la salida de audio por defecto según haya o no auriculares.

    Devuelve True si quedó configurada la salida Bluetooth, False si se usa
    (o se cae a) la salida local.
    """
    nodo = _nodo_sink_bluetooth()
    if nodo is None:
        return False

    try:
        subprocess.run(
            ["wpctl", "set-default", str(nodo["id"])],
            check=True,
            timeout=5,
            capture_output=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.error(f"[audio] No se pudo configurar '{nodo['descripcion']}' como salida por defecto: {exc}")
        return False

    logger.info(f"[audio] Salida de audio por defecto: {nodo['descripcion']} (Bluetooth)")
    return True


def _nodo_sink_bluetooth() -> dict | None:
    """Busca en PipeWire un nodo de salida (`Audio/Sink`) que sea un auricular Bluetooth."""
    try:
        salida = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, timeout=5, check=True
        ).stdout
        nodos = json.loads(salida)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
        logger.error(f"[audio] No se pudo consultar pw-dump: {exc}")
        return None

    for objeto in nodos:
        props = (objeto.get("info") or {}).get("props") or {}
        nombre_nodo = props.get("node.name", "")
        if props.get("media.class") == "Audio/Sink" and nombre_nodo.startswith("bluez_output."):
            return {
                "id": objeto["id"],
                "nombre": nombre_nodo,
                "descripcion": props.get("node.description", nombre_nodo),
            }
    return None


# =============================================================================
# Emparejamiento guiado por voz (BlueZ / bluetoothctl)
# =============================================================================


class _SesionBluetoothctl:
    """Envoltorio mínimo sobre un proceso `bluetoothctl` interactivo."""

    def __init__(self) -> None:
        self._proceso = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._lineas: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self) -> None:
        assert self._proceso.stdout is not None
        for linea in self._proceso.stdout:
            self._lineas.put(_PATRON_ANSI.sub("", linea.rstrip("\n")))

    def comando(self, texto: str) -> None:
        assert self._proceso.stdin is not None
        self._proceso.stdin.write(texto + "\n")
        self._proceso.stdin.flush()

    def lineas_durante(self, segundos: float):
        limite = time.monotonic() + segundos
        while True:
            restante = limite - time.monotonic()
            if restante <= 0:
                return
            try:
                yield self._lineas.get(timeout=restante)
            except queue.Empty:
                return

    def esperar_resultado(self, patron_ok: re.Pattern, patron_fail: re.Pattern, timeout: float) -> bool | None:
        for linea in self.lineas_durante(timeout):
            if patron_ok.search(linea):
                return True
            if patron_fail.search(linea):
                return False
        return None

    def cerrar(self) -> None:
        try:
            self.comando("exit")
            self._proceso.wait(timeout=3)
        except Exception:
            self._proceso.kill()


def emparejar_auriculares(nombre_objetivo: str | None = None, *, timeout_escaneo: float = _TIMEOUT_ESCANEO_SEGUNDOS) -> bool:
    """Empareja auriculares Bluetooth guiando cada paso por voz. Nunca lanza."""
    with _LOCK_EMPAREJAMIENTO:
        hablar("Buscando auriculares Bluetooth.")
        sesion = _SesionBluetoothctl()
        try:
            sesion.comando("agent NoInputNoOutput")
            sesion.comando("default-agent")
            sesion.comando("power on")
            time.sleep(0.3)
            sesion.comando("scan on")

            mac, nombre = _buscar_dispositivo_de_audio(sesion, nombre_objetivo, timeout_escaneo)
            sesion.comando("scan off")

            if mac is None:
                hablar(
                    "No se encontró ningún auricular Bluetooth. Verificá que estén "
                    "encendidos y en modo de emparejamiento, y probá de nuevo."
                )
                return False

            hablar(f"Encontrado {nombre}. Emparejando.")
            sesion.comando(f"pair {mac}")
            if sesion.esperar_resultado(_PATRON_PAIR_OK, _PATRON_PAIR_FAIL, _TIMEOUT_PASO_SEGUNDOS) is not True:
                hablar(f"No se pudo emparejar con {nombre}. Intentá de nuevo.")
                return False

            sesion.comando(f"trust {mac}")
            time.sleep(0.5)

            hablar("Conectando.")
            sesion.comando(f"connect {mac}")
            if sesion.esperar_resultado(_PATRON_CONNECT_OK, _PATRON_CONNECT_FAIL, _TIMEOUT_PASO_SEGUNDOS) is not True:
                hablar(
                    f"{nombre} quedó emparejado, pero no se pudo conectar ahora. "
                    "Se va a reconectar solo la próxima vez que esté cerca y encendido."
                )
                return False

            time.sleep(2.0)
            configurar_salida_default()
            hablar(f"{nombre} conectado y listo para usarse.")
            return True
        except Exception as exc:
            logger.exception(f"[bluetooth] Error inesperado durante el emparejamiento: {exc}")
            hablar("Ocurrió un error inesperado durante el emparejamiento. Revisá los logs del daemon.")
            return False
        finally:
            sesion.cerrar()


def _buscar_dispositivo_de_audio(
    sesion: _SesionBluetoothctl, nombre_objetivo: str | None, timeout_escaneo: float
) -> tuple[str | None, str | None]:
    vistos: set[str] = set()
    for linea in sesion.lineas_durante(timeout_escaneo):
        coincidencia = _PATRON_DISPOSITIVO_NUEVO.search(linea)
        if not coincidencia:
            continue
        mac, nombre = coincidencia.group(1), coincidencia.group(2).strip()
        if mac in vistos:
            continue
        vistos.add(mac)

        if nombre_objetivo:
            # Nombre explícito: el usuario ya identificó el dispositivo, no hace
            # falta el chequeo de UUID de audio (muchos auriculares -- ej. el JBL
            # Wave Beam 2 -- no anuncian esos UUIDs en el escaneo, solo después
            # de emparejar/SDP, así que exigirlo acá descartaría el dispositivo
            # real por un falso negativo).
            if nombre_objetivo.lower() not in nombre.lower():
                continue
            return mac, nombre

        # Sin nombre (escaneo "a ciegas"): el chequeo de UUID sigue siendo la
        # única forma de no emparejar por error con un celular o mouse cercano.
        if _es_dispositivo_de_audio(sesion, mac):
            return mac, nombre
    return None, None


def _es_dispositivo_de_audio(sesion: _SesionBluetoothctl, mac: str) -> bool:
    sesion.comando(f"info {mac}")
    info = "\n".join(sesion.lineas_durante(2.0)).lower()
    return any(uuid in info for uuid in _UUIDS_AUDIO)


# =============================================================================
# Servidor de socket Unix (habla con blindia/audio/bluetooth_audio.py)
# =============================================================================


def _atender_comando(comando: dict) -> dict:
    """Despacha un pedido ya parseado. Nunca deja que una excepción tumbe el daemon."""
    tipo = comando.get("cmd")
    try:
        if tipo == "hablar":
            hablar(str(comando.get("texto", "")))
            return {"ok": True}
        if tipo == "auriculares_conectados":
            return {"ok": True, "conectado": auriculares_conectados()}
        if tipo == "configurar_salida_default":
            return {"ok": True, "bluetooth": configurar_salida_default()}
        if tipo == "emparejar":
            timeout_escaneo = comando.get("timeout_escaneo") or _TIMEOUT_ESCANEO_SEGUNDOS
            emparejado = emparejar_auriculares(
                nombre_objetivo=comando.get("nombre_objetivo"),
                timeout_escaneo=float(timeout_escaneo),
            )
            return {"ok": True, "emparejado": emparejado}
        return {"ok": False, "error": f"comando desconocido: {tipo!r}"}
    except Exception as exc:  # un pedido mal formado no puede tumbar el daemon
        logger.exception("Error atendiendo un pedido")
        return {"ok": False, "error": str(exc)}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        linea = self.rfile.readline()
        if not linea:
            return
        try:
            comando = json.loads(linea.decode("utf-8"))
        except json.JSONDecodeError as exc:
            respuesta = {"ok": False, "error": f"JSON inválido: {exc}"}
        else:
            respuesta = _atender_comando(comando)
        self.wfile.write((json.dumps(respuesta) + "\n").encode("utf-8"))


class _Servidor(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.unlink(missing_ok=True)  # socket viejo de una corrida anterior que no cerró bien

    servidor = _Servidor(str(SOCKET_PATH), _Handler)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=servidor.shutdown).start())

    logger.info(f"Escuchando en {SOCKET_PATH}")
    try:
        servidor.serve_forever()
    finally:
        servidor.server_close()
        SOCKET_PATH.unlink(missing_ok=True)
        logger.info("Daemon detenido.")


if __name__ == "__main__":
    main()
