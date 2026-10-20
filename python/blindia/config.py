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

# Última IP conocida del servidor de reconocimiento visual remoto, resuelta
# periódicamente por scripts/server_ip_resolver.py (corre en el HOST, igual
# patrón que el daemon de audio Bluetooth -- el contenedor no puede resolver
# nombres mDNS/.local, ver docs/DEVELOPMENT.md, "Reconocimiento de
# producto"). `blindia.product.vision_fallback` lee este archivo en vez de
# resolver el nombre directo.
SERVER_IP_FILE: Path = APP_ROOT / "data" / "server_ip.txt"

# Credenciales de vinculación de este equipo físico (serial + clave),
# escritas por scripts/usb_vinculacion_daemon.py vía el protocolo de
# docs/VINCULACION-USB.md. Si no existe, el equipo no está vinculado
# todavía -- el resto de la app funciona igual, simplemente no hay nada
# que mandarle a la web de administración.
DEVICE_CREDENTIALS_FILE: Path = APP_ROOT / "data" / "device_credentials.json"

# Código de enlace (8 caracteres) impreso en la caja del anteojo. Extensión
# opcional v1.1 del protocolo de vinculación (ver docs/VINCULACION-USB.md):
# le permite al anteojo "decirle" ese código a la web por el cable en vez de
# que el cliente lo tipee a mano. NO es una credencial de API -- nunca se usa
# para hablar con la web de reconocimiento, solo para la vinculación por USB.
DEVICE_PAIRING_FILE: Path = APP_ROOT / "data" / "pairing_code.txt"

# Cola persistente de identificaciones todavía no confirmadas por la web
# (ver `blindia.backend.identifications`) y contador para generar
# `event_id` (serial + contador) estable ante reintentos y reinicios.
IDENTIFICATION_QUEUE_FILE: Path = APP_ROOT / "data" / "identification_queue.json"
IDENTIFICATION_COUNTER_FILE: Path = APP_ROOT / "data" / "identification_counter.txt"

# Última versión de configuración remota ya aplicada (ver
# `blindia.backend.remote_config`), para poder pedir `?since=<version>` y
# que la web conteste "sin cambios" la mayoría de las veces.
REMOTE_CONFIG_VERSION_FILE: Path = APP_ROOT / "data" / "remote_config_version.txt"

# Versión de firmware reportada a la web en cada latido de telemetría (ver
# `blindia.backend.telemetry`). No está atada a ningún build automático
# todavía -- bump manual si en algún momento hace falta distinguir
# versiones en el panel de administración.
FIRMWARE_VERSION: str = "1.0.0"


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
        frames_de_calentamiento: Cuántos frames descartar (leer y tirar)
            justo antes del frame real que se guarda en cada captura. La
            cámara queda abierta entre apretones del botón, a veces varios
            segundos inactiva -- el driver V4L2/UVC puede devolver en el
            primer `read()` un frame viejo, de antes del período inactivo,
            aunque `CAP_PROP_BUFFERSIZE` esté en 1 (no todos los drivers lo
            respetan). Ver docs/DEVELOPMENT.md, "Bug: anuncia el producto
            anterior", para el diagnóstico completo.
    """

    source: str | int | None = None
    resolution: tuple[int, int] = (1280, 720)
    fps: int = 10
    jpeg_quality: int = 95
    frames_de_calentamiento: int = 2


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

    # Umbral usado por `blindia.product.parser.ocr_tiene_sentido()` para
    # decidir si el texto detectado alcanza para identificar un producto,
    # o si conviene ir directo al respaldo remoto de reconocimiento visual
    # (ver `VisionFallbackSettings`). Se mide en letras alfabéticas, no en
    # caracteres totales, para no penalizar detecciones cortas pero reales
    # (ej. "adidas", 6 letras) igual que ruido sin letras (ej. "01", "-",
    # 0 letras) -- ver docs/DEVELOPMENT.md, "Reconocimiento de producto",
    # para los datos reales de esta placa que calibraron este número.
    min_letras_para_tener_sentido: int = 4

    # Segundo filtro de `ocr_tiene_sentido()`: por encima de este ratio de
    # transiciones mayúscula/minúscula entre letras consecutivas, el texto
    # se considera ruido (ej. "sDpIpD" leído sobre una textura) aunque
    # tenga letras de sobra. Texto real de producto nunca alterna case
    # letra por letra -- ver docs/DEVELOPMENT.md, "Reconocimiento de
    # producto", para los 8 casos que calibraron este número (incluye por
    # qué se descartó un chequeo de ratio de vocales en su lugar).
    max_ratio_transiciones_capitalizacion: float = 0.5


@dataclass(frozen=True)
class VisionFallbackSettings:
    """Configuración del respaldo remoto de reconocimiento visual.

    Se usa SOLO cuando el OCR local (RapidOCR) no detecta ningún fragmento
    de texto en absoluto -- ver `blindia.product.vision_fallback` y
    docs/DEVELOPMENT.md, sección "Reconocimiento de producto", para el
    porqué de esta arquitectura (servidor propio en la PC del usuario en
    vez de un respaldo en la nube).

    Attributes:
        host: Nombre mDNS de la PC del usuario en la red WiFi local
            (confirmado funcionando desde el HOST: `getent hosts
            blindia-server.local` resuelve bien). El contenedor donde
            corre la app NO puede resolver nombres mDNS/.local (resolver
            de DNS aislado del host, sin soporte mdns4 -- confirmado con
            `docker exec ... getent hosts` fallando), así que este valor
            NO lo usa `vision_fallback.py` directo: lo usa
            `scripts/server_ip_resolver.py` (corre en el host, igual
            patrón que el daemon de audio Bluetooth), que resuelve este
            nombre cada ~30s y escribe la IP en `SERVER_IP_FILE` -- eso sí
            lo lee `vision_fallback.py` desde el contenedor.
        port: Puerto del servidor FastAPI (no el de LM Studio -- ese es
            interno al servidor, esta app nunca le habla directo).
        timeout_segundos: Timeout corto a propósito: si el servidor no
            está prendido o tarda demasiado, el pipeline tiene que caer al
            mensaje de reserva sin colgarse.
    """

    host: str = "blindia-server.local"
    port: int = 8000
    timeout_segundos: float = 8.0


@dataclass(frozen=True)
class ApiSettings:
    """Configuración del cliente de la API web de administración (ver docs/API-DISPOSITIVO.md).

    Attributes:
        base_url: Dirección base de la API. **Solo la de producción**
            (`https://blind-ia.vercel.app`) -- nunca una dirección de
            "vista previa" de Vercel, que cambia en cada despliegue y
            además puede estar protegida por autenticación propia de
            Vercel. Es un dato de configuración (acá) a propósito, no una
            constante escondida en el cliente HTTP, para poder apuntar a
            un entorno de desarrollo sin tocar código si hiciera falta.
        request_timeout_segundos: Timeout corto a propósito: un pedido a
            la web nunca debe poder demorar el pipeline del botón (que de
            hecho nunca espera a ninguno de estos pedidos, ver
            `blindia.backend`) ni trabar el hilo de fondo por mucho tiempo
            si la red está caída.
        telemetry_interval_segundos: Cada cuánto mandar un latido de
            telemetría mientras la app esté corriendo (además de uno al
            arrancar). El contrato pide "cada 60-120s"; 90 es el punto
            medio.
        config_poll_interval_segundos: Cada cuánto pedir configuración
            remota (además de una vez al arrancar). El contrato pide
            "cada ~5 min".
        max_eventos_en_cola: Tope de la cola persistente de
            identificaciones sin confirmar (ver
            `blindia.backend.identifications`). El contrato pide "al
            menos 20"; se deja bastante margen por encima para no perder
            eventos reales en una salida prolongada sin señal.
        reintento_backoff_segundos: Escalera de espera entre reintentos
            de un mismo evento ante un error de red o 5xx (ver la tabla
            de errores del contrato) -- 5s, 15s, 60s, y de ahí en más el
            tope de 5 minutos.
        pausa_tras_401_segundos: Cuánto esperar antes de volver a intentar
            cualquier pedido autenticado después de un 401 (serial/clave
            no coinciden) -- el contrato pide "no insistir cada pocos
            segundos", así que no tiene sentido reintentar con el mismo
            backoff corto que una falla de red transitoria.
        pausa_tras_429_segundos: Cuánto esperar antes de mandar el
            próximo pedido después de un 429 (límite de eventos por
            minuto superado).
    """

    base_url: str = "https://blind-ia.vercel.app"
    request_timeout_segundos: float = 8.0
    telemetry_interval_segundos: float = 90.0
    config_poll_interval_segundos: float = 300.0
    max_eventos_en_cola: int = 200
    reintento_backoff_segundos: tuple[float, ...] = (5.0, 15.0, 60.0, 300.0)
    pausa_tras_401_segundos: float = 300.0
    pausa_tras_429_segundos: float = 60.0
