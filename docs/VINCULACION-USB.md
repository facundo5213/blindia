# Vinculación por USB — especificación para la web de administración

Este documento especifica cómo la web de administración (navegador
Chrome/Edge) tiene que conectarse al anteojo por USB, leer su número de
serie, y escribirle sus credenciales (serial + clave) una sola vez, al dar
de alta el equipo. Incluye además una extensión opcional (v1.1, sección 6)
para que el *cliente* vincule su anteojo a su cuenta sin tipear nada,
conectando el cable. **El lado del anteojo (gadget USB + daemon) ya está
implementado y confirmado con hardware real; la web todavía no** (ver
"Estado" al final).

## Investigación: cómo se presenta la placa por USB

Confirmado en vivo sobre el hardware real (UNO Q), leyendo la
configuración del gadget USB compuesto que ya arma `adbd.service`
(`/usr/lib/android-sdk/platform-tools/adbd-usb-gadget`, vendor Arduino, no
parte de este repo):

Al conectar la placa a una PC por el puerto USB-C de datos, el sistema
operativo la ve como **un solo dispositivo USB compuesto** con dos
funciones:

1. **ADB** (vía FunctionFS) -- lo que usa App Lab para detectar/gestionar
   el equipo. `idVendor=0x2341`, `idProduct=0x0078` (valores específicos
   de UNO Q; VENTUNO Q usa `0x007A`).
2. **CDC-ACM** (puerto serie virtual, nombre interno `acm.GS0`) -- aparece
   como un puerto COM en Windows o `/dev/ttyACM*`/`/dev/ttyUSB*` en
   Mac/Linux.

No hay ninguna función de red por USB (ni RNDIS ni ECM/NCM) -- esa vía
queda descartada.

**Hallazgo clave**: el puerto serie `acm.GS0` (`/dev/ttyGS0` del lado de la
placa) **ya está en uso**. Hay un servicio systemd,
`arduino-router-serial.service` ("Proxy for the Arduino Router Monitor to
ttyGS0 serial"), que lo reclama apenas aparece el device node -- es el
mismo canal que usan `arduino-app-cli monitor` y el monitor serie de App
Lab para hablar con el MCU. **No se puede reusar ese puerto para este
protocolo sin romper esa función.**

### De dónde sale el número de serie

`/sys/devices/soc0/serial_number` -- es el identificador de hardware del
SoC. Confirmado que es el mismo valor que Arduino ya usa como
`serialnumber` del propio gadget USB (visible para el host como el string
descriptor "serial number" del dispositivo). Estable, único por placa, ya
validado por el propio fabricante para este propósito exacto.

## Mecanismo propuesto

**Web Serial API** (`navigator.serial`, soportada en Chrome/Edge) hablando
con un **segundo puerto CDC-ACM nuevo y dedicado**, agregado al gadget
compuesto existente -- no se toca ni se comparte `acm.GS0`.

### Por qué esto y no las alternativas

- **WebUSB hablando ADB directo** (la otra vía obvia, ya que ADB es lo que
  usa App Lab): descartado. Requeriría reimplementar el protocolo ADB
  completo en JavaScript (hay librerías open-source para esto, pero es
  bastante más complejo que un protocolo propio de texto plano), y en
  Windows la interfaz ADB normalmente necesita un driver específico
  (Google USB Driver, o WinUSB vía Zadig) instalado a mano antes de que
  cualquier acceso funcione -- nada "simple y confiable" para un usuario
  común dando de alta el equipo por primera vez.
- **Reusar `acm.GS0`**: descartado, ver "Hallazgo clave" arriba -- rompería
  el monitor serie del MCU.
- **Networking por USB (RNDIS/ECM)**: no existe hoy en el gadget, y
  agregarlo es un cambio de sistema más grande e intrusivo que agregar un
  segundo puerto serie, sin ninguna ventaja real para este caso de uso
  (un intercambio de texto corto, una sola vez).

CDC-ACM es la opción con menor fricción para el usuario: driver nativo en
Windows/Mac/Linux desde hace más de una década, sin instalar nada, y Web
Serial API está diseñada exactamente para este tipo de intercambio simple
con un dispositivo embebido.

### Cambio de sistema: implementado vía drop-in, sin editar `adbd-usb-gadget`

Se descartó editar `/usr/lib/android-sdk/platform-tools/adbd-usb-gadget`
directo (script de sistema, fuera de este repo, provisto por Arduino) para
que una actualización del paquete no pise el cambio. En su lugar:

- **`scripts/usb_vinculacion_gadget_add.sh`** / **`scripts/usb_vinculacion_gadget_remove.sh`**
  (este repo): agregan/sacan la función `acm.GS1` del gadget compuesto que
  ya arma `adbd-usb-gadget`, operando directo sobre configfs
  (`/sys/kernel/config/usb_gadget/g1`).
- **`scripts/systemd/adbd-usb-vinculacion.conf`** (este repo, para instalar
  en `/etc/systemd/system/adbd.service.d/`): un **drop-in de systemd** que
  intercala esos dos scripts entre el `setup`/`activate` y el
  `reset`/`activate` originales de `adbd.service`, sin tocar el unit ni el
  script de Arduino. El orden importa: agregar `acm.GS1` tiene que pasar
  ANTES de que `activate` bindee el UDC (después de bindeado, Linux no deja
  agregar funciones en caliente), y sacarlo tiene que pasar ANTES del
  `reset` original (que hace `rmdir` del gadget entero y falla si queda
  algo nuestro adentro). El propio archivo trae el detalle de por qué hace
  falta re-declarar la lista completa de `ExecStartPost`/`ExecStopPost`.

**Instalación** (requiere sudo -- corrido por el usuario, no por el agente):

```sh
sudo mkdir -p /etc/systemd/system/adbd.service.d
sudo cp ~/ArduinoApps/blindia/scripts/systemd/adbd-usb-vinculacion.conf \
    /etc/systemd/system/adbd.service.d/
sudo systemctl daemon-reload
sudo systemctl restart adbd.service
```

**Para revertir** (no toca nada de Arduino, nunca hace falta restaurar un
backup de `adbd-usb-gadget` porque nunca se editó):

```sh
sudo rm /etc/systemd/system/adbd.service.d/adbd-usb-vinculacion.conf
sudo systemctl daemon-reload
sudo systemctl restart adbd.service
```

Esto hace aparecer un segundo puerto serie (`/dev/ttyGS1` del lado de la
placa) libre para uso propio, sin tocar `acm.GS0`/`ttyGS0` (el del monitor
del MCU).

## Protocolo

Texto plano, un comando por línea (terminada en `\n`), baud rate 115200.
Pensado para ser trivial de implementar tanto en JS (Web Serial) como en
Python (lado placa).

### 1. Conexión y desambiguación

La placa va a listar (potencialmente) dos puertos serie con el mismo
`usbVendorId`/`usbProductId` (uno es el monitor del MCU, el otro es este).
Web Serial no distingue de forma confiable entre interfaces ACM del mismo
dispositivo en todos los sistemas operativos, así que la desambiguación se
hace a nivel de protocolo, no de metadata del puerto:

```js
const port = await navigator.serial.requestPort({
  filters: [{ usbVendorId: 0x2341, usbProductId: 0x0078 }]
});
await port.open({ baudRate: 115200 });
```

Si `requestPort()` lista más de un puerto candidato, mostrarle las dos
opciones al usuario es aceptable -- el paso siguiente (`PING`) confirma
cuál es el correcto sin ambigüedad.

**Importante -- descartar el buffer antes de mandar nada**: en la prueba
real (ver "Estado") el primer intento de `PING` devolvió como primera
línea un eco de lo que el propio cliente mandó ("PING"), y recién la
segunda línea fue la respuesta real ("PONG blindia") -- una ventana de
carrera del lado de la placa entre abrir el puerto y terminar de ponerlo
en modo raw, ya mitigada ahí (el daemon descarta el buffer de entrada
apenas termina de configurar el puerto). Como defensa adicional del lado
de la web, que no cuesta nada y cubre cualquier otro caso similar:

- Al abrir el puerto, leer y descartar cualquier cosa que ya esté en el
  buffer antes de mandar el primer comando (ej. leer con un timeout corto
  una vez, tirar lo que llegue, recién ahí mandar `PING`).
- Nunca asumir que la *primera* línea que llega después de un comando es
  la respuesta esperada -- parsear cada línea recibida y **ignorar
  cualquiera que no matchee el formato de respuesta esperado** para ese
  comando (ej. si se espera `PONG blindia` y llega `PING`, descartarla y
  seguir esperando la próxima línea dentro del mismo timeout).

### 2. Confirmar que es el puerto correcto

```
→ PING
← PONG blindia
```

Timeout recomendado: 1 segundo. Si no llega `PONG blindia` en ese tiempo,
es el puerto equivocado (probablemente el del monitor del MCU) -- pedirle
al usuario que elija el otro puerto listado, o reintentar
`requestPort()`.

### 3. Leer el número de serie

```
→ GET_SERIAL
← SERIAL 674441474
```

El valor es el contenido crudo de `/sys/devices/soc0/serial_number` en la
placa, sin modificar.

### 4. Consultar si ya está vinculado (antes de sobrescribir)

```
→ GET_STATUS
← LINKED
```
o
```
→ GET_STATUS
← NOT_LINKED
```

Pensado para que la web pueda avisarle al usuario y pedir confirmación
antes de reemplazar unas credenciales ya existentes, en vez de
pisarlas en silencio.

### 5. Escribir las credenciales

```
→ SET_CREDENTIALS {"serial":"674441474","clave":"<clave-generada-por-la-web>"}
← OK
```

o, si algo falla:

```
← ERROR <motivo>
```

El payload es una línea de JSON. La placa lo valida (JSON bien formado,
tiene `serial` y `clave`, el `serial` coincide con el que ella misma
reportó en `GET_SERIAL`) antes de escribirlo.

### 6. (Opcional, extensión v1.1) Código de enlace de la caja

Pensado para que el **cliente** vincule su propio anteojo a su cuenta
conectando el cable, **sin tipear nada** -- clave para personas ciegas, que
no tienen por qué leer ni escribir un serial y un código impresos en la
caja. Es **opcional y compatible hacia atrás**: la web sigue funcionando
sin esto (el cliente tipea el código a mano), y un firmware que no la
implemente nunca rompe nada por eso (ver "Comportamiento ante comandos
desconocidos" más abajo).

**Por qué hace falta (seguridad)**: el serial de hardware (sección 3) no
es secreto -- está impreso en la caja y son pocas cifras. Si la web
vinculara solo con el serial, cualquiera podría adueñarse de anteojos
todavía no vendidos. Por eso la web exige **serial + código de enlace**
(el de la caja) para vincular. Esta extensión deja que el anteojo le
informe ese código a la web por el cable: misma seguridad que tipearlo a
mano, sin tipearlo.

**`SET_PAIRING`** -- lo manda la web de administración al dar de alta,
después de `SET_CREDENTIALS`:

```
→ SET_PAIRING 8SZAXW25
← OK
```
o `← ERROR <motivo>`.

- Código de exactamente 8 caracteres alfanuméricos en mayúsculas (sin
  guion). Cualquier otra cosa -- largo distinto, minúsculas, símbolos --
  devuelve `ERROR` **sin pisar** el código guardado anteriormente.
- Se guarda con el mismo tratamiento que la clave (ver "Dónde quedan los
  archivos" abajo). **No es una credencial de API**: nunca se usa para
  hablar con la web de reconocimiento, que sigue autenticando solo con
  serial + clave.
- `SET_PAIRING` es *best-effort* del lado de la web: si devuelve `ERROR` o
  no responde, el alta por `SET_CREDENTIALS` ya quedó hecha igual.

**`GET_PAIRING`** -- lo manda la web del cliente al conectar el cable:

```
→ GET_PAIRING
← PAIRING 8SZAXW25
```
o `← ERROR sin codigo` si nunca se guardó ninguno.

- Responde en milisegundos (no hay E/S lenta de por medio) -- bien dentro
  del margen de 1,5s que puede esperar la web antes de asumir que el
  firmware no soporta la extensión.
- El código sobrevive a reinicios de la placa (vive en un archivo en
  `data/`, no en memoria).

### Comportamiento ante comandos desconocidos

Un comando desconocido (incluidos `SET_PAIRING`/`GET_PAIRING` si algún día
se agregan comandos nuevos todavía) **nunca cierra el puerto ni reinicia
nada** -- el daemon devuelve `ERROR comando desconocido: ...` y sigue
atendiendo pedidos normalmente. Esto es lo que le permite a un firmware
viejo convivir con una web que ya mande `SET_PAIRING`/`GET_PAIRING`: la
web simplemente interpreta la falta de respuesta esperada (o el `ERROR`)
como "esta placa no soporta la extensión".

### Dónde quedan los archivos (lado placa)

- **`data/device_credentials.json`** -- serial + clave (sección 5),
  agregado a `.gitignore` (no es código fuente, es secreto de este equipo
  físico). Formato:

  ```json
  {"serial": "674441474", "clave": "..."}
  ```

- **`data/pairing_code.txt`** -- código de enlace (sección 6, extensión
  v1.1), mismo tratamiento que el archivo anterior (también en
  `.gitignore`). Contiene únicamente el código, sin formato extra.

Si ninguno de los dos archivos existe, el resto de la app (`hablar()`,
captura, OCR, etc.) funciona exactamente igual que ahora -- el anteojo
simplemente no tiene nada que mandarle a la web de administración todavía
hasta que se vincule. Ningún módulo existente depende de estos archivos.

## Estado

**Lado del anteojo: implementado y confirmado con hardware real.**

- El cambio de sistema al gadget USB (sección arriba) -- aplicado y
  probado. Conectado directo a una notebook, Windows muestra "Android
  Device" (ADB) **y dos puertos COM nuevos**, sin errores. ADB y el
  monitor del MCU confirmados funcionando sin cambios.
- **`scripts/usb_vinculacion_daemon.py`** -- el daemon que escucha en
  `/dev/ttyGS1` y habla este protocolo (`PING`/`GET_SERIAL`/`GET_STATUS`/
  `SET_CREDENTIALS`/`SET_PAIRING`/`GET_PAIRING`), instalado como
  `scripts/systemd/blindia-usb-vinculacion.service` (`systemd --user`,
  arranca solo con la placa, reinicia solo si se cae). Lógica del
  protocolo verificada: `PING`, `GET_SERIAL` (devuelve el serial real de
  hardware), `GET_STATUS` antes/después de vincular, `SET_CREDENTIALS` con
  JSON inválido, con serial que no coincide, y con el caso correcto
  (escribe el archivo, `GET_STATUS` pasa a `LINKED`); y, de la extensión
  v1.1, `GET_PAIRING` sin código guardado (`ERROR sin codigo`),
  `SET_PAIRING` con código inválido (`ERROR`, sin pisar el anterior),
  `SET_PAIRING` válido seguido de `GET_PAIRING` devolviendo ese mismo
  código, y un comando desconocido inventado que no corta la conexión ni
  afecta a los demás comandos. Reiniciado tras cada cambio de código y
  confirmado `active (running)`.
- **Prueba de punta a punta con hardware real (Windows + puerto COM de
  verdad)**: `PING` → `PONG blindia` confirmado en uno de los dos puertos
  COM listados (el otro no respondió, como se espera del puerto del
  monitor del MCU). Encontrado y corregido en el camino: el primer intento
  devolvió un eco de lo mandado ("PING") en vez de la respuesta, por una
  ventana de carrera entre abrir el puerto y terminar de ponerlo en modo
  raw -- el daemon ahora descarta el buffer de entrada (`termios.tcflush`)
  justo después de aplicar modo raw, antes de atender cualquier pedido.
  Ver la nota en "1. Conexión y desambiguación" para la mitigación
  equivalente recomendada del lado de la web.

**Nota sobre el alcance de las pruebas de `SET_PAIRING`/`GET_PAIRING`**: a
diferencia de `PING`/`GET_SERIAL`/`GET_STATUS`/`SET_CREDENTIALS` (ver punto
anterior, probados contra un puerto COM real en Windows), estos dos
comandos nuevos solo están verificados a nivel de lógica (llamando
directo a la función que atiende cada comando, igual que se hizo antes de
la primera prueba con hardware real). Todavía no se repitió la prueba de
punta a punta por el cable físico para esta extensión en particular --
no hay motivo para esperar un comportamiento distinto (mismo daemon, mismo
puerto, mismo modo raw ya corregido), pero queda como la verificación
pendiente más inmediata del lado del anteojo.

**Pendiente, del lado de la web (fuera de este repo)**:

- Implementar el flujo de `navigator.serial` descripto arriba.
- **Importante para desambiguar los dos puertos COM**: no asumir un
  número de puerto fijo (no es estable entre PCs ni se puede predecir
  desde este lado) -- usar siempre el paso 2 (`PING` → `PONG blindia`)
  para confirmar cuál de los puertos listados es el de vinculación antes
  de mandar cualquier otro comando.
- Extensión v1.1 (opcional): mandar `SET_PAIRING` después de
  `SET_CREDENTIALS` al dar de alta, y `GET_PAIRING` del lado del cliente
  al conectar el cable, con el orden y los timeouts de la sección 6. Tratar
  cualquier `ERROR`/falta de respuesta como "este firmware no soporta la
  extensión" y caer al flujo manual (el cliente tipea el código de la
  caja) sin bloquear nada.
