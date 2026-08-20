#!/usr/bin/env bash
#
# Manda un mensaje a un contacto HACIÉNDOLE DAR EL TURNO AL AGENTE, de modo que
# el mensaje quede en el contexto de esa conversación.
#
#   ./avisar_por_agente.sh <hermes_home> <platform> <chat_id> "<texto>"
#
# Ejemplo (Renata avisándole a Álvaro por Signal):
#
#   ./avisar_por_agente.sh ~/.hermes-renata signal +573175099523 \
#       "Ya puedo darte precios intradía de cacao, no solo cierres diarios."
#
# ---------------------------------------------------------------------------
# POR QUÉ NO MANDARLO A PELO
#
# Lo directo es hablarle al daemon de signal-cli por JSON-RPC:
#
#   curl -s -X POST http://127.0.0.1:8790/api/v1/rpc -H 'Content-Type: application/json' \
#     -d '{"jsonrpc":"2.0","id":"1","method":"send",
#          "params":{"recipient":["+57..."],"message":"..."}}'
#
# Entrega bien y no toca el daemon (no hay que pararlo, no hay pelea por el lock
# de la cuenta como con el signal-cli CLI). Pero el mensaje sale POR FUERA del
# agente: no queda en su sesión. Cuando el contacto responda "listo, gracias",
# el agente lee esa respuesta sin tener ni idea de a qué se refiere.
#
# Este script hace lo otro: resume la sesión real de esa conversación y le pide
# al agente que sea ÉL quien mande el mensaje con su tool `send_message`. El
# turno queda grabado, así que la respuesta del contacto aterriza con contexto.
#
# ---------------------------------------------------------------------------
# PRECONDICIÓN — que la conversación esté quieta
#
# Si el gateway tiene el agente de ese chat cacheado en memoria y el contacto
# escribe mientras este script corre, hay dos escritores sobre la misma sesión y
# el último gana. El gateway desaloja los agentes ociosos (~1h; se ve como
# "Agent cache idle-TTL evict" en gateway.log), así que basta con no usarlo
# encima de una conversación viva. Para un aviso que sale de una charla de hace
# rato, no hay problema.
#
set -euo pipefail

HERMES_HOME_ARG="${1:?falta hermes_home, ej. ~/.hermes-renata}"
PLATFORM="${2:?falta platform, ej. signal}"
CHAT_ID="${3:?falta chat_id, ej. +573175099523}"
MENSAJE="${4:?falta el texto del mensaje}"

export HERMES_HOME="$(cd "${HERMES_HOME_ARG/#\~/$HOME}" && pwd)"
PY="$HERMES_HOME/hermes-agent/venv/bin/python"
SESSIONS="$HERMES_HOME/sessions/sessions.json"

# El session_id NO es estable: cambia cuando la sesión anterior expira o se
# finaliza. Por eso se resuelve por clave de canal en cada corrida, en vez de
# hardcodearlo.
SID="$("$PY" - "$SESSIONS" "$PLATFORM" "$CHAT_ID" <<'PYEOF'
import json, sys
sessions_path, platform, chat_id = sys.argv[1:4]
key = f"agent:main:{platform}:dm:{chat_id}"
data = json.load(open(sessions_path))
entry = data.get(key)
if not entry:
    print(f"No hay sesión para {key}. Canales conocidos:", file=sys.stderr)
    for k in data:
        print(f"  {k}", file=sys.stderr)
    sys.exit(1)
print(entry["session_id"])
PYEOF
)"

echo "Sesión: $SID  ($PLATFORM:$CHAT_ID)" >&2

# -t messaging: sin el toolset explícito el agente no tiene send_message y se
# inventa que lo mandó. Mismo gotcha que en cronjobs.md (`enabled_toolsets`).
exec "$PY" -m hermes_cli.main chat -Q -r "$SID" -t messaging -q \
  "Mandá este mensaje TAL CUAL, sin agregar ni quitar nada, usando send_message
   con target '${PLATFORM}:${CHAT_ID}'. No respondas nada más que la
   confirmación del envío.

   Mensaje:
   ${MENSAJE}"
