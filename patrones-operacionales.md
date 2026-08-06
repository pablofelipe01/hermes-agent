# Patrones operacionales

Patrones probados en producción que extienden lo cubierto en
[ejemplos.md](./ejemplos.md), [cronjobs.md](./cronjobs.md) y
[instrucciones.md](./instrucciones.md):

1. [Múltiples Hermes en el mismo servidor](#1-múltiples-hermes-en-el-mismo-servidor) — aislamiento vía `HERMES_HOME`.
2. [Loop de trading autónomo](#2-loop-de-trading-autónomo) — cron + skill + helper script.
3. [Migración Binance Algo Order (-4120)](#3-migración-binance-algo-order--4120) — workaround para `STOP_MARKET` / `TAKE_PROFIT_MARKET` desde 2025-12-09.
4. [Anti-alucinación en tareas de consolidación temporal](#4-anti-alucinación-en-tareas-de-consolidación-temporal) — skill disciplinado con fuente única, no conversación libre.
5. [MCPs que producen archivos: registrar un resource](#5-mcps-que-producen-archivos-registrar-un-resource) — fix del `Unknown resource` cuando un tool devuelve una ruta de archivo.
6. [MCPs solapados: deshabilitar uno para evitar confusión del modelo](#6-mcps-solapados-deshabilitar-uno-para-evitar-confusión-del-modelo) — dos stacks que cubren la misma función (email/calendario Fastmail vs Google).
7. [MCPs que scrapean con sesión: que lleguen datos no prueba que la sesión viva](#7-mcps-que-scrapean-con-sesión-que-lleguen-datos-no-prueba-que-la-sesión-viva) — el health check que miente durante meses.
8. [Entregables agendados sobre una sesión que caduca: avisar y degradar](#8-entregables-agendados-sobre-una-sesión-que-caduca-avisar-y-degradar-no-fallar-en-silencio) — skill de cara al usuario + cron watchdog + renovación humana del login.
9. [Automatizar UI ajena: verificar el efecto, no el intento](#9-automatizar-ui-ajena-verificar-el-efecto-no-el-intento) — el click que "funciona" y no hace nada; instrumentar y avisar, no solo arreglar.

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
> que el agente la lea **vía `read_resource`**, ese MCP **debe** registrar un
> `@mcp.resource` que resuelva ese `file://...`. Tool que produce ruta + cero
> resources + ningún tool de lectura = `Unknown resource` garantizado en
> cuanto algo intente leer el archivo. Revisar este patrón en cualquier MCP
> con `download_*` / `export_*` / `save_*`.

La condición no es solo "devuelve una ruta" — son **dos** condiciones juntas:

1. El archivo es **de texto** que el agente quiere leer como contenido, **y**
2. **no existe ningún tool** que reciba la ruta y devuelva ese contenido.

Si falta cualquiera de las dos, no hay bug: un PDF/binario no se lee útilmente
como texto, y un tool de lectura por ruta (`parse_x(path)`) le da al agente un
camino que funciona sin tocar `read_resource`.

### Excepción verificada: stonex-mcp (revisado 2026-05-28)

stonex **comparte la carencia estructural** (no registra ningún `@mcp.resource`,
y `download_daily_statement` devuelve `{"path": ...}`) pero **NO tiene el bug**,
porque falla ambas condiciones de arriba:

- El extracto es un **PDF binario** — `read_resource` → `read_text()` sería
  inútil aunque estuviera registrado.
- Existe un **tool de lectura por ruta**: `extract_statement_data(pdf_path)`
  parsea el PDF server-side y devuelve JSON estructurado. Y
  `download_and_extract_daily` encadena descarga+extracción en una sola
  llamada, así el agente ni maneja la ruta.

Verificación empírica: **cero** `Unknown resource` de stonex en todo el
histórico de `errors.log` (a diferencia de barchart). El único ruido de stonex
en el log son warnings benignos de reintento de conexión en el arranque.

**Conclusión: no tocar stonex** — el diseño "parsear server-side y devolver
JSON" es el correcto para PDFs. El contraste con barchart es justo lo que
define cuándo aplica el patrón: barchart devolvía un CSV (texto) y **no tenía
tool de lectura** → el agente quedaba forzado a `read_resource` → callejón sin
salida.

### Nota de operación

Hermes hace discovery de MCPs **solo al arrancar** — no recarga tools ni
resources en caliente. Si reinicias o editas un MCP individual, hay que
`sudo systemctl restart hermes-gateway` para que el gateway lo vuelva a
descubrir. El log lo confirma en `~/.hermes/logs/agent.log`:
`MCP server '<name>' (HTTP): registered N tool(s)`.

Caso real (2026-05-28): barchart-mcp solo tenía tools; los crons de
cobertura fallaban con `Unknown resource` al leer el CSV recién bajado.
Fix aplicado = resource template de arriba.

---

## 6. MCPs solapados: deshabilitar uno para evitar confusión del modelo

### Por qué

Cuando dos MCPs cubren la **misma función** (p. ej. dos backends de email o de
calendario), el modelo ve toolsets redundantes y puede elegir el equivocado,
mezclar resultados de ambos, o dudar en cada turno. No es un bug del runtime —
es ambigüedad de herramientas. La solución más limpia mientras un stack no sea
el canónico es **dejar uno solo conectado a Hermes**.

### Caso real (2026-06-02)

AROCO tenía dos stacks solapados:

| Función     | Fastmail            | Google                    |
|-------------|---------------------|---------------------------|
| Email       | `mail` (8766)       | `gmail` (8778)            |
| Calendario  | `calendar` (8767)   | `gcalendar` (8779)        |

Google pasó a ser el stack principal (cuenta `alvaro.acosta@aroco.co`), así que
se desconectaron los dos MCPs de Fastmail para que Hermes opere sin ambigüedad.

### Cómo (reversible, sin borrar nada)

1. Comentar el bloque del MCP en `~/.hermes/config.yaml` bajo `mcp_servers:`
   (dejar nota de fecha/motivo y cómo revertir):

   ```yaml
   mcp_servers:
     # Fastmail MCPs deshabilitados 2026-06-02 para evitar conflicto/confusión
     # con stack Google (gmail/gcalendar). Procesos siguen en 8766/8767;
     # solo se desconectaron de Hermes. Revertir = descomentar + restart.
     # mail:
     #   url: http://localhost:8766/mcp
     # calendar:
     #   url: http://localhost:8767/mcp
     gmail:
       url: http://localhost:8778/mcp
     gcalendar:
       url: http://localhost:8779/mcp
   ```

2. `sudo systemctl restart hermes-gateway` — Hermes solo descubre MCPs al
   arrancar (ver Nota de operación del patrón #5).

3. Verificar en `~/.hermes/logs/agent.log` que el conteo bajó y que los servers
   deshabilitados ya no aparecen:
   `MCP: registered N tool(s) from M server(s)`. En este caso pasó de
   **128 tools / 12 servers** a **100 tools / 10 servers**.

4. Smoke test funcional read-only con el stack que queda:

   ```bash
   hermes -t mcp-gmail,mcp-gcalendar -z "Ping gmail, lista calendarios Google y
   eventos de hoy. No envíes ni modifiques nada."
   ```

### Notas

- **Tools que se pierden y no tienen equivalente directo:** `mail` (Fastmail)
  exponía `send_calendar_invite` y `cancel_meeting`, que `gcalendar` no replica
  1:1 — el equivalente Google es crear el evento con invitados vía
  `mcp_gcalendar_create_event`. Revisar siempre qué tools únicas vive solo en el
  MCP que se apaga antes de desconectarlo.
- Los **procesos del MCP apagado siguen corriendo** en su puerto (solo se
  cortó el `url:` desde Hermes). Si se quiere liberar recursos, detener además
  su servicio/contenedor — pero dejarlos vivos hace el revert instantáneo.
- Regla generalizable: ante toolsets redundantes, **un solo stack canónico
  conectado**; el resto comentado con fecha/motivo, no borrado.

---

## 7. MCPs que scrapean con sesión: que lleguen datos no prueba que la sesión viva

### Por qué

Un MCP que scrapea un sitio autenticado con Playwright (`storage_state.json` +
Chromium headless) suele traer un `check_session()` para avisar cuándo hay que
regenerar la sesión. Es la única defensa contra cookies vencidas, así que si
ese check miente, miente en silencio y por meses.

Caso real (`barchart-mcp`, julio 2026): `check_session` devolvía `ok: true` y
`get_options_chain` devolvía datos correctos. Todo verde. La sesión llevaba
semanas caída — el sitio **sirve los mismos datos a usuarios anónimos con una
cuota diaria de vistas**, así que el MCP venía funcionando de prestado, sujeto
a un límite que nadie estaba mirando y con fallos intermitentes inexplicables.

La lección no es sobre Barchart: **en un sitio con tier gratuito anónimo, "la
request devolvió datos" y "sigo logueado" son afirmaciones independientes.**
Un health check que confunde las dos no vale nada.

### Las tres trampas

**1. Verificar la sesión por el string equivocado.**

```python
# MAL: frágil de dos formas distintas
logged_in = "/login" not in url and "Sign In" not in html
```

Falla porque (a) el markup decía `LOGIN`, no `Sign In` — nunca matcheó; y (b)
la página de perfil devuelve 200 con su shell completo a usuarios anónimos, así
que ni la URL ni el status distinguen nada. Un `not in html` sobre la página
entera es especialmente traicionero: cualquier cambio de copy lo rompe, y
rompe hacia el lado optimista.

```python
# BIEN: mirar el bloque del header que muestra la cuenta, y solo ese
block = page.locator(".bc-user-block").first.inner_text()
# anónimo -> "LOGIN Try Barchart for Free"; logueado -> nombre/email
anon = (not block) or re.search(r"\blogin\b|create account", block, re.I)
```

**2. No verificar el plan.** El login puede estar vivo pero la suscripción
caída, y las tools degradan sin decirlo. Truco general: pedir una URL que solo
existe para suscriptores y mirar **si redirige**. La página de venta es el
delator.

```python
page.goto("https://www.barchart.com/my/barchart-plus")
if "/get-barchart-premier" in page.url:  # rebotó al upsell
    plan = "free"
elif "/my/barchart-plus" in page.url:
    plan = "plus"
```

**3. Asumir el formato de un valor en vez de leerlo.** El mismo MCP tenía
`list_expirations()` devolviendo `0` en silencio: filtraba los `<option>` del
dropdown con `^\d{2}-\d{2}-\d{4}$` esperando `MM-DD-YYYY`, pero los valores
reales son **rutas de navegación**:

```
/futures/quotes/CCU26/options/sep-26
```

Y por lo mismo, pasar la expiración como query param (`?expiration=...`) no
hacía nada: el sitio navega por ruta, así que el parámetro se ignoraba y la
tool devolvía siempre el mes por defecto — silenciosamente, que es lo peor.
La forma correcta es cargar la página, **leer el dropdown** y navegar al
`value` que trae:

```python
opts = page.eval_on_selector_all(
    "#bc-options-toolbar__dropdown-month option",
    "els => els.map(e => ({value: e.value, label: e.textContent.trim()}))")
match = next(e for e in opts if wanted in (e["label"].lower(), ...))
page.goto("https://www.barchart.com" + match["value"])
```

Regla: **nunca inventes el formato de un identificador del sitio.** Sondealo
una vez con un script de dos líneas y programá contra lo que devuelve. Si un
filtro puede vaciar una lista, que falle ruidoso o devuelva las opciones
válidas — nunca `[]` sin explicación.

### Cómo debe verse un `check_session` honesto

Devolver **estado desglosado, no un booleano**. Quién, qué plan, y la evidencia
cruda para depurar sin abrir un navegador:

```json
{
  "ok": false,
  "logged_in_as": "cuenta@ejemplo.com",
  "plan": "free",
  "user_block": "LOGIN Try Barchart for Free",
  "final_url": "https://www.barchart.com/get-barchart-premier?ref=tryPremier"
}
```

Para identificar la cuenta, el DOM rara vez sirve. Las **cookies de analytics**
suelen tener el email en claro y son mucho más estables que cualquier selector:

```python
# ab.storage.userId.<uuid> guarda "g:<email>|e:...|c:..." doble-urlencoded
for c in ctx.cookies():
    if c["name"].startswith("ab.storage.userId"):
        dec = urllib.parse.unquote(urllib.parse.unquote(c["value"]))
        m = re.search(r"g:([^|]+@[^|]+)", dec)
```

### Notas

- **Verificar antes de guardar, no después.** El `setup_login.py` que genera el
  `storage_state.json` debe comprobar login + plan y **negarse a escribir** si
  la sesión quedó anónima. Si no, se copia al servidor una sesión inútil que
  "parece" funcionar — el mismo bug, ahora en producción.
- **El fix hace visible el problema, no lo cura.** Al arreglar el check, la
  sesión sigue caída: hay que regenerarla igual. Esperar que el conteo de
  fallos *suba* tras el fix es lo normal y es buena señal.
- **Rebuildear el contenedor no basta.** Hermes solo descubre MCPs y sus
  descripciones al arrancar: si cambian los docstrings de las tools, hace falta
  `sudo systemctl restart hermes-gateway` o el modelo sigue creyendo la firma
  vieja (ver patrón #5).
- Un `check_session` que solo puede decir `true` **no es un check**; es un
  comentario. Antes de confiar en uno, forzá el caso negativo (borrá las
  cookies) y comprobá que efectivamente diga `false`.

## 8. Entregables agendados sobre una sesión que caduca: avisar y degradar, no fallar en silencio

### Por qué

El patrón #7 arregla el *detectar* que una sesión scrapeada murió. Este es el
paso siguiente: cuando un **skill de cara al usuario** depende de esa sesión —
"que Renata baje la cadena de opciones y me la mande por correo" — la sesión va
a caducar tarde o temprano, y el login no se puede rehacer headless (SSO de
Google + Cloudflare). Si el skill no contempla ese estado, el usuario pide su
informe y recibe: nada, un error críptico, o —peor— datos de la cuota anónima
que "parecen" reales. La sesión caduca es el estado *esperado*, no la excepción.

### Caso real (`barchart-options-chain` de Renata, 2026-07-17)

Alvaro pidió por Telegram el informe de opciones de cacao y "no funcionaba". Dos
causas independientes:

1. La sesión de Barchart (`renata@aroco.co`) estaba caduca → el fetch daba
   **HTTP 401**. Ningún aviso; el skill simplemente no producía nada.
2. Aunque hubiera datos, el skill entregaba el CSV crudo por Telegram, no el
   correo que el usuario esperaba.

### Patrón de solución

**1. El script de fetch distingue "caduca" de "otro error" con un exit code.**
No basta con que truene; el llamador tiene que poder ramificar.

```python
try:
    resp = opener.open(req)
except urllib.error.HTTPError as e:
    if e.code in (401, 403):
        print("SESION_CADUCA", file=sys.stderr); sys.exit(2)   # → renovar login
    print(f"ERROR_HTTP {e.code}", file=sys.stderr); sys.exit(3) # → reintentar/avisar
```

**2. El skill verifica ANTES de trabajar y degrada con un mensaje accionable.**
Si `check_session` (honesto, ver #7) dice caduca, o el fetch sale con exit 2, no
sigue: avisa por el mismo canal donde se pidió, y dice **exactamente cómo se
arregla**, no "hubo un error".

> ⚠️ No pude generar el informe: la sesión de Barchart (`renata@aroco.co`) está
> caducada. Renovarla desde una laptop: `python setup_login.py` (login con
> Google) → `scp storage_state.json aroco-server:~/projects/data/barchart/` →
> `docker compose restart` del `barchart-mcp`.

**3. Un cron watchdog detecta la caducidad ANTES de que el usuario la sufra.**
Mismo patrón que el chequeo de sesión del notetaker Meet: un cron diario corre
un skill corto que llama `check_session` y **solo** manda Telegram si está caída
(silencioso si todo bien). Así el aviso llega de mañana, no en medio de la
reunión / la petición urgente.

- > ❌ **Corregido 2026-08-04 — esto era exactamente al revés.** Aquí decía
  > "deliver `local` + el skill manda el Telegram él mismo con `send_message`".
  > **No funciona:** el scheduler construye el agente con
  > `disabled_toolsets=["cronjob","messaging","clarify"]`, que pisa el
  > `messaging` del job. El agente no encuentra la tool, **asume que el sistema
  > entregará su texto y responde "AVISO ENVIADO"** — el cron queda
  > `last_status: ok` y nadie recibe nada. Costó 8 días de avisos perdidos y
  > ~11 reuniones. Seguir este bullet como estaba reproduce el bug.
- **Lo correcto:** `deliver` = los destinos reales
  (`telegram:<chat_id>,telegram:<chat_id>`, acepta varios por coma), el skill
  **responde el aviso como su texto final**, y responde `[SILENT]` cuando todo
  está bien (suprime la entrega, `cron/scheduler.py:SILENT_MARKER`). En el skill,
  prohibir explícitamente las tools de envío y el "AVISO ENVIADO".
- **Ojo con la frecuencia del cron al aplicar `[SILENT]`:** en un watchdog diario
  un `[SILENT]` de más es inocuo; en un cron `*/15` son ~56 mensajes/día. Probar
  el camino "todo bien" antes de dejarlo en producción.
- **Gotcha:** el cron debe declarar `enabled_toolsets` con el MCP del check (el
  `messaging` es inútil, ver arriba); sin eso el modelo alucina la llamada (ver
  patrón #4). `hermes cron edit --deliver` preserva `enabled_toolsets`.

### Reglas generalizables

- **La renovación es un paso humano y hay que decirlo en el skill**, no
  esconderlo. El agente no puede hacer un SSO interactivo; que no lo intente ni
  finja que puede. Documentá los comandos de renovación dentro del propio
  `SKILL.md`.
- **Verificá salud al principio del turno, no al final.** Descubrir la sesión
  muerta después de haber "armado" el informe desperdicia el turno y confunde.
- **El canal de entrega es parte del requisito.** "Mándamelo por correo bonito
  como las notas de reunión" no es lo mismo que "adjunta el CSV". Ojo con las
  limitaciones del transporte: `renata-gmail.send_message` **no adjunta
  archivos** → la tabla va como HTML en el cuerpo; si hace falta un archivo
  descargable, la vía es Drive con link, no un adjunto.
- **No se puede probar de punta a punta con la sesión muerta.** Al reescribir un
  skill así, dejá el aviso/degradación listos y verificá el camino feliz en la
  primera corrida real tras renovar — en especial que el agente pueda ejecutar
  los `docker cp/exec` desde su terminal.

---

## 9. Automatizar UI ajena: verificar el efecto, no el intento

Descubierto en el notetaker de Renata (2026-08-06): durante ~6 semanas el bot
entró a las reuniones y grabó **0 líneas en el 34% de los casos**, sin un solo
error en ningún log. El código hacía esto:

```python
await _enable_captions(page)   # click en "Subtítulos"; el bool se descartaba
```

El click "funcionaba" (encontraba un botón, lo clickeaba) pero los subtítulos no
quedaban activos, y el bot pasaba dos horas leyendo una región del DOM que no
existía. Caso completo en `renata-notetaker-reuniones.md`.

### Reglas generalizables

- **El predicado correcto no es "¿se ejecutó mi acción?" sino "¿existe el estado
  que la acción debía producir?".** Verificar contra **el mismo selector/recurso
  que consume el código de abajo** — así la comprobación significa algo. Un
  `click()` que no lanza excepción no prueba nada sobre la UI de un tercero.
- **Cuidado con los controles *toggle*.** Reintentar un click sobre un toggle no
  es idempotente: puede *deshacer* lo que ya estaba bien. Verificar **antes** de
  clickear, no solo después.
- **Un proceso largo debe verificar sus precondiciones al arrancar, no al
  entregar.** Dos horas de captura sobre un estado inválido son dos horas
  perdidas; el chequeo cuesta segundos y va antes del bucle.
- **Registrar el resultado del chequeo en el artefacto** (aquí `captions_ok` en
  el job). Sin eso no se distingue "el mecanismo falló" de "no había nada que
  capturar", y la falla se vuelve indiagnosticable a posteriori. El campo es
  además lo que permite que el aviso diga *por qué*.
- **"Vacío" casi nunca es un caso normal — no lo trates como éxito.** El skill
  marcaba `sent` ante una transcripción vacía y la reunión desaparecía sin
  rastro. Un resultado vacío en un proceso que debía producir algo es una
  **falla que hay que reportar**, y el aviso debe llegar a un humano.
- **Los tres cambios van juntos: arreglar, instrumentar, avisar.** Solo el
  arreglo deja el sistema igual de opaco ante el próximo cambio de UI de Google
  — que va a ocurrir.

### Cómo probarlo sin el sistema externo

La lógica de reintento/verificación se testea con un doble del `Page` que
simule "el estado aparece tras N intentos" — sin Meet, sin red, en segundos.
Casos mínimos: **funciona al 1er intento** · **se rinde tras N** · **0 acciones
si ya estaba en el estado deseado** (el toggle) · **reintenta y logra**.
