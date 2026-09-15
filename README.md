# 🥸 BlindIA

Wearable assistive glasses for people with low vision / blindness. A
camera-equipped glasses frame detects products on a shelf and reads their
price aloud, triggered by a physical button.

## How it works

A press of the physical button (read on the MCU, relayed to Linux over the
Router Bridge) triggers the pipeline:

```
button press -> camera capture -> OCR (RapidOCR) -> product/price parsing -> spoken result
```

The result is spoken through Bluetooth headphones if connected, falling
back to the board's local speaker otherwise. Voice synthesis uses
[Piper](https://github.com/rhasspy/piper) (neural TTS) as the primary
engine, with `espeak-ng` as a fallback if Piper isn't available.

## Hardware

- Arduino UNO Q (MPU running the Python app + MCU running the button sketch)
- USB camera
- A physical push button wired to a digital pin
- Bluetooth headphones (optional -- falls back to the local speaker)

## Project layout

```
app.yaml                # Arduino App manifest
python/                 # the Linux-side app (capture, OCR, product parsing, audio client)
sketch/                 # MCU sketch: debounced button read, notifies Python over the Bridge
scripts/                # host-side Bluetooth/voice daemon (see docs/DEVELOPMENT.md for why
                         # it's a separate process) + its systemd unit
python-libraries/        # a patched RapidOCR wheel (see python-libraries/README.md for why)
docs/DEVELOPMENT.md      # full technical documentation: module-by-module breakdown, the
                         # Piper TTS setup, Bluetooth pairing/troubleshooting, known issues,
                         # and the reasoning behind the non-obvious decisions
```

## Documentation

- **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** -- the detailed technical
  reference: architecture, module-by-module notes, audio/Bluetooth setup
  and troubleshooting, product recognition heuristics, and what's still
  pending.
- **[python-libraries/README.md](python-libraries/README.md)** -- why a
  patched RapidOCR wheel is vendored instead of installed from PyPI.
