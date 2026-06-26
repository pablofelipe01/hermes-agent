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
| `renata-meet-mcp` | Bot Playwright que entra al Meet y captura la transcripción | 8783 | 🚧 Fase 2 |
| cron + skill `reunion-notetaker` | Auto-dispara el bot y, al terminar, resume + envía correo | — | ⏳ Fase 3 |
| Drive/Notion, Whisper, solapadas | Persistencia y robustez extra | — | ⏳ Fase 4 (opcional) |

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

## Fase 2 — `renata-meet-mcp` (puerto 8783) 🚧

Bot que entra al Meet y captura la reunión. Self-hosted, Playwright + Chromium.

### Pre-requisito manual (clave): sesión web sembrada

Google **bloquea logins headless "frescos"**. Hay que sembrar una vez un
`storage_state` (cookies) iniciando sesión como `renata@aroco.co` en un navegador
controlado, y mantener la sesión caliente. Mismo principio que el `storage_state`
del intel-mcp de StoneX. (Re-verificación periódica de Google = mantenimiento
ocasional.)

### Captura recomendada: subtítulos en vivo, no audio

Leer el DOM de los **captions** de Meet da el texto **con nombre de quien habla**
(diarización gratis) y evita montar audio virtual (PulseAudio) + Whisper.
Trade-off: los captions a veces pierden palabras y hay que fijar idioma. Whisper
queda como fallback de mayor fidelidad para la Fase 4.

### Flujo del bot

1. Abre el `meet_link`, apaga cámara y micrófono.
2. Entra ("Join now"; si no es host puede caer en sala de espera → alguien la admite).
3. (Recomendado) avisa por el chat: "Soy Renata, tomo notas de la reunión" — consentimiento.
4. Acumula los captions mientras dura la reunión.
5. Sale cuando la reunión termina / se queda sola; guarda la transcripción como artefacto.

Tool central prevista: `attend_meeting(url, max_minutes)` → transcripción.

### Riesgos a tener presentes

- Re-autenticación periódica de Google (mantenimiento de la sesión).
- Sala de espera si Renata no es host/invitada explícita.
- Concurrencia: reuniones solapadas = varios Chromium = más RAM/CPU.
- Automatizar Meet va técnicamente contra los ToS de Google (riesgo bajo, uso interno).

---

## Fase 3 — cron + skill `reunion-notetaker` ⏳

- **Cron de Renata** (cada ~5 min): llama `renata-gcalendar.upcoming_meetings`;
  si hay reunión con Meet por empezar, dispara `renata-meet.attend_meeting`.
- **Skill `reunion-notetaker`**: al terminar, toma la transcripción, genera
  **resumen + acciones** con el modelo de Renata y lo entrega vía
  `renata-gmail.send_message(use_html=True)`.
- Recordar el gotcha `enabled_toolsets` de los crons (ver [cronjobs.md](./cronjobs.md)).

## Fase 4 (opcional) ⏳

Guardar en Drive/Notion, transcripción de alta fidelidad con Whisper, y manejo de
reuniones solapadas.
