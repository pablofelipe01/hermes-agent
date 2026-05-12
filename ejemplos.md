# Ejemplos — Extender Hermes con nuevas capacidades (MCP)

Hermes se extiende añadiendo **MCP servers** que exponen tools propias del cliente
(integrar APIs internas, conectores SaaS, datasets, etc.). Este documento es la
plantilla mínima para crear uno nuevo cuando despliegues Hermes en un cliente.

---

## Patrón general

Cada nueva capacidad vive en su propio contenedor Docker, expone tools sobre
HTTP en `127.0.0.1` y se registra en `~/.hermes/config.yaml`. Hermes lo
descubre al arranque y las tools quedan disponibles para el modelo en el
Telegram (o cualquier gateway que tengas configurado).

```
┌─────────────────┐      ┌──────────────────────┐
│  Hermes nativo  │ HTTP │  contenedor MCP      │
│  (systemd)      │ ◄───►│  127.0.0.1:876X/mcp  │
│                 │      │  FastMCP + tools     │
└─────────────────┘      └──────────────────────┘
        ▲
        │ Telegram
        │
   ┌──────────┐
   │ Operador │
   └──────────┘
```

**Por qué containerizado y no stdio:** Hermes es instalación nativa (systemd).
Cada MCP en su propio contenedor permite múltiples conexiones concurrentes,
deploy/restart independiente, y aislamiento de dependencias. Stdio sería más
simple para tools que acceden al filesystem local; el resto va por HTTP.

---

## Estructura de archivos

Convención: cada MCP en `~/projects/agents/<nombre>-mcp/`.

```
~/projects/agents/example-mcp/
├── .env                    # secretos (chmod 600, NO se commitea)
├── .gitignore              # incluye .env
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── server.py               # las tools en sí
└── README.md               # qué hace este MCP, cómo configurarlo
```

---

## Plantilla mínima — `server.py`

Tool de ejemplo (`say_hello`) que demuestra el patrón. Sustituye por tu lógica:

```python
"""Example MCP server — minimal template."""
from __future__ import annotations

import os
from typing import Any

from fastmcp import FastMCP

GREETING_PREFIX = os.environ.get("GREETING_PREFIX", "Hola")

mcp = FastMCP("example")


@mcp.tool
def say_hello(name: str) -> dict[str, Any]:
    """Saluda a alguien por su nombre. Tool de ejemplo.

    Args:
        name: Nombre de la persona a saludar.
    """
    return {"greeting": f"{GREETING_PREFIX}, {name}"}


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run()
    else:
        port = int(os.environ.get("MCP_PORT", "8768"))
        mcp.run(transport=transport, host="0.0.0.0", port=port)
```

Notas:
- Las tools son funciones decoradas con `@mcp.tool`. El docstring se lo pasa al modelo como descripción — escribe en español o inglés según prefieras, sé claro.
- Usa type hints en parámetros y return — FastMCP genera el schema JSON automáticamente.
- Devuelve `dict` para respuestas estructuradas; el modelo puede inspeccionar campos.
- Para errores recuperables, devuelve `{"ok": False, "error": "..."}` en lugar de raise.

---

## Plantilla — `Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV MCP_TRANSPORT=streamable-http \
    MCP_PORT=8768 \
    PYTHONUNBUFFERED=1

EXPOSE 8768

CMD ["python", "server.py"]
```

---

## Plantilla — `docker-compose.yml`

```yaml
services:
  example-mcp:
    build: .
    container_name: example-mcp
    restart: unless-stopped
    ports:
      - "127.0.0.1:8768:8768"   # SOLO localhost, nunca 0.0.0.0
    environment:
      MCP_TRANSPORT: streamable-http
      MCP_PORT: 8768
      TZ: America/Bogota         # ajusta a la zona horaria del cliente
      GREETING_PREFIX: ${GREETING_PREFIX}
      # MY_API_KEY: ${MY_API_KEY}
```

**Convenciones de puerto:** `876X` consecutivos, comenzando en `8765`. Usa un
puerto distinto para cada MCP del cliente. Bind siempre a `127.0.0.1` — el
acceso es solo desde Hermes en el mismo host.

---

## Plantilla — `requirements.txt`

```
fastmcp>=2.0
# Añade aquí dependencias específicas de tus tools
```

---

## Plantilla — `.env`

```
# Secretos y configuración no-pública. chmod 600. NUNCA commitear.
GREETING_PREFIX=Hola
# MY_API_KEY=...
```

```bash
chmod 600 .env
```

---

## Plantilla — `.gitignore`

```
.env
__pycache__/
*.pyc
```

---

## Despliegue

```bash
cd ~/projects/agents/example-mcp
docker compose up -d --build

# Verificar arranque
docker compose logs --tail 20 example-mcp
# Esperar ver: "Uvicorn running on http://0.0.0.0:8768"
```

---

## Registrar en Hermes

Editar `~/.hermes/config.yaml`. Buscar la sección `mcp_servers:` (puede estar
comentada por defecto — descomentar) y añadir:

```yaml
mcp_servers:
  example:
    url: http://localhost:8768/mcp
```

Si ya tienes otros MCPs registrados, añadir como una entrada más bajo el mismo
bloque.

**Antes de reiniciar, hacer backup del config:**

```bash
cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak.$(date +%Y%m%d-%H%M%S)
```

Reiniciar Hermes para que descubra el MCP nuevo:

```bash
sudo systemctl restart hermes-gateway
```

---

## Verificar discovery

```bash
sleep 5
grep "MCP server 'example'" ~/.hermes/logs/agent.log | tail -1
```

Debe aparecer una línea tipo:

```
MCP server 'example' (HTTP): registered N tool(s): mcp_example_say_hello, ...
```

Si no aparece o sale error, revisar:

```bash
docker compose -f ~/projects/agents/example-mcp/docker-compose.yml logs --tail 30
sudo journalctl -u hermes-gateway -n 50 --no-pager
```

---

## Test desde Telegram

Manda al bot:

> *"Saluda a Juan usando la tool de ejemplo"*

Hermes debe llamar `mcp_example_say_hello(name="Juan")` y responder con el
resultado.

---

## Pitfalls comunes

1. **Puerto colisionado.** Si otro MCP ya usa el puerto, el contenedor falla
   silenciosamente o Hermes no lo descubre. Usar puertos secuenciales y
   verificar con `docker ps`.

2. **Bind a `0.0.0.0` en lugar de `127.0.0.1`.** Expone el MCP a la red. Aunque
   no sea grave para tools sin secretos, es buena higiene mantenerlo localhost.
   El bind dentro del contenedor sí es `0.0.0.0` (es interno); el mapeo en
   `docker-compose.yml` es donde se controla.

3. **TZ del contenedor.** Por defecto es UTC. Si el MCP maneja fechas
   user-facing, añadir `TZ: America/Bogota` (o lo que aplique) en
   `docker-compose.yml`.

4. **Config de Hermes editado pero olvidaron `restart`.** Hermes lee el config
   solo al arrancar — cualquier cambio requiere `systemctl restart
   hermes-gateway`.

5. **`.env` filtrado al repo.** Asegurarse que `.gitignore` lo excluye antes
   del primer commit. Si llegó al repo, rotar el secreto.

6. **Tools sin docstring o con descripción vaga.** El modelo decide cuándo
   llamar una tool basándose en el docstring. Vale la pena escribir descripción
   clara con ejemplos de cuándo usarla.

7. **Devolver objetos no serializables.** FastMCP serializa la respuesta a
   JSON. `datetime`, `Decimal`, etc. requieren conversión explícita (ej:
   `.isoformat()`).

---

## Ejemplos en producción

MCPs ya construidos en este servidor que pueden servir como referencia:

| MCP | Puerto | Capacidad |
|-----|--------|-----------|
| `geocampo-mcp` | 8765 | Informe agroclimático de la finca de cacao, clasificado con umbrales agronómicos |
| `mail-mcp` | 8766 | Send/receive correo + invitaciones de calendario (SMTP/IMAP/CalDAV) |
| `calendar-mcp` | 8767 | Lectura y gestión de la agenda (CalDAV) |
| `notion-mcp` | 8768 | Acceso a un workspace Notion vía integration interna |
| `barchart-mcp` | 8769 | Cadena de opciones de futuros vía sesión Playwright autenticada |
| `stonex-mcp` | 8770 | Cuenta de futuros StoneX (summary, positions, descarga + parseo de daily statements PDF) |
| `inventory-mcp` | 8771 | Google Sheet de inventario (read/write) vía service account |

Ver el `server.py` de cada uno para ver patrones más complejos (manejo de
credenciales, parsing de respuestas, errores recuperables).

### Patrones de autenticación observados

Cada MCP de la tabla resuelve auth de manera distinta — útil como mapa
mental cuando despliegues uno nuevo:

- **API key estática** (geocampo, notion): variable de entorno en `docker-compose.yml` desde un `.env` con `chmod 600`. Simple, sin renovación.
- **SMTP/IMAP/CalDAV con usuario+app-password** (mail, calendar): mismo patrón que API key, pero el secreto es un app-password del proveedor (Fastmail, Gmail, etc.).
- **Sesión web persistida** (barchart): login Playwright al primer arranque, cookies+storage en un volumen montado; refresh transparente cuando la sesión expira.
- **OAuth/OIDC con refresh token** (stonex): login Playwright bootstrap → guarda accessToken+refreshToken en disco → refresca con el endpoint OAuth del proveedor; fallback a Playwright si el refresh muere. Headers extra del proveedor (ej. `authentication-type: OKTA`) hay que descubrirlos interceptando requests reales de la SPA.
- **Service account de Google** (inventory): JSON key descargada de GCP, montada read-only en `/data`, scope `spreadsheets`. La sheet se comparte con el email del SA como Editor. Sin expiración; rotar manualmente cada 6-12 meses.

Para el último caso (service account), los pasos de provisión están fuera de
este patrón porque dependen de la consola GCP del cliente. Conviene
documentarlos en el `README.md` del MCP correspondiente, no acá.

---

## Cuándo NO usar este patrón

- **Tools puramente informacionales sin estado:** un built-in skill de Hermes
  (en `~/.hermes/skills/`) puede ser más simple. Los skills son procedimientos
  documentados que el modelo sigue, no código que se ejecuta.

- **Acceso a filesystem local:** stdio MCP con `command:` puede ser más
  apropiado que HTTP — ver docs de Hermes (`mcp_servers` permite `command:`
  además de `url:`).

- **Una sola operación de un solo uso:** probablemente sirva un script Python
  que el operador invoca a mano, sin necesidad de exponerlo al modelo.
