#!/usr/bin/env python3
"""Avisa cuando toca re-sembrar la sesión de Google del bot Meet de Renata.

Por qué existe: la sesión caduca a los 14 días JUSTOS del login interactivo
(tres intervalos consecutivos medidos: 4-ago→18-ago, 19-ago→2-sep,
4-sep→18-sep). El cron `Notetaker chequeo sesion` detecta la caducidad, pero
avisa cuando ya se perdieron reuniones. Este avisa ANTES.

Por qué no basta el mtime del storage_state: el bot lo reescribe tras cada
asistencia y tras cada `verify_session` con la sesión viva, así que su mtime
es "última vez que algo funcionó", no "cuándo se sembró".

Por qué la cookie SID: cambia en cada login interactivo y sobrevive a las
re-escrituras del bot, así que un cambio de SID ES un re-sembrado. Se guarda
solo su hash — el valor es una credencial de sesión.

Ojo: las expiraciones que traen las cookies NO sirven de reloj. La más corta
(COMPASS) va a ~9 días y se renueva sola; SID dice 399 días. El límite de 14
días es un challenge del lado de Google ("Demuestra que eres tú"), muy
probablemente por la IP de datacenter, y no está escrito en ningún lado del
archivo.

Modo no_agent: stdout vacío = silencio (no entrega). Cualquier texto se
entrega tal cual.
"""
import datetime
import hashlib
import json
import sys
from pathlib import Path

STORAGE = Path("/home/aroco/projects/data/renata-meet/storage_state.json")
STATE = Path("/home/aroco/projects/data/renata-meet/.reseed_clock.json")

# Avisar a los 11 días. Ojo con el truncado: `(now - desde).days` descarta las
# horas, así que un umbral de 12 con el reloj sembrado a mediodía no dispara
# hasta el día 13 por la mañana — un solo día antes de la caducidad. Con 11 el
# aviso cae en el día 11 o 12 según la hora del re-sembrado, o sea 2-3 días de
# margen sobre los 14 observados.
UMBRAL_DIAS = 11


def sid_hash(path: Path) -> str | None:
    """Hash del SID actual, o None si no se puede leer."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for c in data.get("cookies", []):
        if c.get("name") == "SID" and c.get("value"):
            return hashlib.sha256(c["value"].encode()).hexdigest()
    return None


def main() -> int:
    now = datetime.datetime.now(datetime.timezone.utc)

    if not STORAGE.exists():
        print(
            "⚠️ Renata (notetaker): no existe el storage_state del bot Meet\n"
            f"({STORAGE}). Renata no puede entrar a ninguna reunión."
        )
        return 0

    actual = sid_hash(STORAGE)
    if actual is None:
        print(
            "⚠️ Renata (notetaker): el storage_state del bot Meet no se puede "
            "leer o no trae la cookie SID. Revisar el archivo: Renata "
            "probablemente no pueda entrar a las reuniones."
        )
        return 0

    try:
        estado = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        estado = {}

    # SID nuevo (o primer arranque) = hubo re-sembrado: reiniciar el reloj y
    # callar. No hay nada que pedirle a nadie.
    if estado.get("sid_hash") != actual:
        STATE.write_text(
            json.dumps(
                {"sid_hash": actual, "desde": now.isoformat()},
                indent=2,
            ),
            encoding="utf-8",
        )
        return 0

    try:
        desde = datetime.datetime.fromisoformat(estado["desde"])
    except (KeyError, ValueError):
        # Estado corrupto: re-anclar en vez de avisar en falso.
        STATE.write_text(
            json.dumps({"sid_hash": actual, "desde": now.isoformat()}, indent=2),
            encoding="utf-8",
        )
        return 0

    dias = (now - desde).days
    if dias < UMBRAL_DIAS:
        return 0

    print(
        f"🔄 Renata: toca re-sembrar la sesión del bot Meet "
        f"(van {dias} días desde el último login).\n\n"
        "La sesión caduca a los 14 días justos, así que esto es preventivo: "
        "si esperas al aviso de caducidad ya se habrán perdido reuniones.\n\n"
        "Son ~5 min en una Mac:\n\n"
        "mkdir -p ~/reseed && cd ~/reseed\n"
        "scp aroco@100.89.179.48:/home/aroco/projects/repos/hermes-agent/"
        "scripts/reseed_renata.py .\n"
        "python3 -m venv venv && source venv/bin/activate\n"
        "pip install playwright && python3 -m playwright install chromium\n"
        "python3 reseed_renata.py\n"
        "scp ~/storage_state_renata.json aroco@100.89.179.48:"
        "/home/aroco/projects/data/renata-meet/storage_state.json\n\n"
        "El aviso se apaga solo en cuanto detecte el login nuevo."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
