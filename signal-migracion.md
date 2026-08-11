# Migrar un agente de Telegram a Signal

Estado: **solo Renata** está en Signal (2026-08-11). **Hermes se queda en
Telegram** por decisión de Pablo — no es una migración global ni un pendiente.
Este documento es el runbook por si algún día se migra otro agente.

## Por qué

Los chats de bots de Telegram **no son cifrados extremo a extremo**: son *cloud
chats* y Telegram puede leerlos en sus servidores. Por Renata circulan
transcripciones íntegras de los comités de AROCO y análisis de posiciones de
cacao. En Signal ese contenido viaja cifrado E2E.

No es un cambio cosmético de canal: es sacar el contenido más sensible de la
operación de un canal que un tercero puede leer.

## Cómo funciona (y en qué se diferencia de Telegram)

Signal **no tiene API de bots**. Hermes no habla con un servicio de bots: maneja
una **cuenta real de Signal** a través de `signal-cli`, que corre como daemon
JSON-RPC en el servidor. Consecuencias prácticas:

- El agente necesita **su propio número de teléfono**, como una persona.
- Le hablas **como a un contacto normal**, no a un `@bot`.
- Hay un proceso más que mantener (`signal-cli`), aparte del gateway.

```
Signal (app de Pablo) ──E2E──► número de Renata
                                  │
                        signal-cli daemon  (127.0.0.1:8790, JSON-RPC/SSE)
                                  │
                        hermes-renata-gateway  (adaptador gateway/platforms/signal.py)
```

## ⚠️ Lo primero: NO se puede registrar el número desde este servidor

Este runbook decía originalmente que se registraba con `signal-cli register
--captcha`. **No funciona en este servidor.** Devuelve:

```
Failed to register: [403] Authorization failed! (AuthorizationFailedException)
```

...con un captcha recién generado y válido. La causa es la IP:

```
$ curl -s https://ifconfig.me | xargs -I{} curl -s https://ipinfo.io/{}/json
"city": "Ashburn", "region": "Virginia", "country": "US",
"org": "AS213230 Hetzner Online GmbH"
```

Signal **rechaza los registros que salen de rangos de datacenter**, y aquí encima
se intenta registrar un número colombiano desde una IP estadounidense. Se suma un
segundo problema: el token de captcha queda **ligado a la IP que lo pidió**, así
que resolverlo en el navegador de un portátil y enviarlo desde el servidor tampoco
sirve.

> **No gastes captchas intentándolo.** No es un problema de token ni de versión de
> `signal-cli` (0.14.7 es la última). Es la IP, y no se arregla reintentando.

El binario **no tiene opción de proxy**, así que tampoco se puede enrutar el
registro por otra salida.

## La vía que funciona: vincular como dispositivo

La SIM va en un teléfono, el número se registra con **la app de Signal normal**
(desde red móvil, sin captchas raros), y el servidor se une como **dispositivo
vinculado**. Vincular no pasa por el flujo de registro, así que la IP de datacenter
deja de importar.

**Contrapartida a asumir:** el teléfono queda como **dispositivo principal** y hay
que conservarlo. Si se pierde o se borra, no se pueden añadir dispositivos nuevos y
el agente se queda sin vía de recuperación. Guardarlo con bloqueo de pantalla: va a
tener copia de todo lo que pase por el agente.

### 1. En el teléfono

1. Meter la SIM dedicada y registrar el número con la app de Signal.
2. Poner el nombre de perfil del agente (lo que ve quien le escriba).
3. Crear el **PIN** que ofrece Signal y guardarlo en un gestor de contraseñas —
   es el bloqueo de registro, impide que alguien se apropie del número si la SIM
   se recicla. **No guardarlo en el servidor.**

### 2. En el servidor

```bash
CFG=/home/aroco/projects/data/renata-signal

# Imprime un URI sgnl://linkdevice?… y se queda esperando el escaneo.
/opt/signal-cli/signal-cli --config $CFG link -n "Renata (servidor)"
```

Ese URI hay que convertirlo en QR. **No hay `qrencode` en el servidor**; venv
desechable, sin tocar paquetes del sistema:

```bash
python3 -m venv /tmp/qrvenv && /tmp/qrvenv/bin/pip install -q qrcode
/tmp/qrvenv/bin/python -c "
import qrcode, sys
q = qrcode.QRCode(border=2); q.add_data(sys.argv[1]); q.make(fit=True)
q.print_ascii(invert=True)" 'sgnl://linkdevice?…'
```

Escanear desde el teléfono en **Ajustes → Dispositivos vinculados → Vincular un
dispositivo**. El comando responde `Associated with: +57…`.

> ⚠️ El URI `sgnl://linkdevice?…` es **una credencial**: quien lo escanee entra a
> la cuenta. Mostrarlo solo en la terminal, nunca en un artifact, un repo o un
> chat. Caduca en pocos minutos; si expira, se genera otro.

### 3. Probar el envío, y solo entonces montar el daemon

Con el daemon **parado** (el CLI y el daemon se pelean por el lock de la cuenta):

```bash
/opt/signal-cli/signal-cli --config $CFG -a $NUM send -m "prueba" +57XXXXXXXXXX
```

Devuelve un timestamp si salió. Después:

```bash
sed 's/NUMERO_AQUI/'"$NUM"'/' ~/.hermes-renata/signal-cli-renata.service.template \
  | sudo tee /etc/systemd/system/signal-cli-renata.service
sudo systemctl daemon-reload && sudo systemctl enable --now signal-cli-renata
ss -tlnp | grep 8790   # debe escuchar SOLO en 127.0.0.1
```

### 4. Variables y arranque

En `~/.hermes-renata/.env` (chmod 600; los valores reales viven ahí, no en este
repo):

```bash
SIGNAL_HTTP_URL=http://127.0.0.1:8790
SIGNAL_ACCOUNT=+57XXXXXXXXXX
SIGNAL_ALLOWED_USERS=+57<PABLO>,+57<ALVARO>     # ← ver aviso de abajo
```

Y **reiniciar**, no recargar: `sudo systemctl restart hermes-renata-gateway`. Un
`reload` (SIGUSR1) no sirve — las variables de entorno se leen al arrancar el
proceso. Hermes habilita la plataforma solo si `SIGNAL_HTTP_URL` **y**
`SIGNAL_ACCOUNT` están presentes (`gateway/config.py`), así que no hay que tocar
`config.yaml`. En el log debe aparecer:

```
Signal adapter initialized: url=http://127.0.0.1:8790 account=+573****1987
✓ signal connected
Gateway running with N platform(s)
```

## ⚠️ `SIGNAL_ALLOWED_USERS` por defecto es `*` — abierto

```python
# gateway/platforms/signal.py
dm_allowed_str = os.getenv("SIGNAL_ALLOWED_USERS", "*")
```

**Si no se configura, cualquiera que conozca el número puede darle órdenes al
agente.** En Telegram la lista blanca (`TELEGRAM_ALLOWED_USERS`) ya está puesta y
es fácil suponer que el comportamiento se hereda: no se hereda, y el default es
el inseguro. Poner la lista **antes** del primer arranque, no después.

### Cómo verificar que quedó aplicada

**`/proc/<pid>/environ` no sirve** y da un falso negativo alarmante: el gateway
carga el `.env` por dotenv ya en marcha, y eso no modifica el entorno inicial del
`exec` que `/proc` refleja. Comprobar así:

```bash
cd ~/.hermes-renata/hermes-agent && ./venv/bin/python -c "
from dotenv import load_dotenv; load_dotenv('/home/aroco/.hermes-renata/.env')
import os
from gateway.platforms.signal import _parse_comma_list
raw = os.getenv('SIGNAL_ALLOWED_USERS', '*')
print('parseado    :', sorted(_parse_comma_list(raw)))
print('modo abierto:', '*' in _parse_comma_list(raw))"
```

Mismo criterio para el daemon: escucha en `127.0.0.1` y **no tiene autenticación
propia** — quien alcance el puerto 8790 controla la cuenta de Signal del agente.
Nunca exponerlo por el túnel de cloudflared (ver `patrones-operacionales.md`).

## Crons

`signal` es plataforma válida de entrega (`_KNOWN_DELIVERY_PLATFORMS` en
`cron/scheduler.py`). Editar con el CLI, no a mano sobre `jobs.json`:

```bash
HERMES_HOME=/home/aroco/.hermes-renata \
  ~/.hermes-renata/hermes-agent/venv/bin/python -m hermes_cli.main \
  cron edit <JOB_ID> --deliver "signal:+57<PABLO>,signal:+57<ALVARO>"
```

Jobs de Renata migrados: `Notetaker resumen` (`9938e112e700`), `Notetaker chequeo
sesion` (`617c04658aaf`), `Barchart chequeo sesion` (`ad5af3d40798`). El job
`Notetaker entrar` (`5117c30930d8`) entrega `local` y no se toca.

## Apagar Telegram del todo

Quitar `TELEGRAM_BOT_TOKEN` y `TELEGRAM_ALLOWED_USERS` del `.env` y reiniciar
**solo desconecta** al agente. Para que el contenido salga de verdad de Telegram
hacen falta dos pasos manuales que no se pueden hacer desde el servidor:

1. Borrar el bot en **@BotFather** (`/deletebot`) — si no, sigue existiendo.
2. Borrar las conversaciones viejas con ese bot en cada teléfono: el historial
   permanece en los servidores de Telegram hasta que se elimina desde la app.

Comprobación de que el gateway lo soltó: el log dice `Gateway running with 1
platform(s)` y `grep -i telegram` sobre `.env` y `cron/jobs.json` no devuelve nada
(salvo comentarios).

## Si algún día se migra otro agente

**Hermes se queda en Telegram** (decisión de 2026-08-11). Lo que sigue solo aplica
si se retoma para él o para un agente nuevo.

### Cada agente necesita su propio número

No se puede reutilizar el número de otro agente. Signal **no enruta por
dispositivo**: cada mensaje entrante llega a *todos* los dispositivos vinculados
de la cuenta, así que los dos agentes lo procesarían y **los dos contestarían**,
con la misma identidad y el mismo nombre de perfil. Dejarían de ser dos
interlocutores distintos.

Ojo también: **la app de Signal solo admite una cuenta por instalación**, así que
el teléfono de un agente no sirve para registrar el de otro. O segundo teléfono, o
la vía de abajo.

### Alternativa a vincular: registrar desde un portátil

Si no se quiere un teléfono permanente por agente, se puede registrar con
`signal-cli` **desde una máquina con IP residencial** (que sí pasa el filtro que
bloquea al servidor), y mover el estado después:

```bash
brew install signal-cli
signal-cli -a $NUM register --captcha 'signalcaptcha://…'   # captcha del MISMO navegador
signal-cli -a $NUM verify 123456                            # SMS a la SIM
tar czf agente-signal.tgz -C ~/.local/share/signal-cli data
scp agente-signal.tgz servidor:/home/aroco/projects/data/<agente>-signal/
```

Así el servidor queda como dispositivo **principal** y no hace falta conservar
ningún teléfono. Dos cuidados: el `.tgz` son **claves privadas en claro**, y hay
que **borrar la copia del portátil** después — dos primarios vivos de la misma
cuenta se desincronizan y uno acaba desregistrado.

### Grupos: desactivados por defecto, y la variable está mal nombrada

`SIGNAL_GROUP_ALLOWED_USERS` **lleva IDs de GRUPO, no de usuario**, pese al nombre.
Vacío (el default) ⇒ el agente **ignora todos los mensajes de grupo**, lo que
parece una avería sin serlo. `*` ⇒ todos los grupos.

Para mandar los avisos automáticos a un grupo en vez de al hilo privado:

| Variable | Valor |
|---|---|
| `SIGNAL_GROUP_ALLOWED_USERS` | `<groupId>` |
| `SIGNAL_HOME_CHANNEL` | `group:<groupId>` |
| `deliver` de crons | `signal:group:<groupId>` |

El `chat_id` de grupo es `group:<groupId>` (`gateway/platforms/signal.py`), y el
deliver de cron parte por el **primer** `:` (`cron/scheduler.py`), así que
`signal:group:<id>` es un destino válido. El ID sale de `signal-cli … listGroups`,
o se crea el grupo con `signal-cli … updateGroup -n "…" -m +57… -m +57…`, que lo
devuelve directo.

> Quien esté en ese grupo **puede darle órdenes al agente**, no solo leer los
> avisos. Es una lista de mando, no de espectadores.

## Pendiente

- Rate limits: el adaptador trae `signal_rate_limit.py` con pacing por lotes, lo
  que sugiere que upstream chocó con límites al mandar muchos adjuntos. Los
  informes de Renata llevan PDFs; vigilar los primeros envíos.
- Renovación: la sesión de `signal-cli` no caduca como la cookie de Google, pero
  Signal fuerza versiones mínimas de cliente y rechaza las viejas — actualizar
  `/opt/signal-cli/` periódicamente.
