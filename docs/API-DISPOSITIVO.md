# API del dispositivo Blind IA (firmware → web)

Contrato para el firmware del anteojo (Arduino / ESP32). Con esta API el anteojo:

1. **Avisa su estado** (batería, Bluetooth, versión de firmware) → se ve en el panel del cliente.
2. **Reporta cada producto identificado** → aparece en el historial del cliente y descuenta UAD (plan Plus).
3. **Descarga la configuración** que el cliente eligió en su panel (voz, volumen, velocidad, nivel de detalle…).

Todo es HTTP + JSON. No se envían imágenes (todavía).

> **Nota de implementación en este repo**: el contrato abajo está escrito pensando en un ESP32 (NVS, `millis()`, el ejemplo en C++, sección 6). Esta placa es Linux + Python, así que la implementación real vive en `python/blindia/backend/` y todo lo persistente va en archivos de `data/` excluidos de git -- ver "Implementación en este repo" al final para el mapeo concreto.

---

## 1. Ciclo de vida de un aparato

1. **Alta por USB (admin):** el admin conecta el anteojo por USB a una computadora con Chrome/Edge y, en el panel admin (`/admin/dispositivos`), toca **"Vincular anteojo por USB"**. La web habla con el anteojo por **Web Serial** (ver [docs/VINCULACION-USB.md](VINCULACION-USB.md)): lee su **número de serie de hardware**, lo registra, genera su **clave** y se la **escribe al anteojo**. Las credenciales **ya no se cargan a mano ni se graban en el firmware**: llegan por USB y el anteojo las guarda en `data/device_credentials.json`.
   - **Número de serie:** el del hardware, tal cual lo informa la placa (p. ej. `674441474`). Va en cada pedido a la API (`X-Device-Serial`).
   - **Clave del dispositivo** (`bia_...`, 47 caracteres): es la contraseña del aparato. La genera la web; en la base **solo se guarda su hash**.
   - **Código de enlace** (`ABCD-2345`): lo genera la web en el mismo alta; va impreso en la caja y lo usa el *cliente* para enlazar el aparato a su cuenta. El firmware **no** lo usa.
   - *Alternativa:* si no se puede usar USB, el admin puede dar de alta un aparato a mano (la web genera serial `BIA-…` y clave, y la clave se graba a mano en el firmware).
2. **Cliente:** compra, ingresa a su panel y enlaza el aparato con serial + código (o el admin se lo asigna directamente al vincularlo por USB).
3. **Uso:** el firmware llama a la API con las credenciales que recibió por USB. Si el aparato **todavía no está enlazado a una cuenta**, la telemetría funciona pero los productos se rechazan con `409 device_not_linked`.
4. **Reemplazo de credenciales:** repetir la vinculación por USB sobre un anteojo que ya estaba vinculado (la web pide confirmación): la clave anterior deja de funcionar al instante.

## 2. Conexión y autenticación

| | Desarrollo | Producción |
|---|---|---|
| URL base | `http://<IP-o-nombre-de-la-PC>:3000` (misma red que el anteojo) | **`https://blind-ia.vercel.app`** (o el dominio propio, si se agrega) |
| Protocolo | HTTP permitido | **Solo HTTPS** |

> **Producción:** usar solo `https://blind-ia.vercel.app`, nunca una dirección de *Preview* de Vercel (cambian en cada despliegue y están protegidas). Guardar la URL base como **configuración** del anteojo, no fija en el código. HTTPS valida con los certificados del sistema: el anteojo necesita el **reloj en hora (NTP)**.
>
> En desarrollo, `npm run dev` debe estar corriendo en la PC y el firewall de Windows tiene que dejar entrar el puerto 3000. `localhost` NO sirve desde el ESP32: hay que usar la IP de la PC (`ipconfig`).

Cada pedido lleva **dos cabeceras**:

```
X-Device-Serial: BIA-1A2B3C4D
Authorization: Bearer bia_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Si el serial o la clave no coinciden → `401` (siempre el mismo mensaje, no se revela cuál falló).

Los cuerpos son JSON (`Content-Type: application/json`), **máximo 4 KB**.

## 3. Endpoints

### `POST /api/device/telemetry` — estado / latido

Todos los campos son opcionales. **Cada llamada actualiza "última conexión"**, así que `{}` sirve como latido.

```json
{ "battery_pct": 82, "bluetooth_connected": true, "firmware": "1.2.0" }
```

| Campo | Tipo | Regla |
|---|---|---|
| `battery_pct` | entero | 0 a 100 |
| `bluetooth_connected` | booleano | `true`/`false` |
| `firmware` | texto | hasta 32 caracteres |

Respuesta `200`: `{ "ok": true, "server_time": 1792345726 }` (`server_time` = hora Unix en segundos; útil para sincronizar el reloj).

### `POST /api/device/identifications` — producto identificado

```json
{
  "event_id": "BIA-1A2B3C4D-000142",
  "product": "Yerba mate Playadito 1 kg",
  "price_ars": 4890,
  "confidence": 0.93,
  "uad_cost": 1,
  "seconds_ago": 0
}
```

| Campo | Tipo | Obligatorio | Regla |
|---|---|---|---|
| `event_id` | texto | **sí** | 8–64 caracteres, **único por evento**. Si se reenvía el mismo, la web **no lo duplica** (`duplicate: true`). |
| `product` | texto | **sí** | 1–200 caracteres |
| `price_ars` | entero | no | pesos argentinos, `0` a `100000000` |
| `confidence` | número | no | `0` a `1` |
| `uad_cost` | entero | no | `1` a `10`, por defecto `1` -- *la web también acepta `0` (confirmado), para identificaciones resueltas localmente sin IA; ver "Implementación en este repo"* |
| `seconds_ago` | entero | no | segundos desde que ocurrió (si el evento esperó en cola sin señal). `0` a `604800` |

Respuestas:

- `201` evento nuevo: `{ "ok": true, "duplicate": false, "uad": { "used": 3, "included": 50, "remaining": 47, "excess": 0, "charge": 0 } }`
- `200` evento ya recibido: igual, con `"duplicate": true`.
- `409 device_not_linked`: el aparato no está enlazado a ninguna cuenta.
- `429 rate_limited`: más de 30 eventos en un minuto.

`uad` = consumo de la semana en curso (el plan Plus incluye 50 UAD semanales; el excedente cuesta $100 ARS c/u). El firmware puede usarlo para avisar por voz "te quedan N análisis".

### `GET /api/device/config[?since=<version>]` — configuración del cliente

```json
{
  "ok": true,
  "changed": true,
  "version": 1792345715,
  "server_time": 1792345726,
  "linked": true,
  "settings": {
    "nickname": "Mi Blind IA",
    "volume": 70,
    "speech_rate": "normal",
    "voice": "femenina",
    "detail_level": "breve",
    "confirm_sound": true
  },
  "plan": { "plus": true },
  "uad": { "used": 3, "included": 50, "remaining": 47, "excess": 0, "charge": 0 }
}
```

| Ajuste | Valores | Cómo aplicarlo en el firmware (sugerencia) |
|---|---|---|
| `volume` | 0–100 | volumen del parlante |
| `speech_rate` | `lenta` · `normal` · `rapida` | velocidad de la voz (≈ 0,8× · 1,0× · 1,25×) |
| `voice` | `femenina` · `masculina` | voz del TTS |
| `detail_level` | `breve` · `detallada` | `breve`: "producto, precio". `detallada`: agrega marca, presentación y promociones si las detecta |
| `confirm_sound` | true/false | beep corto al apretar el botón, antes de la respuesta |
| `nickname` | texto o `null` | nombre que el cliente le puso (opcional: "Hola, Mi Blind IA") |

**Ahorro de datos:** guardá el `version` que recibiste. En la próxima consulta mandá `?since=<version>`; si el cliente no cambió nada, la respuesta es mínima: `{ "ok": true, "changed": false, "version": ..., "server_time": ... }`. `plan` y `uad` solo vienen con `"changed": true`.

Si el aparato no está enlazado: `"linked": false` y `plan`/`uad` en `null` (los ajustes vienen con valores de fábrica).

## 4. Códigos de error

| HTTP | `error` | Qué hacer |
|---|---|---|
| 400 | `bad_request` | Cuerpo inválido (el campo `message` explica cuál). **No reintentar igual**: es un bug del firmware. |
| 401 | `unauthorized` | Serial o clave mal. No insistir cada pocos segundos; esperar ≥ 5 min y avisar al usuario (p. ej. LED de error). |
| 409 | `device_not_linked` | Dejar de mandar productos. Seguir con telemetría y revisar `config` cada tanto (`linked`) hasta que el cliente lo enlace. |
| 429 | `rate_limited` | Esperar 60 s antes de seguir. |
| 5xx / sin red | — | Reintentar con espera creciente (5 s, 15 s, 60 s, máx. 5 min). |

## 5. Comportamiento recomendado del firmware

- **Al conectar a WiFi:** `GET /config` y `POST /telemetry` (batería + firmware).
- **Latido:** `POST /telemetry` cada 60–120 s mientras haya conexión, y además cuando la batería cambie ≥ 5 puntos o se pase del 20 % hacia abajo. El panel considera al aparato "en línea" si avisó en los últimos 5 minutos.
- **Configuración:** `GET /config?since=<version>` al conectar y cada ~5 minutos.
- **`event_id`:** debe ser **único y estable ante reintentos**. Ejemplo: `<serial>-<contador de 6 dígitos>` con el contador guardado en memoria no volátil (NVS/EEPROM) para que no se repita tras reiniciar. Si el pedido falla por red, **reenviá el mismo `event_id`** (la web ignora el duplicado).
- **Sin señal:** guardá los eventos en una cola (idealmente persistente, p. ej. los últimos 20) y mandalos cuando vuelva la red, con `seconds_ago` = segundos transcurridos desde el evento (`(millis() - tEvento) / 1000`).
- **Privacidad:** el producto y el precio son datos de la vida cotidiana de una persona; no registrar la clave ni los eventos en logs persistentes.
- **Seguridad:** guardar la clave en una zona no legible fácilmente (NVS cifrada / flash encryption en ESP32). En producción usar HTTPS validando el certificado (no `setInsecure()`).

## 6. Ejemplo ESP32 (Arduino + ArduinoJson)

```cpp
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char* BASE_URL   = "http://192.168.0.10:3000";          // desarrollo; producción: "https://tu-dominio"
const char* SERIAL_NO  = "BIA-1A2B3C4D";
const char* DEVICE_KEY = "bia_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx";

long configVersion = 0;

// Devuelve el código HTTP (o <0 si falló la red). Si outBody != nullptr, guarda la respuesta.
int api(const char* method, const String& path, const String& body, String* outBody = nullptr) {
  HTTPClient http;
  WiFiClientSecure secure;
  WiFiClient plain;

  String url = String(BASE_URL) + path;
  if (url.startsWith("https")) {
    secure.setInsecure();            // SOLO desarrollo: en producción cargar el certificado raíz (secure.setCACert)
    http.begin(secure, url);
  } else {
    http.begin(plain, url);
  }
  http.addHeader("X-Device-Serial", SERIAL_NO);
  http.addHeader("Authorization", String("Bearer ") + DEVICE_KEY);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(8000);

  int code = (String(method) == "GET") ? http.GET() : http.POST(body);
  if (outBody && code > 0) *outBody = http.getString();
  http.end();
  return code;
}

void sendTelemetry(int batteryPct, bool bluetooth) {
  JsonDocument doc;
  doc["battery_pct"] = batteryPct;
  doc["bluetooth_connected"] = bluetooth;
  doc["firmware"] = "1.0.0";
  String body; serializeJson(doc, body);
  api("POST", "/api/device/telemetry", body);
}

// eventId: ej. "BIA-1A2B3C4D-000142" (único y estable ante reintentos)
bool sendIdentification(const String& eventId, const String& product, int priceArs, float confidence) {
  JsonDocument doc;
  doc["event_id"] = eventId;
  doc["product"] = product;
  if (priceArs >= 0) doc["price_ars"] = priceArs;
  doc["confidence"] = confidence;
  String body; serializeJson(doc, body);
  int code = api("POST", "/api/device/identifications", body);
  return code == 200 || code == 201;   // 200 = duplicado (ya estaba): también cuenta como éxito
}

void syncConfig() {
  String resp;
  int code = api("GET", "/api/device/config?since=" + String(configVersion), "", &resp);
  if (code != 200) return;
  JsonDocument doc;
  if (deserializeJson(doc, resp)) return;
  if (!doc["changed"].as<bool>()) return;             // sin cambios

  configVersion = doc["version"].as<long>();
  int volume = doc["settings"]["volume"];             // 0-100
  String rate = doc["settings"]["speech_rate"];       // lenta | normal | rapida
  String voice = doc["settings"]["voice"];            // femenina | masculina
  String detail = doc["settings"]["detail_level"];    // breve | detallada
  bool beep = doc["settings"]["confirm_sound"];
  // ...aplicar al parlante / TTS y persistir en NVS
}
```

## 7. Probar la web sin hardware

```bash
npm run device:sim -- --serial BIA-1A2B3C4D --key bia_xxxx --url http://localhost:3000 --battery 80
npm run device:sim -- --serial BIA-1A2B3C4D --key bia_xxxx --loop        # latidos + productos cada tanto
```

Mientras corre, el panel del cliente (`/panel`) muestra la batería, "En línea" y los productos, y se actualiza solo cada 30 s.

## 8. Qué NO hace esta API (todavía)

- **No recibe imágenes ni audio.** La identificación del producto la hace el aparato (o su propio backend) y reporta el *resultado*. Si se quiere que la web/la nube analice la foto, hay que agregar un endpoint de imagen (decisión pendiente).
- **No hay comandos de la web hacia el aparato** (apagar, buscar, etc.): el aparato consulta (`/config`), la web no le empuja nada.
- **No hay firma OTA de firmware** ni descarga de actualizaciones.
- Las credenciales **no se pueden pedir por la API**: solo llegan por USB (o, en el alta manual, grabadas a mano).

---

## Implementación en este repo

**Estado: implementado y probado contra la API de producción** (`https://blind-ia.vercel.app`), con el anteojo real ya vinculado por USB.

Toda la lógica vive en `python/blindia/backend/` (corre dentro del contenedor de la app -- a diferencia de otras piezas de este proyecto, HTTPS hacia `blind-ia.vercel.app` funciona bien desde ahí, confirmado en vivo, así que no hace falta un daemon de host aparte como sí hace falta para Bluetooth o mDNS):

| Módulo | Qué hace | Endpoint(s) |
|---|---|---|
| `backend/client.py` | Cliente HTTP de bajo nivel: lee `data/device_credentials.json`, arma `X-Device-Serial`/`Authorization`, clasifica errores según la tabla de la sección 4. Nunca loguea la clave. Nunca lanza. | -- |
| `backend/telemetry.py` | Latido al arrancar y cada `ApiSettings.telemetry_interval_segundos` (90s, dentro del rango 60-120s del contrato). `bluetooth_connected` real (vía `blindia.audio.bluetooth_audio.auriculares_conectados`), `firmware` fijo (`config.FIRMWARE_VERSION`). **Nunca manda `battery_pct`** (esta placa no mide batería -- se omite el campo, nunca se manda en `null`). | `POST /telemetry` |
| `backend/identifications.py` | Cola persistente (`data/identification_queue.json`, hasta `ApiSettings.max_eventos_en_cola` = 200, el contrato pide "al menos 20") con reintentos de backoff creciente (5s/15s/60s/300s) ante red caída o 5xx, pausa larga ante 401, pausa ante 429, y pausa hasta que se confirme vinculación ante 409. `event_id` = `<serial>-<contador de 6 dígitos>`, contador persistente en `data/identification_counter.txt` -- nunca se repite, ni tras reiniciar la app. | `POST /identifications` |
| `backend/remote_config.py` | Encuesta `GET /config?since=<version>` al arrancar y cada `ApiSettings.config_poll_interval_segundos` (5 min). Versión aplicada persistida en `data/remote_config_version.txt`. Aplica **solo** `volume` y `speech_rate` (al daemon de audio Bluetooth, `scripts/bluetooth_audio_daemon.py`, cmd `ajustar_voz`) y `detail_level` (leído por `main.py` antes de hablar). **Ignora `voice` y `confirm_sound` a propósito**: no hay selección de voz del TTS ni beep de confirmación implementados, y no se agregaron solo para que esos dos campos "hagan algo". | `GET /config` |
| `backend/state.py` | Dato compartido mínimo entre los dos módulos de arriba: si el equipo está vinculado (`linked`), para que la cola de identificaciones sepa cuándo reintentar después de un 409 sin que `identifications.py` y `remote_config.py` se importen entre sí. | -- |

**Dónde se conecta con el resto de la app** (`python/main.py`): los tres loops de fondo arrancan junto con la cámara/OCR. El nivel de detalle aplicado (`remote_config.nivel_detalle_actual()`) se pasa a `describe_product()`. Encolar un evento es trabajo local (archivos, no red) -- el pipeline del botón nunca espera a la web (punto 8 de este contrato).

**Qué se reporta a `/identifications`, y qué no:**

- **Solo identificaciones reales -- nunca una lectura fallida.** Si ninguno de los dos caminos llegó a identificar nada (ni nombre ni precio por OCR; ni `producto_detectado` por el respaldo remoto), no se encola nada -- el anteojo sigue anunciando por voz el mensaje de reserva de siempre, pero eso no es una identificación y no le llega a la web.
- **`uad_cost` según quién resolvió la identificación**, no el valor fijo del ejemplo del contrato (que sigue siendo válido como *default* de la API, pero este firmware siempre lo manda explícito):
  - **`0`** -- identificación resuelta por el **OCR local** (`blindia.product.parser.parse_product`, sin ningún servidor de por medio).
  - **`1`** -- identificación resuelta por el **servidor de IA remoto** (`blindia.product.vision_fallback`, cuando el OCR no encontró texto y hubo que mandar la foto).
- `confidence` se manda siempre como `None` (se omite) en el camino de OCR: no hay ninguna señal de confianza agregada hoy en `RecognizedProduct`/`OcrResult` que valga la pena reportar (ver `docs/DEVELOPMENT.md`, "Reconocimiento de producto", sobre por qué el score de OCR no es confiable como señal). En el camino de respaldo remoto sí se manda, tal cual la informa el servidor (`confianza_producto`).
- `ApiSettings.base_url` (en `python/blindia/config.py`) apunta a `https://blind-ia.vercel.app` -- la dirección de producción, nunca una de *Preview* de Vercel, como pide este contrato.
- Reloj NTP: ya estaba sincronizado en esta placa (`timedatectl status` → `System clock synchronized: yes`), no hizo falta activar nada.
