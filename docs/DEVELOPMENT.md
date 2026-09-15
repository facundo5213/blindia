# Development notes

Detailed, module-by-module documentation for BlindIA. See the top-level
[README](../README.md) for a short project overview.

## Current stage

Full loop, end to end: a physical button press (read on the MCU, relayed to
Linux over the Router Bridge -- see "Hooking up the physical button" below)
triggers the USB camera to take a photo, extracts any visible text
(RapidOCR, PP-OCRv5 mobile weights), parses a best-effort product name +
price out of that text, and speaks the result out loud (Piper TTS primary,
espeak-ng fallback -- see "Audio Bluetooth" below) over Bluetooth headphones
or the local speaker.

## Structure

```
python/
├── main.py                   # entry point; wires everything together
├── requirements.txt           # onnxruntime (rapidocr's real deps ship via python-libraries/)
└── blindia/
    ├── config.py              # central settings (camera, OCR, paths)
    ├── exceptions.py          # BlindIAError and subclasses
    ├── models.py              # CaptureResult, OcrResult, RecognizedProduct
    ├── pipeline.py            # CapturePipeline: trigger -> capture -> save
    ├── capture/
    │   └── service.py         # CameraCaptureService: owns the camera peripheral
    ├── storage/
    │   └── image_store.py     # ImageStore: saves frames as timestamped JPEGs
    ├── ocr/
    │   └── service.py         # OcrService: RapidOCR text extraction from a frame
    ├── product/
    │   └── parser.py          # parse_product: OcrResult -> RecognizedProduct
    └── audio/
        └── bluetooth_audio.py # hablar(): voice output over Bluetooth headphones, local speaker fallback

python-libraries/
├── README.md                  # why rapidocr ships as a patched local wheel
└── rapidocr-3.9.2-py3-none-any.whl
```

`CapturePipeline.capture()` is the one reusable call future triggers should
make: the physical button (via the Bridge, once the sketch reads it) and,
later, a manual "recapture" action. It returns a `CaptureResult`
(`image_path`, in-memory `frame`, `captured_at`), which `main.py` feeds into
`OcrService.extract_text()` and then `parse_product()`.

Captured photos are saved to `data/captures/` (gitignored).

## Product recognition

**MVP decision: the OCR text *is* the product identifier.** A gondola price
label already prints brand + product name + price, so `parse_product()`
(`blindia/product/parser.py`) turns `OcrResult.text` into a `RecognizedProduct`
(`name`, `price`, plus the original `raw_text` as a fallback) with a couple of
regex heuristics -- no visual recognition model. This covers the main use
case (a legible price label in frame) at zero extra latency, RAM, or cost
over what OCR already pays.

Three visual-recognition alternatives were evaluated and deliberately
**not** implemented in this MVP:

- **Local generic classifier** (`arduino:image_classification` Brick +
  built-in `mobilenet-image-classification` model). Technically viable on
  this board's CPU (lightweight, no crash risk, already provisioned by the
  framework) but the model is a generic ImageNet-1k classifier -- it
  outputs coarse container categories ("bottle", "can"), not brand or
  product identity. Doesn't produce the kind of answer this app needs.
- **Custom-trained local classifier** (Edge Impulse model, closed set of
  known products, same Brick). Would give real product-level accuracy and
  run fully offline, but requires collecting and labeling a training photo
  set per product first -- a separate project, not something addable on
  top of the current pipeline.
- **Cloud multimodal fallback** (`arduino:cloud_llm` Brick; Claude/GPT/Gemini
  all support `images=[...]` already, see its example
  `bricks/arduino/cloud_llm/06_multimodal`). Would give real brand/product
  recognition from packaging/logo even without legible text, at low
  per-call cost (a fraction of a cent with a cheap model). Deferred because
  it needs network connectivity, adds request latency on top of OCR's
  ~2-3s, needs an API key provisioned, and has a variable per-use cost the
  project doesn't want to take on yet.

The natural next step, if/when this is revisited: gate the cloud fallback to
only fire when `RecognizedProduct.name`/`price` come back `None` (OCR found
nothing usable), instead of calling it on every capture.

## Audio Bluetooth

`blindia/audio/bluetooth_audio.py` is the module the rest of the system uses
to speak. Its only real entry point is `hablar(texto)`: it decides on its
own whether to use connected Bluetooth headphones or fall back to the
board's local speaker, so no other module needs to know anything about
Bluetooth, PipeWire, or how those failures are handled. Independent module,
same spirit as `capture/`, `ocr/`, and `product/`: nothing in `main.py`,
`pipeline.py`, or the trigger modules was touched to add it.

It's built mostly on system tools already present on this board, plus one
Python dependency (Piper, in its own venv -- see "Voz: Piper TTS" below):

- **`bluetoothctl`** (BlueZ) to scan, pair, `trust`, and connect the
  headphones. Once a device is `trusted`, BlueZ reconnects it automatically
  whenever it's nearby and powered on -- no app code needed for that part.
- **`pw-dump` / `wpctl`** (PipeWire + WirePlumber -- confirmed to be what
  actually runs on this Debian image; there is no native PulseAudio, though
  `pipewire-pulse` provides a compatible socket) to detect whether a
  Bluetooth headset is active as an audio sink and to set it as the default
  output.
- **Piper** for the actual text-to-speech synthesis (primary engine, fully
  offline): a small neural TTS model, noticeably clearer and more natural
  than a formant synthesizer -- see "Voz: Piper TTS" below for the model
  used and why it needs its own venv.
- **`espeak-ng`** as the **fallback** engine, used only if Piper isn't
  installed or a synthesis call fails: fully offline too, no network
  dependency, consistent with the OCR module's local-only philosophy on
  this board -- just noticeably more robotic-sounding than Piper.

Design choice, different from `capture/` and `ocr/`: this module does
**not** define its own exception hierarchy. Its functions never raise --
they log via `Logger` and always announce failures with `hablar()` itself,
returning `bool` instead. It doesn't make sense for the module that *is*
the system's voice-announcement layer to raise an exception for someone
else to turn into a spoken message.

### Installation

`espeak-ng` (the fallback engine) is not installed on this board by
default -- install it once:

```bash
sudo apt update && sudo apt install -y espeak-ng
```

Everything else this module needs (`bluetoothctl`, `wpctl`, `pw-dump`,
PipeWire's `libspa-0.2-bluetooth` plugin for A2DP sinks) was already present
and verified on the board; no extra install needed for those. Piper (the
primary engine) has its own install steps -- see the next section.

### Voz: Piper TTS

**Why Piper, and why it needs its own venv.** `espeak-ng` is a formant
synthesizer -- instant to run, but audibly robotic. Piper is a small neural
TTS model (ONNX Runtime), clearer and more natural, still fully offline. It
runs as a **separate host-side venv** at `scripts/.venv-piper/`, not
installed into the system Python: `pip install piper-tts` fails there with
`externally-managed-environment` (PEP 668, standard on this Debian image),
and a dedicated venv keeps this self-contained without touching the base
system's Python packages. The daemon's systemd unit (`ExecStart` in
`scripts/systemd/blindia-bluetooth-audio.service`) runs the **whole
daemon** with `.venv-piper/bin/python3` (not `/usr/bin/python3` -- the venv
still has the full stdlib, so nothing else about the daemon changes) so it
can `import piper` directly, in-process.

**Install steps** (host-side, no `sudo` needed -- run once):

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

(`pasimple` is a small ctypes binding to `libpulse-simple.so.0` -- already
present on this board, since `espeak-ng` itself is linked against it -- used
to play Piper's synthesized WAV through PipeWire's PulseAudio-compatible
socket. Plain `aplay`/ALSA was tried first and rejected: there's no
`pipewire-alsa` redirect configured on this image, so ALSA playback would
bypass PipeWire entirely and always hit the local speaker, ignoring
whichever sink `configurar_salida_default()` just set.)

**Model used: `es_ES-davefx-medium`** (Spain Spanish, male voice, "medium"
quality tier), **not** the higher-quality `es_AR-daniela-high` (Argentine
Spanish) that would otherwise be the obvious pick for this project. That
one was tried first and measured too slow for a live assistant on this
board's CPU (no NPU on the UNO Q):

| Model | Tier | Cold call (model load + synth) | Warm synth only (model cached) |
|---|---|---|---|
| `es_AR-daniela-high` | high | ~28-30s | ~7-16s per typical phrase |
| `es_ES-davefx-medium` | medium | ~13-15s | ~1-3s per typical phrase |

`es_AR` only ships a "high" tier voice in Piper's catalog (no
medium/low) -- so getting the Argentine accent back at usable speed would
mean training/finding a different model, out of scope for now. The
model-load cost is paid once, at daemon startup or first `hablar()` call
after a restart (`PiperVoice.load(...)`, cached in `_voz_piper_cache` in
`bluetooth_audio_daemon.py`) -- it's the **warm, per-call** number that
matters for responsiveness, since the daemon stays running for the app's
whole lifetime.

**Fallback behavior**: `_reproducir()` in `bluetooth_audio_daemon.py` tries
Piper first (`_reproducir_con_piper`); if the model/venv isn't available,
fails to load, or synthesis raises, it logs why and falls through to
espeak-ng (`_reproducir_con_espeak`) automatically -- same "never fail
silently" contract `hablar()` already has for Bluetooth-vs-local-speaker.

**Voice tuning** (`bluetooth_audio_daemon.py`, top of the file):
`_PIPER_LENGTH_SCALE = 1.15` (>1.0 slows down Piper's native pace a bit,
for clarity); espeak-ng's own tuning stays as the fallback-path settings
(`_PALABRAS_POR_MINUTO = 140`, `_PAUSA_ENTRE_PALABRAS = 6`).

### Testing on the real board

Voice-guided pairing, run from the app's `python/` directory inside the
running app (e.g. via `arduino-app-cli app logs --follow` in one terminal to
watch what gets logged, and a shell into the container in another):

```bash
python3 -m blindia.audio.bluetooth_audio --emparejar
# or, to only consider a device whose Bluetooth name contains a substring:
python3 -m blindia.audio.bluetooth_audio --emparejar --nombre "Sony"
```

Put the headphones in pairing mode first. You should hear, in order:
"Buscando auriculares Bluetooth" -> "Encontrado <nombre>. Emparejando." ->
"Conectando." -> "<nombre> conectado y listo para usarse." Any failure at
any step is announced instead (not left silent) and also logged.

To sanity-check the module's pieces independently of pairing:

```python
from blindia.audio.bluetooth_audio import hablar, auriculares_conectados
hablar("Prueba de audio.")           # should come out of whatever is connected right now
print(auriculares_conectados())      # True only once PipeWire actually has a bluez_output sink
```

**Fixed on 2026-09-01: audio now routes through Bluetooth headphones end to
end** (`auriculares_conectados()` returns `True` and `hablar()` plays
through them, verified live with a JBL Wave Beam 2). This was an
operating-system integration gap on this board's image, **not a bug in
`blindia`'s code** -- see "Bluetooth audio routing (resolved 2026-09-01)"
below for the full diagnosis and the fix. The local-speaker fallback still
exists and still works exactly as designed (e.g. if the headphones aren't
connected).

### Bluetooth audio routing (resolved 2026-09-01)

**Status: fixed.** Two operating-system-level changes were needed, applied
in this order, both host-side infrastructure and not app code. This section
keeps the full diagnosis (three sessions, two ruled-out hypotheses) since
the root cause was specific enough that it's worth understanding before
touching either fix again.

What's confirmed working, at the BlueZ level:

- Scanning, pairing, trusting, and connecting all succeed --
  `bluetoothctl info <MAC>` shows `Paired: yes`, `Bonded: yes`,
  `Trusted: yes`, `Connected: yes`, and a real battery percentage back from
  the headphones.
- Two real bugs in `emparejar_auriculares()` / `_buscar_dispositivo_de_audio()`
  (`scripts/bluetooth_audio_daemon.py`) were found and fixed while verifying
  this:
  1. `bluetoothctl`'s scan output wraps `NEW` in ANSI color codes (e.g.
     `[\x1b[0;92mNEW\x1b[0m] Device ...`) even when stdout isn't a TTY, so
     the original regex-based device-detection never matched *any* scanned
     device, ever. Fixed by stripping ANSI escape sequences
     (`_PATRON_ANSI`) from every line read from the `bluetoothctl` process
     before matching against it.
  2. The audio-capability pre-check (`_es_dispositivo_de_audio`, requiring
     an audio UUID in `bluetoothctl info` *before* pairing) rejected the
     JBL Wave Beam 2 even once it was visible, because it doesn't advertise
     full UUIDs pre-pairing (common -- many headsets only expose them after
     SDP, which happens during pairing). Fixed by skipping that check when
     `nombre_objetivo` is explicitly given (the caller already identified
     the device by name); it's still applied as a safety filter for a
     blind scan with no name.

What's confirmed broken, one layer up, at the PipeWire/WirePlumber level:

- After a successful BlueZ connect, `pw-dump` / `wpctl status` show
  **no Bluetooth device or sink at all** -- only `Built-in Audio` and the
  USB webcam's ALSA nodes. `auriculares_conectados()` (which looks for a
  `bluez_output.*` `Audio/Sink` node) correctly reports `False`, and
  `hablar()` correctly falls back to the local speaker exactly as designed.
- This isn't a startup-ordering race: restarting `wireplumber`
  (`systemctl --user restart wireplumber`) with the headphones already
  connected doesn't fix it either.
- WirePlumber's debug log (`WIREPLUMBER_DEBUG=3 wireplumber`, run in the
  foreground) confirms `script:monitors/bluez.lua` **does** get enabled as
  part of the active `main` profile (`hardware.bluetooth = required`) --
  but the log never mentions the device, its MAC, or anything
  `org.bluez`/A2DP-related, even though the ALSA monitor (`monitors/alsa.lua`)
  enables and enumerates hardware normally in the same run. So the bluez5
  SPA monitor loads, but never appears to see the connected device.

Two follow-up hypotheses were tested in a later session and **both ruled
out**, then a third, more specific cause was confirmed:

- **D-Bus permissions: ruled out.** Captured live D-Bus traffic with
  `sudo busctl monitor --system org.bluez` while restarting `wireplumber`
  -- **zero messages** to or from `org.bluez` appeared during the restart.
  If `Media1.RegisterEndpoint` were being called and rejected by policy,
  the call and its error reply would both show up in the monitor; it never
  even attempts the call. (The D-Bus policy file itself, actually at
  `/usr/share/dbus-1/system.d/bluetooth.conf` -- not `/etc/dbus-1/system.d/`,
  which is unused on this board -- was also read and looks fine: the
  `context="default"` policy already allows `send_destination="org.bluez"`
  for every user.)
- **Contention with `lightdm.service`: ruled out (and actively harmful to
  "fix").** `journalctl -u bluetooth -b` showed `bluetoothd` *does* get a
  full set of A2DP `MediaEndpoint1` objects registered on every boot -- but
  by a second, independent PipeWire/WirePlumber pair running under the
  `lightdm` user (the board's graphical login session that isn't actually
  used on this headless setup). Confirmed via
  `busctl --system call org.freedesktop.DBus /org/freedesktop/DBus
  org.freedesktop.DBus GetConnectionCredentials s ":1.31"` (the registering
  sender) returning `UnixUserID: 103` (`lightdm`), `ProcessID: 892`, a
  wholly separate `wireplumber` process from `arduino`'s own (a different
  PID, `ps -p 892` -> `lightdm /usr/bin/wireplumber`). Disabling
  `lightdm.service` (`sudo systemctl disable --now lightdm.service` --
  confirmed reversible, and reverted at the end of that session with
  `sudo systemctl enable --now lightdm.service`) removed that competing
  registration, but the `arduino`-user WirePlumber *still* never registered
  anything on the next reboot either -- and without `lightdm`'s
  registration as a fallback, even the plain BlueZ-level
  `bluetoothctl connect <MAC>` started failing outright with
  `org.bluez.Error.Failed br-connection-profile-unavailable` (no A2DP
  profile handler registered with `bluetoothd` at all anymore). So
  `lightdm` was never blocking `arduino`'s WirePlumber -- it was the
  *only* thing making Bluetooth audio profiles work on this board at all.
- **Real root cause, confirmed**: the `arduino`-user WirePlumber's `bluez5`
  monitor (`script:monitors/bluez.lua`, installed at
  `/usr/share/wireplumber/scripts/monitors/bluez.lua`) does start -- its
  debug log shows `startStopMonitor:550 <WpLogind> Seat state changed:
  online` -- but, unlike the ALSA and libcamera monitors in the exact same
  run, it never proceeds any further: no SPA `bluez5` device/adapter object
  gets created, and it never touches `org.bluez` at all. This held true
  both with and without `lightdm` present, across every restart tested (via
  the empty `busctl monitor` capture and zero registration activity in
  `journalctl -u bluetooth` for any `arduino`-session WirePlumber run).
  Something specific to how `bluez.lua` runs in the `arduino` user's
  session stops it right after that "Seat state changed: online" log line,
  before it ever reaches BlueZ -- not resource contention, not D-Bus
  policy. Reading `bluez.lua` confirmed why: `startStopMonitor` only calls
  `createMonitor()` when the logind-reported seat state is `"active"`. The
  `arduino` session runs headless over SSH and has no logind seat at all
  (`loginctl list-sessions` shows `SEAT` empty for both of its sessions),
  so the state it gets is `"online"`, never `"active"` -- the monitor is
  gated forever.

**Fix, part 1 -- disable bluez seat-monitoring for the `arduino` session.**
WirePlumber's own shipped config (`/usr/share/wireplumber/wireplumber.conf`)
already has a `mixin.systemwide-session` block that disables this exact
feature (`monitor.bluez.seat-monitoring = disabled`), meant for its
`main-systemwide`/`main-embedded` profiles -- but this board's active
profile is plain `main`, which doesn't inherit it. Rather than switch
profiles (which also disables state persistence and device reservation --
more than needed), a user-scope drop-in applies just that one key to `main`:

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

No `sudo` needed (user-owned config dir). After creating it:
`systemctl --user restart wireplumber`.

**Fix, part 2 -- stop `lightdm.service` from also managing Bluetooth
audio.** This part alone was tried in isolation first (see "ruled out"
above) and made things worse, because at that point `arduino`'s WirePlumber
still couldn't register anything (part 1 not applied yet), leaving zero
registrars. Once part 1 is in place, `arduino`'s WirePlumber *can* register
-- but `lightdm`'s WirePlumber is still running and tries to as well, and
the two collide (`spa.bluez5.native: RegisterProfile() failed:
org.bluez.Error.NotPermitted`, `listen(): Address already in use`, and a
transport stuck in an "unknown" state -- visible in
`journalctl --user -u wireplumber`). With both fixes applied, disabling
`lightdm.service` removes the collision and `arduino`'s WirePlumber becomes
the sole, working Bluetooth audio manager:

```bash
sudo systemctl disable --now lightdm.service
```

Reversible with `sudo systemctl enable --now lightdm.service` (this board
is headless -- confirmed with the user before disabling -- so `lightdm`'s
graphical login session isn't used for anything).

**After both fixes**, a device that's already `Paired`/`Trusted` in BlueZ
needs one reconnect to pick up the newly-registered endpoints
(`bluetoothctl disconnect <MAC>` then `connect <MAC>` -- or just power-cycle
the headphones) if it was connected before the fixes went in. From a clean
boot, no manual step is needed: `wpctl status` shows the headset as an
`[bluez5]` device with a sink node, set as the default output, and
`auriculares_conectados()` returns `True`.

### Hooking up the physical button

**Short press (capture trigger): done.** `sketch/sketch.ino` debounces D2
(sample-counting debounce, not a "quiet time" timer -- see the comment at
the top of the file for why that distinction matters) and calls
`Bridge.notify("boton_presionado")` once per press. `blindia/triggers/button.py`
(`ButtonTrigger`) receives it on the Python side and is what `main.py` waits
on in its main loop -- see `blindia/triggers/base.py` for the `Trigger`
interface every event source (this one, or `KeyboardTrigger`) implements.

**Long press (Bluetooth pairing): not wired yet.** Once the sketch
distinguishes a long press, wire it to
`iniciar_emparejamiento_en_segundo_plano()` from
`blindia/audio/bluetooth_audio.py` -- it's non-blocking (runs the scan in a
background thread) and already announces every step by voice on its own:

```python
from blindia.audio.bluetooth_audio import iniciar_emparejamiento_en_segundo_plano

Bridge.provide("on_long_press", lambda: iniciar_emparejamiento_en_segundo_plano())
```

### Bluetooth troubleshooting

- **Nothing found during scan**: most headphones only advertise while
  actively in pairing mode (often a specific button combo, sometimes
  timed). Re-enter pairing mode and retry -- the scan window is 20s by
  default (`emparejar_auriculares(timeout_escaneo=...)` to change it).
- **Found, but pairing fails**: this module registers a `NoInputNoOutput`
  agent, which auto-accepts "Just Works" Bluetooth pairing (the vast
  majority of modern A2DP headphones). Older devices that require typing or
  confirming a PIN are **not** supported by this flow -- there is no screen
  to show a PIN on. Check `arduino-app-cli app logs` for the raw
  `bluetoothctl` output that triggered the failure.
- **Paired, but `auriculares_conectados()` returns `False`**: check
  `bluetoothctl info <MAC>` for `Connected: yes`, then `wpctl status` /
  `pw-dump` for a `bluez_output.*` sink. As of 2026-08-31 this gap does
  **not** close on its own on this board -- see "Known limitation:
  Bluetooth audio routing" above for the full diagnosis (PipeWire's bluez5
  monitor loads but never sees the connected device). Retrying
  `configurar_salida_default()` won't help until that's fixed upstream.
- **Was working, stopped reconnecting automatically**: confirm the device
  is still `trusted` (`bluetoothctl info <MAC>` -> `Trusted: yes`); if not,
  re-run `--emparejar`.
- **`bluetoothctl` scripting looks fragile / stops matching expected
  lines**: its plain-text output isn't a stable API and can vary slightly
  across BlueZ versions. If pairing silently stops working after a BlueZ
  upgrade, check `bluetoothctl` interactively first to see if its message
  wording changed, then update the regexes at the top of
  `bluetooth_audio.py` (`_PATRON_*`).

## Testing today

`main.py` runs one capture at startup as a smoke test, then waits for the
physical button:

```bash
arduino-app-cli app start /home/arduino/ArduinoApps/blindia
arduino-app-cli app logs /home/arduino/ArduinoApps/blindia --follow
```

A successful run logs the capture path, the OCR text, the parsed
`RecognizedProduct` (`name=... price=...`), and a per-stage timing
breakdown (`[timing] ...` lines -- capture, OCR, product parsing, and the
full synthesis+playback round trip). If the camera is disconnected or busy,
or the OCR engine fails to load, the failure is logged instead of crashing
the app.

## Next steps

- Long-press Bluetooth pairing: not wired yet, see "Hooking up the physical
  button" above.
- Revisit visual product recognition (see above) if OCR-only identification
  proves insufficient in practice.
- ~~Wire `hablar()` into `run_capture()`~~ -- **done**.
- ~~Physical button (short press -> capture)~~ -- **done**, see "Hooking up
  the physical button" above.
- ~~Bluetooth audio routing (PipeWire never creates a sink for the connected
  headset)~~ -- **fixed 2026-09-01**, see "Bluetooth audio routing (resolved
  2026-09-01)" above.
