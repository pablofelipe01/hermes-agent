# Instrucciones — Despliegue de Hermes Agent en producción

Playbook reproducible basado en la instalación realizada el **2026-05-07** en este servidor.
Úsalo para desplegar Hermes en otro servidor con el mismo stack:

| Componente | Valor |
|------------|-------|
| OS objetivo | Ubuntu 24.04 LTS (otras distros funcionan pero los comandos asumen apt) |
| Hermes | v0.13.0+ desde `github.com/NousResearch/hermes-agent` |
| Modelo LLM | `anthropic/claude-sonnet-4.6` (cambiable) |
| Provider | OpenRouter |
| Gateway | Telegram bot |
| Despliegue | systemd system-level service (auto-arranca al boot) |

Tiempo estimado end-to-end: 30–45 min.
Costo estimado en OpenRouter para tests del despliegue: $0.05–$0.20 USD.

---

## 0. Prerequisitos

### En el servidor
- Ubuntu 24.04+ con acceso sudo
- Conectividad saliente HTTPS a `api.telegram.org` y `openrouter.ai`
- Paquetes del SO:
  ```bash
  sudo apt update
  sudo apt install -y python3.11 git curl build-essential ffmpeg sqlite3
  ```
- Asegurar que `~/.local/bin` está en `$PATH` (el installer lo añade a `~/.bashrc` si falta)

### Cuentas externas (preparar antes de empezar)
1. **OpenRouter API key** — crearla en https://openrouter.ai/keys (formato `sk-or-v1-...`)
2. **Bot de Telegram** — crear con [@BotFather](https://t.me/BotFather):
   - `/newbot` → escoger nombre visible → escoger username (debe terminar en `_bot`)
   - BotFather devuelve token con formato `123456789:ABCdef...` (~46 chars)
3. **Tu Telegram user ID numérico** — mandar `/start` a [@userinfobot](https://t.me/userinfobot); responde con un ID tipo `1234567890`

---

## Fase 1 — Instalación del binario

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

El installer:
- Clona el repo en `~/.hermes/hermes-agent/`
- Crea venv con `uv` y resuelve dependencias
- Instala wrapper en `~/.local/bin/hermes` (script bash de 5 líneas que invoca el venv)
- Genera defaults: `~/.hermes/SOUL.md` (personalidad), `~/.hermes/config.yaml` (template), etc.

### Verificación
```bash
hermes --version    # debe imprimir "Hermes Agent v0.X.Y"
which hermes        # debe ser ~/.local/bin/hermes
hermes doctor       # status inicial — algunos checks fallan hasta tener API key
```

NOTA: si invocas el installer desde una sesión sin TTY (Claude Code, Docker exec, ssh con `-T`), el installer detecta no-TTY y omite su wizard interactivo automáticamente. La configuración se completa en las fases 2-4 editando archivos directamente.

---

## Fase 2 — API key de OpenRouter

Editar `~/.hermes/.env` y añadir al final:
```
OPENROUTER_API_KEY=sk-or-v1-...tu_key...
```

Verificar permisos restrictivos:
```bash
ls -la ~/.hermes/.env   # debe ser -rw-------
chmod 600 ~/.hermes/.env  # si no lo está
```

---

## Fase 3 — Configuración del modelo y provider

Editar `~/.hermes/config.yaml`. Mínimo necesario:

```yaml
model:
  default: "anthropic/claude-sonnet-4.6"   # o claude-opus-4-7, etc.
  provider: "openrouter"                   # explícito > "auto"
```

Validar:
```bash
hermes doctor
# debe reportar: "OpenRouter API connectivity: PASS"
```

NOTA sobre telemetría: Hermes NO tiene telemetría externa (no usa PostHog, Mixpanel, Sentry, ni similares). Las referencias a "telemetry" en el código son a logs operacionales locales. No hay nada que deshabilitar para conseguir "zero tracking".

---

## Fase 4 — Smoke tests funcionales

Sin TTY no se puede usar el CLI interactivo. Usar el modo one-shot con `hermes -z`:

```bash
# Test 1 — modelo correcto
hermes -z "¿Qué modelo estás usando? Responde en una línea."
# Esperado: "Estoy usando anthropic/claude-sonnet-4.6 vía OpenRouter."

# Test 2 — escribir memoria persistente
hermes -z "Recuerda este dato sobre el servidor: desplegado el $(date +%F)."
# Esperado: confirmación de que guardó

# Test 3 — leer memoria desde una sesión NUEVA
hermes -z "¿Qué sabes sobre este servidor?"
# Esperado: debe regurgitar la fecha que guardó en test 2 → confirma persistencia cross-session
```

Verificar archivos creados:
```bash
ls -la ~/.hermes/memories/   # debe haber al menos USER.md tras test 2
```

---

## Fase 5 — Configurar Telegram gateway

### 5.1 Añadir token y allowlist a `~/.hermes/.env`
```
# Telegram gateway
TELEGRAM_BOT_TOKEN=TU_TOKEN_DE_BOTFATHER
TELEGRAM_ALLOWED_USERS=TU_USER_ID_NUMERICO
```

Para múltiples usuarios autorizados, separar por coma sin espacios: `TELEGRAM_ALLOWED_USERS=111,222,333`

### 5.2 Probar en foreground antes de instalar como service
```bash
nohup hermes gateway run > /tmp/hermes-gateway.log 2>&1 &
sleep 8
tail -20 ~/.hermes/logs/gateway.log
```

Buscar en el log:
- `Connecting to telegram...`
- `Connected to Telegram (polling mode)`
- `telegram connected`
- `Gateway running with 1 platform(s)`

### 5.3 Identificar el username del bot (para encontrarlo en Telegram)
```bash
TOKEN=$(grep TELEGRAM_BOT_TOKEN ~/.hermes/.env | cut -d= -f2)
curl -s "https://api.telegram.org/bot$TOKEN/getMe" | python3 -m json.tool
# campo .result.username — algo tipo "Aroco_Agent_bot"
```

### 5.4 Test end-to-end
Mandar mensaje al bot desde Telegram (`t.me/<username>`). Verificar respuesta y revisar log:
```bash
grep -E "inbound|response ready" ~/.hermes/logs/gateway.log | tail -5
```

### 5.5 Detener el foreground antes de pasar a systemd
```bash
PID=$(pgrep -f "hermes.*gateway run")
kill -TERM $PID
sleep 3
kill -0 $PID 2>/dev/null && echo "TODAVÍA VIVO" || echo "OK detenido"
```

---

## Fase 6 — Instalación como systemd system-level service

```bash
# CRÍTICO: usar path ABSOLUTO al binario porque sudo NO hereda el PATH del usuario
sudo /home/$USER/.local/bin/hermes gateway install --system --run-as-user $USER

# Arrancar
sudo systemctl start hermes-gateway

# Verificar
sudo systemctl status hermes-gateway --no-pager
sudo journalctl -u hermes-gateway -n 30 --no-pager
```

El installer crea:
- Unit file en `/etc/systemd/system/hermes-gateway.service`
- Symlink en `multi-user.target.wants/` → service queda **enabled at boot** sin acción adicional
- El proceso corre como el usuario que pasaste con `--run-as-user` (NO como root) — buena práctica de mínimos privilegios

### Verificación final
```bash
sudo systemctl is-active hermes-gateway     # debe imprimir: active
sudo systemctl is-enabled hermes-gateway    # debe imprimir: enabled
```

Mandar un último mensaje desde Telegram para confirmar que la instancia gestionada por systemd también responde.

---

## Comandos operativos del día a día

| Acción | Comando |
|--------|---------|
| Estado del service | `sudo systemctl status hermes-gateway` |
| Restart (tras editar `config.yaml` o `.env`) | `sudo systemctl restart hermes-gateway` |
| Stop (sin que systemd lo reanime) | `sudo systemctl stop hermes-gateway` |
| Logs en tiempo real (systemd) | `sudo journalctl -u hermes-gateway -f` |
| Logs detallados de Hermes | `tail -f ~/.hermes/logs/gateway.log` |
| Solo errores | `tail -f ~/.hermes/logs/errors.log` |
| Consumo de tokens y costo | https://openrouter.ai/activity |
| Actualizar Hermes a última versión | `hermes update` |
| Cambiar modelo | editar `model.default` en `~/.hermes/config.yaml` + `systemctl restart` |
| Ampliar allowlist | añadir IDs separados por coma a `TELEGRAM_ALLOWED_USERS` en `.env` + restart |

---

## Pitfalls — lecciones aprendidas durante el despliegue

1. **Sin TTY, varios comandos fallan silenciosamente.** Estos REQUIEREN terminal interactivo y no funcionan en sesiones Claude Code, Docker exec sin `-it`, scripts CI, o ssh con `-T`:
   - `hermes` (CLI interactivo)
   - `hermes setup`
   - `hermes gateway setup`
   - `hermes login` (OAuth)

   Workarounds: editar `.env`/`config.yaml` a mano (Fases 2-3, 5.1) y usar `hermes -z PROMPT` para tests one-shot (Fase 4).

2. **`sudo` no hereda el PATH del usuario.** `sudo hermes ...` falla con "command not found". Solución: path absoluto `sudo /home/$USER/.local/bin/hermes ...`. Aplica especialmente a Fase 6.

3. **Dos archivos `.env` distintos coexisten en este servidor:**
   - `~/.env` — env vars generales, sourced por `~/.bashrc` línea ~119 (`set -a; source ~/.env; set +a`)
   - `~/.hermes/.env` — exclusivo de Hermes, leído directamente por el binario al arrancar
   - NO mezclar — cada uno tiene su propósito. La key de OpenRouter y los vars de Telegram van en `~/.hermes/.env`, no en `~/.env`.

4. **Exit code 1 al recibir SIGTERM es intencional.** El gateway sale con código 1 explícitamente para que systemd `Restart=on-failure` pueda reanimarlo si fue un crash. Un `systemctl stop` graceful no dispara restart porque systemd sabe que fue ordenado.

5. **Logs duplicados, propósitos distintos:**
   - `journalctl -u hermes-gateway` — stdout/stderr del proceso (lo que systemd captura). Bueno para debug rápido de arranque y errores fatales.
   - `~/.hermes/logs/gateway.log` — logs estructurados de Hermes (inbound messages, responses, batches, conexiones). Bueno para forensic detallado.
   - `~/.hermes/logs/errors.log` — solo warnings/errors. Si está vacío, todo bien. Ojo: el shutdown diagnostic puede meter falsos positivos (procesos con la palabra "hermes" en su cmdline son listados aunque no sean Hermes).

6. **Privacy mode del bot de Telegram.** Por default está activado en BotFather → el bot solo ve mensajes que lo mencionan en grupos. Si quieres que lea TODO en grupos, desactivar en BotFather: `/setprivacy → @<tu_bot> → Disable`. Para uso solo-DM (lo recomendado), dejar default.

7. **El installer se actualiza vía `hermes update`** — pulls del repo upstream. Tras un update, casi siempre conviene `sudo systemctl restart hermes-gateway` para que cargue la versión nueva.

---

## Seguridad

- **Permisos de archivos sensibles:**
  - `~/.hermes/.env` debe ser `chmod 600` (verificar tras cualquier edit)
  - `~/.env` general también `chmod 600`

- **Rotación de secretos:**
  - Token de Telegram filtrado → `/revoke` en BotFather, reemplaza el viejo, edita `.env`, restart service
  - OpenRouter API key filtrada → regenerar en https://openrouter.ai/keys, edita `.env`, restart service

- **Allowlist:** arrancar con el mínimo (solo tu user ID) y expandir caso por caso. Cualquier mensaje de un user fuera de la allowlist es rechazado por el gateway antes de llegar al modelo (no consume tokens).

- **Secret redaction interno de Hermes:** ENABLED por default — escanea outputs de tools y respuestas del modelo antes de enviarlos por el chat, para evitar exfiltrar credenciales que aparezcan accidentalmente en logs/contextos.

- **El proceso del service corre como usuario sin privilegios** (configurado con `--run-as-user`). NO se ejecuta como root.

---

## Apéndice — Estructura de `~/.hermes/`

```
~/.hermes/
├── .env                    # secrets (chmod 600)
├── config.yaml             # configuración no-secreta
├── SOUL.md                 # personalidad del agente
├── auth.json               # credenciales OAuth si aplica
├── hermes-agent/           # checkout del repo (gestionado por hermes update)
│   ├── venv/               # virtualenv con dependencias
│   ├── scripts/install.sh  # installer oficial
│   └── ...                 # código fuente
├── logs/
│   ├── gateway.log         # logs estructurados
│   ├── agent.log           # logs por sesión
│   └── errors.log          # warnings/errors
├── memories/               # memoria persistente cross-session (USER.md, etc.)
├── sessions/               # historial de sesiones
├── skills/                 # 89 skills built-in
├── state.db                # SQLite con estado del agente
├── kanban.db               # SQLite con tasks/kanban
├── gateway_state.json      # estado actual del gateway (lo escribe el proceso)
├── gateway.pid             # PID del gateway corriendo
├── gateway.lock            # lock de instancia única
└── channel_directory.json  # canales/usuarios autorizados
```
