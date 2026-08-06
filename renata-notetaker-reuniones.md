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
  en `span.NWpY1d`. ⚠️ El botón es un **toggle** y el click falla en silencio si
  la barra no renderizó: hay que **verificar que la región existe**, no confiar
  en el click (ver "Mantenimiento — transcripciones vacías").
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
  guardar el **Doc en Drive** (Fase 4), enviar el correo con
  `renata-gmail.send_message(use_html=true)` a
  `renata@aroco.co, alvaro.acosta@aroco.co, <correo-personal-de-Pablo>` **con el
  enlace al Doc**, y `mark_sent`.

  > **Orden Drive → correo (2026-08-04):** antes el correo salía primero y el Doc
  > después, así que el acta por correo no traía enlace al Doc y había que buscarlo
  > a mano en Drive. Ahora el Doc se crea primero y su `webViewLink` se incrusta al
  > final del correo (`Ver notas completas en Google Docs`). El skill lleva un
  > fallback explícito: **si Drive falla, el correo sale igual sin el enlace** — el
  > acta nunca se pierde por un fallo de Drive.

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
| Notetaker entrar | `reunion-join` | `*/15 6-19 * * *` | gcalendar, meet | Fase A: detecta y programa la entrada. Turno mínimo. |
| Notetaker resumen | `reunion-resumen` | `*/15 6-19 * * *` | meet, gmail, drive | Fase B: resume+correo+Doc de **UNA** reunión por corrida. |

Claves del diseño anti-cuelgue:
- **Separados:** la detección nunca queda bloqueada detrás de un resumen.
- **Una reunión por corrida** en la Fase B: aunque terminen varias a la vez, cada
  turno hace solo una y la siguiente se atiende en la próxima corrida (cada 15 min).

> **Corrección de cobertura (2026-07-14):** el "entrar" corría `55 6-18 * * *`
> (**solo a los :55**, mirando 15 min adelante), así que una reunión que empezaba
> lejos del tope de hora (p. ej. **12:45**) caía en un punto ciego y Renata no
> entraba. Ahora corre **`*/15 6-19`** con `upcoming_meetings(within_minutes=20)`
> → ticks cada 15 min con ventana de 20 = cobertura sin huecos a cualquier minuto.
> El anti-duplicado (código+fecha) evita doble-entrada cuando dos ticks ven la
> misma reunión; `start_at` mantiene el job esperando en background hasta la hora
> exacta. **Síntoma que delató el bug:** la reunión no genera ni job (a diferencia
> de la sesión caducada, que sí crea job pero en `error:"no se pudo entrar"`).

```bash
hermes cron create "*/15 6-19 * * *" "..." --name "Notetaker entrar"  --skill reunion-join    --deliver local
hermes cron create "*/15 6-19 * * *" "..." --name "Notetaker resumen" --skill reunion-resumen --deliver local
```

- `--deliver local` → no spamea Telegram (el entregable es el correo + el Doc).
- **Gotcha `enabled_toolsets`** (ver [cronjobs.md](./cronjobs.md)): la CLI no tiene
  flag, así que tras crear cada job hay que **editar `~/.hermes-renata/cron/jobs.json`**
  y añadirle su `enabled_toolsets` (los de la tabla), luego
  `systemctl reload hermes-renata-gateway`. Sin esto el cron alucina tools.

### Probar la Fase B sin una reunión en vivo (recipe)

Para validar el tramo resumen→correo→Doc no hace falta una reunión real: se
**siembra un job "terminado y sin enviar"** con una transcripción sintética y se
fuerza el cron `reunion-resumen`. Así se probó la auto-copia el 2026-07-01
(end-to-end OK). Pasos:

1. **Antes de sembrar, confirmar que la cola está vacía** (`sent=true` en todos)
   para no arrastrar una reunión real en la prueba:
   ```bash
   docker exec renata-meet-mcp sh -c 'for f in /data/jobs/*.json; do \
     python3 -c "import json;d=json.load(open(\"$f\"));print(d[\"status\"],d[\"sent\"],d[\"job_id\"])"; done'
   ```
2. **Sembrar** transcripción (`>5` líneas útiles, formato `Hablante: texto`) en
   `/data/transcripts/` y un job en `/data/jobs/<code>_<TS>.json` con
   `status:"done"`, `sent:false`, `lines:>5`, `transcript_path` apuntando al archivo.
   Usar un `code` obvio de prueba (p. ej. `test-prueba-flujo`).
3. **Disparar el cron real** (corre con sus toolsets vía el scheduler del gateway):
   ```bash
   HERMES_HOME=/home/aroco/.hermes-renata \
     .../venv/bin/python -m hermes_cli.main cron run 9938e112e700   # id de "Notetaker resumen"
   ```
   El scheduler lo toma en el siguiente tick (~1 min); el job pasa a `sent:true`.
4. **Verificar** en la sesión de la corrida
   (`~/.hermes-renata/sessions/session_cron_<id>_<TS>.json`): que
   `send_message.to` incluye los tres correos y que `send_message`/`create_doc`
   devolvieron `ok:true` (con message id / webViewLink).
5. **Limpiar** el job y la transcripción sembrados
   (`rm` dentro del contenedor). No se reprocesan una vez `sent:true`, pero mejor no
   dejar basura.

> Caveats: (a) el correo se **entrega de verdad** a Pablo y Álvaro — avisar que es
> una prueba. (b) `cron run` deja esta prueba como "última corrida" del cron, sin
> efecto en el ciclo normal `*/15`. (c) **No** reconstruir `renata-meet` si hay una
> asistencia viva (ver Riesgo #1 de la Fase 2).

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
HTML + transcripción al final) **antes** de enviar el correo, para poder incrustar
el `webViewLink` del Doc en el acta (ver nota en Fase 3 → Skill). Toolset del cron:
`mcp-renata-drive`.

**El Doc trae más que el correo:** además del resumen ejecutivo, decisiones y tabla
de acciones, lleva al final la **transcripción completa** de la reunión. Por eso el
enlace en el correo dice explícitamente "(incluye la transcripción)".

## Mantenimiento — sesión de Google del bot Meet (caduca cada ciertas semanas)

> Esta sección cubre la falla "**no entra**" (`status:"error"`). Si el job dice
> `done` con `lines:0`, es la otra falla → "Mantenimiento — transcripciones
> vacías", más abajo.

La sesión web sembrada (`storage_state.json`, ver Fase 2) **caduca sola**. El bot
la re-guarda tras **cada asistencia exitosa**, así que mientras hay reuniones se
renueva sola; pero si pasan varios días sin una asistencia OK (o Google invalida
la cookie), la sesión muere y **no puede auto-renovarse headless** (Google bloquea
el re-login sin interacción). Pasó en producción: caducó ~2026-07-09 y del 9 al 14
de julio Renata falló **todas** las reuniones.

### Síntoma

Renata recibe las convocatorias e **intenta** entrar (se crean jobs en
`/data/jobs/`), pero cada job queda en:

```json
{ "status": "error", "started_at": null, "error": "no se pudo entrar" }
```

`started_at: null` = ni siquiera llega a la sala. El resto (gateway, contenedores,
crons) está sano — engaña, porque `cron list` muestra `ok` (el turno del agente
corrió bien; lo que falla es la entrada al Meet).

### Diagnóstico (1 comando)

```bash
# verify_session vía el MCP (handshake streamable-http)
BASE=http://127.0.0.1:8783/mcp
H=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
curl -s -D /tmp/h.txt "${H[@]}" -X POST $BASE -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"c","version":"1"}}}' >/dev/null
SID=$(grep -i mcp-session-id /tmp/h.txt | awk '{print $2}' | tr -d '\r')
curl -s "${H[@]}" -H "mcp-session-id: $SID" -X POST $BASE -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
curl -s "${H[@]}" -H "mcp-session-id: $SID" -X POST $BASE -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"verify_session","arguments":{}}}' | grep -o '"structuredContent":{[^}]*}'
```

- Caducada → `"logged_in":false,"email":null,"final_url":"https://accounts.google.com/...signin..."`
- Sana → `"logged_in":true,"email":"renata@aroco.co","final_url":"https://meet.google.com/landing"`

### Remedio — re-sembrar (paso manual desde una Mac, ~5 min)

Google bloquea el login headless, así que hay que loguearse **una vez de forma
interactiva** en una Mac y subir el `storage_state`. Es el mismo método de la
Fase 2 (sembrar en la Mac y subir), repetido.

1. En la Mac, dentro de un venv:
   ```bash
   mkdir -p ~/Desktop/renata && cd ~/Desktop/renata
   python3 -m venv venv && source venv/bin/activate
   pip install playwright && python3 -m playwright install chromium
   ```
2. Bajar `reseed_renata.py` — está versionado en este repo, en
   [`scripts/reseed_renata.py`](./scripts/reseed_renata.py):
   ```bash
   scp aroco@<server>:/home/aroco/projects/repos/hermes-agent/scripts/reseed_renata.py .
   ```
   (headed; abre Google, esperas a estar DENTRO como `renata@aroco.co`, pulsas
   Enter, comprueba Meet y guarda el archivo solo si NO rebotó al login):
   ```python
   from playwright.sync_api import sync_playwright
   OUT = "storage_state_renata.json"
   with sync_playwright() as p:
       b = p.chromium.launch(headless=False)
       c = b.new_context(locale="es-ES", viewport={"width":1280,"height":800})
       pg = c.new_page(); pg.goto("https://accounts.google.com/", wait_until="load")
       input(">>> Inicia sesion como renata@aroco.co; cuando estes DENTRO pulsa Enter...")
       pg.goto("https://meet.google.com/", wait_until="load"); pg.wait_for_timeout(3000)
       if "accounts.google.com" in pg.url or "signin" in pg.url:
           print("[X] Sigue en login, NO se guardo. Reintenta.")
       else:
           c.storage_state(path=OUT); print("[OK] Guardado:", OUT, "| Meet:", pg.url)
       b.close()
   ```
   ```bash
   python3 reseed_renata.py    # entra como renata@aroco.co (+ 2FA), Enter al estar dentro
   ```
3. Subir al server, reemplazando el archivo:
   ```bash
   scp storage_state_renata.json aroco@<server>:/home/aroco/projects/data/renata-meet/storage_state.json
   ```
4. Verificar con el comando de diagnóstico → `logged_in:true`. **No** hace falta
   reiniciar el contenedor: el bot lee el archivo fresco en cada asistencia (el
   volumen `/data` está montado rw). Y como cuando está caducada **no hay
   asistencias vivas** (todas en error), tampoco aplica el Riesgo #1.
5. (Opcional) Prueba real: crea un Meet, `start_attendance(url=...)`, y confirma
   `status:"in_call"` con `list_jobs` — deberías ver a Renata entrar.

> El `storage_state.json` lleva las cookies de sesión de Renata: **sensible**, no
> commitear ni compartir. Borrarlo de la Mac al terminar.

### Historial de caducidades

| Caducó | Se detectó | Reuniones perdidas | Cómo nos enteramos |
|---|---|---|---|
| ~2026-07-09 | 2026-07-14 | todas del 9 al 14-jul | Pablo lo notó → se creó el cron de chequeo |
| 2026-07-27 | 2026-08-04 | ~11 (3 comités el 3-ago; "Revisión CRM y Plataformas" el 4-ago) | Pablo lo notó otra vez — el cron **sí** detectaba pero no avisaba (ver gotcha abajo) |

Duración observada de la cookie: **2–3 semanas** sin asistencias que la renueven.
Re-sembrada el 2026-08-04 a las 16:04 (`logged_in:true`, `meet.google.com/home`).

### Chequeo automático de sesión (cron `Notetaker chequeo sesion`) ✅

Desplegado 2026-07-14 para no volver a enterarse por una reunión perdida. Corre
`verify_session` y **avisa por Telegram SOLO si la sesión caducó**; si está sana,
calla. Horario: **`30 6,9,12,15 * * *`** (4 veces al día, ampliado 2026-08-04 —
con un solo chequeo a las 6:30 una caducidad de media mañana costaba el día
entero de reuniones).

- Skill: `~/.hermes-renata/skills/note-taking/reunion-sesion-check/SKILL.md`
  — llama `renata-meet.verify_session`; si `logged_in:false` **responde el texto
  del aviso** (lo entrega el cron); si `logged_in:true` responde `[SILENT]`
  (marcador que suprime la entrega — solo queda en el log de auditoría).
- Cron: `deliver = telegram:<chat_id_pablo>,telegram:<chat_id_alvaro>` (Pablo + Álvaro;
  `deliver` acepta varios destinos separados por coma).

#### ⚠️ Gotcha crítico: `send_message` NO existe dentro de un cron

**Un cron no puede avisar llamando `send_message`, por más que su
`enabled_toolsets` incluya `messaging`.** El scheduler lo desactiva a la fuerza:

```python
# cron/scheduler.py — construcción del AIAgent
enabled_toolsets=_resolve_cron_enabled_toolsets(job, _cfg),
disabled_toolsets=["cronjob", "messaging", "clarify"],   # <— pisa el job
```

**Cómo se manifiesta (falla silenciosa, la peor clase):** el agente detecta bien
el problema, no encuentra `send_message`, asume que "el sistema" entregará su
texto y responde algo como `AVISO ENVIADO — sesión caducada`. El cron queda en
`last_status: ok`. Nadie recibe nada. Verificado en producción: **del 28-jul al
4-ago de 2026 el chequeo detectó la caducidad todos los días y ningún aviso
llegó**; se perdieron ~11 reuniones (3 comités el 3-ago) y nos enteramos porque
Pablo lo notó.

**El patrón correcto para cualquier cron que deba avisar condicionalmente:**

1. `deliver` = los destinos reales (`telegram:<chat_id>,telegram:<chat_id>`), no
   `local`.
2. El skill **responde el aviso como su texto final** — el scheduler lo entrega.
3. Para el caso "todo bien", responder `[SILENT]`: suprime la entrega
   (`cron/scheduler.py:SILENT_MARKER`). Así el aviso sigue siendo condicional
   sin depender de `send_message`.
4. En el skill, prohibir explícitamente llamar herramientas de envío y decir
   "no digas AVISO ENVIADO" — si no, el modelo alucina la entrega.

**Diagnóstico rápido** de si un cron pudo avisar: abrir
`~/.hermes-renata/sessions/session_cron_<id>_<TS>.json` y mirar la clave `tools`
— es la lista real de herramientas que tuvo. Si `send_message` no está ahí, no
envió nada. Y revisar `last_delivery_error` en `cron/jobs.json`: `None` con
`deliver` ≠ `local` significa entrega OK.

> El mismo bug afectaba a `Barchart chequeo sesion` (`ad5af3d40798`); corregido
> igual el 2026-08-04.

## Mantenimiento — transcripciones vacías (la OTRA falla)

> **Antes de diagnosticar, separar las dos fallas.** Se confunden porque el
> resultado visible es el mismo ("no llegaron las notas"), pero la causa y el
> remedio no tienen nada que ver:
>
> | Job dice | Falla | Sección |
> |---|---|---|
> | `status:"error"`, `"no se pudo entrar"` | no entra → sesión caducada | la anterior |
> | `status:"done"` con **`lines:0`** | entra pero no captura | **esta** |

### Síntoma

El job queda **`done`** y **`sent:true`** — aparentemente todo bien —, pero el
archivo en `/data/transcripts/` pesa **0 bytes** y nunca llegó ni correo ni Doc.
Comparando `created` vs `updated` se ve que el bot **estuvo la reunión entera**
(1-2 h) grabando nada. No hay error en ninguna parte: por eso pasó desapercibido
meses.

**Frecuencia real medida el 2026-08-06:** 11 de las 32 reuniones a las que sí
logró entrar salieron vacías (**~34%**), entre el 26-jun y el 6-ago.

### Causa raíz

Son **dos defectos encadenados**. El segundo se descubrió volcando el DOM de una
reunión real y es el que invalidaba cualquier reintento.

**(a) El resultado del click se descartaba.**

```python
await _enable_captions(page)      # ← el booleano se tiraba
await _set_caption_language(page)
```

Si la barra inferior aún no había renderizado (se auto-oculta, ver Fase 2) o
Google cambiaba la etiqueta, el click fallaba **en silencio** y el bot pasaba
horas leyendo `div[role="region"][aria-label="Subtítulos"]`, una región que
nunca llegó a existir.

**(b) El regex del botón era ambiguo — y por eso reintentar no servía.**

`_RE_CAPTIONS = /(subt[íi]tulos|captions)/` matchea **tres** botones distintos, y
`_click_by_text` clickea el primero en orden de documento:

| # | `aria-label` | Efecto |
|---|---|---|
| 1 | **Abrir ajustes de subtítulos** ← se clickeaba este | abre un panel |
| 2 | Ir a los subtítulos más recientes | scroll |
| 3 | Activar/Desactivar subtítulos | **el toggle real** |

En una sala recién abierta solo existe "Activar subtítulos" y el click acierta —
por eso funcionaba a veces. Pero en cuanto el panel de ajustes está en el DOM,
el primer match pasa a ser "Abrir ajustes de subtítulos": el bot abre un panel,
cree que activó la captura y **los tres reintentos caen en el mismo botón
equivocado**. Un mecanismo de recuperación que no puede recuperarse.

> ⚠️ Con el toggle en un label y sus *ajustes* en otro que comparte la palabra,
> anclar el patrón (`^activar subtítulos$`) no es cosmético: es la diferencia
> entre activar la captura y abrir un menú.

**Bug latente adicional:** el botón de subtítulos es un **toggle**. Si Meet ya
los traía activos (recuerda la preferencia entre reuniones), el click los
**apagaba** — el "arreglo" ingenuo de clickear más veces habría empeorado la cosa.

### Lo que quedó sin explicar

En la reunión del 2026-08-06 el primer intento debió acertar (sala nueva = un
solo match) y aun así `captions_ok` salió `false`. **No sabemos por qué falló ese
primer click**, y una sala vacía no reproduce el fallo: ahí activa bien. Por eso
el fix incluye volcar evidencia (abajo) en vez de seguir conjeturando.

### Arreglo (2026-08-06) — los tres cambios van juntos

**1. Verificar el DOM en vez de confiar en el click** (`meet_bot.py`):

```python
async def _captions_active(page) -> bool:
    """True si la región de subtítulos está montada. Mismo selector que
    CaptionCollector: si no está, la captura es imposible."""
    for sel in _CAP_SELECTORS:
        if await page.query_selector(sel):
            return True
    return False

async def _enable_captions(page, attempts: int = 3) -> bool:
    for i in range(attempts):
        if await _captions_active(page):
            return True          # ya activos → NO clickear (es toggle)
        if await _find_by_label(page, _RE_CAPTIONS_OFF):   # "Desactivar..." = ya puestos
            if await _wait_captions_mounted(page, 4):
                return True
        if await _click_by_text(page, _RE_CAPTIONS_ON, timeout=6000):
            if await _wait_captions_mounted(page):         # 8 s, no 5
                return True
        elif await _enable_captions_via_menu(page):        # fallback: menú ⋮
            return True
        ...
```

El predicado correcto no es "¿se pudo clickear?" sino **"¿existe la región que
el colector va a leer?"**. Verificar contra el mismo selector que consume el
código de captura es lo que hace la comprobación significativa.

**Labels anclados** (`_RE_CAPTIONS_ON` / `_OFF`), por el defecto (b) de arriba, y
**fallback al menú ⋮** (`_enable_captions_via_menu`) para cuando Meet colapsa el
toggle fuera de la barra.

**2. Registrar el resultado.** El job guarda `captions_ok`, `caption_language` y
`captions_debug`, publicados **apenas se saben** (callback `meta_cb`, separado
del `status_cb`) para que el fallo sea visible en vivo. `list_jobs` los expone.
Jobs anteriores al fix → `captions_ok: null`. Sin esto no hay forma de distinguir
"los subtítulos fallaron" de "nadie habló".

**2-bis. Evidencia al fallar** (`_dump_captions_failure`): screenshot + todos los
`aria-label` de la barra en `/data/debug/captions_fail_<ts>.{png,txt}`. Una sala
vacía **no reproduce** el fallo — sin esta foto del momento exacto, diagnosticar
un fallo en reunión real es conjeturar.

> ### ⚠️ No metas una dimensión ortogonal dentro del `status`
>
> El primer intento de (2) marcaba `status = "in_call_sin_subtitulos"`. Parecía
> inocuo y rompió el **anti-duplicado** de `start_attendance`, que comparaba
> contra una lista blanca de estados:
>
> ```python
> if j.get("status") in ("queued","waiting","joining","in_call","done"):   # ← el nuevo no está
> ```
>
> El cron no reconoció el estado, lo trató como reunión sin atender y **lanzó una
> segunda Renata a una reunión en curso** (2026-08-06, 17:15). Se salvó porque
> Google no admite la misma cuenta dos veces en la misma sala y la segunda murió
> con `no_join_button`.
>
> Dos lecciones:
> - El `status` es el **avance** del job. Si los subtítulos van bien o mal es otra
>   dimensión → campo aparte (`captions_ok`), nunca un estado más.
> - El anti-duplicado ahora usa **lista negra** (`_RETRYABLE_STATUSES = ("error",)`):
>   cualquier estado que no sea fallo terminal bloquea el duplicado. Así, añadir un
>   estado nuevo falla del lado seguro en vez de reabrir el agujero en silencio.

**3. Avisar en vez de callar.** El skill `reunion-resumen` marcaba `sent` y
terminaba cuando la transcripción venía vacía — la reunión se evaporaba sin
rastro. Ahora responde un aviso que el cron entrega por Telegram:

```
⚠️ Reunión sin transcripción
Reunión: <título> (<start_at>)
Motivo probable: <según captions_ok: false → "no se pudieron activar los
subtítulos"; true → "se activaron pero nadie habló"; null → "desconocido">
Fin: <ended_reason> · líneas: <lines>
```

### ⚠️ El cron de resumen tenía `deliver: local`

`Notetaker resumen` (`9938e112e700`) entregaba a `local`, o sea **a nadie**. El
aviso del punto 3 no habría llegado por más bien redactado que estuviera.
Cambiado a `telegram:<pablo>,telegram:<alvaro>` — mismo patrón condicional que
el chequeo de sesión (ver el gotcha de `send_message` arriba).

**Cuidado con la frecuencia:** este cron corre **cada 15 min de 6 a 19h** (56
corridas/día), muy distinto del chequeo de sesión (4/día). El skill **debe**
responder `[SILENT]` cuando no hay pendientes *y* cuando el envío ya salió por
correo. Si no, son ~56 mensajes de Telegram al día. Verificado antes de dejarlo
en producción.

### Probar sin una reunión en vivo

La lógica de captions se puede testear **sin Meet**, con un `Page` falso que
simule "la región aparece tras N clicks":

```bash
docker cp test_captions.py renata-meet-mcp:/tmp/ && \
  docker exec renata-meet-mcp python /tmp/test_captions.py
```

Casos que deben pasar: activa al 1er click · devuelve `False` tras 3 intentos
fallidos · **0 clicks si ya estaban activos** (el toggle) · reintenta y logra.

Para el aviso, la receta de la Fase B (más arriba) pero sembrando el job con
`"lines": 0, "captions_ok": false`. **Poner `deliver local` durante la prueba**
para no mandar mensajes de test al equipo, y restaurar el destino real después.

> ⚠️ **Un test verde aquí no significa que funcione en una reunión real.** El
> `Page` falso valida la lógica de reintento, no la UI de Google. Y una sala
> vacía tampoco sirve de prueba: ahí los subtítulos activan bien y el fallo del
> 2026-08-06 **no se reproduce**. El único diagnóstico fiable de un fallo real es
> el volcado de `_dump_captions_failure` de esa reunión.

**Inspeccionar el DOM real** (sala vacía, no molesta a nadie — el link de una
reunión terminada sigue vivo):

```bash
# vuelca screenshot + HTML a /data/debug
capture_debug(url="https://meet.google.com/<code>")
```

Luego, sobre el HTML, listar **en orden de documento** los botones que matchea un
patrón antes de confiar en él — así se descubrió el defecto (b):

```python
hits = [lab for tag in botones if RX.search(lab)]   # el bot clickea hits[0]
```

### Pendiente

`max_minutes = 120` corta las reuniones largas: la del 2026-08-05 terminó con
`ended_reason: max_minutes` y la reunión seguía viva. Subirlo a 240 y anotar en
las notas cuando el corte sea por límite.

## Futuro ⏳

Integrar con el CRM de AROCO y con los calendarios del equipo; transcripción de
alta fidelidad con Whisper; manejo de reuniones solapadas.
