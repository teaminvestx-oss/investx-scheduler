# === instagram/post_instagram.py ===
# Posts a PNG image to Instagram using instagrapi (unofficial API).
# No Meta Developer App, no Facebook Page, no burocracia.
#
# Required env vars:
#   INSTAGRAM_USERNAME  — username de Instagram (sin @)
#   INSTAGRAM_PASSWORD  — contraseña de Instagram

import os
import re
from pathlib import Path


def _get_client():
    """Creates and authenticates an instagrapi Client."""
    from instagrapi import Client

    username = os.environ["INSTAGRAM_USERNAME"]
    password = os.environ["INSTAGRAM_PASSWORD"]

    cl = Client()
    # Ajustes para evitar detección como bot
    cl.delay_range = [1, 3]

    session_file = Path("/tmp/instagram_session.json")

    if session_file.exists():
        try:
            cl.load_settings(str(session_file))
            cl.login(username, password)
            print("[instagram/post] Sesión restaurada desde caché.")
            return cl
        except Exception as e:
            print(f"[instagram/post] Sesión caducada, re-login: {e}")
            session_file.unlink(missing_ok=True)

    print(f"[instagram/post] Login como @{username}...")
    cl.login(username, password)
    cl.dump_settings(str(session_file))
    print("[instagram/post] Login OK.")
    return cl


def post_to_instagram(image_path: str, caption: str) -> str:
    """
    Publica image_path en Instagram. Devuelve el media ID.
    """
    cl = _get_client()

    print(f"[instagram/post] Subiendo imagen: {image_path}")
    media = cl.photo_upload(
        path=image_path,
        caption=caption,
    )
    media_id = str(media.id)
    print(f"[instagram/post] Publicado correctamente. media_id={media_id}")
    return media_id


def build_caption(week_label: str, lectura: str) -> str:
    # Eliminar tags HTML que pueda tener la lectura (viene del template Jinja2)
    clean = re.sub(r"<[^>]+>", "", lectura).strip()
    if len(clean) > 300:
        clean = clean[:297] + "..."
    return (
        f"🕵️ Insider Trading — {week_label}\n\n"
        f"{clean}\n\n"
        f"¿Quieres las operaciones completas? Canal premium en bio.\n\n"
        f"#InsiderTrading #Bolsa #Inversión #NVDA #Mercados"
    )
