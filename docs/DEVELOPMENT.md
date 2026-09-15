# Notas de desarrollo

Documentación detallada, módulo por módulo, de BlindIA. Ver el
[README](../README.md) de nivel superior para una descripción breve del
proyecto.

## Etapa actual

Circuito completo, de punta a punta: un apretón del pulsador físico (leído
en el MCU, transmitido a Linux por el Router Bridge -- ver "Conectar el
pulsador físico" más abajo) dispara la cámara USB para tomar una foto,
extrae cualquier texto visible (RapidOCR, pesos PP-OCRv5 mobile), parsea un
nombre de producto + precio best-effort de ese texto, y dice el resultado
en voz alta (Piper TTS principal, espeak-ng de respaldo -- ver "Audio
Bluetooth" más abajo) por auriculares Bluetooth o el parlante local.

## Estructura

```
python/
├── main.py                   # punto de entrada; conecta todo
├── requirements.txt           # onnxruntime (las dependencias reales de rapidocr vienen por python-libraries/)
└── blindia/
    ├── config.py              # configuración central (cámara, OCR, paths)
    ├── exceptions.py          # BlindIAError y subclases
    ├── models.py              # CaptureResult, OcrResult, RecognizedProduct
    ├── pipeline.py            # CapturePipeline: trigger -> captura -> guardado
    ├── capture/
    │   └── service.py         # CameraCaptureService: dueño del periférico de cámara
    ├── storage/
    │   └── image_store.py     # ImageStore: guarda frames como JPEGs con timestamp
    ├── ocr/
    │   └── service.py         # OcrService: extracción de texto de un frame con RapidOCR
    ├── product/
    │   └── parser.py          # parse_product: OcrResult -> RecognizedProduct
    └── audio/
        └── bluetooth_audio.py # hablar(): salida de voz por auriculares Bluetooth, respaldo a parlante local

python-libraries/
├── README.md                  # por qué rapidocr se distribuye como un wheel local parcheado
└── rapidocr-3.9.2-py3-none-any.whl
```

`CapturePipeline.capture()` es la llamada reutilizable que deben hacer los
triggers: el pulsador físico (vía el Bridge) y, más adelante, una acción
manual de "recapturar". Devuelve un `CaptureResult` (`image_path`, `frame`
en memoria, `captured_at`), que `main.py` le pasa a
`OcrService.extract_text()` y después a `parse_product()`.

Las fotos capturadas se guardan en `data/captures/` (gitignoreado).

## Reconocimiento de producto

**Decisión del MVP: el texto del OCR *es* el identificador del producto.**
Una etiqueta de precio de góndola ya imprime marca + nombre del producto +
precio, así que `parse_product()` (`blindia/product/parser.py`) convierte
`OcrResult.text` en un `RecognizedProduct` (`name`, `price`, más el
`raw_text` original como respaldo) con un par de heurísticas de regex -- sin
modelo de reconocimiento visual. Esto cubre el caso de uso principal (una
etiqueta de precio legible en cuadro) sin latencia, RAM ni costo extra por
encima de lo que ya paga el OCR.

Se evaluaron tres alternativas de reconocimiento visual y se decidió
deliberadamente **no** implementarlas en este MVP:

- **Clasificador genérico local** (Brick `arduino:image_classification` +
  el modelo integrado `mobilenet-image-classification`). Técnicamente
  viable en la CPU de esta placa (liviano, sin riesgo de crash, ya
  provisto por el framework) pero el modelo es un clasificador genérico de
  ImageNet-1k -- devuelve categorías gruesas de envase ("botella", "lata"),
  no marca ni identidad del producto. No produce el tipo de respuesta que
  necesita esta app.
- **Clasificador local entrenado a medida** (modelo de Edge Impulse,
  conjunto cerrado de productos conocidos, mismo Brick). Daría precisión
  real a nivel de producto y correría totalmente offline, pero requiere
  primero juntar y etiquetar un set de fotos de entrenamiento por
  producto -- un proyecto aparte, no algo que se pueda agregar sobre el
  pipeline actual.
- **Respaldo multimodal en la nube** (Brick `arduino:cloud_llm`; Claude/GPT/Gemini
  ya soportan `images=[...]`, ver su ejemplo
  `bricks/arduino/cloud_llm/06_multimodal`). Daría reconocimiento real de
  marca/producto a partir del envase/logo aunque no haya texto legible, a
  bajo costo por llamada (una fracción de centavo con un modelo barato). Se
  descartó por ahora porque necesita conectividad de red, agrega latencia
  de request por encima de los ~2-3s del OCR, necesita una API key
  provisionada, y tiene un costo variable por uso que el proyecto no quiere
  asumir todavía.

El paso natural siguiente, si/cuando se retoma esto: activar el respaldo en
la nube solo cuando `RecognizedProduct.name`/`price` vuelvan `None` (el OCR
no encontró nada usable), en vez de llamarlo en cada captura.

## Audio Bluetooth

`blindia/audio/bluetooth_audio.py` es el módulo que usa el resto del
sistema para hablar. Su único punto de entrada real es `hablar(texto)`:
decide sola si usar los auriculares Bluetooth conectados o caer al parlante
local de la placa, así que ningún otro módulo necesita saber nada de
Bluetooth, PipeWire, ni cómo se manejan esas fallas. Módulo independiente,
mismo espíritu que `capture/`, `ocr/` y `product/`: no se tocó nada de
`main.py`, `pipeline.py` ni los módulos de trigger para agregarlo.

Está construido mayormente sobre herramientas de sistema ya presentes en
esta placa, más una dependencia de Python (Piper, en su propio venv -- ver
"Voz: Piper TTS" más abajo):

- **`bluetoothctl`** (BlueZ) para escanear, emparejar, dar `trust`, y
  conectar los auriculares. Una vez que un dispositivo es `trusted`, BlueZ
  lo reconecta automáticamente cada vez que está cerca y prendido -- no
  hace falta código de la app para esa parte.
- **`pw-dump` / `wpctl`** (PipeWire + WirePlumber -- confirmado que es lo
  que realmente corre en esta imagen Debian; no hay PulseAudio nativo,
  aunque `pipewire-pulse` provee un socket compatible) para detectar si un
  auricular Bluetooth está activo como salida de audio y configurarlo como
  salida por defecto.
- **Piper** para la síntesis de texto a voz en sí (motor principal,
  totalmente offline): un modelo TTS neuronal chico, notoriamente más claro
  y natural que un sintetizador de formantes -- ver "Voz: Piper TTS" más
  abajo para el modelo usado y por qué necesita su propio venv.
- **`espeak-ng`** como motor de **respaldo**, usado solo si Piper no está
  instalado o una llamada de síntesis falla: también totalmente offline,
  sin dependencia de red, consistente con la filosofía solo-local del
  módulo de OCR en esta placa -- solo que suena notoriamente más robótico
  que Piper.

Decisión de diseño, distinta de `capture/` y `ocr/`: este módulo **no**
define su propia jerarquía de excepciones. Sus funciones nunca lanzan --
loguean vía `Logger` y siempre anuncian las fallas con el propio
`hablar()`, devolviendo `bool` en cambio. No tiene sentido que el módulo
que *es* la capa de anuncio por voz del sistema lance una excepción para
que otro la convierta en un mensaje hablado.

### Instalación

`espeak-ng` (el motor de respaldo) no viene instalado en esta placa por
defecto -- instalarlo una vez:

```bash
sudo apt update && sudo apt install -y espeak-ng
```

Todo lo demás que necesita este módulo (`bluetoothctl`, `wpctl`, `pw-dump`,
el plugin `libspa-0.2-bluetooth` de PipeWire para sinks A2DP) ya estaba
presente y verificado en la placa; no hace falta instalación extra para
eso. Piper (el motor principal) tiene sus propios pasos de instalación --
ver la siguiente sección.

### Voz: Piper TTS

**Por qué Piper, y por qué necesita su propio venv.** `espeak-ng` es un
sintetizador de formantes -- instantáneo para correr, pero audiblemente
robótico. Piper es un modelo TTS neuronal chico (ONNX Runtime), más claro y
natural, y también totalmente offline. Corre como un **venv aparte del
lado host** en `scripts/.venv-piper/`, no instalado en el Python del
sistema: `pip install piper-tts` falla ahí con
`externally-managed-environment` (PEP 668, estándar en esta imagen
Debian), y un venv dedicado mantiene esto autocontenido sin tocar los
paquetes de Python del sistema base. La unidad de systemd del daemon
(`ExecStart` en `scripts/systemd/blindia-bluetooth-audio.service`) corre
**todo el daemon** con `.venv-piper/bin/python3` (no `/usr/bin/python3` --
el venv igual tiene la stdlib completa, así que nada más del daemon
cambia) para poder hacer `import piper` directamente, en el mismo proceso.

**Pasos de instalación** (del lado host, sin `sudo` -- correr una sola vez):

```bash
cd ~/ArduinoApps/blindia/scripts
python3 -m venv .venv-piper
.venv-piper/bin/pip install piper-tts pasimple

mkdir -p ../data/piper
cd ../data/piper
curl -L -o es_ES-davefx-medium.onnx \
  "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx"
curl -L -o es_ES-davefx-medium.onnx.json \
  "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json"

systemctl --user daemon-reload
systemctl --user restart blindia-bluetooth-audio
```

(`pasimple` es un binding chico por ctypes a `libpulse-simple.so.0` -- ya
presente en esta placa, ya que el propio `espeak-ng` está linkeado contra
esa librería -- usado para reproducir el WAV sintetizado por Piper a través
del socket compatible con PulseAudio de PipeWire. Se probó primero un
`aplay`/ALSA directo y se descartó: no hay ninguna redirección
`pipewire-alsa` configurada en esta imagen, así que la reproducción por
ALSA saltearía PipeWire por completo y siempre pegaría en el parlante
local, ignorando cualquier sink que acabara de configurar
`configurar_salida_default()`.)

**Modelo usado: `es_ES-davefx-medium`** (español de España, voz masculina,
nivel de calidad "medium"), **no** el `es_AR-daniela-high` (español
argentino) de mayor calidad que de otro modo sería la elección obvia para
este proyecto. Ese se probó primero y resultó medido demasiado lento para
un asistente en vivo en la CPU de esta placa (sin NPU en la UNO Q):

| Modelo | Nivel | Llamada en frío (carga de modelo + síntesis) | Solo síntesis en caliente (modelo cacheado) |
|---|---|---|---|
| `es_AR-daniela-high` | high | ~28-30s | ~7-16s por frase típica |
| `es_ES-davefx-medium` | medium | ~13-15s | ~1-3s por frase típica |

`es_AR` solo distribuye una voz de nivel "high" en el catálogo de Piper (no
hay medium/low) -- así que recuperar el acento argentino a velocidad usable
implicaría entrenar/buscar otro modelo, fuera de alcance por ahora. El
costo de carga del modelo se paga una sola vez, al arrancar el daemon o en
la primera llamada a `hablar()` después de un reinicio
(`PiperVoice.load(...)`, cacheado en `_voz_piper_cache` dentro de
`bluetooth_audio_daemon.py`) -- el número **en caliente, por llamada** es
el que importa para la capacidad de respuesta, ya que el daemon se queda
corriendo durante toda la vida de la app.

**Comportamiento de respaldo**: `_reproducir()` en
`bluetooth_audio_daemon.py` intenta primero con Piper
(`_reproducir_con_piper`); si el modelo/venv no está disponible, falla al
cargar, o la síntesis lanza una excepción, loguea por qué y cae
automáticamente a espeak-ng (`_reproducir_con_espeak`) -- mismo contrato de
"nunca fallar en silencio" que ya tiene `hablar()` entre Bluetooth y
parlante local.

**Ajuste de voz** (`bluetooth_audio_daemon.py`, arriba del archivo):
`_PIPER_LENGTH_SCALE = 1.15` (>1.0 hace un poco más lento el ritmo nativo
de Piper, para más claridad); el ajuste propio de espeak-ng se mantiene
como la configuración del camino de respaldo
(`_PALABRAS_POR_MINUTO = 140`, `_PAUSA_ENTRE_PALABRAS = 6`).

### Probando en la placa real

Emparejamiento guiado por voz, corrido desde la carpeta `python/` de la app
dentro de la app corriendo (por ejemplo vía `arduino-app-cli app logs --follow`
en una terminal para ver qué se loguea, y una shell dentro del contenedor
en otra):

```bash
python3 -m blindia.audio.bluetooth_audio --emparejar
# o, para considerar solo un dispositivo cuyo nombre Bluetooth contenga una subcadena:
python3 -m blindia.audio.bluetooth_audio --emparejar --nombre "Sony"
```

Poné primero los auriculares en modo de emparejamiento. Deberías escuchar,
en orden: "Buscando auriculares Bluetooth" -> "Encontrado <nombre>.
Emparejando." -> "Conectando." -> "<nombre> conectado y listo para
usarse." Cualquier falla en cualquier paso se anuncia (nunca se deja en
silencio) y también se loguea.

Para verificar las piezas del módulo por separado del emparejamiento:

```python
from blindia.audio.bluetooth_audio import hablar, auriculares_conectados
hablar("Prueba de audio.")           # debería salir por lo que esté conectado ahora
print(auriculares_conectados())      # True solo cuando PipeWire realmente tiene un sink bluez_output
```

**Arreglado el 2026-09-01: el audio ahora rutea por auriculares Bluetooth
de punta a punta** (`auriculares_conectados()` devuelve `True` y
`hablar()` reproduce por ellos, verificado en vivo con un JBL Wave Beam 2).
Esto era un problema de integración del sistema operativo en la imagen de
esta placa, **no un bug del código de `blindia`** -- ver "Ruteo de audio
Bluetooth (resuelto 2026-09-01)" más abajo para el diagnóstico completo y
el arreglo. El respaldo al parlante local sigue existiendo y sigue
funcionando exactamente como estaba pensado (por ejemplo, si los
auriculares no están conectados).

### Ruteo de audio Bluetooth (resuelto 2026-09-01)

**Estado: arreglado.** Hicieron falta dos cambios a nivel de sistema
operativo, aplicados en este orden, ambos infraestructura del lado host y
no código de la app. Esta sección mantiene el diagnóstico completo (tres
sesiones, dos hipótesis descartadas) porque la causa raíz fue lo
suficientemente específica como para que valga la pena entenderla antes de
volver a tocar cualquiera de los dos arreglos.

Lo que está confirmado funcionando, a nivel de BlueZ:

- Escanear, emparejar, dar trust, y conectar funcionan todos bien --
  `bluetoothctl info <MAC>` muestra `Paired: yes`, `Bonded: yes`,
  `Trusted: yes`, `Connected: yes`, y un porcentaje real de batería
  devuelto por los auriculares.
- Se encontraron y arreglaron dos bugs reales en `emparejar_auriculares()`
  / `_buscar_dispositivo_de_audio()` (`scripts/bluetooth_audio_daemon.py`)
  mientras se verificaba esto:
  1. La salida de escaneo de `bluetoothctl` envuelve `NEW` en códigos de
     color ANSI (ej. `[\x1b[0;92mNEW\x1b[0m] Device ...`) incluso cuando
     stdout no es una TTY, así que la detección de dispositivos basada en
     regex original nunca matcheaba *ningún* dispositivo escaneado, jamás.
     Arreglado sacando las secuencias de escape ANSI (`_PATRON_ANSI`) de
     cada línea leída del proceso `bluetoothctl` antes de matchear contra
     ella.
  2. El chequeo previo de capacidad de audio (`_es_dispositivo_de_audio`,
     que exige un UUID de audio en `bluetoothctl info` *antes* de
     emparejar) rechazaba al JBL Wave Beam 2 aunque ya fuera visible,
     porque no anuncia UUIDs completos antes de emparejar (algo común --
     muchos auriculares solo los exponen después de SDP, que pasa durante
     el emparejamiento). Arreglado salteando ese chequeo cuando se da
     explícitamente `nombre_objetivo` (quien llama ya identificó el
     dispositivo por nombre); sigue aplicándose como filtro de seguridad
     para un escaneo a ciegas sin nombre.

Lo que está confirmado roto, una capa más arriba, a nivel de
PipeWire/WirePlumber:

- Después de un connect exitoso de BlueZ, `pw-dump` / `wpctl status` no
  muestran **ningún dispositivo ni sink de Bluetooth** -- solo `Built-in
  Audio` y los nodos ALSA de la webcam USB. `auriculares_conectados()`
  (que busca un nodo `Audio/Sink` `bluez_output.*`) correctamente devuelve
  `False`, y `hablar()` correctamente cae al parlante local exactamente
  como está pensado.
- No es una carrera de orden de arranque: reiniciar `wireplumber`
  (`systemctl --user restart wireplumber`) con los auriculares ya
  conectados tampoco lo arregla.
- El log de debug de WirePlumber (`WIREPLUMBER_DEBUG=3 wireplumber`,
  corrido en primer plano) confirma que `script:monitors/bluez.lua`
  **sí** se activa como parte del perfil `main` activo
  (`hardware.bluetooth = required`) -- pero el log nunca menciona el
  dispositivo, su MAC, ni nada relacionado con `org.bluez`/A2DP, aunque el
  monitor de ALSA (`monitors/alsa.lua`) se activa y enumera hardware
  normalmente en la misma corrida. Así que el monitor SPA de bluez5 carga,
  pero nunca parece ver el dispositivo conectado.

Se probaron dos hipótesis de seguimiento en una sesión posterior y **ambas
se descartaron**, y después se confirmó una tercera causa, más específica:

- **Permisos de D-Bus: descartado.** Se capturó tráfico D-Bus en vivo con
  `sudo busctl monitor --system org.bluez` mientras se reiniciaba
  `wireplumber` -- **cero mensajes** hacia o desde `org.bluez` aparecieron
  durante el reinicio. Si se estuviera llamando a `Media1.RegisterEndpoint`
  y fuera rechazado por policy, tanto la llamada como su respuesta de
  error aparecerían en el monitor; ni siquiera llega a intentar la
  llamada. (También se leyó el archivo de policy de D-Bus en sí,
  ubicado en realidad en `/usr/share/dbus-1/system.d/bluetooth.conf` -- no
  en `/etc/dbus-1/system.d/`, que no se usa en esta placa -- y se ve bien:
  la policy `context="default"` ya permite `send_destination="org.bluez"`
  para todos los usuarios.)
- **Contención con `lightdm.service`: descartado (y activamente
  perjudicial de "arreglar").** `journalctl -u bluetooth -b` mostró que
  `bluetoothd` *sí* recibe un set completo de objetos `MediaEndpoint1` A2DP
  registrados en cada boot -- pero por un segundo par de
  PipeWire/WirePlumber independiente corriendo bajo el usuario `lightdm`
  (la sesión gráfica de login de la placa, que en realidad no se usa en
  este setup headless). Confirmado vía
  `busctl --system call org.freedesktop.DBus /org/freedesktop/DBus
  org.freedesktop.DBus GetConnectionCredentials s ":1.31"` (el remitente
  que registra) devolviendo `UnixUserID: 103` (`lightdm`),
  `ProcessID: 892`, un proceso `wireplumber` completamente separado del
  propio de `arduino` (un PID distinto, `ps -p 892` ->
  `lightdm /usr/bin/wireplumber`). Deshabilitar `lightdm.service`
  (`sudo systemctl disable --now lightdm.service` -- confirmado reversible,
  y revertido al final de esa sesión con
  `sudo systemctl enable --now lightdm.service`) sacó ese registro en
  competencia, pero el WirePlumber del usuario `arduino` *igual* nunca
  registró nada en el siguiente reboot tampoco -- y sin el registro de
  `lightdm` como respaldo, hasta el simple
  `bluetoothctl connect <MAC>` a nivel de BlueZ empezó a fallar
  directamente con `org.bluez.Error.Failed br-connection-profile-unavailable`
  (ya ningún manejador de perfil A2DP registrado con `bluetoothd`). Así que
  `lightdm` nunca estuvo bloqueando al WirePlumber de `arduino` -- era lo
  *único* que hacía funcionar los perfiles de audio Bluetooth en esta
  placa.
- **Causa raíz real, confirmada**: el monitor `bluez5` del WirePlumber del
  usuario `arduino` (`script:monitors/bluez.lua`, instalado en
  `/usr/share/wireplumber/scripts/monitors/bluez.lua`) sí arranca -- su
  log de debug muestra `startStopMonitor:550 <WpLogind> Seat state changed:
  online` -- pero, a diferencia de los monitores de ALSA y libcamera en
  esa misma corrida exacta, nunca avanza más allá: no se crea ningún
  objeto SPA de dispositivo/adaptador `bluez5`, y nunca toca `org.bluez`
  para nada. Esto se mantuvo tanto con como sin `lightdm` presente, en cada
  reinicio probado (vía la captura vacía de `busctl monitor` y cero
  actividad de registro en `journalctl -u bluetooth` para cualquier
  corrida de WirePlumber de la sesión de `arduino`). Algo específico de
  cómo corre `bluez.lua` en la sesión del usuario `arduino` lo frena justo
  después de esa línea de log "Seat state changed: online", antes de que
  llegue siquiera a BlueZ -- no es contención de recursos, no es policy de
  D-Bus. Leer `bluez.lua` confirmó por qué: `startStopMonitor` solo llama a
  `createMonitor()` cuando el estado de seat reportado por logind es
  `"active"`. La sesión de `arduino` corre headless por SSH y no tiene
  ningún seat de logind (`loginctl list-sessions` muestra `SEAT` vacío
  para sus dos sesiones), así que el estado que obtiene es `"online"`,
  nunca `"active"` -- el monitor queda bloqueado para siempre.

**Arreglo, parte 1 -- deshabilitar el seat-monitoring de bluez para la
sesión de `arduino`.** La propia configuración de fábrica de WirePlumber
(`/usr/share/wireplumber/wireplumber.conf`) ya tiene un bloque
`mixin.systemwide-session` que deshabilita exactamente esta función
(`monitor.bluez.seat-monitoring = disabled`), pensado para sus perfiles
`main-systemwide`/`main-embedded` -- pero el perfil activo de esta placa es
el `main` plano, que no lo hereda. En vez de cambiar de perfil (lo cual
también deshabilita la persistencia de estado y la reserva de
dispositivos -- más de lo necesario), un drop-in a nivel de usuario aplica
solo esa clave a `main`:

```
~/.config/wireplumber/wireplumber.conf.d/99-blindia-bluez-no-seat.conf
```
```
wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
  }
}
```

No hace falta `sudo` (carpeta de config de propiedad del usuario). Después
de crearlo: `systemctl --user restart wireplumber`.

**Arreglo, parte 2 -- que `lightdm.service` deje de manejar también el
audio Bluetooth.** Esta parte sola se probó aislada primero (ver
"descartado" arriba) y empeoró las cosas, porque en ese momento el
WirePlumber de `arduino` todavía no podía registrar nada (la parte 1
todavía no estaba aplicada), dejando cero registradores. Una vez que la
parte 1 está en su lugar, el WirePlumber de `arduino` *sí puede* registrar
-- pero el WirePlumber de `lightdm` sigue corriendo e intenta hacerlo
también, y los dos chocan (`spa.bluez5.native: RegisterProfile() failed:
org.bluez.Error.NotPermitted`, `listen(): Address already in use`, y un
transporte que queda trabado en estado "unknown" -- visible en
`journalctl --user -u wireplumber`). Con los dos arreglos aplicados,
deshabilitar `lightdm.service` saca la colisión y el WirePlumber de
`arduino` se vuelve el único manejador de audio Bluetooth, y funciona:

```bash
sudo systemctl disable --now lightdm.service
```

Reversible con `sudo systemctl enable --now lightdm.service` (esta placa es
headless -- confirmado con el usuario antes de deshabilitarlo -- así que la
sesión de login gráfica de `lightdm` no se usa para nada).

**Después de los dos arreglos**, un dispositivo que ya está
`Paired`/`Trusted` en BlueZ necesita una reconexión para tomar los
endpoints recién registrados (`bluetoothctl disconnect <MAC>` y después
`connect <MAC>` -- o simplemente apagar y prender los auriculares) si
estaba conectado antes de aplicar los arreglos. Desde un boot limpio, no
hace falta ningún paso manual: `wpctl status` muestra el auricular como un
dispositivo `[bluez5]` con un nodo de sink, configurado como salida por
defecto, y `auriculares_conectados()` devuelve `True`.

### Conectar el pulsador físico

**Apretón corto (trigger de captura): hecho.** `sketch/sketch.ino` hace
debounce de D2 (debounce por conteo de muestras, no un temporizador de
"tiempo de quietud" -- ver el comentario al principio del archivo para el
porqué de esa distinción) y llama a `Bridge.notify("boton_presionado")`
una vez por apretón. `blindia/triggers/button.py` (`ButtonTrigger`) lo
recibe del lado Python y es lo que `main.py` espera en su loop principal --
ver `blindia/triggers/base.py` para la interfaz `Trigger` que implementa
cada fuente de eventos (este o `KeyboardTrigger`).

**Apretón largo (emparejamiento Bluetooth): todavía no conectado.** Una vez
que el sketch distinga un apretón largo, conectarlo a
`iniciar_emparejamiento_en_segundo_plano()` de
`blindia/audio/bluetooth_audio.py` -- no bloquea (corre el escaneo en un
hilo aparte) y ya anuncia cada paso por voz sola:

```python
from blindia.audio.bluetooth_audio import iniciar_emparejamiento_en_segundo_plano

Bridge.provide("on_long_press", lambda: iniciar_emparejamiento_en_segundo_plano())
```

### Troubleshooting de Bluetooth

- **No se encuentra nada durante el escaneo**: la mayoría de los
  auriculares solo se anuncian mientras están activamente en modo de
  emparejamiento (a menudo una combinación específica de botones, a veces
  con tiempo límite). Volver a entrar en modo de emparejamiento y
  reintentar -- la ventana de escaneo es de 20s por defecto
  (`emparejar_auriculares(timeout_escaneo=...)` para cambiarla).
- **Se encuentra, pero el emparejamiento falla**: este módulo registra un
  agente `NoInputNoOutput`, que acepta automáticamente el emparejamiento
  Bluetooth "Just Works" (la gran mayoría de los auriculares A2DP
  modernos). Los dispositivos más viejos que requieren tipear o confirmar
  un PIN **no** están soportados por este flujo -- no hay pantalla donde
  mostrar un PIN. Revisar `arduino-app-cli app logs` para la salida cruda
  de `bluetoothctl` que disparó la falla.
- **Emparejado, pero `auriculares_conectados()` devuelve `False`**:
  revisar `bluetoothctl info <MAC>` por `Connected: yes`, después
  `wpctl status` / `pw-dump` por un sink `bluez_output.*`. Al 2026-08-31
  este problema **no** se cierra solo en esta placa -- ver "Limitación
  conocida: ruteo de audio Bluetooth" más arriba para el diagnóstico
  completo (el monitor bluez5 de PipeWire carga pero nunca ve el
  dispositivo conectado). Reintentar `configurar_salida_default()` no va a
  ayudar hasta que se arregle a nivel más profundo.
- **Andaba, dejó de reconectarse solo**: confirmar que el dispositivo sigue
  `trusted` (`bluetoothctl info <MAC>` -> `Trusted: yes`); si no, correr
  `--emparejar` de nuevo.
- **El scripting de `bluetoothctl` se ve frágil / deja de matchear las
  líneas esperadas**: su salida en texto plano no es una API estable y
  puede variar levemente entre versiones de BlueZ. Si el emparejamiento
  deja de funcionar en silencio después de una actualización de BlueZ,
  revisar `bluetoothctl` interactivamente primero para ver si cambió el
  texto de sus mensajes, y después actualizar los regex al principio de
  `bluetooth_audio.py` (`_PATRON_*`).

## Testing hoy

`main.py` corre una captura al arrancar como smoke test, y después espera
al pulsador físico:

```bash
arduino-app-cli app start /home/arduino/ArduinoApps/blindia
arduino-app-cli app logs /home/arduino/ArduinoApps/blindia --follow
```

Una corrida exitosa loguea la ruta de la captura, el texto del OCR, el
`RecognizedProduct` parseado (`name=... price=...`), y un desglose de
tiempos por etapa (líneas `[timing] ...` -- captura, OCR, parseo de
producto, y el viaje completo de síntesis+reproducción). Si la cámara está
desconectada u ocupada, o el motor de OCR falla al cargar, la falla se
loguea en vez de tirar abajo la app.

## Próximos pasos

- Emparejamiento Bluetooth con apretón largo: todavía no conectado, ver
  "Conectar el pulsador físico" más arriba.
- Retomar el reconocimiento visual de producto (ver más arriba) si la
  identificación solo-por-OCR resulta insuficiente en la práctica.
- ~~Conectar `hablar()` en `run_capture()`~~ -- **hecho**.
- ~~Pulsador físico (apretón corto -> captura)~~ -- **hecho**, ver
  "Conectar el pulsador físico" más arriba.
- ~~Ruteo de audio Bluetooth (PipeWire nunca crea un sink para el
  auricular conectado)~~ -- **arreglado el 2026-09-01**, ver "Ruteo de
  audio Bluetooth (resuelto 2026-09-01)" más arriba.
