"""Re-siembra la sesión de Google del bot Meet de Renata.

Se corre en una Mac (o cualquier equipo con pantalla), NO en el servidor:
Google bloquea el login headless, así que hay que autenticarse a mano una vez
y subir las cookies resultantes.

    python3 -m venv venv && source venv/bin/activate
    pip install playwright && python3 -m playwright install chromium
    python3 reseed_renata.py

Abre un Chromium limpio, esperas a estar DENTRO como renata@aroco.co (con 2FA),
pulsas Enter, y el script comprueba Meet y guarda el archivo SOLO si no rebotó
al login. Luego:

    scp storage_state_renata.json \
        aroco@<server>:/home/aroco/projects/data/renata-meet/storage_state.json

El archivo lleva las cookies de sesión de Renata: es SENSIBLE. No commitearlo,
no compartirlo, borrarlo de la Mac cuando termines.
"""

from playwright.sync_api import sync_playwright

OUT = "storage_state_renata.json"

with sync_playwright() as p:
    b = p.chromium.launch(headless=False)
    c = b.new_context(locale="es-ES", viewport={"width": 1280, "height": 800})
    pg = c.new_page()
    pg.goto("https://accounts.google.com/", wait_until="load")

    input(">>> Inicia sesion como renata@aroco.co; cuando estes DENTRO pulsa Enter...")

    pg.goto("https://meet.google.com/", wait_until="load")
    pg.wait_for_timeout(3000)

    if "accounts.google.com" in pg.url or "signin" in pg.url:
        print("[X] Sigue en login, NO se guardo. Reintenta.")
    else:
        c.storage_state(path=OUT)
        print("[OK] Guardado:", OUT, "| Meet:", pg.url)

    b.close()
