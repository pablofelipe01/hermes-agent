# Renata — informes de Barchart / StoneX a Google Sheets

Skill + tool para que **Renata** (segundo agente AROCO, instancia Hermes nativa en
`~/.hermes-renata/`, ver [replicar-agente-cliente.md](./replicar-agente-cliente.md))
arme un **informe de mercado como documento de Google Sheets** a pedido, desde
dos fuentes: la cadena de opciones de cacao de **Barchart** y los reportes de
inteligencia de cacao de **StoneX**.

> Identificadores sensibles (spreadsheet IDs, emails personales) van como
> placeholders `<ASÍ>`. Los valores reales viven solo en el servidor:
> `~/projects/agents/renata-drive-mcp/`, `~/projects/data/google/` y las hojas en
> el Drive de `renata@aroco.co`.

## Qué resuelve

Pedido de Pablo: *"cuando le pida un informe de Barchart o de StoneX, que me lo
cree un documento en Google Sheets"*. Renata lo hace desde Telegram:

- *"Pasame el informe de opciones de cacao a una hoja"* → rama **Barchart**.
- *"Armá los reportes de StoneX de cacao en una planilla"* → rama **StoneX**.

Resultado: una hoja de Google (fija por fuente, pestaña nueva por pedido),
compartida con Pablo y Álvaro, **link por correo** a ambos y **confirmación por
Telegram**.

## Arquitectura — una tool genérica + un skill orquestador

La plomería de escribir a Sheets **no** vive en el skill: vive en una tool
genérica del MCP de Drive, para que sea reutilizable por cualquier futuro
informe. El skill solo baja los datos de la fuente y arma las matrices.

| Pieza | Qué hace | Dónde | Estado |
|-------|----------|-------|--------|
| `write_sheet_tabs` | Tool genérica: crea/reusa una Sheet por nombre y le agrega pestañas | `renata-drive-mcp` (:8784) | ✅ |
| `informe-a-sheets` | Skill que enruta Barchart/StoneX → arma tablas → llama la tool → correo + Telegram | `~/.hermes-renata/skills/finance/` | ✅ |

Decisiones tomadas con Pablo (2026-07-29):
- **StoneX** (narrativo) → pestaña *Índice* + una pestaña por reporte con el texto.
- **Hoja fija por fuente**, pestaña nueva por pedido (preserva historial — mismo
  patrón que [renata-cacao-precios.md](./renata-cacao-precios.md)).
- **Entrega:** Telegram **y** correo a Pablo + Álvaro.

---

## La tool `write_sheet_tabs` (en `renata-drive-mcp`)

Genérica, no asume ninguna fuente de datos — cualquier skill le pasa tablas:

```python
write_sheet_tabs(
    spreadsheet_title: str,                 # p.ej. "AROCO — Opciones Cacao (Barchart)"
    tabs: list[{"name": str, "values": list[list]}],
    share_with: list[str] | None = None,    # solo aplica al CREAR la hoja
    folder_id: str | None = None,           # solo al crear
) -> {"ok", "spreadsheet_id", "url", "created", "tabs": [{"name","rows","gid","url"}]}
```

Comportamiento:

- **Busca-o-crea** la hoja por nombre (idempotente, `'me' in owners`). Comparte
  con `share_with` y la mueve a `folder_id` **solo la primera vez** que la crea.
- **Agrega pestañas nuevas** con nombre único (`#2`, `#3`… si colisiona). **Nunca
  sobrescribe** — es el patrón "hoja fija, pestaña por pedido".
- Recorta cada celda de texto a **49.000 chars** (`CELL_MAX`; límite duro de
  Sheets = 50.000/celda). Para textos largos, el skill parte en varias filas.

### Truco de auth (reusado de renata-cacao-mcp)

`renata-drive-mcp` ya se autentica con el token OAuth de Drive de Renata
(`/data/token_renata_drive.json`, scope `drive`). **El scope `drive` también
autoriza la Sheets API**, así que no hace falta otro consentimiento: se agrega un
helper `_sheets()` que construye `build("sheets", "v4", ...)` con las mismas
credenciales. **Sin dependencias nuevas** (`google-api-python-client` ya estaba).
El mount `/data:ro` no molesta porque el refresh OAuth ocurre en memoria (no
reescribe el token).

---

## Rama A — Barchart (cadena de opciones de cacao)

Hoja fija: `AROCO — Opciones Cacao (Barchart)`. Reusa el fetcher del skill
hermano [renata-opciones-cacao-correo] (`fetch_options.py`, corre **dentro** del
container `barchart-mcp` vía `docker cp`/`exec`).

1. `check_session()` — si la sesión es anónima/free → **avisa por Telegram y no
   sigue** (la cadena que devuelve la cuota anónima "parece" válida; por eso se
   mira `plan`, no solo que haya datos).
2. Resuelve el front-month (`list_expirations("CC*0")`; fallback a símbolos
   conocidos `CCU/CCZ/CCH/CCK<YY>`).
3. Baja la cadena con el script del container → CSV + resumen JSON.
4. Arma **una pestaña** `Opciones <SYM> <fecha>`: bloque de resumen (strikes,
   OI/Vol por lado, put/call ratios, strikes de mayor OI/Vol) + la cadena completa.
5. `write_sheet_tabs(...)`.

> **Dependencia frágil:** la sesión Barchart Plus (`renata@aroco.co`, login Google)
> guardada en `~/projects/data/barchart/storage_state.json` **caduca**. No se
> puede re-loguear headless. Renovación por cookies (Cookie-Editor) o
> `setup_login.py` — ver el patrón #7 de
> [patrones-operacionales.md](./patrones-operacionales.md).

## Rama B — StoneX (reportes de inteligencia de cacao)

Hoja fija: `AROCO — Informes StoneX (Cacao)`. Fuente: MCP `renata-intel`
(`get_cocoa_report_pack` / `get_named_reports` / `latest_reports`). Los reportes
son **narrativos** (Morning Commentary, Ratios, Differentials, Weekly, COT…), así
que se vuelcan como:

- **Pestaña `Índice <fecha>`** — una fila por reporte: *Reporte · Fecha · Resumen*.
- **Una pestaña por reporte** `<título corto> <fecha>` — el texto **partido en
  filas** (un párrafo por celda, ≤49k chars). El texto pasa por el LLM para armar
  las filas; es el costo de dejar narrativa en una hoja.

---

## Entrega y compartir

- La hoja se comparte con Pablo (`<PABLO_EMAIL>`, cuenta externa) y Álvaro
  (`<ALVARO_EMAIL>`, @aroco.co) **solo la primera vez** que se crea. A las cuentas
  externas hay que mandarles `sendNotificationEmail=True`, si no falla silencioso;
  el acceso de invitado externo **re-verifica cada 7 días**.
- **Correo** (vía `renata-gmail`): compacto — resumen + link a la hoja, **no** las
  ~400 filas (ya están en la Sheet). Destinatarios fijos: Pablo + Álvaro.
- **Telegram:** confirmación con el link directo a la pestaña.

## Desambiguación vs. otros skills

- Informe de opciones **por correo** (sin hoja) → skill `barchart-options-chain`
  (manda la tabla como correo HTML). Ver [renata-opciones-cacao-correo].
- **Precios históricos** de cacao (CC=F) a un sheet → skill `precios-cacao`
  ([renata-cacao-precios.md](./renata-cacao-precios.md)).
- Este skill es específicamente para **informe de Barchart o StoneX cuyo destino
  es una Google Sheet.**

## Cómo se probó

La tool se validó **en aislado** antes del rebuild del container vivo: se
construyó la imagen (`docker compose build`) y se corrió un one-off
(`docker compose run --rm --no-deps`) con el cliente in-memory de FastMCP
(`Client(server.mcp)`) — sin abrir puerto ni tocar el `renata-drive-mcp` de
:8784. Se verificó: tool registrada, crea la hoja con las pestañas y valores, y en
una segunda llamada **reusa la misma hoja** (`created:false`) y **dedup** la
pestaña (`… #2`). La hoja de prueba se borró. Luego `docker compose up -d` (rebuild
del container vivo) + `systemctl reload hermes-renata-gateway` (re-descubre la tool
e indexa el skill).

## Extensión futura

`write_sheet_tabs` es genérica: cualquier informe nuevo que produzca tablas puede
volcarse a Sheets pasándole `spreadsheet_title` + `tabs`, sin tocar el MCP.
