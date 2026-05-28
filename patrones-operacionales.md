# Patrones operacionales

Tres patrones probados en producción (mayo 2026) que extienden lo cubierto en
[ejemplos.md](./ejemplos.md), [cronjobs.md](./cronjobs.md) y
[instrucciones.md](./instrucciones.md):

1. [Múltiples Hermes en el mismo servidor](#1-múltiples-hermes-en-el-mismo-servidor) — aislamiento vía `HERMES_HOME`.
2. [Loop de trading autónomo](#2-loop-de-trading-autónomo) — cron + skill + helper script.
3. [Migración Binance Algo Order (-4120)](#3-migración-binance-algo-order--4120) — workaround para `STOP_MARKET` / `TAKE_PROFIT_MARKET` desde 2025-12-09.
4. [Anti-alucinación en tareas de consolidación temporal](#4-anti-alucinación-en-tareas-de-consolidación-temporal) — skill disciplinado con fuente única, no conversación libre.
5. [MCPs que producen archivos: registrar un resource](#5-mcps-que-producen-archivos-registrar-un-resource) — fix del `Unknown resource` cuando un tool devuelve una ruta de archivo.

Todos se basan en una sola instancia de Hermes corriendo nativa (no Docker —
esa es la forma upstream del agente; los MCPs sí van en contenedores).

---

## 1. Múltiples Hermes en el mismo servidor

### Por qué

Una sola máquina puede alojar N instancias de Hermes para clientes / propósitos
distintos, sin Docker, **siempre que cada una apunte a un `HERMES_HOME`
diferente**. El gateway de Hermes no expone puerto HTTP (usa polling de
Telegram), así que no hay choque a ese nivel — el único punto de colisión
son los MCPs si comparten puerto.

### Topología

```
servidor/
├── ~/.hermes/                         ← Hermes #1 (cliente A)
│   ├── hermes-agent/  (runtime + venv)
│   ├── config.yaml
│   └── .env  (TELEGRAM_BOT_TOKEN_A, etc)
│
├── ~/.hermes-clienteB/                ← Hermes #2 (cliente B)
│   ├── hermes-agent/  (copia independiente del runtime)
│   ├── config.yaml
│   └── .env  (TELEGRAM_BOT_TOKEN_B, etc)
│
└── /etc/systemd/system/
    ├── hermes-gateway.service          ← Hermes #1
    └── hermes-clienteB-gateway.service ← Hermes #2
```

### Pasos para desplegar un Hermes adicional

#### 1.1 Copiar el runtime

```bash
cp -a ~/.hermes/hermes-agent ~/.hermes-clienteB/hermes-agent
```

(~1.6 GB con venv + node_modules. Vale la pena duplicar para aislamiento:
así un `pip install` o un update en un Hermes no rompe el otro).

#### 1.2 Crear config.yaml propio

```bash
cp ~/.hermes/config.yaml ~/.hermes-clienteB/config.yaml
```

Sanitizar la sección del Hermes #1 — al mínimo:

```yaml
# ~/.hermes-clienteB/config.yaml
mcp_servers: {}     # arranca vacío; agrega cuando levantes MCPs propios
timezone: 'America/Bogota'
```

#### 1.3 Crear `.env` propio

```bash
cat > ~/.hermes-clienteB/.env <<'EOF'
OPENROUTER_API_KEY=...
TELEGRAM_BOT_TOKEN=...   # bot distinto creado en @BotFather
TELEGRAM_ALLOWED_USERS=...
EOF
chmod 600 ~/.hermes-clienteB/.env
```

#### 1.4 Crear el unit systemd

Clon del unit existente, cambiando rutas y `HERMES_HOME`:

```ini
# /etc/systemd/system/hermes-clienteB-gateway.service
[Unit]
Description=Hermes ClienteB Agent Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<user>
Group=<user>
ExecStart=/home/<user>/.hermes-clienteB/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --replace
WorkingDirectory=/home/<user>/.hermes-clienteB/hermes-agent
Environment="HOME=/home/<user>"
Environment="USER=<user>"
Environment="PATH=/home/<user>/.hermes-clienteB/hermes-agent/venv/bin:/usr/bin:/usr/local/bin"
Environment="VIRTUAL_ENV=/home/<user>/.hermes-clienteB/hermes-agent/venv"
Environment="HERMES_HOME=/home/<user>/.hermes-clienteB"
Restart=always
RestartSec=60
RestartForceExitStatus=75
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-clienteB-gateway
```

#### 1.5 Verificación

```bash
systemctl is-active hermes-clienteB-gateway   # → active
tail ~/.hermes-clienteB/logs/gateway.log      # → "✓ telegram connected"
```

Y desde Telegram, escribirle al bot del cliente B. Si responde, ambos
Hermes están corriendo independientes.

### Operaciones (comandos paralelos al Hermes principal)

| Operación | Hermes #1 (default) | Hermes #2 (clienteB) |
|---|---|---|
| Estado | `systemctl status hermes-gateway` | `systemctl status hermes-clienteB-gateway` |
| Logs en vivo | `tail -f ~/.hermes/logs/gateway.log` | `tail -f ~/.hermes-clienteB/logs/gateway.log` |
| CLI seguro (sin tocar daemon) | `HERMES_HOME=~/.hermes hermes …` | `HERMES_HOME=~/.hermes-clienteB hermes …` |
| Reiniciar | `sudo systemctl restart hermes-gateway` | `sudo systemctl restart hermes-clienteB-gateway` |

### Gotchas

- **Puertos MCP**: si Hermes #1 ocupa 8765-8773, asignar al #2 una franja
  distinta (ej. 8776+). El gateway no choca, pero los MCPs sí (cada uno bind
  a un puerto fijo).
- **Skills custom**: viven en `<HERMES_HOME>/skills/`. La librería upstream
  está en `<HERMES_HOME>/hermes-agent/skills/` y se duplica con el `cp -a`
  inicial — eso está bien.
- **Cron `enabled_toolsets`**: el bug descrito en [cronjobs.md](./cronjobs.md)
  aplica igual a cada Hermes; revisar siempre.

---

## 2. Loop de trading autónomo

### Por qué

Cuando se quiere que Hermes **opere activamente** un sistema externo (trading,
monitoreo de precios, ejecución repetida de tareas con decisión) sin que el
usuario tenga que pedírselo cada vez. Patrón: un cronjob de Hermes invoca cada
N minutos una skill que es **autocontenida** y orquesta `ver → decidir →
ejecutar → reportar`.

A diferencia de un cronjob simple de notificaciones (`cronjobs.md`), aquí
el tick **toma decisiones y ejecuta tools de escritura**.

### Arquitectura

```
/start-loop  (skill user-facing)
   │
   ▼
helper script  →  hermes cron create --skill loop-tick --deliver telegram:CHAT_ID
                  fix enabled_toolsets en jobs.json
                  systemctl restart hermes-gateway
                  │
                  ▼
              cronjob "*/N * * * *"
                  │
                  ▼ cada N min
        agente fresh ejecuta skill loop-tick:
          1. snapshot del estado vía MCP
          2. evaluar lo que existe
          3. decidir si actuar
          4. ejecutar tools de escritura (con guardrails server-side)
          5. reportar a Telegram

/stop-loop  (skill user-facing)
   │
   ▼
hermes cron rm <job_id>
   (no toca el estado externo — solo apaga el tick)
```

### Componentes

#### 2.1 La skill `loop-tick` (no tiene slash command)

Es la skill que el cron invoca. Tags incluyen `internal` para señalar que no
se llama directo desde Telegram.

```markdown
---
name: loop-tick
description: "Tick autónomo. Pensado para cronjob, NO mensaje directo. Sin slash command."
tags: [autonomous, cron, loop, internal]
---

# Tick autónomo

Eres <agente> en modo loop. [Rol concreto.]

## Ciclo del tick

### Paso 1 — Snapshot
- mcp_X_get_state
- mcp_X_get_open_items
- ...

### Paso 2 — Evaluar lo que existe
[Criterios concretos.]

### Paso 3 — Decidir nuevo (opcional)
[Solo si hay capacidad / sentido.]

### Paso 4 — Ejecutar
[Tools con guardrails. Reportar guardrail rejections, no insistir.]

### Paso 5 — Reportar
**Si hubo cambios**: reporte detallado.
**Si solo monitoreaste**: una línea compacta.
**Si hay algo crítico**: flag explícito al inicio (⚠️).

## Constraints duros
- [Lista de límites operativos verificables.]

## Principios
- Calidad > cantidad. 0 acciones es válido.
- Cada tick independiente (sin memoria entre ticks).
- Honestidad sobre razonamiento débil.

## Anti-patrones
- ❌ Llamar a otra skill cara desde aquí. Tu scan debe ser ligero.
- ❌ Inventar tools que no existen.
- ❌ Reportes largos cuando no hubo cambios.
```

#### 2.2 Helper script `<HERMES_HOME>/bin/loop-ctl.sh`

Encapsula `start/stop/status/tick-now` aplicando el fix de `enabled_toolsets`
descrito en [cronjobs.md](./cronjobs.md):

```bash
#!/usr/bin/env bash
set -euo pipefail

HERMES_HOME="/home/USER/.hermes-X"
HERMES_BIN="${HERMES_HOME}/hermes-agent/venv/bin/python -m hermes_cli.main"
JOBS_JSON="${HERMES_HOME}/cron/jobs.json"
JOB_NAME="my-loop"
LOOP_SKILL="loop-tick"
SCHEDULE="*/5 * * * *"
REQUIRED_TOOLSETS='["mcp-mytool", "web"]'
DELIVER_TARGET="telegram:<TU_CHAT_ID>"

run_hermes() { HERMES_HOME="${HERMES_HOME}" ${HERMES_BIN} "$@"; }

find_job_id() {
    python3 - <<PY
import json
try:
    d = json.load(open("${JOBS_JSON}"))
    for j in d.get("jobs", []):
        if j.get("name") == "${JOB_NAME}":
            print(j["id"]); break
except FileNotFoundError: pass
PY
}

fix_toolsets() {
    python3 - <<PY
import json
p = "${JOBS_JSON}"
d = json.load(open(p))
for j in d.get("jobs", []):
    if j.get("id") == "$1":
        j["enabled_toolsets"] = ${REQUIRED_TOOLSETS}
json.dump(d, open(p, "w"), indent=2)
PY
}

case "${1:-status}" in
    start)
        [[ -n "$(find_job_id)" ]] && { echo "already running"; exit 0; }
        run_hermes cron create "${SCHEDULE}" \
            --skill "${LOOP_SKILL}" --name "${JOB_NAME}" \
            --deliver "${DELIVER_TARGET}"
        job_id="$(find_job_id)"
        fix_toolsets "${job_id}"
        sudo systemctl restart hermes-X-gateway
        echo "✓ started job=${job_id}"
        ;;
    stop)
        job_id="$(find_job_id)"
        [[ -z "${job_id}" ]] && { echo "not running"; exit 0; }
        run_hermes cron rm "${job_id}"
        echo "✓ stopped"
        ;;
    status)
        job_id="$(find_job_id)"
        [[ -z "${job_id}" ]] && { echo "loop_active: false"; exit 0; }
        echo "loop_active: true · job_id: ${job_id}"
        # ... imprimir last_run, last_status, next_run_at, completed_runs
        ;;
    tick-now)
        run_hermes cron run "$(find_job_id)"
        ;;
esac
```

#### 2.3 Skills user-facing `/start-loop`, `/stop-loop`, `/status-loop`

Son skills *finas*: solo describen al modelo que ejecute el script
correspondiente vía la tool de terminal. Ejemplo `/start-loop`:

```markdown
---
name: start-loop
description: "Arranca el loop autónomo. Comando /start-loop."
tags: [loop, control]
---

## Workflow

1. Avisa al usuario: "Arrancando loop, procedo..."
2. Ejecuta vía terminal: `bash /home/USER/.hermes-X/bin/loop-ctl.sh start`
3. Reporta el `job_id` resultante y el próximo tick estimado.
4. Si el script falla, reporta el error literal — no insistas.
```

### Gotchas críticos

- **`deliver: origin` falla si el cron se crea desde CLI** (no desde
  conversación Telegram). Síntoma: `last_delivery_error: "no delivery target
  resolved for deliver=origin"`. Fix: usar `--deliver telegram:<CHAT_ID>`
  explícito en el script. Patrón de descubrimiento que está en
  [cronjobs.md](./cronjobs.md).

- **`enabled_toolsets` debe estar correcto** o el tick alucina las tool
  calls. Ver [cronjobs.md § "Gotcha enabled_toolsets"](./cronjobs.md). El
  script lo aplica explícitamente tras `cron create`.

- **Cada tick es contexto nuevo**. No hay memoria entre ticks salvo lo que
  el agente lee del estado externo. Esto es feature, no bug: evita drift de
  decisiones a largo plazo.

- **Spam de "no opero"**: con `*/5` y un mercado calmado, son ~12 mensajes/h.
  Considerar `*/15` si molesta, o instruir explícitamente "no reportar si
  no hubo cambios" + cambiar el delivery a un thread silenciable.

- **Stop apaga el cron, no el mundo**. Si el agente abrió posiciones / hizo
  cambios externos, **siguen vivos**. La skill `/stop-loop` debe dejar esto
  claro en su confirmación al usuario.

- **Guardrails server-side, no en el prompt**. El prompt los menciona, pero
  los **valida el tool**. Si el modelo "olvida" un guardrail, la tool lo
  rechaza con `{"error": ..., "guardrail": true}`. Diseñar así protege contra
  drift del prompt y contra modelos futuros que reinterpreten las reglas.

---

## 3. Migración Binance Algo Order (-4120)

### Síntoma

Desde el 2025-12-09, llamadas a `/fapi/v1/order` con tipos condicionales
fallan con:

```json
{"code": -4120, "msg": "Order type not supported for this endpoint.
                       Please use the Algo Order API endpoints instead."}
```

Aplica a TODOS los tipos con `stopPrice` / `callbackRate`:

- `STOP`, `STOP_MARKET`
- `TAKE_PROFIT`, `TAKE_PROFIT_MARKET`
- `TRAILING_STOP_MARKET`

**Importante**: `GET /fapi/v1/exchangeInfo` sigue listándolos en `orderTypes`
del símbolo (la documentación está rezagada respecto al cambio). El rechazo
es a nivel de endpoint, no de configuración del símbolo. Aplica a mainnet
**y** testnet.

### Fix — nuevos endpoints

| Operación | Método | Path | Nota |
|---|---|---|---|
| Crear orden condicional | `POST` | `/fapi/v1/algoOrder` | `algoType=CONDITIONAL` requerido |
| Listar abiertas | `GET` | `/fapi/v1/openAlgoOrders` | params: `symbol` opcional |
| Cancelar una | `DELETE` | `/fapi/v1/algoOrder` | params: `symbol`, `algoId` |

Diferencia clave en el payload:

```diff
- "stopPrice": "80562"
+ "triggerPrice": "80562"
+ "algoType": "CONDITIONAL"
```

Response trae **`algoId`** (no `orderId`). Son IDs distintos del bucket
regular — guardar y cancelar separados.

### Ejemplo SL para LONG (cerrar posición completa al gatillar)

```bash
POST /fapi/v1/algoOrder
{
  "algoType": "CONDITIONAL",
  "symbol": "BTCUSDT",
  "side": "SELL",
  "type": "STOP_MARKET",
  "triggerPrice": "80562",
  "closePosition": "true",
  "workingType": "MARK_PRICE",
  "priceProtect": "true"
}
```

Response (success):

```json
{
  "algoId": 1000000075496921,
  "algoType": "CONDITIONAL",
  "orderType": "STOP_MARKET",
  "algoStatus": "NEW",
  "triggerPrice": "80562.00",
  "closePosition": true,
  "reduceOnly": true,
  ...
}
```

### Implementación Python sin SDK

```python
import time, hmac, hashlib
from urllib.parse import urlencode
import requests

API_KEY = "..."
SECRET = b"..."
BASE = "https://testnet.binancefuture.com"  # o mainnet

def signed_request(method, path, params):
    params = {**params, "timestamp": int(time.time() * 1000)}
    qs = urlencode(params)
    sig = hmac.new(SECRET, qs.encode(), hashlib.sha256).hexdigest()
    r = requests.request(
        method,
        f"{BASE}{path}?{qs}&signature={sig}",
        headers={"X-MBX-APIKEY": API_KEY},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()

# Crear SL
sl = signed_request("POST", "/fapi/v1/algoOrder", {
    "algoType": "CONDITIONAL",
    "symbol": "BTCUSDT",
    "side": "SELL",
    "type": "STOP_MARKET",
    "triggerPrice": "80562",
    "closePosition": "true",
    "workingType": "MARK_PRICE",
    "priceProtect": "true",
})
print("SL algoId:", sl["algoId"])

# Listar todas las algo abiertas del símbolo
opens = signed_request("GET", "/fapi/v1/openAlgoOrders",
                       {"symbol": "BTCUSDT"})

# Cancelar una
signed_request("DELETE", "/fapi/v1/algoOrder",
               {"symbol": "BTCUSDT", "algoId": sl["algoId"]})
```

### Limpieza al cerrar posiciones

Las algo orders con `closePosition=true` **no siempre se auto-cancelan**
cuando se cierra manualmente la posición. Tras un `close_position`, recorrer
`GET /fapi/v1/openAlgoOrders` y cancelar las huérfanas — si no, quedan
en el book listas para gatillarse contra una posición nueva del mismo símbolo.

### Cancelación combinada (regulares + algo)

Para cancelar TODO lo pendiente de un símbolo, hacer dos llamadas:

```python
# Regulares
signed_request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": "BTCUSDT"})

# Algo (una por una)
for a in signed_request("GET", "/fapi/v1/openAlgoOrders",
                        {"symbol": "BTCUSDT"}):
    signed_request("DELETE", "/fapi/v1/algoOrder",
                   {"symbol": "BTCUSDT", "algoId": a["algoId"]})
```

### Referencias

- [Binance docs — New Algo Order](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Algo-Order)
- [freqtrade #12610 — discusión de la migración y workarounds](https://github.com/freqtrade/freqtrade/issues/12610)
- [Binance Futures error codes](https://developers.binance.com/docs/derivatives/usds-margined-futures/error-code)

---

## 4. Anti-alucinación en tareas de consolidación temporal

### Por qué

Hermes tiene salvaguardas fuertes en los skills individuales (`cobertura-cacao`,
`analisis-mercado-cacao`, `analisis-general` — todos con cláusulas
`NUNCA inventar precios/noticias`) y en los prompts de cron (`Si alguna tool
falla, di cuál y por qué — NO inventes datos`). Esas salvaguardas funcionan:
los reportes daily emiten honestamente "balance intraday no disponible —
get_account_summary falló con login failure" cuando no pueden leer la fuente.

Sin embargo, cuando alguien pide en una **conversación interactiva libre**
una tarea de consolidación temporal —tipo "dame un resumen de la semana
pasada", "qué pasó este mes en AROCO"— el LLM no tiene un skill que regule
la tarea. Construye narrativa cohesiva a partir de fragmentos parciales,
rellena huecos con plausibilidades, y a veces invierte datos para que
encajen en el arco que está armando.

### Incidente que originó el patrón (2026-05-26)

Un informe semanal "Resumen del mercado — semana del 18 al 22 de mayo"
entregado al cliente contenía tres categorías de alucinación verificables
contra los reportes daily reales:

1. **Aritmética imposible.** "+13% hasta $4,709 USD/t partiendo de zona
   $3,700–$3,800/t". De $3,800 a $4,709 son +23.9%, no +13%. Las tres
   cifras (inicio, pico, %) eran mutuamente excluyentes.
2. **Desplazamiento temporal.** El "spike del miércoles 20 de mayo" se
   atribuía a un artículo del WSJ del **11 de mayo**. Un artículo del 11
   no puede reportar un evento del 20. El evento real (si existió) ocurrió
   antes de la ventana y fue reubicado dentro de ella para tener un evento
   dramático que contar.
3. **Inversión de un dato fundamental.** El daily real del 20-may reportó:
   *"Stocks ICE-US 25/26 muy por encima del promedio de 5 años → menor
   tensión en oferta física."* El informe semanal afirmó lo contrario:
   *"~2.66M bags muy por debajo del promedio histórico de 4-5M → soporte
   estructural alcista."* La inversión cambia la conclusión de neutro/bajista
   a alcista.

Adicionalmente: citaba como contemporáneos datos COT de 3 semanas antes
de la ventana, citaba artículos del 24-26 de mayo dentro de un informe que
cubre 18-22, y presentaba primas de opciones con rangos específicos
después de admitir que la cadena de Barchart estuvo truncada toda la
semana.

### Diagnóstico

No falló nada de la cadena MCP → skill → cron. Falló la **ausencia** de
un skill que cubriera la tarea "consolidar la semana pasada". Esa tarea
se ejecutó como conversación libre, sin las restricciones duras que sí
tienen los skills disciplinados y los crons.

### Patrón de solución

Para cualquier tarea de **consolidación a través de una ventana temporal**
(resumen semanal/mensual, retrospectiva de N días, "qué pasó", etc.),
crear un skill dedicado con las siguientes propiedades:

#### 4.1 Fuente única declarada

El skill lee de **una sola fuente estructurada** (típicamente los markdown
de `~/.hermes/cron/output/`, o un Sheet, o una tabla de BD). NO mezcla
fuentes en vivo durante la consolidación.

```markdown
## Fuente única — reportes diarios cron

Glob los archivos del filesystem en este árbol:
- `~/.hermes/cron/output/*/YYYY-MM-DD_*.md`

Identificar el tipo de reporte por su encabezado (no por path —
el hash del subdirectorio cambia si el job se recrea).
```

#### 4.2 Restricción dura: no llamar MCPs en vivo

```markdown
1. NUNCA llamar MCPs en vivo. Este skill solo lee archivos del
   filesystem. Si la lectura falla, abortar el brief y reportar
   el error. No suplir con MCPs — derrota el propósito del skill.
```

La tentación de "completar con un check_session a barchart" es la puerta
por la que entra la fabricación. Cerrarla explícitamente.

#### 4.3 Formato fijo con secciones obligatorias

El output tiene una estructura predefinida. Las secciones existen aunque
estén vacías o marcadas `sin dato` / `no disponible esta ventana`. Esto
elimina el incentivo a "rellenar para que la sección quede bonita".

```markdown
*🏦 CUENTA STONEX*
- Balance EOD inicio: $X (lunes {dd})
- Balance EOD cierre: $X (viernes {dd})
- ...

*⚠️ INCIDENCIAS OPERATIVAS*
{Fallos reportados explícitamente por los daily}

*📋 DATOS NO DISPONIBLES EN LA VENTANA*
{Lista explícita de cosas que un resumen "completo" tendría pero
esta semana no se pudo obtener}
```

La sección `DATOS NO DISPONIBLES` es la que más previene alucinación —
declara explícitamente que el agente sabe que hay huecos y no los
está rellenando.

#### 4.4 Detección y declaración de contradicciones

```markdown
### Paso 4 — Detección de contradicciones

Si dos reportes diarios de la ventana se contradicen:
- Mismo Market Intel citado con datos distintos → contradicción.
  Declararla explícitamente en el brief, NO resolverla eligiendo uno.
- Balance EOD distinto sin movimiento explicado → declarar discrepancia.
```

Resolver contradicciones silenciosamente es el mecanismo por el cual el
informe del incidente invirtió el dato de stocks. La regla es:
**reportar la contradicción, no arbitrarla**.

#### 4.5 Prohibición de narrativa cronológica libre

```markdown
3. NUNCA reorganizar la cronología. Si un evento aparece citado el
   viernes pero la noticia es del lunes anterior a la ventana, NO
   presentarlo como "evento del viernes". Marcar fecha del evento real.
```

El patrón "el miércoles pasó X" solo es lícito si el reporte del
miércoles lo dice literalmente. Sin esa restricción, el LLM
reordena eventos para construir un arco narrativo dramático.

#### 4.6 Redirección a skills predictivos

```markdown
Si Pablo pide un análisis nuevo o predictivo ("qué creés que va a
pasar", "view de mercado para la próxima semana"), redirigir a
/analisis-general o /analisis-mercado-cacao. Este skill solo
consolida lo ya reportado.
```

Separar "consolidar lo que fue" de "opinar sobre lo que será". Mezclar
ambos en una sola tarea es lo que produjo el "view alcista moderado"
inventado del informe original.

### Skill de referencia

La implementación concreta para AROCO está en
`~/.hermes/skills/finance/resumen-semanal-aroco/SKILL.md`. Se invoca con
`/resumen-semanal` y consolida la semana laboral anterior por defecto.
Sirve como plantilla para crear consolidadores equivalentes
(mensual, trimestral, retrospectiva por cliente, etc.).

### Regla generalizable

> Cuando se pida al LLM una tarea de **agregación temporal** sobre una
> ventana definida, esa tarea necesita un skill dedicado con fuente
> única, restricciones duras anti-fabricación y formato fijo con
> sección explícita de "datos no disponibles". Una conversación libre
> sin esas guardarraíles producirá narrativa cohesiva con datos
> inventados — la cohesión narrativa es exactamente el incentivo
> que el LLM optimiza cuando no hay restricción.

Aplicar este patrón también a: retrospectivas mensuales, briefs de
performance, reportes para clientes externos, cualquier consolidación
que cruce más de 1 día de fuentes.

---

## 5. MCPs que producen archivos: registrar un resource

### Síntoma

Un MCP tiene un tool que **descarga/genera un archivo** y devuelve su ruta
(ej. `download_csv` → `{"path": "/data/csvs/CC_0__default__20260528_165000.csv"}`).
El agente recibe la ruta, construye `file:///data/csvs/<archivo>.csv` y llama
`read_resource` para leer el contenido — y obtiene:

```
Resource not found: Unknown resource: 'file:///data/csvs/<archivo>.csv'
```

El archivo **existe en disco con ese nombre exacto**. No es un problema de
nombre ni de timestamp. Pega sobre todo en **crons** (descargan el CSV →
intentan leerlo → fallan en silencio → la cadena se queda sin los datos
que acababa de bajar).

### Diagnóstico

Es un desajuste de **forma de API MCP**, no de filesystem. FastMCP distingue
dos cosas:

- **tools** (`@mcp.tool`) — lo que el server expone para *hacer* cosas.
- **resources** (`@mcp.resource`) — lo que el server expone para *leer*.

`resources/read` (lo que hace `read_resource`) solo resuelve **resources
registrados**. Si el server solo declara tools y ninguno matchea
`file:///data/csvs/...`, responde `Unknown resource`. Devolver una ruta como
string desde un tool **no** la registra como resource — el camino de lectura
queda como un callejón sin salida.

Confirmar con un grep:

```bash
grep -nE "@mcp\.resource" server.py    # si no hay nada → este es el bug
```

### Fix — registrar un resource template

Registrar el patrón de URI que el agente ya intenta leer:

```python
import os
from pathlib import Path

CSV_DIR = Path(os.environ.get("BARCHART_DATA_DIR", "/data")) / "csvs"

@mcp.resource("file:///data/csvs/{name}")
def read_csv_resource(name: str) -> str:
    # Guard contra path-traversal: el name resuelto debe quedar dentro de CSV_DIR.
    target = (CSV_DIR / name).resolve()
    if not str(target).startswith(str(CSV_DIR.resolve()) + os.sep):
        raise ValueError(f"Ruta fuera de {CSV_DIR}: {name}")
    if not target.is_file():
        raise FileNotFoundError(f"No existe: {name}")
    return target.read_text()
```

Resuelve exactamente la llamada que el agente ya hace, **sin tocar el skill
ni el cron**. El `{name}` captura el filename completo (incl. `.csv`).

Alternativa/refuerzo: añadir un tool `read_csv(filename)` que devuelva el
contenido — más descubrible, pero obliga a cambiar quien hoy usa
`read_resource`. El resource template es el fix mínimo y el patrón MCP correcto.

### Verificación (handshake real, no solo el puerto)

```bash
# initialize → guardar Mcp-Session-Id → notifications/initialized → resources/read
curl -s -X POST http://127.0.0.1:8769/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":4,"method":"resources/read",
       "params":{"uri":"file:///data/csvs/<archivo>.csv"}}'
# OK = la respuesta trae "contents" con el texto del CSV
```

Tras editar: rebuild del contenedor del MCP **y** restart de Hermes
(no re-descubre en caliente — ver gotcha en
[§1](#1-múltiples-hermes-en-el-mismo-servidor) y abajo).

### Regla generalizable

> Si un tool de un MCP **escribe un archivo y devuelve su ruta** esperando
> que el agente la lea, ese MCP **debe** registrar un `@mcp.resource` que
> resuelva ese `file://...`. Tool que produce ruta + cero resources =
> `Unknown resource` garantizado en cuanto algo intente leer el archivo.
> Revisar este patrón en cualquier MCP con `download_*` / `export_*` /
> `save_*` (candidatos en AROCO: stonex `download_daily_statement`,
> `download_and_extract_daily`).

### Nota de operación

Hermes hace discovery de MCPs **solo al arrancar** — no recarga tools ni
resources en caliente. Si reinicias o editas un MCP individual, hay que
`sudo systemctl restart hermes-gateway` para que el gateway lo vuelva a
descubrir. El log lo confirma en `~/.hermes/logs/agent.log`:
`MCP server '<name>' (HTTP): registered N tool(s)`.

Caso real (2026-05-28): barchart-mcp solo tenía tools; los crons de
cobertura fallaban con `Unknown resource` al leer el CSV recién bajado.
Fix aplicado = resource template de arriba.
