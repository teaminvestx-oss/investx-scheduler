# === econ_calendar.py ===
# InvestX - Economic Calendar (ForexFactory)
# - Fuente: nfs.faireconomy.media (JSON público, sin API key, sin bloqueo datacenter)
# - Filtra: USD, impacto High (+ Medium opcional vía env)
# - Interpretación Bloomberg-style vía OpenAI
# - Anti-duplicado diario (state file)

from __future__ import annotations

import os
import json
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional

import requests

from utils import call_gpt_mini

# -----------------------------
# CONFIG
# -----------------------------
TZ = ZoneInfo("Europe/Madrid")

FF_THISWEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FF_NEXTWEEK_URL = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

# Impactos a incluir (en orden descendente de relevancia)
IMPACTS_INCLUDE = set(
    x.strip().lower()
    for x in os.getenv("ECON_IMPACTS", "high").split(",")
    if x.strip()
)  # por defecto solo "high"; añade "medium" con ECON_IMPACTS=high,medium

HTTP_TIMEOUT = int(os.getenv("ECON_HTTP_TIMEOUT", "20"))

STATE_FILE = "econ_calendar_state.json"


# -----------------------------
# Telegram
# -----------------------------
def _send_telegram(message: str) -> None:
    token = (
        os.getenv("INVESTX_TOKEN")
        or os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("TELEGRAM_TOKEN")
    )
    chat_id = (
        os.getenv("CHAT_ID")
        or os.getenv("TELEGRAM_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT")
    )

    if not token or not chat_id:
        print("[econ] Telegram no configurado. Imprimo mensaje:")
        print(message)
        return

    max_len = 3900
    chunks = [message[i:i + max_len] for i in range(0, len(message), max_len)] or [""]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    for idx, chunk in enumerate(chunks, 1):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            requests.post(url, json=payload, timeout=20).raise_for_status()
        except Exception as e:
            print(f"[econ] ERROR enviando Telegram (chunk {idx}): {e}")


# -----------------------------
# Estado anti-duplicado
# -----------------------------
def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_state(d: Dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass

def _already_sent(day_key: str) -> bool:
    return _load_state().get("last_sent_day") == day_key

def _mark_sent(day_key: str) -> None:
    st = _load_state()
    st["last_sent_day"] = day_key
    st["last_sent_at"] = datetime.now(TZ).isoformat()
    _save_state(st)


# -----------------------------
# Fetch ForexFactory
# -----------------------------
def _fetch_ff(url: str) -> Optional[List[Dict[str, Any]]]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; InvestX-Bot/1.0)",
        "Accept": "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[econ] Error fetching {url}: {e}")
        return None


def _parse_ff_date(event: Dict[str, Any]) -> Optional[date]:
    """Parsea la fecha del evento ForexFactory (ISO 8601 con timezone)."""
    raw = event.get("date") or ""
    if not raw:
        return None
    try:
        # ForexFactory devuelve: "2025-04-07T08:30:00-04:00"
        dt = datetime.fromisoformat(raw)
        return dt.date()
    except Exception:
        return None


def fetch_ff_events(target_date: date) -> List[Dict[str, Any]]:
    """
    Devuelve los eventos de ForexFactory para target_date.
    Intenta la semana actual; si target_date es la siguiente semana, usa nextweek.
    """
    today = datetime.now(TZ).date()
    # Si target_date está en la semana siguiente a la actual, usar nextweek
    days_ahead = (target_date - today).days
    url = FF_NEXTWEEK_URL if days_ahead >= 5 else FF_THISWEEK_URL

    data = _fetch_ff(url)

    # Si no hay datos en la URL principal, probar la otra
    if data is None:
        alt_url = FF_THISWEEK_URL if url == FF_NEXTWEEK_URL else FF_NEXTWEEK_URL
        data = _fetch_ff(alt_url)

    if not data:
        return []

    results = []
    for ev in data:
        ev_date = _parse_ff_date(ev)
        if ev_date != target_date:
            continue

        country = (ev.get("country") or "").strip().upper()
        impact  = (ev.get("impact") or "").strip().lower()
        title   = (ev.get("title") or "").strip()

        # Solo USD y los impactos configurados
        if country != "USD":
            continue
        if impact not in IMPACTS_INCLUDE:
            continue
        if not title:
            continue

        results.append({
            "time":     ev.get("date", ""),     # ISO completo, para ordenar
            "time_str": _format_time(ev.get("date", "")),
            "event":    title,
            "impact":   impact,
            "forecast": ev.get("forecast") or "",
            "previous": ev.get("previous") or "",
        })

    # Ordenar por hora
    results.sort(key=lambda x: x["time"])
    return results


def _format_time(iso_str: str) -> str:
    """Convierte ISO 8601 a hora Madrid legible."""
    if not iso_str:
        return "--:--"
    try:
        dt = datetime.fromisoformat(iso_str).astimezone(TZ)
        return dt.strftime("%H:%M")
    except Exception:
        return "--:--"


# -----------------------------
# Resumen IA (Bloomberg-style)
# -----------------------------
def _ai_summary(day_label: str, events: List[Dict[str, Any]]) -> str:
    if not events:
        lines = [
            f"*AGENDA MACRO — EE.UU. ({day_label})*",
            "",
            "Sin eventos de alto impacto programados.",
            "",
            "*Lectura rápida:* Sesgo neutral por falta de referencias macro.",
        ]
        return "\n".join(lines)

    compact = "\n".join(
        f"- {e['time_str']} {e['event']}"
        + (f" | prev: {e['previous']}" if e["previous"] else "")
        + (f" | est: {e['forecast']}" if e["forecast"] else "")
        for e in events
    )

    system = (
        "Eres el analista macro de un canal financiero estilo Bloomberg para traders. "
        "Escribes SIEMPRE en español neutro, conciso y accionable. "
        "NO inventes datos. Usa solo lo que aparece en los eventos.\n"
        "Formato:\n"
        "1) Título con fecha\n"
        "2) Lista de eventos con hora (hora Madrid) y nombre\n"
        "3) Interpretación en 2-4 bullets sobre volatilidad esperada y sesgo (risk-on/off/neutral)\n"
        "4) Nota final muy corta de gestión de riesgo\n"
    )
    user = (
        f"Fecha: {day_label}\n"
        f"Eventos (hora Madrid):\n{compact}\n\n"
        "Redacta el resumen siguiendo el formato indicado."
    )

    try:
        return (call_gpt_mini(system, user, max_tokens=400) or "").strip()
    except Exception as e:
        # Fallback sin IA
        lines = [f"*AGENDA MACRO — EE.UU. ({day_label})*", ""]
        for ev in events:
            lines.append(f"- {ev['time_str']} — {ev['event']}")
        lines += ["", f"_(IA no disponible: {e})_"]
        return "\n".join(lines)


# -----------------------------
# Entrypoint público
# -----------------------------
def run_econ_calendar(force: bool = False, force_tomorrow: bool = False) -> None:
    """
    - force=False: respeta anti-duplicados (1 envío/día)
    - force=True:  envía aunque ya se haya enviado hoy
    - force_tomorrow=True: usa mañana como fecha objetivo
    """
    now = datetime.now(TZ)
    target = (now.date() + timedelta(days=1)) if force_tomorrow else now.date()
    day_key = target.isoformat()

    if (not force) and _already_sent(day_key):
        print(f"[econ] Ya enviado para {day_key}. Skipping.")
        return

    print(f"[econ] Descargando ForexFactory para {day_key} (USD, impacto: {IMPACTS_INCLUDE})")

    events = fetch_ff_events(target)
    day_label = target.strftime("%d/%m/%Y")

    text = _ai_summary(day_label, events)
    _send_telegram(text)
    _mark_sent(day_key)
    print(f"[econ] OK enviado para {day_key} ({len(events)} eventos, force={force}).")
