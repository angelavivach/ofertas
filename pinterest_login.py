#!/usr/bin/env python3
"""Abre Pinterest en un navegador visible para que inicies sesión tú una sola vez.

La sesión se guarda en la carpeta pinterest_profile/, que no se sube a git
porque contiene cookies. Yo no veo ni guardo tu contraseña.
Uso: ~/.venv/bin/python pinterest_login.py
"""

import os

from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, "pinterest_profile")


def main():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            locale="es-ES",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        page.goto("https://es.pinterest.com/login/", wait_until="domcontentloaded")
        print("Inicia sesión en la ventana que se ha abierto.")
        input("Cuando veas tu página de inicio de Pinterest, pulsa Enter aquí para guardar la sesión... ")
        context.close()
    print(f"Sesión guardada en {PROFILE_DIR}")


if __name__ == "__main__":
    main()
