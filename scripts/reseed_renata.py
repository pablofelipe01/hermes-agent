"""Re-siembra la sesión de Google del bot Meet de Renata.

Se corre en una Mac (o cualquier equipo con pantalla), NO en el servidor:
Google bloquea el login headless, así que hay que autenticarse a mano una vez
y subir las cookies resultantes.

    python3 -m venv venv && source venv/bin/activate
    pip install playwright && python3 -m playwright install chromium
    python3 reseed_renata.py

Abre un navegador limpio, esperas a estar DENTRO como renata@aroco.co (con 2FA)
y pulsas Enter EN LA TERMINAL — sin cerrar la ventana del navegador, que el
script la necesita viva para leer las cookies. Luego:

    scp ~/storage_state_renata.json \
        aroco@TU_SERVER:/home/aroco/projects/data/renata-meet/storage_state.json

Sustituye TU_SERVER por la IP o el hostname reales. Si dejas un placeholder
entre <angulos>, zsh lo interpreta como redirección y falla con
"no such file or directory". Usa la IP de la VPN si el SSH del servidor solo
escucha por ahí.

Después de subirlo, verificar SIEMPRE con `verify_session` del renata-meet-mcp
antes de darlo por hecho: debe responder `logged_in: true`. Un scp que no se
ejecutó se parece mucho a uno que sí.

El archivo lleva las cookies de sesión de Renata: es SENSIBLE. No commitearlo,
no compartirlo, borrarlo de la Mac cuando termines.
"""

import os

from playwright.sync_api import sync_playwright

# Absoluto a propósito: con una ruta relativa el archivo aparece en el cwd desde
# el que se lanzó el script, y se pierde de vista.
OUT = os.path.expanduser("~/storage_state_renata.json")

with sync_playwright() as p:
    # El Chrome del sistema pasa el login de Google con menos fricción que el
    # Chromium de Playwright, al que a veces responde "este navegador puede no
    # ser seguro". Si no está instalado, seguimos con Chromium.
    try:
        b = p.chromium.launch(headless=False, channel="chrome")
        print("[i] Usando Chrome del sistema")
    except Exception as e:
        print("[i] Chrome no disponible, usando Chromium de Playwright:", e)
        b = p.chromium.launch(headless=False)

    c = b.new_context(locale="es-ES", viewport={"width": 1280, "height": 800})
    pg = c.new_page()
    pg.goto("https://accounts.google.com/", wait_until="load")

    input(">>> Inicia sesion como renata@aroco.co; cuando estes DENTRO pulsa Enter "
          "(NO cierres la ventana)...")

    # Guardar ANTES de comprobar Meet: si la comprobación falla, al menos queda
    # un archivo que inspeccionar en vez de nada.
    c.storage_state(path=OUT)
    print("[OK] Guardado:", OUT)

    try:
        pg.goto("https://meet.google.com/", wait_until="load")
        pg.wait_for_timeout(4000)
        print("[i] URL final en Meet:", pg.url)
        if "accounts.google.com" in pg.url or "signin" in pg.url:
            print("[!] Meet rebota al login -> la sesion NO quedo buena, reintenta")
        else:
            c.storage_state(path=OUT)  # re-guarda ya con las cookies de Meet
            print("[OK] Sesion verificada y re-guardada")
    except Exception as e:
        print("[!] No se pudo comprobar Meet:", e)

    b.close()
