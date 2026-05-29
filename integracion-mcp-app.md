# Conectar una app a los MCPs de AROCO: Barchart, StoneX e Inventario físico

Guía para darle **funciones agénticas** a una app nueva consumiendo los tres MCP
servers que ya corren en el servidor de AROCO. Hermes los usa hoy; una app nueva
puede hablarles exactamente igual.

> Los identificadores sensibles (número de cuenta StoneX, Sheet ID, credenciales,
> host interno) están como placeholders `<ASÍ>`. Los valores reales viven solo en
> el servidor: `~/projects/agents/<mcp>/docker-compose.yml` y `~/.env`.

---

## 0. Lo que tienen en común

| MCP | Puerto | Endpoint | Transporte | Contenedor | Propósito |
|-----|--------|----------|------------|------------|-----------|
| Barchart | 8769 | `http://localhost:8769/mcp` | `streamable-http` | `barchart-mcp` | Cadena de opciones de futuros (cacao, etc.) |
| StoneX | 8770 | `http://localhost:8770/mcp` | `streamable-http` | `stonex-mcp` | Cuenta del bróker + Market Intelligence |
| Inventario | 8771 | `http://localhost:8771/mcp` | `streamable-http` | `inventory-mcp` | Google Sheet de inventario físico de cacao |

**Protocolo:** los tres son servidores **MCP** (Model Context Protocol) sobre
HTTP *streamable* (`fastmcp`). El endpoint es siempre la ruta `/mcp`.

**Seguridad de red — IMPORTANTE:** los tres bindean a **`127.0.0.1`** (solo
localhost). No hay autenticación en el endpoint MCP en sí: la frontera de
seguridad es que solo se accede desde la propia máquina. Por eso:

- Si la app corre **en el mismo servidor** → conéctate directo a `http://localhost:87XX/mcp`.
- Si corre **en otra máquina** → NO expongas estos puertos a internet tal cual.
  Usa un túnel SSH (`ssh -L 8770:localhost:8770 <servidor>`) o un reverse
  proxy con auth (nginx + token/mTLS).

### Cómo hablarle a un MCP desde la app

Dos caminos:

**A) Cliente MCP (recomendado).** Cualquier SDK MCP (Python `fastmcp`/`mcp`,
TypeScript `@modelcontextprotocol/sdk`, etc.) sabe hacer el handshake, listar
tools y llamarlas con validación de schema.

```python
# Python — pip install fastmcp
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://localhost:8771/mcp") as c:
        tools = await c.list_tools()                 # descubre las tools
        res = await c.call_tool("read_inventory", {"limit": 5})
        print(res.data)

asyncio.run(main())
```

```typescript
// TypeScript — npm i @modelcontextprotocol/sdk
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const client = new Client({ name: "aroco-app", version: "1.0.0" });
await client.connect(new StreamableHTTPClientTransport(new URL("http://localhost:8770/mcp")));
const result = await client.callTool({ name: "get_positions", arguments: {} });
```

**B) Si la app es un agente LLM** (Claude, etc.): pásale estos servidores como
MCP servers en su config y el modelo llama las tools solo. Mismo endpoint/transport.

---

## 1. Barchart MCP — cadena de opciones de futuros (puerto 8769)

Scrapea `barchart.com` con una sesión de navegador persistente (Playwright
headless dentro del contenedor) y captura el JSON del proxy `core-api` que sirve
la cadena de opciones.

### Tools

| Tool | Args | Devuelve |
|------|------|----------|
| `check_session()` | — | `{ok, logged_in_as, final_url}` — valida que la sesión siga logueada |
| `list_expirations(symbol="CC*0")` | `symbol` | fechas de expiración disponibles |
| `get_options_chain(symbol="CC*0", expiration="")` | `symbol`, `expiration` | cadena de opciones como JSON (strike, bid/ask, last, volume, OI, IV, calls+puts) |
| `download_csv(symbol="CC*0", expiration="")` | `symbol`, `expiration` | descarga el CSV nativo a `/data/csvs/`, devuelve `{ok, path, filename, size_bytes}` |
| `list_csvs(limit=50)` | `limit` | CSVs ya descargados |

**Símbolos:** son símbolos de Barchart. `CC*0` = cacao nearest, `CCK26` = cacao
May 2026, `CL*0` = crudo nearest, etc.

**Resource:** el contenido de un CSV se lee vía resource MCP
`file:///data/csvs/<filename>` (la ruta sale de `download_csv`/`list_csvs`).

### Autenticación (lo que la app NO maneja, pero conviene saber)

- La sesión vive en `storage_state.json` (cookies de Barchart, login con Google),
  montado en el contenedor vía un volumen `data/barchart:/data`.
- **La sesión puede vencer.** Cuando `check_session()` devuelve `ok: false`,
  hay que regenerarla: correr `setup_login.py` en una máquina con navegador,
  loguearse en Barchart, y copiar el `storage_state.json` resultante al volumen.
- La app no toca esto: solo debería llamar `check_session()` y, si falla,
  reportar "sesión Barchart vencida, requiere re-login manual".

### Latencia

Cada llamada lanza un navegador headless y navega la página real (~5–15 s).
No es hot-path: cachéalo en la app si vas a consultarlo seguido.

---

## 2. StoneX MCP — cuenta + Market Intelligence (puerto 8770)

Habla con las APIs internas de StoneX usando un token OAuth/Okta que el server
gestiona y refresca solo. La cuenta del bróker se configura por env var
(`account_id` default en las tools de statement).

### Tools — cuenta

| Tool | Args | Devuelve |
|------|------|----------|
| `ping()` | — | `"pong"` (sanity check) |
| `get_account_summary()` | — | balance EOD + balance overview en tiempo real (dos endpoints, suelen diferir) |
| `get_positions()` | — | posiciones agregadas **por producto** (ej. "Cocoa": OTE/NLV/asset class) |
| `get_positions_detail(lookback_days=90)` | `lookback_days` | posiciones con detalle por contrato (cantidad, dirección long/short, vencimiento, precio promedio) |
| `download_daily_statement(date_str=None, account_id=<cuenta>)` | fecha `YYYY-MM-DD` (default ayer) | genera/baja el Daily Statement PDF, devuelve `{path, filename, bytes, date}` |
| `extract_statement_data(pdf_path, include_raw_text=False)` | ruta al PDF | datos estructurados: balances, P&L por moneda con MTD/YTD |
| `download_and_extract_daily(date_str=None, ...)` | — | las dos anteriores en una sola llamada |

### Tools — Market Intelligence (intel.stonex.com)

| Tool | Args | Devuelve |
|------|------|----------|
| `list_intel_articles(market_id=16974, page_size=20, only_primary=False)` | `market_id` (16974=Cocoa) | lista de artículos (title, abstract, fecha, autor, url) |
| `get_intel_article(article_id)` | UUID | contenido completo del artículo |
| `get_latest_cocoa_intel(limit=3, only_primary=True)` | `limit` 1-5 | los N artículos de cacao más recientes **con** su contenido |

### Autenticación (gestionada por el server)

- Credenciales en `~/.env` (`STONEX_USER` / `STONEX_PASSWORD`), inyectadas al
  contenedor vía `env_file`.
- El server mantiene `access_token` + `refresh_token` en `/data/auth_state.json`
  (volumen `data/stonex`). Estrategia automática: token vivo → úsalo;
  vencido → refresh con Okta; refresh muerto → re-login Playwright con
  usuario/clave. **El refresh_token rota en cada uso** y se persiste.
- La app **no maneja tokens**: solo llama las tools. Si todo el flujo de auth
  falla (clave cambió, MFA nuevo), las tools devuelven error y hay que revisar
  credenciales/sesión en el server.

---

## 3. Inventario físico MCP — Google Sheet (puerto 8771)

Lee y escribe una Google Sheet de inventario de cacao de AROCO vía
**service account** (no OAuth de usuario). Esta es la pieza de "inventarios físicos".

- **Sheet ID:** `<INVENTORY_SHEET_ID>` (env var en `docker-compose.yml`).
- **Hoja default (gid):** `<INVENTORY_DEFAULT_GID>` → hoja "Inventario de disponobilidad" (sí, con el typo).

### Tools

| Tool | Args | Devuelve |
|------|------|----------|
| `ping()` | — | `"pong"` |
| `get_sheet_info()` | — | metadata: título, URL, y por hoja: gid, nombre, filas, cols, `header_row` detectado, headers |
| `read_inventory(worksheet_name=None, limit=None, header_row=None)` | — | filas como lista de dicts `{header: valor}` |
| `query_inventory(filter_column, filter_value, worksheet_name=None, columns=None, case_sensitive=False, header_row=None)` | filtra `columna == valor` | filas que matchean |
| `append_row(values, worksheet_name=None, header_row=None)` | `values={header: valor}` | agrega fila al final |
| `update_cell(row, column, value, worksheet_name=None, header_row=None)` | `column` = nombre de header o letra ("A") | actualiza una celda, devuelve `{cell, old_value, new_value}` |

**Detección de headers:** autodetecta la fila de headers (maneja hojas con
título/metadata arriba y headers jerárquicos de dos filas que mergea, p.ej.
`Corriente` + `B` → `Corriente B`). Puedes forzar `header_row` si hace falta.

### Autenticación y escritura

- **Service account JSON** en `/data/service_account.json` (montado read-only desde
  el volumen `data/sheets`). Scope: `…/auth/spreadsheets` (lectura **y escritura**).
- **OJO — escritura real:** `append_row` y `update_cell` **modifican la hoja de
  producción**. Para la app, si es solo lectura, expón únicamente
  `get_sheet_info` / `read_inventory` / `query_inventory` y bloquea las de escritura,
  o ponelas detrás de una confirmación explícita.
- El service account debe tener compartida la hoja (como editor para escribir).
  Si agregás otra spreadsheet, hay que compartirla con el email del service account.

---

## 4. Checklist para integrar en una app

1. **Red:** ¿la app corre en el mismo server? Si no, montá túnel SSH o proxy con auth
   para los puertos 8769/8770/8771 (no los abras a internet sin auth).
2. **Cliente MCP:** usá un SDK MCP (Python `fastmcp` o TS `@modelcontextprotocol/sdk`),
   apuntá a `http://<host>:87XX/mcp`, transporte `streamable-http`.
3. **Descubrimiento:** llamá `list_tools()` al conectar para ver firmas exactas
   (esta tabla puede quedar desactualizada; el server es la fuente de verdad).
4. **Salud:** `ping()` en StoneX/Inventario y `check_session()` en Barchart
   como health-checks antes de operaciones pesadas.
5. **Errores de sesión:** Barchart y StoneX dependen de sesiones que pueden vencer.
   Manejá el caso "sesión vencida" en la app y avisá; el re-login es manual/server-side.
6. **Escritura:** en Inventario, tratá `append_row`/`update_cell` con cuidado
   (tocan producción).

---

## 5. Operación de los contenedores (referencia)

```bash
# Estado
docker ps --filter name=barchart-mcp --filter name=stonex-mcp --filter name=inventory-mcp

# Logs
docker logs -f stonex-mcp

# Reiniciar uno
cd ~/projects/agents/inventory-mcp && docker compose restart
```

- Código fuente: `~/projects/agents/{barchart,stonex,inventory}-mcp/server.py`
- Datos/sesiones: `~/projects/data/{barchart,stonex,sheets}/` (sensibles, no a git).
- Registro en Hermes: `~/.hermes/config.yaml` → `mcp_servers:`.
