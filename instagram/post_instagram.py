# === instagram/post_instagram.py ===
# Posts to Instagram via Make.com webhook (reliable, bypasses server IP blocks).
#
# Required env vars:
#   MAKE_WEBHOOK_URL   — webhook URL from Make.com scenario
#   IMGBB_API_KEY      — free image hosting to get a public URL for Make.com
#
# Optional fallback (if no MAKE_WEBHOOK_URL):
#   INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD — direct instagrapi (may be blocked)

import base64
import json
import os
import re

import requests

_HTTP_TIMEOUT = 30


def _upload_to_imgbb(image_path: str) -> str:
    """Sube la imagen a imgBB y devuelve la URL pública."""
    api_key = os.environ["IMGBB_API_KEY"]
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": api_key, "image": image_b64},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    url = resp.json()["data"]["url"]
    print(f"[instagram/post] Imagen subida a imgBB: {url}")
    return url


def _post_via_make(image_path: str, caption: str) -> str:
    """Envía webhook a Make.com que postea en Instagram."""
    webhook_url = os.environ["MAKE_WEBHOOK_URL"]
    image_url   = _upload_to_imgbb(image_path)

    resp = requests.post(
        webhook_url,
        json={"image_url": image_url, "caption": caption},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    print(f"[instagram/post] Webhook Make.com enviado OK.")
    return "make_webhook_ok"


def _post_via_instagrapi(image_path: str, caption: str) -> str:
    """Fallback directo con instagrapi (puede fallar en IPs de servidor)."""
    from instagrapi import Client

    username    = os.environ["INSTAGRAM_USERNAME"]
    password    = os.environ["INSTAGRAM_PASSWORD"]
    session_b64 = os.environ.get("INSTAGRAM_SESSION", "")

    cl = Client()
    cl.delay_range = [1, 3]

    if session_b64:
        try:
            session = json.loads(base64.b64decode(session_b64).decode())
            cl.set_settings(session)
            cl.login(username, password)
            print("[instagram/post] Sesión restaurada + re-auth OK.")
        except Exception as e:
            print(f"[instagram/post] Sesión falló ({e}), login limpio...")
            cl.login(username, password)
    else:
        cl.login(username, password)

    print(f"[instagram/post] Subiendo imagen: {image_path}")
    media = cl.photo_upload(path=image_path, caption=caption)
    media_id = str(media.id)
    print(f"[instagram/post] Publicado. media_id={media_id}")
    return media_id


def post_to_instagram(image_path: str, caption: str) -> str:
    """Publica la card en Instagram. Usa Make.com si está configurado."""
    if os.environ.get("MAKE_WEBHOOK_URL"):
        return _post_via_make(image_path, caption)
    return _post_via_instagrapi(image_path, caption)


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
