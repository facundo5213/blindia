# 🥸 BlindIA

Anteojos asistivos para personas con baja visión / ciegas. Un armazón de
anteojos con cámara detecta productos en una góndola y lee en voz alta su
precio, disparado por un pulsador físico.

## Cómo funciona

Un apretón del pulsador físico (leído en el MCU, transmitido a Linux por el
Router Bridge) dispara el pipeline:

```
apretón del botón -> captura de cámara -> OCR (RapidOCR) -> parseo de producto/precio -> resultado hablado
```

El resultado se dice por auriculares Bluetooth si están conectados, cayendo
al parlante local de la placa si no. La síntesis de voz usa
[Piper](https://github.com/rhasspy/piper) (TTS neuronal) como motor
principal, con `espeak-ng` como respaldo si Piper no está disponible.

## Hardware

- Arduino UNO Q (MPU corriendo la app Python + MCU corriendo el sketch del botón)
- Cámara USB
- Un pulsador físico cableado a un pin digital
- Auriculares Bluetooth (opcional -- cae al parlante local si no hay)

## Estructura del proyecto

```
app.yaml                # manifiesto de la Arduino App
python/                 # la app del lado Linux (captura, OCR, parseo de producto, cliente de audio)
sketch/                 # sketch del MCU: lectura del botón con debounce, avisa a Python por el Bridge
scripts/                # daemon de Bluetooth/voz del lado host (ver docs/DEVELOPMENT.md para el porqué
                         # de que sea un proceso aparte) + su unidad de systemd
python-libraries/        # un wheel de RapidOCR parcheado (ver python-libraries/README.md para el porqué)
docs/DEVELOPMENT.md      # documentación técnica completa: desglose módulo por módulo, la
                         # instalación de Piper TTS, emparejamiento/troubleshooting de Bluetooth,
                         # problemas conocidos, y el razonamiento detrás de las decisiones no obvias
```

## Documentación

- **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** -- la referencia técnica
  detallada: arquitectura, notas módulo por módulo, configuración y
  troubleshooting de audio/Bluetooth, heurísticas de reconocimiento de
  producto, y qué queda pendiente.
- **[python-libraries/README.md](python-libraries/README.md)** -- por qué
  se incluye un wheel de RapidOCR parcheado en vez de instalarlo desde PyPI.
