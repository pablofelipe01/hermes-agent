# Replicar un agente-cliente en el mismo servidor

Cómo levantar un **segundo (o tercer, cuarto…) agente Hermes** en un servidor que
ya tiene Hermes corriendo, aislado del resto. Es el patrón usado para **Renata**
sobre la instalación base de AROCO: cada cliente es un Hermes nativo
independiente que **no comparte** config, sesiones, memoria ni estado con los demás.

> Si vas a desplegar Hermes desde cero en un servidor limpio, usa
> [`instrucciones.md`](./instrucciones.md). Este documento asume que **ya hay** al
> menos un Hermes instalado y funcionando, y que quieres añadir otro al lado.

Tiempo estimado: 20–30 min (sin contar los MCPs propios del cliente).

---

## La idea en una variable: `HERMES_HOME`

Hermes guarda **todo** su estado (config, persona, skills, sesiones, memoria,
crons, DBs) bajo un directorio "home". Por defecto es `~/.hermes/`. Apuntando la
variable de entorno `HERMES_HOME` a otra carpeta, el mismo binario corre como un
agente completamente distinto.

```
~/.hermes/            → agente base (AROCO)        HERMES_HOME=~/.hermes
~/.hermes-renata/     → agente cliente 2 (Renata)  HERMES_HOME=~/.hermes-renata
```

Cada uno tiene su propio service systemd, su propio bot de Telegram, su propia
key de inferencia (facturación separada) y su propio bloque de puertos para MCPs.
El único acoplamiento es que comparten el host y el usuario `aroco`.

```
┌───────────────────────────── servidor (usuario aroco) ──────────────────────────────┐
│                                                                                     │
│  systemd: hermes-gateway                    systemd: hermes-renata-gateway          │
│      │                                            │                                 │
│  HERMES_HOME=~/.hermes                      HERMES_HOME=~/.hermes-renata            │
│      │                                            │                                 │
│  config.yaml + .env + SOUL.md               (idem, propios)                         │
│      │                                            │                                 │
│  MCPs 8765–8773  (Docker, 127.0.0.1)        MCPs 8780–8785  (Docker)                │
│                                                                                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Anatomía de un agente-cliente

```
~/.hermes-<cliente>/
├── hermes-agent/          # checkout del código + venv propio (aislado)
├── config.yaml            # ⭐ config del agente (modelo, timezone, mcp_servers)
├── .env                   # ⭐ secretos: key inferencia + token Telegram + users
├── SOUL.md                # ⭐ persona + "tarea estrella" del cliente
├── hermes-<cliente>-gateway.service   # unit systemd (se enlaza a /etc/systemd)
├── skills/                # skills heredadas del catálogo + las propias
├── memories/  sessions/  logs/  cron/  state.db  kanban.db   # estado (autogenerado)
```

Las **tres piezas que editas por cliente** son `config.yaml`, `.env` y `SOUL.md`.
Todo lo demás se copia o se autogenera.

---

## 0. Prerequisitos

Antes de empezar, ten listo (ver detalle en [`instrucciones.md` §0](./instrucciones.md)):

1. **Key de inferencia propia del cliente** (OpenRouter `sk-or-v1-...`).
   Usa una key separada por cliente → facturación independiente.
2. **Bot de Telegram propio** vía [@BotFather](https://t.me/BotFather) → `/newbot`.
   No reutilices el bot de otro agente.
3. **User IDs de Telegram autorizados** (vía [@userinfobot](https://t.me/userinfobot)).
4. Decide el **bloque de puertos** para los MCPs del cliente (ver tabla abajo).

### Mapa de puertos del servidor (mantener actualizado)

| Agente  | Bloque de puertos MCP |
|---------|-----------------------|
| AROCO   | 8765–8773             |
| Renata  | 8780–8785             |
| *nuevo* | siguiente bloque libre (ej. 8790+) |

Asigna un bloque **nuevo y disjunto** a cada cliente para que nunca colisionen.

---

## 1. Crear el home del agente

Copia el código de un agente existente (trae el `venv` ya armado), o clónalo
limpio desde upstream y crea el venv. La vía rápida reutilizando un agente sano:

```bash
CLIENTE=acme    # ← nombre corto del cliente, en minúsculas

# 1. Carpeta home + copia del código (incluye venv)
cp -a ~/.hermes-renata/hermes-agent ~/.hermes-$CLIENTE/hermes-agent
mkdir -p ~/.hermes-$CLIENTE
```

> Si prefieres un checkout limpio: `git clone https://github.com/NousResearch/hermes-agent`
> dentro de `~/.hermes-$CLIENTE/hermes-agent` y recrea el venv siguiendo
> [`instrucciones.md`](./instrucciones.md). El `cp -a` es más rápido pero arrastra
> la versión exacta del agente origen.

No copies `config.yaml`, `.env`, `SOUL.md`, `sessions/`, `memories/`, `state.db`
ni `logs/` del otro cliente — esos son específicos y los creas a continuación.

---

## 2. Las tres piezas por cliente

### 2a. `.env` (chmod 600)

```bash
cat > ~/.hermes-$CLIENTE/.env <<'EOF'
# Hermes_<Cliente> environment file. Distinto de los demás ~/.hermes*/.env.
OPENROUTER_API_KEY=sk-or-v1-...        # key PROPIA del cliente
TELEGRAM_BOT_TOKEN=123456789:ABC...    # bot propio (@BotFather)
TELEGRAM_ALLOWED_USERS=11111111,22222222   # sin esto el gateway NO arranca
EOF
chmod 600 ~/.hermes-$CLIENTE/.env
```

### 2b. `config.yaml`

Copia el de un agente existente y cambia solo lo relevante. Campos que importan:

```yaml
model:
  default: anthropic/claude-sonnet-4.6
  provider: openrouter
  base_url: https://openrouter.ai/api/v1

timezone: 'America/Bogota'

telegram:
  allowed_chats: ''          # el control fino de acceso va en .env (ALLOWED_USERS)

mcp_servers:                 # ⭐ engancha aquí las tools del cliente (paso 5)
  <cliente>-correo:
    url: http://localhost:8790/mcp
  <cliente>-datos:
    url: http://localhost:8791/mcp
```

El resto (compression, memory, curator, personalities, etc.) déjalo con los
valores por defecto del agente que copiaste.

### 2c. `SOUL.md` — la persona y el flujo estrella

Es el archivo que da identidad al agente. Estructura que funciona bien
(patrón Renata):

1. **Quién es**: nombre, rol, correo, tono, canal (Telegram), zona horaria.
2. **Tarea estrella**: una palabra clave (p. ej. *"el reporte"*) dispara un flujo
   de extremo a extremo **sin pedir confirmaciones intermedias**:
   `obtener datos de un MCP → analizar → redactar → entregar por otro MCP → responder una sola línea`.
3. **Reglas**: no inventar datos, qué va por correo vs. por Telegram, fuentes
   permitidas, etc.

El corazón replicable es ese pipeline: **MCP de datos → análisis → MCP de entrega
→ confirmación**. Cambian las fuentes y el dominio; la forma se mantiene.

---

## 3. El service systemd

Copia el unit de otro cliente y cambia **solo el nombre y `HERMES_HOME`**:

```ini
# ~/.hermes-<cliente>/hermes-<cliente>-gateway.service
[Unit]
Description=Hermes_<Cliente> Agent Gateway - Messaging Platform Integration
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=aroco
Group=aroco
ExecStart=/home/aroco/.hermes-<cliente>/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --replace
WorkingDirectory=/home/aroco/.hermes-<cliente>/hermes-agent
Environment="HOME=/home/aroco"
Environment="USER=aroco"
Environment="LOGNAME=aroco"
Environment="VIRTUAL_ENV=/home/aroco/.hermes-<cliente>/hermes-agent/venv"
Environment="HERMES_HOME=/home/aroco/.hermes-<cliente>"   # ⭐ la clave del aislamiento
Restart=always
RestartSec=60
RestartMaxDelaySec=300
RestartSteps=5
RestartForceExitStatus=75
KillMode=mixed
KillSignal=SIGTERM
ExecReload=/bin/kill -USR1 $MAINPID
TimeoutStopSec=210
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Instálalo y arráncalo:

```bash
sudo ln -s ~/.hermes-$CLIENTE/hermes-$CLIENTE-gateway.service \
           /etc/systemd/system/hermes-$CLIENTE-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-$CLIENTE-gateway
systemctl status hermes-$CLIENTE-gateway --no-pager
```

> `--replace` en `ExecStart` permite reemplazar un gateway anterior limpiamente al
> reiniciar. `HERMES_HOME` es lo único que separa este agente de los demás: apunta
> a su carpeta y así no comparten config, sesiones ni estado.

---

## 4. Verificar el agente base

Antes de añadir MCPs, confirma que el agente arranca y responde por Telegram:

- `journalctl -u hermes-$CLIENTE-gateway -f` no muestra errores de auth/token.
- Manda `/start` al bot del cliente desde un usuario autorizado y responde.
- Un usuario **no** autorizado debe ser ignorado.

Si el bot no responde, casi siempre es `TELEGRAM_BOT_TOKEN` o
`TELEGRAM_ALLOWED_USERS` en el `.env`.

---

## 5. MCPs propios del cliente (las herramientas)

Cada capacidad externa = **un contenedor Docker en su propio puerto**. El patrón
completo (server FastMCP, Dockerfile, docker-compose, registro y deploy) está en
[`ejemplos.md`](./ejemplos.md). Resumen aplicado a un agente-cliente:

```
~/projects/agents/<cliente>-<algo>-mcp/
├── server.py            # FastMCP + @mcp.tool()
├── requirements.txt
├── Dockerfile
└── docker-compose.yml   # ports: "127.0.0.1:<puerto>:<puerto>"
```

`docker-compose.yml` mínimo:

```yaml
services:
  <cliente>-correo-mcp:
    build: .
    container_name: <cliente>-correo-mcp
    restart: unless-stopped
    ports:
      - "127.0.0.1:8790:8790"          # solo localhost; Hermes lo alcanza por localhost
    environment:
      MCP_TRANSPORT: streamable-http
      MCP_PORT: 8790
      TZ: America/Bogota
    volumes:
      - /home/aroco/projects/data/<cliente>:/data:ro   # secretos/tokens fuera de git
```

Levántalo y regístralo en el `config.yaml` del cliente (`mcp_servers`, paso 2b),
luego recarga Hermes:

```bash
cd ~/projects/agents/<cliente>-correo-mcp && docker compose up -d --build
sudo systemctl reload hermes-$CLIENTE-gateway   # o restart, para re-descubrir MCPs
```

> Los datos sensibles del cliente (tokens OAuth, credenciales) viven en
> `~/projects/data/<cliente>/` y **nunca** se commitean. El MCP los monta como
> volumen `:ro`.

---

## Checklist de réplica

- [ ] Bloque de puertos asignado y anotado en la tabla de arriba.
- [ ] `~/.hermes-<cliente>/hermes-agent/` con su venv propio.
- [ ] `.env` (chmod 600): key propia + token Telegram propio + ALLOWED_USERS.
- [ ] `config.yaml`: modelo, `timezone`, `mcp_servers`.
- [ ] `SOUL.md`: persona + tarea estrella.
- [ ] Service con `HERMES_HOME` correcto, `enable --now`, status OK.
- [ ] Bot responde por Telegram a usuario autorizado; ignora a no autorizados.
- [ ] MCPs del cliente arriba (Docker), registrados, Hermes recargado.
- [ ] Datos sensibles en `~/projects/data/<cliente>/`, fuera de git.

---

## Qué NO compartir entre agentes

| Recurso | Por qué separado |
|---------|------------------|
| Key de inferencia | Facturación por cliente |
| Bot de Telegram | Identidad y control de acceso propios |
| Puertos MCP | Evitar colisiones en el host |
| `HERMES_HOME` | Aísla config, sesiones, memoria y estado |
| `~/projects/data/<cliente>/` | Secretos del cliente, nunca cruzados |
