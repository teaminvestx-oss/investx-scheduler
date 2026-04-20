# === instagram/run_instagram_insider.py ===
# Orchestrates: load cached insider data → render card → post to Instagram.
# Called from main.py on Mondays at 11:00 Madrid time.

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from instagram.render_card import render_insider_card
from instagram.post_instagram import post_to_instagram, build_caption

TZ         = ZoneInfo("Europe/Madrid")
CACHE_FILE = "insider_instagram_cache.json"
STATE_FILE = "insider_instagram_state.json"


# ── State helpers ──────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _week_key(dt: datetime) -> str:
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _already_posted_this_week(dt: datetime) -> bool:
    return _load_state().get("posted_week") == _week_key(dt)


def _mark_posted(dt: datetime) -> None:
    state = _load_state()
    state["posted_week"] = _week_key(dt)
    state["posted_at"]   = dt.isoformat()
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _load_cache() -> dict | None:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ── Main runner ────────────────────────────────────────────────────────────────

def run_instagram_insider(force: bool = False) -> None:
    now = datetime.now(TZ)

    if not force and _already_posted_this_week(now):
        print("[instagram] Ya publicado en Instagram esta semana. Skipping.")
        return

    cache = _load_cache()
    if not cache:
        print("[instagram] No hay datos cacheados del Insider Trading. Skipping.")
        return

    data      = cache.get("template_data")
    if not data:
        print("[instagram] Cache sin template_data. Skipping.")
        return

    week_label = data.get("week_label", "")
    lectura    = data.get("lectura", "")

    print(f"[instagram] Renderizando card para {week_label}...")
    try:
        card_path = render_insider_card(data)
    except Exception as e:
        print(f"[instagram] ERROR render_card: {e}")
        return

    caption = build_caption(week_label, lectura)
    print(f"[instagram] Publicando en Instagram...")
    try:
        media_id = post_to_instagram(card_path, caption)
        _mark_posted(now)
        print(f"[instagram] OK publicado (media_id={media_id}).")
    except Exception as e:
        print(f"[instagram] ERROR post_instagram: {e}")
