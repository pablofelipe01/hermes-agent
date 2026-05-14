# Cronjobs — Operación, testeo y troubleshooting

Guía de los **scheduled jobs** de Hermes (`hermes cron …`): cómo crearlos,
operarlos, testearlos sin spammear destinatarios reales, y el gotcha más
caro que descubrimos en producción.

Basado en el despliegue AROCO, incluyendo el incidente del **2026-05-14**
donde un cronjob entregó datos alucinados a un destinatario externo.

---

## 0. Qué es un cronjob Hermes

Cada job es un **prompt + horario + destino** que el scheduler interno de
Hermes ejecuta en un agente nuevo. No es un cron del sistema operativo —
todo vive en proceso dentro del servicio `hermes-gateway`.

```
┌────────────────────────────┐
│  hermes-gateway (systemd)  │
│                            │
│  cron.scheduler  ──tick──►  agente nuevo  ──tool calls──►  MCPs, web,
│       ▲                          │                          terminal,
│       │                          ▼                          file, …
│  cron/jobs.json          cron/output/<id>/                  
└────────────────────────────┘             │
                                           │ delivery
                                           ▼
                                   Telegram / Signal / local
```

- **Estado persistente:** `~/.hermes/cron/jobs.json`
- **Outputs guardados:** `~/.hermes/cron/output/<job_id>/<YYYY-MM-DD_HH-MM-SS>.md`
  (uno por ejecución — fuente de verdad para auditar)
- **Tick:** cada minuto
- **Hora:** local del servidor (en AROCO: `America/Bogota`)

---

## 1. Anatomía de un job

Cada entrada en `jobs.json` tiene estos campos críticos:

| Campo | Qué hace |
|---|---|
| `id` | Hex corto. Se usa con `hermes cron <subcmd> <id>` |
| `name` | Etiqueta humana |
| `prompt` | Lo que el agente recibe como instrucción. **Self-contained** (el agente no tiene memoria de turnos anteriores) |
| `schedule.kind` | `cron` (recurrente), `interval` o `once` (one-shot) |
| `enabled_toolsets` | **Lista blanca** de toolsets disponibles para el agente. Si está `null` / vacío, hereda los defaults de la plataforma |
| `deliver` | `origin`, `local`, o `<plataforma>:<chat_id>` |
| `origin` | Dict con la plataforma+chat de donde se creó (para `deliver: origin`) |
| `enabled` | Bool. Si `false` no se dispara |
| `repeat.times` | Para `once`: 1. Para recurrentes: `null` (infinito) o un entero |

---

## 2. Comandos CLI

Todos bajo `hermes cron …`:

| Comando | Propósito |
|---|---|
| `list` | Tabla de jobs con su próximo run |
| `status` | Verifica que el scheduler esté vivo dentro del gateway |
| `create <schedule> [prompt]` | Crear job |
| `edit <id> [--prompt …] [--deliver …] [--schedule …] [--name …]` | Editar campos comunes. **No edita `enabled_toolsets`** — eso se hace a mano en el JSON |
| `pause <id>` / `resume <id>` | Toggle reversible. Pause cancela un `run` pendiente |
| `run <id>` | Marca el job como "due" para el próximo tick (~60s) |
| `tick` | Ejecuta una pasada del scheduler ahora (one-shot manual del scheduler completo) |
| `remove <id>` | Borrar definitivamente |

---

## 3. Formato del `schedule`

`hermes cron create <schedule>` acepta varios formatos. Internamente todos
se normalizan al campo `schedule.kind` en el JSON:

| Entrada | `kind` | Ejemplo |
|---|---|---|
| Expresión cron 5-campos | `cron` | `'0 6 * * *'` (todos los días 06:00 local) |
| Intervalo legible | `interval` | `'30m'`, `'every 2h'`, `'1d'` |
| Timestamp ISO | `once` | `'2026-05-15T06:10'` (one-shot a esa hora local) |

> ⚠️ Las expresiones cron de Hermes son en **hora local del servidor**, no
> UTC. En AROCO `America/Bogota` (UTC−5). Distinto al `/schedule` remoto
> de claude.ai (que sí es UTC).

---

## 4. Targets de delivery

El campo `deliver` controla a dónde llega la respuesta del agente:

- `origin` — al chat de donde se creó el job (campo `origin`). Es el default
  cuando se crea un job desde Telegram.
- `local` — solo guarda el output en disco, no entrega a nadie.
- `<plataforma>:<chat_id>` — fuerza una entrega específica.
  Ej: `telegram:5130071932`.

**Tip:** redirigir `deliver` a tu propio chat es la forma más rápida de
testear un job sin spammear al destinatario real. Ver §6.

---

## 5. ⚠️ Gotcha crítico: `enabled_toolsets`

**Si el prompt menciona una tool que el toolset no incluye, el modelo
alucina la llamada.** No es opinión — es un bug reproducido en producción
y advertido en la propia doc upstream (`hermes-agent/AGENTS.md:849`).

### Caso real (2026-05-14)

Un job diario para enviar el informe agroclimático de GeoCampo a un
operador externo fue creado con:

```json
"prompt": "Obtén el informe agroclimático de GeoCampo usando mcp_geocampo_get_informe …",
"enabled_toolsets": ["web"]
```

`web` no incluye el MCP `geocampo`. Como el agente no tenía la tool pero
el prompt la nombraba, el modelo **escribió la llamada como texto** y
**se inventó la respuesta**:

```
{"tool": "mcp_geocampo_get_informe"}

I need to call the GeoCampo MCP tool. Let me execute it now.

<function_response>
{"finca":"Finca El Paraíso","ubicacion":"Zona Cacaotera Norte, Ecuador", …}
```

Ni Ecuador, ni Finca El Paraíso, ni los riesgos de Moniliasis del 74%
existían. Llegó por Telegram con alertas críticas y recomendación de
aplicar fungicida. **Era 100% ficción.**

### Cómo se ve el bug

Síntomas en el output guardado (`~/.hermes/cron/output/<id>/*.md`):

- La respuesta empieza con `{"tool": "..."}` o `<function_response>` como
  **texto plano** (no aparece como tool call estructurada en el log).
- El contenido contradice realidades verificables (nombres, lugares, IDs).
- Los logs del scheduler dicen "N MCP tool(s) available" — el MCP está
  registrado, pero el toolset del job no lo expone al agente.

### Cómo arreglarlo

El toolset canónico de un MCP server se llama `mcp-<nombre>`. Para el
servidor `geocampo` el toolset es `mcp-geocampo`. Lista de toolsets MCP:

```bash
grep "registered.*tool" ~/.hermes/logs/agent.log | tail -20
# Cada MCP server N → toolset "mcp-N"
```

Editar `~/.hermes/cron/jobs.json` (no hay flag CLI):

```diff
"enabled_toolsets": [
  "web",
+ "mcp-geocampo"
]
```

Hermes relee `jobs.json` en cada tick; **no requiere restart**.

### Toolsets útiles

| Toolset | Para qué |
|---|---|
| `web` | `web_search`, fetch de URLs |
| `terminal` | Bash (cuidado con la blast radius) |
| `file` | Read / Write / Edit / Glob |
| `mcp-<server>` | Tools expuestas por ese MCP |
| `code_execution` | Sandbox para correr código |

Para que un cronjob pueda llamar a `mcp_geocampo_get_informe` y nada más,
`["mcp-geocampo"]` alcanza. Para uno que verifica archivos del sistema,
`["terminal", "file"]`.

---

## 6. Patrón seguro de testeo

`hermes cron run <id>` dispara el job en el próximo tick — incluyendo
entrega real al destinatario. Para validar sin re-spammear:

```bash
# 1. Backup defensivo
cp ~/.hermes/cron/jobs.json ~/.hermes/cron/jobs.json.bak.$(date +%Y%m%d-%H%M%S)

# 2. Redirigir delivery a tu chat (no toca `origin`)
hermes cron edit <id> --deliver telegram:<TU_CHAT_ID>

# 3. Disparar
hermes cron run <id>

# 4. Esperar ~60–90s y leer el output recién creado
ls -t ~/.hermes/cron/output/<id>/ | head -1

# 5. Verificar contenido. Si OK, restaurar
hermes cron edit <id> --deliver origin
```

**No asumir éxito por el exit code de `run`** — eso solo confirma que el
job quedó marcado como due. La fuente de verdad es el `.md` guardado en
`cron/output/<id>/`.

Si necesitás abortar entre el `run` y el tick, `hermes cron pause <id>`
cancela la ejecución pendiente.

---

## 7. Patrón de auto-verificación

Para jobs críticos que entregan a terceros, montar un **segundo cronjob
one-shot** que valide el output del primero y reporte al operador interno.

Ejemplo: el reporte agroclimático diario corre a las 06:00. Un job
one-shot a las 06:10 verifica que el output sea real:

```bash
hermes cron create '2026-05-15T06:10' \
  "Verifica que el cronjob <id_primario> salió correctamente esta mañana. \
   Pasos: (1) leer el archivo más reciente en ~/.hermes/cron/output/<id_primario>/ \
   — debe tener timestamp de hoy ~06:00, no de ayer; \
   (2) confirmar que el contenido NO menciona 'Ecuador' ni 'Finca El Paraíso' \
   (síntoma del bug histórico); \
   (3) confirmar que la respuesta NO empieza con literal '{\"tool\":' \
   ni '<function_response>' (alucinación); \
   (4) revisar last_status del job <id_primario> en ~/.hermes/cron/jobs.json; \
   (5) reportar a Pablo: 'ok' si todo bien o el detalle de qué falló." \
  --name "Verificación X" \
  --deliver telegram:<TU_CHAT_ID>

# Después editar el JSON para añadir toolsets de file/terminal:
# "enabled_toolsets": ["terminal", "file"]
```

Una vez verificado que el primario es estable durante varios días, este
job one-shot puede borrarse o convertirse en un recurrente con baja
frecuencia (semanal).

---

## 8. Troubleshooting checklist

Cuando un cronjob entrega algo raro o no entrega:

1. **Output en disco:** ¿hay un `.md` reciente en `cron/output/<id>/`?
   - No → el scheduler no lo ejecutó. Revisar `enabled`, `paused_at`,
     `next_run_at`, y `journalctl -u hermes-gateway`.
   - Sí → §2 abajo.

2. **Contenido del output:**
   - ¿Empieza con `{"tool":` o `<function_response>` literal? → bug de
     `enabled_toolsets` (§5). Faltan toolsets en la lista.
   - ¿Contiene `[SILENT]` solo? → el agente decidió no reportar (no es bug).
   - ¿Datos sospechosos / contradicen realidades verificables? → mismo
     bug de §5, o prompt ambiguo.

3. **Delivery:**
   - `last_status: "ok"` pero el destinatario no recibió → mirar
     `last_delivery_error` y `~/.hermes/logs/gateway.log`.

4. **MCP disponible pero no llamado:**
   - Confirmar que el toolset `mcp-<nombre>` está en `enabled_toolsets`
     del job (no basta con que el MCP esté registrado a nivel de gateway).

---

## 9. Referencias

- Listado y registro de MCPs: `~/.hermes/logs/agent.log` →
  buscar `registered.*tool`
- Doc upstream sobre alucinación cross-toolset:
  `~/.hermes/hermes-agent/AGENTS.md:849`
- Implementación del scheduler: `~/.hermes/hermes-agent/cron/`
- Parsing del schedule: `cron/jobs.py::parse_schedule`
- Toolset canónico MCP: `tools/mcp_tool.py:2919` → `f"mcp-{name}"`

Ver también: [`instrucciones.md`](./instrucciones.md) (deploy),
[`ejemplos.md`](./ejemplos.md) (añadir MCP),
[`comandos.md`](./comandos.md) (slash commands).
