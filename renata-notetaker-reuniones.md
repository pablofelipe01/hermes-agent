# Renata notetaker de reuniones (Google Meet)

Flujo para que **Renata** (segundo agente AROCO, instancia Hermes nativa en
`~/.hermes-renata/`, ver [replicar-agente-cliente.md](./replicar-agente-cliente.md))
asista a las reuniones de AROCO a las que la invitan, las transcriba y mande un
resumen con acciones — sin que nadie tenga que conectarla a mano al Meet.

> Identificadores sensibles (tokens, links reales de Meet, IDs de evento) van
> como placeholders `<ASÍ>`. Los valores reales viven solo en el servidor:
> `~/projects/agents/<mcp>/` y `~/projects/data/google/`.

## Arquitectura (por qué no es "solo un skill")

Una reunión dura ~1 h; un turno del agente Hermes no puede quedarse corriendo y
grabando. Por eso el trabajo pesado va en **contenedores MCP** y el agente solo
orquesta (turnos cortos): decidir a qué reunión entrar y, al terminar, resumir y
enviar.

| Pieza | Qué hace | Puerto | Estado |
|-------|----------|--------|--------|
| `renata-gcalendar-mcp` | Calendario de Renata + extrae `meet_link` de cada evento | 8782 | ✅ Fase 1 |
| `renata-meet-mcp` | Bot Playwright que entra al Meet y captura la transcripción | 8783 | ✅ Fase 2 |
| cron + skill `reunion-notetaker` | Auto-dispara el bot y, al terminar, resume + envía correo | — | ✅ Fase 3 |
| `renata-drive-mcp` | Guarda un Google Doc por reunión en carpeta compartida | 8784 | ✅ Fase 4 |
| Notion/CRM, Whisper, solapadas | Integraciones y robustez extra | — | ⏳ futuro |

Decisiones tomadas con Pablo: **notetaker completo** (no solo presencia) y **bot
de navegador propio** (self-hosted, sin servicio externo tipo Recall.ai).

---

## Fase 1 — `renata-gcalendar-mcp` (puerto 8782) ✅

Clon del `gcalendar-mcp` de Álvaro (ver [ejemplos.md](./ejemplos.md) e
[integracion-mcp-app.md](./integracion-mcp-app.md)) pero con **OAuth propio de
Renata** y extracción del link de Meet.

### Estructura

```
~/projects/agents/renata-gcalendar-mcp/
├── server.py            # FastMCP streamable-http, scope calendar
├── Dockerfile           # python:3.12-slim, MCP_PORT=8782
├── docker-compose.yml   # 127.0.0.1:8782:8782, monta /data/google :ro
└── requirements.txt     # fastmcp, google-auth, google-api-python-client
```

### Aislamiento de credenciales

- Token propio: `~/projects/data/google/token_renata_gcal.json` (separado del de
  gmail `token_renata.json` y del de Álvaro `token_alvaro.json`).
- Reusa el mismo `client_secret.json` del proyecto Google.
- `/data` se monta **`:ro`**: funciona porque el server solo LEE el token y lo
  refresca en memoria con el `refresh_token` (mismo patrón que renata-gmail).

### OAuth (paso manual, una vez)

Helper `~/projects/data/google/oauth_flow_renata_gcal.py` (scope
`calendar` + `openid` + `userinfo.email`), dos pasos:

```bash
cd ~/projects/data/google
./.venv/bin/python oauth_flow_renata_gcal.py url        # imprime URL
# Abrir la URL e INICIAR SESIÓN COMO renata@aroco.co (no Álvaro, no Pablo).
# Aceptar permisos → redirige a http://localhost/?code=... (no carga, es normal).
# Copiar esa URL completa y:
OAUTHLIB_INSECURE_TRANSPORT=1 OAUTHLIB_RELAX_TOKEN_SCOPE=1 \
  ./.venv/bin/python oauth_flow_renata_gcal.py exchange '<http://localhost/?code=...>'
```

**Gotcha:** Google devuelve en el redirect el scope `gmail.modify` ya concedido
(superset de lo pedido). Sin `OAUTHLIB_RELAX_TOKEN_SCOPE=1` el `exchange` falla
con "Scope has changed". El token guardado queda con scopes calendar/openid/email.

### Deploy + cableado

```bash
cd ~/projects/agents/renata-gcalendar-mcp && docker compose up -d --build
# Registrar en la config de Renata:
#   ~/.hermes-renata/config.yaml  →  mcp_servers.renata-gcalendar: { url: http://localhost:8782/mcp }
sudo systemctl reload hermes-renata-gateway     # SIGUSR1, re-lee config sin reiniciar
# Verificar descubrimiento:
HERMES_HOME=/home/aroco/.hermes-renata \
  /home/aroco/.hermes-renata/hermes-agent/venv/bin/python -m hermes_cli.main mcp list
```

### Tools (8)

`ping`, `list_calendars`, `list_events`, **`upcoming_meetings`**, `create_event`,
`update_event`, `delete_event`.

Lo distintivo vs el MCP de Álvaro:

- `list_events` añade **`meet_link`** por evento. Helper `_meet_link` robusto a
  las dos formas de Google: `hangoutLink` (legacy) y
  `conferenceData.entryPoints[].uri` (entryPointType `video`).
- **`upcoming_meetings(within_minutes=15, only_with_meet=True)`** — reuniones con
  Meet que arrancan dentro de la ventana. Es el gancho del cron de la Fase 3.

Verificado 2026-06-26: identidad `renata@aroco.co` [owner]; 14 reuniones en los
próximos 30 días, las 14 con `meet_link` correcto.

---

## Fase 2 — `renata-meet-mcp` (puerto 8783) ✅

Bot self-hosted (Playwright + Chromium) que entra al Meet y captura la reunión
por subtítulos. Desplegado y probado end-to-end 2026-06-26.

### Estructura

```
~/projects/agents/renata-meet-mcp/
├── server.py       # FastMCP: ping, verify_session, attend_meeting, capture_debug, test_captions
├── meet_bot.py     # lógica Playwright (join, captions, idioma, CaptionCollector)
├── Dockerfile      # base mcr.microsoft.com/playwright/python:v1.48.0-jammy
├── docker-compose.yml  # 127.0.0.1:8783:8783, shm_size 1gb, /data rw
└── requirements.txt    # fastmcp + playwright==1.48.0 (¡versión exacta!)
```

> **Gotcha imagen base:** la imagen Playwright-python trae los navegadores en
> `/ms-playwright` pero **NO** el paquete pip `playwright`. Hay que instalarlo en
> requirements con la **misma versión** que la imagen (1.48.0), o no matchea el
> navegador.

### Pre-requisito manual (clave): sesión web sembrada

Google **bloquea logins headless "frescos"**. Se siembra UNA vez un
`storage_state` (cookies) logueándose como `renata@aroco.co`. Método usado:
**sembrar en la Mac y subir** (Playwright headed en el Mac de Pablo →
`storage_state_renata.json` → `scp` a `~/projects/data/renata-meet/`).
Verificado: la sesión sembrada desde otra máquina **sobrevive headless en el
server** (`verify_session` → `logged_in:true`). El bot re-guarda el
`storage_state` tras cada sesión para alargar la cookie. Mantenimiento ocasional
cuando Google la caduque.

Script de seed (correr en el Mac, dentro de un venv con `pip install playwright`
y `playwright install chromium`):

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=False); c = b.new_context(); pg = c.new_page()
    pg.goto("https://accounts.google.com/")
    input(">>> Inicia sesion como renata@aroco.co; cuando estes DENTRO pulsa Enter...")
    c.storage_state(path="storage_state_renata.json"); b.close()
```

### Captura: subtítulos en vivo (no audio)

Leer el DOM de los **captions** da el texto **con nombre de quien habla**
(diarización gratis) y evita audio virtual + Whisper. Detalles que costaron
afinar contra una reunión real (UI en español):

- **Entrar:** botón **"Unirme ahora"** (no "Unirte"). Sala de espera →
  "Pedir unirte" (alguien admite). Sin permisos cám/mic en el contexto
  (`permissions=[]`) → entra muteada sin togglear.
- **Barra inferior se auto-oculta** (opacidad) → mover el mouse para revelarla +
  click `force=True`.
- **Subtítulos:** botón `aria-label="Activar subtítulos"`; texto en
  `div[role="region"][aria-label="Subtítulos"]`, cada bloque `div.nMcdL`, nombre
  en `span.NWpY1d`.
- **Idioma:** por defecto reconocía español como inglés. Hay que abrir "Ajustes
  de subtítulos" → combobox "Idioma de la reunión" → "Español (México)".
- **Meet reescribe el enunciado en vivo** (corrige, cambia mayúsculas) → un
  colector confirma cada enunciado cuando se estabiliza (comparación
  case-insensitive). Resultado: `Hablante: texto`, una línea por frase.

Tool central: `attend_meeting(url, max_minutes)` → transcripción + archivo en
`/data/transcripts/`. Tools de tuning: `capture_debug`, `test_captions`.

### Seguro "salir si está sola"

`_attend_core` cuenta participantes por el atributo `data-participant-id` (set de
ids únicos). Si queda ≤1 (solo Renata) durante 3 min seguidos, sale. Cubre salas
vacías (el cron entra a cualquier evento del calendario, haya gente o no) y el
cierre de la reunión. `ended_reason` queda en `meeting_ended` / `alone` /
`max_minutes`.

### Botones de entrar

Según el momento, Meet muestra distintos botones: **"Unirme ahora"** (reunión en
vivo), **"Pedir unirte"** (sala de espera → alguien admite) o **"Unirte
igualmente"** (fuera de hora / antes que el anfitrión / sala vacía). El regex de
entrar contempla los tres; entrar a una sala vacía es seguro porque el
alone-safeguard la saca en 3 min.

### ⚠️ Riesgo operacional #1: no reconstruir con asistencia activa

La asistencia corre como **asyncio task dentro del proceso del contenedor**.
`docker compose up --build` / restart de `renata-meet` **mata la sesión viva**
(Chromium muere, la transcripción en memoria se pierde). Pasó en producción: un
rebuild para añadir una feature sacó a Renata de un Comité real en curso y se
perdió la transcripción. **Antes de reconstruir/reiniciar**, verificar que no haya
jobs en `waiting/joining/in_call` (`list_jobs`) ni Chromium vivo
(`docker exec renata-meet-mcp sh -c 'ps aux | grep -c [c]hrome'`).

### Otros riesgos

- Re-autenticación periódica de Google (mantenimiento de la sesión sembrada).
- Concurrencia: reuniones solapadas = varios Chromium = más RAM/CPU.
- Consentimiento: avisar que Renata toma notas (sobre todo con externos).
- Automatizar Meet va técnicamente contra los ToS de Google (riesgo bajo, uso interno).

---

## Fase 3 — cron + skill `reunion-notetaker` ✅

Desplegada 2026-06-26. Une las piezas para que sea automático.

### Asistencia en background (clave)

Una reunión dura ~1 h; un turno del cron no puede bloquearse esa hora. Por eso
`renata-meet` expone **`start_attendance(url, title)`** que lanza la asistencia
en **background** (asyncio task) y devuelve al instante. El job se persiste en
`/data/jobs/<id>.json`; la transcripción en `/data/transcripts/`. Anti-duplicado
por **código de reunión + fecha** (no entra dos veces a la misma el mismo día).
Tools de cosecha: `list_jobs(only_unsent_done)`, `get_transcript`, `mark_sent`.

### Skill

`~/.hermes-renata/skills/note-taking/reunion-notetaker/SKILL.md` — orquestador de
dos fases:
- **Fase A:** `renata-gcalendar.upcoming_meetings(within_minutes=4)` → por cada
  reunión con Meet, `renata-meet.start_attendance`.
- **Fase B:** `renata-meet.list_jobs(only_unsent_done=true)` → por cada job,
  `get_transcript`, redactar resumen (resumen ejecutivo + decisiones + acciones),
  enviar con `renata-gmail.send_message(use_html=true)` a
  `renata@aroco.co, alvaro.acosta@aroco.co, <correo-personal-de-Pablo>`, y `mark_sent`.

  > **Auto-copia (2026-07-01):** Renata se incluye a sí misma en el "Para"
  > (`renata@aroco.co`) para que cada informe quede archivado en su propio inbox
  > como registro. Va en el "Para" visible (no BCC) porque a Pablo/Álvaro no les
  > molesta verla; funciona sin más porque el correo sale de la misma cuenta
  > `renata-gmail` (Gmail entrega sin problema un mensaje auto-dirigido). El Doc de
  > Drive no cambia: Renata ya tiene acceso por ser dueña de la carpeta.

### Crons — DOS, separados, con turnos cortos

Para que **ningún turno se cuelgue**, el flujo va en dos crons independientes (un
turno largo que hace todo es frágil; además puede chocar con el límite/restart del
gateway):

| Cron | Skill | Schedule | Toolsets | Qué hace |
|------|-------|----------|----------|----------|
| Notetaker entrar | `reunion-join` | `55 6-18 * * *` | gcalendar, meet | Fase A: detecta y programa la entrada. Turno mínimo. |
| Notetaker resumen | `reunion-resumen` | `*/15 6-19 * * *` | meet, gmail, drive | Fase B: resume+correo+Doc de **UNA** reunión por corrida. |

Claves del diseño anti-cuelgue:
- **Separados:** la detección nunca queda bloqueada detrás de un resumen.
- **Una reunión por corrida** en la Fase B: aunque terminen varias a la vez, cada
  turno hace solo una y la siguiente se atiende en la próxima corrida (cada 15 min).
- **Horarios distintos** (`:55` vs `*/15`) → normalmente no corren a la vez.

```bash
hermes cron create "55 6-18 * * *"  "..." --name "Notetaker entrar"  --skill reunion-join    --deliver local
hermes cron create "*/15 6-19 * * *" "..." --name "Notetaker resumen" --skill reunion-resumen --deliver local
```

- `--deliver local` → no spamea Telegram (el entregable es el correo + el Doc).
- **Gotcha `enabled_toolsets`** (ver [cronjobs.md](./cronjobs.md)): la CLI no tiene
  flag, así que tras crear cada job hay que **editar `~/.hermes-renata/cron/jobs.json`**
  y añadirle su `enabled_toolsets` (los de la tabla), luego
  `systemctl reload hermes-renata-gateway`. Sin esto el cron alucina tools.

## Fase 4 — `renata-drive-mcp` (puerto 8784) ✅

Guarda un **Google Doc por reunión** en una carpeta compartida. Clon del
`drive-mcp` de Álvaro (que es solo lectura) **+ tools de escritura**, con OAuth
propio de Renata (`token_renata_drive.json`, scope `drive`).

- `ensure_folder(name, share_with)` — busca-o-crea carpeta (idempotente, filtra
  `'me' in owners`) y la comparte como editor. Carpeta del flujo: **"Notas de
  Reuniones AROCO"**, compartida con `alvaro.acosta@aroco.co` + el correo personal de Pablo.
- `create_doc(name, html, folder_id)` — crea un Doc nativo desde HTML
  (`MediaInMemoryUpload` `text/html` → mimeType `application/vnd.google-apps.document`).

**Gotcha compartir con externos:** compartir con cuentas NO-Google (p. ej.
`@me.com`) **exige `sendNotificationEmail=True`** (con `False` falla en silencio).
`ensure_folder` manda notificación solo si el email no es `@aroco.co`. Las cuentas
externas además quedan como "sesión de invitado" que **re-verifica cada 7 días**.

El skill `reunion-notetaker` (Fase B) llama `ensure_folder` + `create_doc` (resumen
HTML + transcripción al final) tras enviar el correo. Toolset del cron:
`mcp-renata-drive`.

## Futuro ⏳

Integrar con el CRM de AROCO y con los calendarios del equipo; transcripción de
alta fidelidad con Whisper; manejo de reuniones solapadas.
