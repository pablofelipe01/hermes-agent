# Patrones operacionales

Patrones probados en producción que extienden lo cubierto en
[ejemplos.md](./ejemplos.md), [cronjobs.md](./cronjobs.md) y
[instrucciones.md](./instrucciones.md):

1. [Múltiples Hermes en el mismo servidor](#1-múltiples-hermes-en-el-mismo-servidor) — aislamiento vía `HERMES_HOME`.
2. [Anti-alucinación en tareas de consolidación temporal](#2-anti-alucinación-en-tareas-de-consolidación-temporal) — skill disciplinado con fuente única, no conversación libre.
3. [MCPs que producen archivos: registrar un resource](#3-mcps-que-producen-archivos-registrar-un-resource) — fix del `Unknown resource` cuando un tool devuelve una ruta de archivo.
4. [MCPs solapados: deshabilitar uno para evitar confusión del modelo](#4-mcps-solapados-deshabilitar-uno-para-evitar-confusión-del-modelo) — dos stacks que cubren la misma función (email/calendario Fastmail vs Google).
5. [MCPs que scrapean con sesión: que lleguen datos no prueba que la sesión viva](#5-mcps-que-scrapean-con-sesión-que-lleguen-datos-no-prueba-que-la-sesión-viva) — el health check que miente durante meses.
6. [Entregables agendados sobre una sesión que caduca: avisar y degradar](#6-entregables-agendados-sobre-una-sesión-que-caduca-avisar-y-degradar-no-fallar-en-silencio) — skill de cara al usuario + cron watchdog + renovación humana del login.
7. [Automatizar UI ajena: verificar el efecto, no el intento](#7-automatizar-ui-ajena-verificar-el-efecto-no-el-intento) — el click que "funciona" y no hace nada; instrumentar y avisar, no solo arreglar.
8. [Contenedores que lanzan navegador: `init: true` o acumulan zombies](#8-contenedores-que-lanzan-navegador-init-true-o-acumulan-zombies) — el PID 1 de tu app no cosecha huérfanos; 187 zombies invisibles.
9. [Bypass de sandbox del navegador: la lógica corre y no sirve](#9-bypass-de-sandbox-del-navegador-la-lógica-corre-y-no-sirve) — AppArmor + variable de entorno equivocada; el toolset `browser` nativo está roto.
10. [Un agente depurando toca producción — y no necesariamente la suya](#10-un-agente-depurando-toca-producción--y-no-necesariamente-la-suya) — `HERMES_HOME` separa datos, no privilegios.
11. [Avisar por fuera del agente lo deja fuera de contexto](#11-avisar-por-fuera-del-agente-lo-deja-fuera-de-contexto) — el mensaje llega, pero el agente no sabe que lo mandó; `scripts/avisar_por_agente.sh`.
12. [Sin saldo en el proveedor, un cron periódico se vuelve un spammer](#12-sin-saldo-en-el-proveedor-un-cron-periódico-se-vuelve-un-spammer) — el 402 se entrega como mensaje cada tick, tumba al agente entero y deja una marca en disco que dura una hora más que el problema.
13. [Una tool detrás de un túnel de Cloudflare tiene 100 segundos](#13-una-tool-detrás-de-un-túnel-de-cloudflare-tiene-100-segundos) — el scraper que tardaba 95,6 s y "funcionaba"; precalentar con cron + caché corta en vez de pelear con el timeout.
14. [Dos agentes, una cuenta externa: compartir el MCP, no duplicarlo](#14-dos-agentes-una-cuenta-externa-compartir-el-mcp-no-duplicarlo) — una sesión en vez de dos, una copia del parser en vez de dos; y el precio: es todo o nada.

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

## 2. Anti-alucinación en tareas de consolidación temporal

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

## 3. MCPs que producen archivos: registrar un resource

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

## 4. MCPs solapados: deshabilitar uno para evitar confusión del modelo

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

## 5. MCPs que scrapean con sesión: que lleguen datos no prueba que la sesión viva

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

## 6. Entregables agendados sobre una sesión que caduca: avisar y degradar, no fallar en silencio

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

## 7. Automatizar UI ajena: verificar el efecto, no el intento

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
- **Un selector por texto es ambiguo hasta que demuestres lo contrario.** El
  regex `/subtítulos/` matcheaba tres botones ("Abrir ajustes de subtítulos",
  "Ir a los subtítulos más recientes", el toggle real) y el helper clickeaba el
  primero en orden de documento — abría un panel y reportaba éxito. **Listá todos
  los elementos que matchea tu patrón, en orden, contra el DOM real** antes de
  confiar en él; y anclá los labels (`^activar subtítulos$`). Corolario: cuando
  el patrón es ambiguo, **reintentar no arregla nada** — los N intentos caen en
  el mismo elemento equivocado.
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
- **Instrumentá para el fallo que NO podés reproducir.** El fallo ocurría en
  reuniones con gente y una sala vacía no lo reproduce: sin volcar screenshot +
  labels *en el momento del fallo real*, el diagnóstico es conjetura. Si no podés
  reproducir en laboratorio, la instrumentación no es un extra — es el único
  camino a la causa.
- **No metas una dimensión ortogonal dentro de un enum existente.** Marcar
  `status = "in_call_sin_subtitulos"` (avance del job + salud de los subtítulos
  en el mismo campo) rompió en silencio un anti-duplicado que comparaba contra
  una lista blanca de estados, y lanzó un segundo bot a una reunión en curso.
  Dimensión nueva → **campo nuevo**.
- **Preferí lista negra a lista blanca en los guardas.** `status not in
  ("error",)` falla del lado seguro cuando aparece un estado nuevo; `status in
  (...largo...)` deja el agujero abierto sin que nadie se entere. Toda lista
  blanca de estados es una bomba de tiempo para el próximo que añada uno.

### Cómo probarlo sin el sistema externo

La lógica de reintento/verificación se testea con un doble del `Page` que
simule "el estado aparece tras N intentos" — sin Meet, sin red, en segundos.
Casos mínimos: **funciona al 1er intento** · **se rinde tras N** · **0 acciones
si ya estaba en el estado deseado** (el toggle) · **reintenta y logra**.

### Segunda ronda (2026-08-10): el selector era correcto y aun así fallaba

Cuatro días después del arreglo de arriba, el mismo síntoma volvió. La causa no
estaba en nuestro código: **otro notetaker (Read AI, puesto por otra persona del
equipo) abría un modal de consentimiento que tapaba la barra entera**. El botón
correcto estaba en el DOM; el modal se comía el click. Reglas nuevas:

- **"Está en el DOM" no es "se puede pulsar".** Cuando un selector verificado no
  produce efecto, la pregunta ya no es *"¿es el selector correcto?"* sino
  **"¿hay algo encima?"**. Un screenshot lo contesta en 5 segundos; inspeccionar
  el DOM no — el elemento aparece presente y visible. Y ojo: el `force: True` de
  Playwright **no** salva un modal; despacha el click y la app lo ignora.
- **No estás solo en el entorno del tercero.** Una UI ajena y compartida cambia
  no solo porque su dueño la actualice, sino porque **aparecen otros actores**
  (otro bot, otro plugin, otra política). El fallo empezó en agosto y no antes
  porque Read AI no existía en la sala en julio. Al fechar un fallo, preguntar
  *"¿qué cambió alrededor?"*, no solo *"¿qué cambiamos nosotros?"*.
- **Descartar obstáculos periódicamente, no solo al arrancar.** El modal no
  aparece al entrar: aparece cuando al otro bot le da la gana, a mitad de
  reunión. Una precondición que se verifica una vez es una precondición que se
  pierde.
- **Para pulsar a ciegas, lista BLANCA — aquí sí.** Es la excepción a la regla de
  arriba (guardas → lista negra): un guarda debe fallar cerrando, pero **una
  acción sobre un botón desconocido debe no ocurrir**. En la misma pantalla
  convivían "Cancelar" y "Iniciar Read AI" (compartir el audio de un comité con
  un tercero) y "Salir de la llamada". El criterio: *lista negra para decidir si
  sigo; lista blanca para decidir si actúo.*
- **Soltar un recurso compartido también hay que verificarlo.** Salir mal de la
  sala dejaba una sesión fantasma, y entonces el servicio ofrecía "Cambiar aquí"
  en vez de "Unirse ahora" y **todas las entradas siguientes fallaban**. Un
  `finally` que *intenta* liberar y no comprueba nada se lee como correcto y deja
  deuda invisible. **Colgar mal no falla hoy: falla mañana**, y el error aparece
  lejos de su causa — lo habíamos atribuido a credenciales caducadas.
- **Sin logs, cada incidente se investiga desde cero.** El bot no emitía una sola
  línea; el diagnóstico salía de screenshots. Añadir logging por fase fue lo que
  permitió encontrar las otras dos causas **el mismo día**. Cuidado con el
  handler: `uvicorn`/`fastmcp` reconfiguran el root logger y sin handler propio
  no sale nada a `docker logs` — el logging "está puesto" y no se ve.
- **Un `done` que no produjo nada no es un resultado: es un fallo silencioso.**
  Si el guarda anti-duplicado lo trata como terminal, bloquea el reintento
  mientras la tarea todavía era recuperable. Distinguir *terminó* de *terminó
  habiendo producido algo*.
- **Los defaults de espera de un bot deben modelar el comportamiento humano.**
  Salir a los 3 minutos de estar sola es razonable al final de una reunión y
  absurdo al principio: **la gente se conecta tarde.** El mismo umbral aplicado a
  los dos momentos hacía que el bot se fuera justo antes de que llegara el
  primero — y desde fuera se ve idéntico a "el bot no entró". Umbral asimétrico
  según la fase, no una constante.

---

## 8. Contenedores que lanzan navegador: `init: true` o acumulan zombies

### Por qué

En un contenedor sin init, el **PID 1 es el proceso de la app** (`python
server.py`). PID 1 hereda a los huérfanos y es el único que puede cosecharlos,
pero un servidor MCP no llama a `wait()` — no sabe que ese es su trabajo. Cada
Chromium que termina deja una entrada `Z` en la tabla de procesos que **nadie
retira mientras el contenedor viva**.

No rompe nada el primer día: un zombie no consume CPU ni memoria, solo una
entrada de PID. Por eso crece invisible durante semanas y el único aviso es una
línea del MOTD al entrar por SSH.

### Caso real (2026-08-19)

187 zombies en el servidor. Repartidos entre exactamente los tres MCPs que usan
navegador:

| Contenedor        | Zombies | Uptime  |
|-------------------|---------|---------|
| `renata-meet-mcp` | 144     | 8 días  |
| `barchart-mcp`    | 25      | 10 días |
| `stonex-mcp`      | 18      | 10 días |

Ningún otro MCP tenía uno solo. La correlación con "¿lanza Chromium?" fue
perfecta — y es el diagnóstico, no una coincidencia.

### Diagnóstico

```bash
# ¿cuántos y de quién cuelgan?
ps -eo stat --no-headers | grep -c '^Z'
ps -eo stat,ppid --no-headers | awk '$1 ~ /^Z/ {print $2}' | sort | uniq -c | sort -rn

# mapear el PID padre a su contenedor
cat /proc/<pid>/cgroup | grep -oE '[0-9a-f]{64}' | head -1
docker ps --no-trunc --format '{{.ID}} {{.Names}}' | grep ^<hash>
```

### Cómo

`init: true` en el servicio del `docker-compose.yml` — mete `docker-init` (tini)
como PID 1, que sí cosecha:

```yaml
services:
  mi-mcp:
    build: .
    container_name: mi-mcp
    restart: unless-stopped
    # tini como PID 1: cosecha los hijos del navegador.
    init: true
```

Luego `docker compose up -d` (recrea; no hace falta rebuild) y verificar:

```bash
docker exec mi-mcp ps -p 1 -o comm=      # -> docker-init
ps -eo stat --no-headers | grep -c '^Z'  # -> 0
```

### Notas

- **Recrear es seguro si el estado vive en un bind mount**, que es la convención
  de estos MCPs (`~/projects/data/<mcp>:/data`). Las sesiones de Google y de
  Barchart sobrevivieron; se confirma con `verify_session` / `check_session`
  *después* de recrear, no se asume — ver patrón #7.
- ⚠️ **Pero no recrear un MCP que esté en mitad de un trabajo largo.** El bot de
  Meet pierde la reunión y su transcripción. Antes: sin jobs `waiting`/`running`
  y sin nada próximo en el calendario ni en `cron list`.
- **Aplicarlo de entrada a todo MCP nuevo que use Playwright**, no cuando el
  MOTD avise. El coste es una línea; el diagnóstico a posteriori es una tarde.
- La regla general: **si tu contenedor hace `spawn` de procesos, tu PID 1 tiene
  un trabajo que tu app no está haciendo.** Vale para navegadores, `ffmpeg`,
  `pdftoppm` y cualquier herramienta externa invocada por subproceso.

---

## 9. Bypass de sandbox del navegador: la lógica corre y no sirve

### Por qué

Ubuntu 23.10+ trae `kernel.apparmor_restrict_unprivileged_userns = 1`. Con eso
Chromium no puede armar su sandbox y muere al arrancar, aunque no corras como
root y aunque no estés en un contenedor:

```
FATAL:zygote_host_impl_linux.cc:128] No usable sandbox! If you are running on
Ubuntu 23.10+ or another Linux distro that has disabled unprivileged user
namespaces with AppArmor, see ...
```

Hermes **ya contempla esto**: `tools/browser_tool.py` (~línea 1805) detecta el
caso — root, o `apparmor_restrict_unprivileged_userns == 1` — y exporta
`--no-sandbox` al lanzar `agent-browser`. El problema es que lo exporta por la
variable equivocada:

| Variable | ¿La lee agent-browser 0.26? | Separador |
|---|---|---|
| `AGENT_BROWSER_CHROME_FLAGS` | **no** | — |
| `AGENT_BROWSER_ARGS` | sí | **coma**, no espacio |

Resultado: la detección acierta, el bypass "se aplica", y el navegador sigue
sin arrancar. **El toolset `browser` nativo está roto en este servidor, en
ambas instalaciones** (`~/.hermes`, `~/.hermes-renata`).

### La trampa

Esta falla se presenta con la cara de "el navegador no anda en Docker", y a esa
pregunta cualquiera —persona o agente— responde con seguridad "hay que pasarle
`--no-sandbox`". Que es exactamente lo que el código ya hace. Se puede perder
una tarde discutiendo la solución correcta mientras la solución correcta ya está
escrita y no se está aplicando.

Dos detalles que además despistan:

- El diagnóstico "es por el contenedor" es **falso**: Hermes corre nativo. Es
  AppArmor del host, y aplica igual fuera de Docker.
- Los MCPs con Playwright propio (`renata-meet`, `barchart`, `stonex`) funcionan
  normal — tienen su Chrome dentro del contenedor, con sus propios flags. Que
  "el navegador de Renata sí sirve para Meet" no contradice nada.

### Diagnóstico

No leer el código y concluir: **probar la variable contra el binario real**.

```bash
# ¿AppArmor está restringiendo? (1 = sí)
cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns

# ¿Qué variable lee esta versión del CLI?
~/.hermes/hermes-agent/node_modules/agent-browser/bin/agent-browser-linux-x64 \
  --help | grep -iE 'AGENT_BROWSER_ARGS|CHROME_FLAGS|--args'

# La que sirve arranca Chrome; la que no, repite el FATAL de arriba.
AGENT_BROWSER_ARGS="--no-sandbox,--disable-dev-shm-usage" \
  agent-browser --engine chrome --session t --json navigate '{"url":"https://example.com"}'
```

Un `"error":"CDP error (Page.navigate)..."` en esa última línea es **éxito**:
Chrome arrancó, solo se quejó de la URL. El fallo real es el `No usable sandbox`.

### Cómo (si se decide parchear)

Es código upstream vendorizado, así que el parche es una deuda que se re-aplica
en cada `hermes update`. Antes de tocarlo, considerar reportarlo upstream — el
bug es de ellos, no de la instalación.

Si se parchea, **en todas las instalaciones a la vez**, no en una:

```bash
for H in ~/.hermes ~/.hermes-renata; do
  sed -i 's/"AGENT_BROWSER_CHROME_FLAGS"/"AGENT_BROWSER_ARGS"/g;
          s/"--no-sandbox --disable-dev-shm-usage"/"--no-sandbox,--disable-dev-shm-usage"/' \
      "$H/hermes-agent/tools/browser_tool.py"
done
```

Ojo con el separador: el valor pasa de espacios a **comas**. Cambiar solo el
nombre de la variable deja el bug vivo con otra forma.

El parche **no toma efecto hasta reiniciar el gateway** — el proceso ya importó
el módulo. Y un agente no puede reiniciarse a sí mismo: *él es* el proceso del
gateway. Tiene que hacerlo un humano (o el `reload` por SIGUSR1, que re-lee
config pero no re-importa módulos, así que aquí no sirve).

### Notas

- **Estado en AROCO (2026-08-20): sin parchear, a propósito.** Se revirtió un
  parche que había quedado aplicado en el runtime equivocado — ver patrón #12.
- Alternativa sin tocar código, si algún día se quiere: bajar la restricción del
  host (`sysctl kernel.apparmor_restrict_unprivileged_userns=0`). Es debilitar
  una defensa de todo el servidor para un tool; no vale la pena mientras los
  MCPs cubran los casos de scraping reales.
- La regla general: **cuando la lógica correcta ya existe y el síntoma persiste,
  el sospechoso es el contrato con la dependencia** (nombre de variable, formato
  del valor, versión), no la lógica.

---

## 10. Un agente depurando toca producción — y no necesariamente la suya

### Por qué

El aislamiento entre Hermes en un mismo servidor es **solo `HERMES_HOME`** (ver
patrón #1): config, sesiones y logs separados. Pero los tres procesos corren
como el **mismo usuario Unix** (`aroco`, en `sudo` y `docker`, con sudo sin
contraseña). El toolset `terminal` de cualquier agente alcanza el `$HOME` de los
otros dos y puede escribirlo.

Mientras el agente responde preguntas, esto no se nota. Se nota cuando le pedís
que **arregle algo**: pasa de leer a editar, y no tiene forma estructural de
saber cuál de los tres runtimes es el suyo.

### Caso real (2026-08-20)

Álvaro le pidió a Renata por Signal el precio del cacao a una hora puntual.
Renata solo tenía cierres diarios, intentó sacarlo de Barchart con el navegador,
chocó con el `No usable sandbox` del patrón #11 y pidió permiso para arreglarlo.
Con un "Approve to execute" de por medio:

1. Diagnosticó el bug **bien** — encontró que la variable correcta era
   `AGENT_BROWSER_ARGS` y lo verificó contra el binario.
2. Aplicó el parche a `~/.hermes/hermes-agent/tools/browser_tool.py` — el
   runtime de **Hermes**, no el suyo, que es `~/.hermes-renata/`.
3. Pidió `sudo systemctl restart hermes-gateway` — otra vez el servicio ajeno,
   que no le habría servido de nada.

Diagnóstico correcto, ejecución en el vecino. Y como el gateway de Hermes no se
reinició, quedó un desfase silencioso: **código en disco ≠ código en memoria**,
que es el peor estado en el que dejar producción.

### Diagnóstico

Que un agente reporte "ya lo arreglé" no dice *dónde*. Las instalaciones hermanas
sirven de **copia prístina de referencia** — la que no se tocó dice cómo debería
verse la otra:

```bash
# ¿qué runtimes difieren del resto?
for H in ~/.hermes ~/.hermes-renata; do
  echo -n "$H: "; ls -l "$H/hermes-agent/tools/browser_tool.py" | awk '{print $5, $6, $7, $8}'
done

# diff contra una hermana intacta; vacío = limpio
diff ~/.hermes-renata/hermes-agent/tools/browser_tool.py \
     ~/.hermes/hermes-agent/tools/browser_tool.py
```

El **tamaño y la fecha** delatan el archivo tocado antes de leer una línea. Para
reconstruir qué pasó, el transcript de la sesión tiene las llamadas a `patch` y
`terminal` con sus argumentos:

```bash
ls -t ~/.hermes-renata/sessions/*.jsonl | head -1   # transcript más reciente
# roles: user / assistant (con tool_calls) / tool (con el resultado)
```

### Cómo

- **Verificar la ruta, no la intención.** Antes de aceptar un fix de un agente,
  `ls -l` del archivo que dice haber tocado + `diff` contra una hermana.
- **Revertir es barato con una copia prístina al lado**; el `diff` vacío es la
  prueba de que el revert quedó exacto, mejor que releer el parche.
- **"Approve to execute" no es un cheque en blanco.** Autoriza el siguiente paso
  concreto, no una sesión entera de edición en producción. Si el agente encadena
  diagnóstico → parche → reinicio, cada eslabón merece su propia confirmación.
- **Un agente no puede reiniciar su propio gateway** — es el proceso. Cualquier
  fix suyo que necesite reinicio queda a medias por diseño. Tenerlo en cuenta al
  evaluar si conviene que lo intente él.

### Notas

- Contención real, si algún día se quiere cerrar: **un usuario Unix por agente**,
  o mover los Hermes nativos a contenedores como ya están los MCPs. Ninguna de
  las dos está hecha — decisión pendiente, no hay ticket.
- El `sudo` passwordless amplifica esto: no hay ni siquiera una contraseña como
  punto de fricción antes de un `systemctl restart` del servicio de otro cliente.
- La regla general: **el aislamiento por variable de entorno separa datos, no
  privilegios.** Sirve para que dos agentes no se pisen los archivos; no sirve
  para que uno no pueda tocar los del otro.

---

## 11. Avisar por fuera del agente lo deja fuera de contexto

### Por qué

Mandar un mensaje a un contacto sin pasar por el agente es fácil y tentador —
para Signal es un POST al JSON-RPC de signal-cli:

```bash
curl -s -X POST http://127.0.0.1:8790/api/v1/rpc -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"send",
       "params":{"recipient":["+57…"],"message":"…"}}'
```

Entrega perfecto y **no hay que parar el daemon** (el `signal-cli` de línea de
comandos sí se pelea con él por el lock de la cuenta; la API HTTP no).

El costo aparece después. El mensaje salió por fuera de la sesión del agente,
así que **el agente no sabe que existió**. Cuando el contacto responda "listo,
gracias" o "¿y para el mes pasado?", eso llega a una conversación donde nunca se
dijo nada — y el agente contesta desde ese vacío.

Es un fallo silencioso y a destiempo: el envío se ve exitoso, el problema se
manifiesta horas después y en la cara del contacto, no en la terminal.

### Cómo

Que el turno lo dé el agente: resumir la sesión real de esa conversación y
pedirle que mande él el mensaje con su tool `send_message`.

```bash
scripts/avisar_por_agente.sh ~/.hermes-renata signal +57XXXXXXXXX "El texto."
```

Lo que hace por dentro, si se quiere a mano:

```bash
export HERMES_HOME=~/.hermes-renata
# El session_id NO es estable — se resuelve por clave de canal, no se hardcodea.
SID=$(python3 -c "import json;print(json.load(open('$HERMES_HOME/sessions/sessions.json'))\
['agent:main:signal:dm:+57XXXXXXXXX']['session_id'])")
$HERMES_HOME/hermes-agent/venv/bin/python -m hermes_cli.main chat -Q -r "$SID" \
  -t messaging -q "Mandá este mensaje tal cual con send_message a 'signal:+57XXXXXXXXX': …"
```

### Notas

- **`-t messaging` no es opcional.** Sin el toolset explícito el agente no tiene
  `send_message`, y en vez de fallar te dice que lo mandó. Mismo gotcha que
  `enabled_toolsets` en [cronjobs.md](./cronjobs.md).
- **Usarlo con la conversación quieta.** Si el gateway tiene ese agente cacheado
  y el contacto escribe a la vez, hay dos escritores sobre la misma sesión y
  gana el último. El desalojo por inactividad (~1h) se ve en `gateway.log` como
  `Agent cache idle-TTL evict`.
- `send_message` habla con el mismo daemon JSON-RPC, así que **no** necesita el
  gateway corriendo ni para el daemon.
- El envío a pelo sigue siendo lo correcto para lo que **no** es conversación:
  alertas de cron, watchdogs, health checks. Ahí no hay contexto que preservar.
- La regla general: **un canal de entrega no es un canal de conversación.** Si
  esperás respuesta, el mensaje tiene que existir para quien la va a leer.

---

## 12. Sin saldo en el proveedor, un cron periódico se vuelve un spammer

### Por qué

Un cron que entrega a un canal real (`deliver: signal:+57…,signal:+57…`) reparte
también sus **fallos**: cuando el job revienta, el scheduler manda el texto del
error al mismo destinatario, con la misma cadencia del schedule. Si el fallo es
de infraestructura no se arregla solo, así que cada tick es un mensaje idéntico.

Incidente real (Renata, 2026-08-21): la key de OpenRouter se quedó sin crédito a
las 13:00. El job `Notetaker resumen` (`*/15 6-19 * * *`, deliver a dos números
de Signal) empezó a entregar esto cada 15 minutos a Pablo y a Álvaro:

```
HTTP 402: This request requires more credits, or fewer max_tokens.
You requested up to 64000 tokens, but can only afford 62696.
```

Doce corridas fallidas antes de que alguien lo reportara. El destinatario ve un
error crudo de API, no un aviso — y no hay nada que pueda hacer al respecto.

Dos detalles del modo de fallo que valen más que el incidente:

- **El 402 no es "cuota agotada", es "no alcanza para este pedido".** OpenRouter
  compara el saldo contra el **`max_tokens` solicitado**, no contra lo que la
  llamada va a consumir de verdad. Con 62.696 tokens de saldo y un techo de
  64.000, toda petición se rechaza aunque la respuesta fuese a ocupar 300 tokens.
  Un `max_tokens` ajustado al tamaño real de la tarea habría seguido corriendo.
- **Cae todo el agente, no solo el cron.** El pool de credenciales marca la key
  como agotada (`credential pool: marking OPENROUTER_API_KEY exhausted (status=402),
  rotating` → `no available entries`). Con una sola key no hay rotación posible:
  el agente tampoco contesta a los usuarios ni ejecuta sus otros jobs. En el
  incidente eso significó que el notetaker **dejó de entrar a reuniones** — un
  daño mucho peor que los mensajes, y que nadie notó porque ese job entrega
  `local` y falla en silencio.

### Cómo

Diagnóstico, en orden:

```bash
export HERMES_HOME=~/.hermes-renata
$HERMES_HOME/hermes-agent/venv/bin/python -m hermes_cli.main cron list   # last_status/last_error
grep -c "HTTP 402" $HERMES_HOME/cron/output/<job_id>/*.md                 # desde cuándo
grep "credential pool" $HERMES_HOME/logs/agent.log | tail                 # ¿rotó o se quedó sin keys?
```

Contención inmediata — **pausar, no borrar**:

```bash
… cron pause <job_id>      # reversible; `cron resume` lo devuelve tal cual
```

Recrear un job desde cero pierde `enabled_toolsets`, `deliver` y el histórico de
`repeat.completed`. Pausar es siempre la jugada.

Verificar saldo sin adivinar:

```bash
curl -s -H "Authorization: Bearer $OPENROUTER_API_KEY" https://openrouter.ai/api/v1/key
```

### La marca de agotamiento sobrevive al reinicio

Recargar el saldo **no reactiva al agente del todo**. El pool de credenciales
persiste el estado en `$HERMES_HOME/auth.json`, así que el `exhausted` sigue ahí
después de un `systemctl restart`:

```json
"credential_pool": {"openrouter": [{"label": "OPENROUTER_API_KEY",
  "last_status": "exhausted", "last_status_at": 1787344212.32,
  "last_error_code": 402, ...}]}
```

El desbloqueo es por TTL desde `last_status_at`, según el status que lo causó
(`agent/credential_pool.py`): **401 → 5 min, 429 → 1 h, cualquier otro → 1 h**.
Un 402 cae en el caso general: **una hora de cooldown**, cuente o no con saldo.

Lo confuso es que el agente **parece** recuperado: el camino principal usa la
credencial configurada y responde normal. El que sigue bloqueado es el cliente
auxiliar, que sí resuelve por pool:

```
WARNING agent.auxiliary_client: resolve_provider_client: openrouter requested but
  OpenRouter credential pool has no usable entries (credentials may be exhausted)
WARNING agent.auxiliary_client: Auxiliary auto-detect: no provider available.
  Compression, summarization, and memory flush will not work.
```

Y eso apaga **compresión de contexto, resúmenes, flush de memoria y títulos**.
La compresión es la que duele: sin proveedor auxiliar, una conversación larga
descarta los turnos del medio **sin resumirlos**. El agente contesta con
normalidad y simplemente olvida cosas a mitad de charla, sin marca visible.

Limpiarlo a mano en vez de esperar la hora:

```bash
… auth reset openrouter          # "Reset status on 1 openrouter credentials"
python3 -c "import json;print([e['last_status'] for e in
  json.load(open('$HERMES_HOME/auth.json'))['credential_pool']['openrouter']])"
```

Conviene hacerlo aunque el TTL esté por vencer: si no, queda un `exhausted`
viejo en disco esperando a confundir el próximo arranque.

### Notas

- **Un job de cara al usuario no debería entregar sus excepciones.** Si el canal
  de `deliver` es una persona, el error crudo no le sirve; lo que sirve es un
  aviso redactado. Lo estructural sería auto-pausar tras N fallos consecutivos y
  mandar los errores a un canal de diagnóstico aparte.
- **`max_tokens` alto no es gratis ni inocuo.** Además del gasto, es lo que
  decide si una llamada entra o se rechaza cuando el saldo va bajo. Para crons
  cortos, ajustarlo al tamaño real de la respuesta da margen antes del corte.
- **Un watchdog de saldo vale lo que cuesta.** Una sola alerta al bajar de un
  umbral llega antes que el primer 402, y en un canal donde el aviso tiene
  sentido. Compárese con enterarse por doce copias de un stack trace.
- Las keys de inferencia **no se comparten entre agentes** justamente para que la
  facturación de uno no tumbe al otro
  ([replicar-agente-cliente.md](./replicar-agente-cliente.md)) — el reverso es
  que cada agente es un punto único de fallo por saldo. Sin key de respaldo en el
  pool, no hay rotación que valga.

---

## 13. Una tool detrás de un túnel de Cloudflare tiene 100 segundos

### Por qué

Exponer un MCP con `cloudflared` es cómodo: hostname con TLS, Cloudflare Access
delante, la app externa solo necesita el Service Token. Lo que no aparece en
ninguna parte de esa configuración es que **Cloudflare corta la respuesta del
origin a los 100 segundos** y devuelve un `524`. Para tools normales (una query,
un cálculo) es irrelevante. Para una tool que **abre un navegador y parsea un
PDF**, es el límite real de diseño y hay que medirlo antes de prometer nada.

### Incidente (2026-08-28)

Un CRM externo iba a consumir semanalmente una tool que baja los reportes
tabulares de un portal financiero: navega el feed con Playwright, hace scroll
para alcanzar los reportes periódicos, abre el detalle de dos artículos, descarga
sus PDFs embebidos y los parsea. Funcionaba en las pruebas locales.

Medido contra el MCP real:

| Corrida | Tiempo |
|---------|--------|
| En frío (incluye re-login Okta) | **95,6 s** |
| En caliente (sesión viva) | 43,6 s |

El techo son 100 s. **Margen: 4 segundos.**

### Diagnóstico — por qué "en frío" era el caso normal, no el excepcional

El token de la sesión vive **15 minutos**. La llamada del CRM es **semanal**.
Entre una corrida y la siguiente pasan siete días, así que la sesión *siempre*
está muerta cuando llega la petición: cada corrida real paga el re-login
Playwright. La medición "en caliente" solo se da si acabas de correrla a mano —
es decir, exactamente en las pruebas, y nunca en producción.

Es el mismo sesgo del patrón 5: **probar en el estado equivocado**. Ahí era una
sesión viva que ocultaba que el health check mentía; aquí es una sesión viva que
oculta que la tool no cabe en el timeout.

### Patrón — precalentar, no optimizar

Pelear por bajar de 95 s a 60 s es frágil (el portal ajeno decide cuánto tarda).
La solución es que la petición que cruza el túnel **no haga el trabajo**:

1. **La tool cachea su resultado en disco** (`/data/<algo>/latest.json` + una
   copia fechada, que además deja releer una corrida vieja sin volver a la
   fuente).
2. **Un parámetro `max_age_hours` con default sensato** sirve el caché si es
   reciente. Clave: el default tiene que ser **más corto que la periodicidad del
   dato**. Con dato semanal y default de 12 h, un caché de la semana pasada
   **no** se sirve — vence, y la tool va a la fuente. Nunca se entrega dato viejo
   en silencio.
3. **Un cron del sistema precalienta** poco antes de la hora en que llega la
   petición externa, llamando la tool con `max_age_hours=0`. Ese cron corre en
   `localhost`, donde no hay Cloudflare y los 95 s no molestan.

```bash
# crontab -e  (el cron corre en la TZ del server)
MAILTO=""
# Precarga 20 min antes de que el consumidor externo llame.
40 8 * * 1 /home/<user>/projects/agents/<mcp>/warm_<tool>.sh
```

El script solo llama la tool dentro del contenedor y deja rastro en un log. Ojo
al escribirlo: lleva un heredoc de Python **dentro** de otro heredoc, así que los
delimitadores tienen que ser distintos.

Resultado medido: la llamada del consumidor externo pasó de **95,6 s (a 4 s del
524)** a **0,02 s desde caché**, con dato traído esa misma mañana.

### Gotchas

- **Medí la corrida en frío, no la que acabás de correr.** Si la tool mantiene
  sesión, borrá o dejá vencer el estado antes de cronometrar.
- **El default del caché es una decisión de corrección, no de rendimiento.** Si
  es más largo que la periodicidad del dato, servís dato viejo como si fuera
  nuevo. Más corto: el peor caso es una corrida lenta, que es un fallo visible.
- **Devolvé `cached` y `fetched_at` en la respuesta.** El consumidor tiene que
  poder distinguir "esto lo trajo el precalentamiento de hace 20 minutos" de
  "esto salió del portal recién".
- El `connectTimeout` que se configura en el `config.yml` del túnel es el de
  **conexión**, no el de respuesta. No sube el techo de 100 s.
- Sin Access delante, el origin responde `406` en `/mcp`; **con** Access, una
  petición sin token da `403` con cabeceras `cf-access-domain` y `cf-access-aud`.
  Sirve para verificar de un vistazo en qué estado está el hostname.

---

## 14. Dos agentes, una cuenta externa: compartir el MCP, no duplicarlo

### Por qué

Cuando dos agentes en el mismo servidor necesitan el mismo servicio externo, el
reflejo es darle a cada uno su MCP, con su volumen `/data` aislado — que es lo
correcto para las **credenciales de inferencia** y para el estado propio de cada
agente ([replicar-agente-cliente.md](./replicar-agente-cliente.md)). Pero si los
dos MCPs se autentican contra **la misma cuenta del proveedor externo**, el
aislamiento deja de proteger y empieza a costar:

- **Dos sesiones sobre una cuenta se pisan.** Dos Chromium haciendo login con el
  mismo usuario se sobrescriben las cookies; el segundo invalida al primero.
- **Dos copias del scraper.** El día que el proveedor cambie el formato de su PDF
  o el markup de su portal, hay que arreglarlo dos veces — y la segunda se
  olvida.
- **Dos sesiones que mantener vivas.** Caso real: de los dos MCPs que scrapeaban
  el mismo portal, el de un agente tenía el `storage_state` reescrito ese mismo
  día y el del otro llevaba **24 días** sin renovarse. Sesión zombi que nadie
  miraba, esperando a fallar en el peor momento.

### Patrón

Un solo contenedor por **cuenta externa**, declarado en la config de cada agente
que lo necesite, **sin el prefijo del agente** en el nombre — la convención avisa
de que no es suyo:

```yaml
mcp_servers:
  agenteB-drive:
    url: http://localhost:8784/mcp     # propio del agente

  # Compartido con el Hermes principal (sin prefijo, a propósito).
  # Misma cuenta del proveedor: dos containers haciendo login contra ella
  # es una sesión de más, y dos copias del scraper que mantener.
  stonex:
    url: http://localhost:8770/mcp
```

Tras editar la config: **`sudo systemctl restart hermes-<agente>-gateway`**, no
`reload` (ver `comandos.md`: el SIGUSR1 sale con código 75 y cae en el backoff de
la unit — 60 s la primera vez, y subiendo). Verificar el descubrimiento:

```bash
HERMES_HOME=/home/<user>/.hermes-<agente> \
  /home/<user>/.hermes-<agente>/hermes-agent/venv/bin/python -m hermes_cli.main mcp list

grep -a "MCP server 'stonex'" /home/<user>/.hermes-<agente>/logs/agent.log | tail -1
```

Las tools quedan como `mcp_<servidor>_<tool>` — o sea `mcp_stonex_get_cocoa_tables`,
sin rastro de qué agente es. Hay que actualizar los skills que las llamaban por el
nombre viejo: el skill no falla ruidosamente si el nombre ya no existe.

### El precio: es todo o nada

**La config no soporta allowlist de tools por servidor MCP.** Compartir el
servidor le da al segundo agente **todas** sus tools. En el caso real, compartir
el MCP del bróker para que un agente pudiera leer dos reportes de mercado le dio
también `get_account_summary`, `get_positions` y `download_daily_statement` — o
sea, la cuenta de corretaje entera.

Vale la pena decirlo en voz alta antes de compartir, sobre todo si el segundo
agente habla con gente de afuera (reuniones, mensajería). Las opciones reales
son:

| Opción | Cuesta |
|--------|--------|
| Compartir el MCP entero | El segundo agente ve tools que no necesita |
| Un MCP aparte que exponga solo esa tool | Otro contenedor, otro puerto, otra sesión — vuelve el problema original |
| Duplicar el código | Dos sesiones y dos copias que mantener |

No hay opción limpia; hay que elegir a sabiendas. Y recordar que el aislamiento
entre agentes es **de datos, no de privilegios**: todos corren como el mismo
usuario del sistema (patrón 10), así que compartir un MCP no abre una puerta que
estuviera realmente cerrada — solo la hace cómoda de cruzar.

### Señal de que te toca este patrón

Preguntate: **¿los dos MCPs se loguean con el mismo usuario del proveedor?** Si
la respuesta es sí, ya tenés dos sesiones compitiendo, lo sepas o no. Si es no
(cada agente tiene su propia cuenta), duplicar está bien y compartir sería el
error.
