#!/usr/bin/env python3
# === instagram/setup_session.py ===
# Ejecuta esto UNA SOLA VEZ en tu Mac para generar la sesión de Instagram.
# Después copia el valor que imprime como variable de entorno en Render.
#
# Uso:
#   pip3 install instagrapi
#   python instagram/setup_session.py

import base64
import getpass
import json
import sys


def main():
    print("=== Setup sesión Instagram para Render ===\n")

    try:
        from instagrapi import Client
    except ImportError:
        print("ERROR: instagrapi no está instalado.")
        print("Ejecuta: pip3 install instagrapi")
        sys.exit(1)

    username = input("Username de Instagram (sin @): ").strip()
    password = getpass.getpass("Password de Instagram: ")

    cl = Client()
    cl.delay_range = [1, 3]

    print(f"\nHaciendo login como @{username}...")
    print("(Si Instagram pide verificación, introduce el código que recibes por email/SMS)\n")

    try:
        cl.login(username, password)
    except Exception as e:
        print(f"\nERROR al hacer login: {e}")
        print("Asegúrate de que usuario y contraseña son correctos.")
        sys.exit(1)

    session = cl.get_settings()
    session_json = json.dumps(session)
    session_b64  = base64.b64encode(session_json.encode()).decode()

    print("\n" + "=" * 60)
    print("✓ Login OK. Copia la siguiente línea en Render → Environment:")
    print("=" * 60)
    print(f"\nNombre:  INSTAGRAM_SESSION")
    print(f"Valor:   {session_b64}")
    print("\n" + "=" * 60)
    print("Con esta variable Render nunca volverá a pedir verificación.")


if __name__ == "__main__":
    main()
