# === instagram/post_instagram.py ===
# Posts a PNG image to Instagram using instagrapi.
#
# Required env vars:
#   INSTAGRAM_USERNAME  — username sin @
#   INSTAGRAM_PASSWORD  — contraseña
#   INSTAGRAM_SESSION   — sesión base64 generada con setup_session.py (recomendado)

import base64
import json
import os
import re
from pathlib import Path


def _get_client():
    from instagrapi import Client

    username    = os.environ["INSTAGRAM_USERNAME"]
    password    = os.environ["INSTAGRAM_PASSWORD"]
    session_b64 = os.environ.get("INSTAGRAM_SESSION", "")

    cl = Client()
    cl.delay_range = [1, 3]

    # Opción 1: sesión pre-autenticada (generada con login_by_sessionid en Mac)
    # NO llamamos a login() encima — eso corrompe la sesión en cuentas vinculadas a FB
    if session_b64:
        try:
            session = json.loads(base64.b64decode(session_b64).decode())
            cl.set_settings(session)
            cl.set_user(username)
            print("[instagram/post] Sesión restaurada desde INSTAGRAM_SESSION.")
            return cl
        except Exception as e:
            print(f"[instagram/post] Sesión inválida, intentando login directo: {e}")

    # Opción 2: login directo (solo si no hay sesión guardada)
    print(f"[instagram/post] Login como @{username}...")
    cl.login(username, password)
    print("[instagram/post] Login OK.")
    return cl


def post_to_instagram(image_path: str, caption: str) -> str:
    """Publica image_path en Instagram. Devuelve el media ID."""
    cl = _get_client()

    print(f"[instagram/post] Subiendo imagen: {image_path}")
    media = cl.photo_upload(path=image_path, caption=caption)
    media_id = str(media.id)
    print(f"[instagram/post] Publicado. media_id={media_id}")
    return media_id


def build_caption(week_label: str, lectura: str) -> str:
    clean = re.sub(r"<[^>]+>", "", lectura).strip()
    if len(clean) > 300:
        clean = clean[:297] + "..."
    return (
        f"🕵️ Insider Trading — {week_label}\n\n"
        f"{clean}\n\n"
        f"¿Quieres las operaciones completas? Canal premium en bio.\n\n"
        f"#InsiderTrading #Bolsa #Inversión #NVDA #Mercados"
    )
