#!/usr/bin/env python3
"""Autoriza la app de Pinterest una sola vez y guarda el token en pinterest_token.json.

Necesita las variables de entorno PINTEREST_APP_ID y PINTEREST_APP_SECRET.
Uso: ~/.venv/bin/python pinterest_auth.py
"""

import base64
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, "pinterest_token.json")
REDIRECT_URI = "http://localhost:8080/callback"
SCOPES = "boards:read,pins:read"


def main():
    app_id = os.environ.get("PINTEREST_APP_ID")
    app_secret = os.environ.get("PINTEREST_APP_SECRET")
    if not app_id or not app_secret:
        sys.exit("Define PINTEREST_APP_ID y PINTEREST_APP_SECRET en la terminal antes de ejecutar.")

    state = secrets.token_urlsafe(16)
    auth_url = "https://www.pinterest.com/oauth/?" + urllib.parse.urlencode({
        "client_id": app_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    })
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if params.get("state", [""])[0] != state or "code" not in params:
                result["error"] = "La respuesta de Pinterest no es válida o falta el código."
            else:
                result["code"] = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<p>Listo. Ya puedes cerrar esta ventana.</p>".encode("utf-8"))

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", 8080), Callback)
    print("Abriendo Pinterest para que apruebes el acceso...")
    webbrowser.open(auth_url)
    server.handle_request()
    server.server_close()

    if "error" in result:
        sys.exit(result["error"])

    credentials = base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": result["code"],
        "redirect_uri": REDIRECT_URI,
    }).encode()
    req = urllib.request.Request(
        "https://api.pinterest.com/v5/oauth/token",
        data=body,
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        token = json.loads(resp.read().decode())

    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)
    print(f"Token guardado en {TOKEN_PATH}. Este archivo no debe subirse a git.")


if __name__ == "__main__":
    main()
