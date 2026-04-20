# === instagram/post_instagram.py ===
# Posts a PNG image to Instagram via Meta Graph API (two-step: create → publish).
#
# Required env vars:
#   INSTAGRAM_ACCESS_TOKEN  — long-lived page access token
#   INSTAGRAM_ACCOUNT_ID    — Instagram Business Account ID
#
# Image hosting (choose one):
#   IMGBB_API_KEY           — upload to imgBB (free, no extra infra)
#   INSTAGRAM_IMAGE_URL     — pre-hosted public URL (bring your own)

import base64
import os
import time

import requests

_GRAPH_VERSION = "v19.0"
_GRAPH_BASE    = f"https://graph.facebook.com/{_GRAPH_VERSION}"
_HTTP_TIMEOUT  = 30


def _upload_to_imgbb(image_path: str) -> str:
    api_key = os.environ["IMGBB_API_KEY"]
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()
    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": api_key, "image": image_b64, "expiration": 3600},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    url = resp.json()["data"]["url"]
    print(f"[instagram/post] imgBB upload OK: {url}")
    return url


def _resolve_image_url(image_path: str) -> str:
    if os.environ.get("IMGBB_API_KEY"):
        return _upload_to_imgbb(image_path)
    if os.environ.get("INSTAGRAM_IMAGE_URL"):
        return os.environ["INSTAGRAM_IMAGE_URL"]
    raise RuntimeError(
        "Set IMGBB_API_KEY (to auto-upload) or INSTAGRAM_IMAGE_URL "
        "(pre-hosted public URL) to publish images to Instagram."
    )


def post_to_instagram(image_path: str, caption: str) -> str:
    """
    Publishes image_path to Instagram Business Account.

    Returns:
        Published media ID.
    """
    token      = os.environ["INSTAGRAM_ACCESS_TOKEN"]
    account_id = os.environ["INSTAGRAM_ACCOUNT_ID"]

    image_url = _resolve_image_url(image_path)

    # Step 1: create media container
    print(f"[instagram/post] Creating media container...")
    resp = requests.post(
        f"{_GRAPH_BASE}/{account_id}/media",
        params={"access_token": token},
        json={"image_url": image_url, "caption": caption, "media_type": "IMAGE"},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    creation_id = resp.json()["id"]
    print(f"[instagram/post] Container ID: {creation_id}")

    # Brief pause — Meta recommends polling status before publishing
    time.sleep(3)

    # Step 2: publish
    print(f"[instagram/post] Publishing...")
    resp = requests.post(
        f"{_GRAPH_BASE}/{account_id}/media_publish",
        params={"access_token": token},
        json={"creation_id": creation_id},
        timeout=_HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    media_id = resp.json()["id"]
    print(f"[instagram/post] Published media ID: {media_id}")
    return media_id


def build_caption(week_label: str, lectura: str) -> str:
    summary = lectura.strip()
    # Keep caption under Instagram's 2,200 char limit
    if len(summary) > 300:
        summary = summary[:297] + "..."
    return (
        f"🕵️ Insider Trading — {week_label}\n\n"
        f"{summary}\n\n"
        f"¿Quieres las operaciones completas? Canal premium en bio.\n\n"
        f"#InsiderTrading #Bolsa #Inversión #NVDA #Mercados"
    )
