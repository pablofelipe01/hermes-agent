# Migrar los agentes de Telegram a Signal

Estado: **preparado, sin activar** (2026-08-10). Falta el número de teléfono.

## Por qué

Los chats de bots de Telegram **no son cifrados extremo a extremo**: son *cloud
chats* y Telegram puede leerlos en sus servidores. Por Renata circulan
transcripciones íntegras de los comités de AROCO y análisis de posiciones de
cacao. En Signal ese contenido viaja cifrado E2E.

No es un cambio cosmético de canal: es sacar el contenido más sensible de la
operación de un canal que un tercero puede leer.

## Cómo funciona (y en qué se diferencia de Telegram)

Signal **no tiene API de bots**. Hermes no habla con un servicio de bots: maneja
una **cuenta real de Signal** a través de `signal-cli`, que corre como daemon
JSON-RPC en el servidor. Consecuencias prácticas:

- El agente necesita **su propio número de teléfono**, como una persona.
- Le hablas **como a un contacto normal**, no a un `@bot`.
- Hay un proceso más que mantener (`signal-cli`), aparte del gateway.

```
Signal (app de Pablo) ──E2E──► número de Renata
                                  │
                        signal-cli daemon  (127.0.0.1:8790, JSON-RPC/SSE)
                                  │
                        hermes-renata-gateway  (adaptador gateway/platforms/signal.py)
```

## Ya hecho (2026-08-10)

- `signal-cli` **0.14.7 build nativo** en `/opt/signal-cli/` — el build nativo no
  necesita Java, así que **no se instaló ningún JRE** en el servidor.
- Directorio de identidad: `/home/aroco/projects/data/renata-signal/`, `chmod 700`.
- Plantilla de servicio: `~/.hermes-renata/signal-cli-renata.service.template`
  (sin instalar en `/etc/systemd/system/`).
- Puerto reservado: **8790** (los MCP de Renata ocupan 8780-8785).

Nada de esto toca a Renata ni a Hermes en marcha.

## Falta: el número

Signal exige verificación por SMS o llamada de voz. **SIM prepago dedicada** es
la opción elegida: el agente tiene identidad propia y no arrastra la cuenta de
ninguna persona si mañana se apaga.

> ⚠️ Evitar números VoIP (Twilio y similares): Signal rechaza muchos de esos
> rangos y la verificación falla sin explicación clara.

## Activación (cuando exista el número, ~15 min)

```bash
NUM=+57XXXXXXXXXX
CFG=/home/aroco/projects/data/renata-signal

# 1. Registrar. Suele pedir captcha: abrir
#    https://signalcaptchas.org/registration/generate.html
#    y copiar el token del enlace signalcaptcha:// que devuelve.
/opt/signal-cli/signal-cli --config $CFG -a $NUM register --captcha <TOKEN>

# 2. Verificar con el código que llega por SMS al chip
/opt/signal-cli/signal-cli --config $CFG -a $NUM verify 123456

# 3. Ponerle nombre y foto (lo que ve quien le escriba)
/opt/signal-cli/signal-cli --config $CFG -a $NUM updateProfile --given-name "Renata"

# 4. Instalar el servicio desde la plantilla
sed 's/NUMERO_AQUI/'"$NUM"'/' ~/.hermes-renata/signal-cli-renata.service.template \
  | sudo tee /etc/systemd/system/signal-cli-renata.service
sudo systemctl daemon-reload && sudo systemctl enable --now signal-cli-renata
systemctl status signal-cli-renata --no-pager
```

Luego, en `~/.hermes-renata/.env`:

```bash
SIGNAL_HTTP_URL=http://127.0.0.1:8790
SIGNAL_ACCOUNT=+57XXXXXXXXXX
SIGNAL_ALLOWED_USERS=+57<PABLO>,+57<ALVARO>     # ← ver aviso de abajo
```

Y reiniciar: `sudo systemctl restart hermes-renata-gateway`. Hermes habilita la
plataforma solo si `SIGNAL_HTTP_URL` **y** `SIGNAL_ACCOUNT` están presentes
(`gateway/config.py`), así que no hay que tocar `config.yaml`.

## ⚠️ `SIGNAL_ALLOWED_USERS` por defecto es `*` — abierto

```python
# gateway/platforms/signal.py
dm_allowed_str = os.getenv("SIGNAL_ALLOWED_USERS", "*")
```

**Si no se configura, cualquiera que conozca el número puede darle órdenes al
agente.** En Telegram la lista blanca (`TELEGRAM_ALLOWED_USERS`) ya está puesta y
es fácil suponer que el comportamiento se hereda: no se hereda, y el default es
el inseguro. Poner la lista **antes** del primer arranque, no después.

Mismo criterio para el daemon: escucha en `127.0.0.1` y **no tiene autenticación
propia** — quien alcance el puerto 8790 controla la cuenta de Signal del agente.
Nunca exponerlo por el túnel de cloudflared (ver `patrones-operacionales.md`).

## Crons

`signal` es plataforma válida de entrega (`_KNOWN_DELIVERY_PLATFORMS` en
`cron/scheduler.py`), así que basta cambiar el `deliver` de cada job:

```
telegram:<ID_PABLO>,telegram:<ID_ALVARO>   →   signal:+57<PABLO>,signal:+57<ALVARO>
```

Jobs de Renata afectados: `Notetaker resumen` (`9938e112e700`), `Notetaker
chequeo sesion` (`617c04658aaf`), `Barchart chequeo sesion` (`ad5af3d40798`).

## Orden recomendado

1. **Renata primero** — es la que mueve el contenido más sensible, y es el
   agente más pequeño para probar.
2. Convivencia: dejar Telegram activo unos días. Las dos plataformas pueden
   estar habilitadas a la vez; se migran los crons uno a uno y se apaga Telegram
   cuando Signal lleve una semana sin sobresaltos.
3. **Hermes después**, con su propio número (no compartir la cuenta entre
   agentes: los mensajes de los dos llegarían al mismo hilo).

## Pendiente de verificar en producción

- Rate limits: el adaptador trae `signal_rate_limit.py` con pacing por lotes, lo
  que sugiere que upstream chocó con límites al mandar muchos adjuntos. Los
  informes de Renata llevan PDFs; vigilar los primeros envíos.
- Renovación: la sesión de `signal-cli` no caduca como la cookie de Google, pero
  el `signal-cli` sí requiere actualizaciones periódicas (Signal fuerza versiones
  mínimas de cliente y rechaza las viejas).
