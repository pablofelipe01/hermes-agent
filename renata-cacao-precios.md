# Renata — precios de cacao (ICE CC=F) a Google Sheets

MCP para que **Renata** (segundo agente AROCO, instancia Hermes nativa en
`~/.hermes-renata/`, ver [replicar-agente-cliente.md](./replicar-agente-cliente.md))
entregue precios de cacao de cualquier rango, tanto en el chat como exportados a
una hoja de Google.

> Identificadores sensibles (spreadsheet ID, emails personales) van como
> placeholders `<ASÍ>`. Los valores reales viven solo en el servidor:
> `~/projects/agents/renata-cacao-mcp/` y `~/projects/data/google/`.

## Qué resuelve

Pedido de Pablo: *"poder pedir precios de cacao de cuando queramos (últimos 3
años, desde agosto de 2025 a hoy, etc.) y que me los envíes a un sheet de
Google"*. Renata lo hace desde Telegram en lenguaje natural:

- *"Mandame al sheet los precios de cacao de los últimos 3 años"*
- *"Precios de cacao desde agosto de 2025 a hoy en el sheet"*
- *"¿A cuánto cerró el cacao ayer?"* → responde el número en el chat.

## Arquitectura (por qué es un MCP y no solo un skill)

3 años de datos diarios son ~750 filas. Si el agente tuviera que leer esas filas
y después escribirlas, pasarían todas por el contexto del LLM (caro y frágil).
En cambio el MCP **descarga y escribe server-side**: el agente solo pasa el
rango y recibe el link + un resumen. El trabajo pesado vive en el contenedor;
el turno del agente es corto.

| Pieza | Qué hace | Puerto | Estado |
|-------|----------|--------|--------|
| `renata-cacao-mcp` | Baja CC=F de Yahoo y exporta a Google Sheets | 8785 | ✅ |

Decisiones tomadas con Pablo: **una spreadsheet fija con una pestaña nueva por
consulta** (preserva historial) y columnas **Fecha · Cierre · Var % diaria**.

---

## Fuente de datos — Yahoo Finance CC=F

Endpoint público (sin auth):

```
https://query1.finance.yahoo.com/v8/finance/chart/CC=F?period1=<epoch>&period2=<epoch>&interval=1d
```

- `CC=F` = futuro de cacao en **ICE Futures**, cotizado en **USD/tonelada**.
- `period1`/`period2` en epoch; `interval` diario (`1d`/`1wk`/`1mo`) o intradía
  (`1m`, `2m`, `5m`, `15m`, `30m`, `60m`, `90m`).
- Referencia: pico histórico ~USD 12.565 (2024), mínimo 3y ~USD 2.798.

### Intradía (agregado 2026-08-20)

Álvaro preguntó por Signal *"¿a cuánto estaba el martes a las 8:00 am hora
colombiana?"* y Renata solo tenía cierres diarios. Intentó resolverlo por
Barchart y no pudo — `barchart-mcp` expone **solo la cadena de opciones**, no
precios históricos — y de ahí derivó al navegador nativo, que está roto (ver
[patrón #11](./patrones-operacionales.md#11-bypass-de-sandbox-del-navegador-la-lógica-corre-y-no-sirve)).
La respuesta correcta no era arreglar el navegador: era que la fuente que ya
funcionaba entregara la resolución que faltaba.

Tres decisiones que importan más que el `interval` nuevo:

- **Hora de Bogotá, no UTC.** Las velas intradía se etiquetan
  `2026-08-18 08:00` en hora colombiana, y el rango se ancla a medianoche de
  Bogotá: *"el martes"* significa el día calendario de acá. Los intervals
  diarios conservan el anclaje y el formato UTC de siempre — el cambio no toca
  ese camino. La pregunta viene en hora local; la respuesta también.
- **La ventana de histórico intradía de Yahoo es corta y no está documentada.**
  Medida contra CC=F: `1m` ~30 días atrás, `5m`/`15m`/`30m`/`90m` ~60 días,
  `60m` ~730 días. Más viejo devuelve **HTTP 422** crudo. El server valida
  antes de pedir y devuelve un error que le dice al agente qué hacer ("usá un
  interval más grueso"), en vez de un stacktrace que el modelo interpreta como
  "no tengo acceso".
- **`max_rows` pasó a automático**: 30 en diario, 120 en intradía. El default
  fijo de 30 truncaba a las velas más recientes y se comía media sesión — justo
  las de la mañana, que eran las que se estaban pidiendo. Un default pensado
  para una resolución silencia datos en otra.

La sesión de cacao en ICE NY corre ~03:45–12:30 hora Bogotá; fuera de eso las
velas vienen con cierre `null` y se descartan.

## Auth a Sheets — el truco clave (sin service account)

El MCP **no** usa una service account propia. Reusa el token OAuth de Drive de
Renata (`/data/token_renata_drive.json`, ver
[renata-notetaker-reuniones.md](./renata-notetaker-reuniones.md) Fase 4), porque:

> El scope `https://www.googleapis.com/auth/drive` **también autoriza la Google
> Sheets API**. Con ese único token, Renata crea la spreadsheet y escribe las
> celdas con su propia identidad (`renata@aroco.co`) — sin nuevo consentimiento
> OAuth ni una service account extra que administrar.

El volumen se monta `:ro`; el refresh de OAuth ocurre en memoria y no reescribe
el archivo (mismo patrón que `renata-drive-mcp`).

## Estrategia de la hoja

Una spreadsheet fija **"AROCO — Precios Cacao (CC=F)"** (id `<SPREADSHEET_ID>`):

- Se **busca-o-crea por nombre** la primera vez (idempotente) y se comparte como
  *writer* con `<email-pablo>` (externo → `sendNotificationEmail=True`, si no,
  falla silencioso) y `alvaro.acosta@aroco.co`.
- Cada consulta agrega una **pestaña nueva** `"<start>_a_<end> (<interval>)"`
  (con dedup ` #2`, ` #3`… si colisiona). Primera pestaña `Inicio` con una nota.
- Columnas: **Fecha | Cierre (USD/t) | Var % diaria**.

## Estructura

```
~/projects/agents/renata-cacao-mcp/
├── server.py            # FastMCP streamable-http, Yahoo + Sheets/Drive API
├── Dockerfile           # python:3.12-slim, MCP_PORT=8785
├── docker-compose.yml   # 127.0.0.1:8785:8785, monta /data/google :ro
├── requirements.txt     # fastmcp, google-auth, google-api-python-client
└── README.md
```

Variables (docker-compose):

```yaml
GOOGLE_TOKEN_PATH: /data/token_renata_drive.json   # reusa token de Drive
CACAO_SHEET_TITLE: "AROCO — Precios Cacao (CC=F)"
CACAO_SHARE_WITH:  "<email-pablo>,alvaro.acosta@aroco.co"
```

## Tools (`mcp_renata_cacao_*`)

| Tool | Uso |
|------|-----|
| `ping()` | Health-check → `pong`. |
| `get_cocoa_prices(start_date, end_date?, interval="1d", max_rows=None)` | Datos + resumen (min/max/first/last/change%) **en el chat**. Para rangos chicos / preguntas puntuales. Acepta intradía; `max_rows=None` → 30 diario / 120 intradía. |
| `export_cocoa_prices_to_sheet(start_date, end_date?, interval="1d")` | **Escribe server-side** a una pestaña nueva y devuelve `{url, tab, rows, summary}`. Para rangos grandes. |

Fechas en `YYYY-MM-DD`; `end_date` default = hoy.

## Comando `/precios-cacao` (skill orquestador)

Encima del MCP hay un skill que hace el comando usable en lenguaje natural, al
estilo de los skills `finance` de AROCO (`/analisis-general`, `/cobertura`):

```
~/.hermes-renata/skills/finance/precios-cacao/SKILL.md
```

Qué hace el skill:

- **Interpreta el rango** relativo a hoy: "últimos 3 años", "desde agosto de 2025
  a hoy", "este mes", o fechas explícitas → calcula `start_date`/`end_date`.
- **Regla dura de decisión:** si lo invocan con el comando y hay un rango →
  **SIEMPRE `export_cocoa_prices_to_sheet`** y devuelve el link. El chat-only
  (`get_cocoa_prices`) queda reservado para preguntas de un precio puntual
  ("¿a cuánto cerró ayer?"). Esto evita que el agente muestre la tabla en el chat
  en vez de exportar.
- Devuelve un mensaje Telegram con resumen (min/max, primero/último, variación) +
  link a la pestaña.

Uso:

- `/precios-cacao últimos 3 años` · `/precios-cacao desde agosto 2025 a hoy` → al sheet.
- *"¿a cuánto cerró el cacao ayer?"* → responde el número en el chat.

> **Gotcha de testing:** en `hermes chat -q` el skill **no** se auto-inyecta —
> hay que precargarlo con `-s precios-cacao`. En la gateway (Telegram) se carga
> solo por el slash. Tras agregar un skill nuevo, `sudo systemctl reload
> hermes-renata-gateway` para que la gateway lo indexe.

## Deploy

```bash
cd ~/projects/agents/renata-cacao-mcp
docker compose up -d --build
```

Cablear en `~/.hermes-renata/config.yaml` bajo `mcp_servers:`:

```yaml
  renata-cacao:
    url: http://localhost:8785/mcp
```

```bash
sudo systemctl reload hermes-renata-gateway   # SIGUSR1, re-lee config sin reiniciar
```

Verificar: `HERMES_HOME=~/.hermes-renata … hermes mcp list` debe mostrar
`renata-cacao ✓ enabled`. Para cronjobs, el toolset es `mcp-renata-cacao`.

## Prueba end-to-end (2026-07-02)

- `export_cocoa_prices_to_sheet` con 3 años diario → **755 filas**; agosto-2025→hoy
  → **231 filas**; pestañas únicas por rango; compartir OK.
- Turno real de Renata vía `hermes chat -t mcp-renata-cacao -q "…"`: llamó
  `mcp_renata_cacao_get_cocoa_prices` y devolvió el cierre del día correcto.
- Skill `/precios-cacao` (con `-s precios-cacao`): `/precios-cacao últimos 6 meses`
  → exportó 125 filas a una pestaña y devolvió link + resumen. ✅

## Prueba del intradía (2026-08-20)

- Regresión del camino diario primero: mismo rango, mismos encabezados y las
  mismas filas exactas que antes del cambio (`5905.0` / `6046.0` / `+2.39%`).
- `60m` sobre 18–19 ago → 20 velas, etiquetadas en hora de Bogotá, todas dentro
  del día calendario colombiano.
- Validaciones: `1m` a 227 días atrás y `5m` a 200 días → `ValueError` en
  español, no HTTP 422; interval inválido → lista de los aceptados.
- Turno real de Renata vía CLI con la pregunta textual de Álvaro: eligió
  `interval="60m"` sola y respondió **martes 18-ago 08:00 = USD 5.933/t**,
  **miércoles 19-ago 08:00 = USD 6.005/t**. ✅

⚠️ El gateway cachea los esquemas de tools al arrancar. Tras cambiar la **firma**
o la **descripción** de un tool no basta con recrear el contenedor: hay que
`restart` del gateway (el `reload`/SIGUSR1 re-lee config, no re-registra tools).
Si no, el agente sigue creyendo la descripción vieja y no usa lo nuevo.

Este MCP sigue el patrón general de [ejemplos.md](./ejemplos.md) e
[integracion-mcp-app.md](./integracion-mcp-app.md).
