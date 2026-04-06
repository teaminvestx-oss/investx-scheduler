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
# Formato del mensaje
# -----------------------------
DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

def _build_message(target_date: date, events: List[Dict[str, Any]]) -> str:
    """
    Construye el bloque de cabecera + eventos con formato Telegram Markdown.
    La IA solo aporta la interpretación, nunca el listado de eventos.
    """
    day_name = DIAS_ES[target_date.weekday()]
    day_label = f"{day_name} {target_date.strftime('%d/%m/%Y')}"

    lines = [f"🗓 *AGENDA MACRO — EE.UU.*", f"_{day_label}_", ""]

    if not events:
        lines += [
            "Sin eventos de alto impacto programados para hoy.",
            "",
            "📌 *Lectura:* Sesgo neutral. Sin referencias macro relevantes.",
        ]
        return "\n".join(lines)

    lines.append("⏱ *Horario* \\(hora Madrid\\):\n")
    for ev in events:
        # Línea principal: hora en negrita + nombre del evento
        line = f"*{ev['time_str']}* — {ev['event']}"
        # Datos previo / estimación en la misma línea si existen
        meta = []
        if ev["previous"]:
            meta.append(f"ant: {ev['previous']}")
        if ev["forecast"]:
            meta.append(f"est: {ev['forecast']}")
        if meta:
            line += f"  _({', '.join(meta)})_"
        lines.append(line)

    lines.append("")

    # Bloque de interpretación IA
    interpretation = _ai_interpretation(day_label, events)
    if interpretation:
        lines += ["📊 *Análisis macro*\n", interpretation]

    return "\n".join(lines)


def _ai_interpretation(day_label: str, events: List[Dict[str, Any]]) -> str:
    """IA genera SOLO la interpretación (no el listado de eventos)."""
    compact = "\n".join(
        f"- {e['time_str']} {e['event']}"
        + (f" | ant: {e['previous']}" if e["previous"] else "")
        + (f" | est: {e['forecast']}" if e["forecast"] else "")
        for e in events
    )

    system = (
        "Eres analista macro de un desk institucional estilo Bloomberg. "
        "Escribes en español neutro, conciso y accionable para traders. "
        "NO menciones que eres IA. NO repitas la lista de eventos. "
        "Formato: 2-3 bullets cortos sobre volatilidad esperada y sesgo "
        "(risk-on / risk-off / neutral), seguidos de una línea final "
        "que empiece por '⚠️ Riesgo:' con la recomendación de gestión."
    )
    user = (
        f"Eventos macro de EE.UU. para {day_label} (hora Madrid):\n{compact}\n\n"
        "Redacta solo la interpretación siguiendo el formato."
    )

    try:
        return (call_gpt_mini(system, user, max_tokens=300) or "").strip()
    except Exception:
        return ""


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

    text = _build_message(target, events)
    _send_telegram(text)
    _mark_sent(day_key)
    print(f"[econ] OK enviado para {day_key} ({len(events)} eventos, force={force}).")
