# === earnings_weekly.py ===
# Earnings semanales desde Investing.com usando endpoint oficial filtrado
# - Filtrado por Estados Unidos (country=5)
# - Impacto = 3 estrellas
# - Semana L-V
# - Formato profesional en español

import os
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

import requests

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_FILE = "earnings_weekly_state.json"
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "1"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://es.investing.com/earnings-calendar/",
}

API_URL = "https://es.investing.com/earnings-calendar/Service/getCalendarFilteredData"


# =====================================================
# Estado (solo 1 envío por día)
# =====================================================

def _load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def _save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _already_sent(today):
    return _load_state().get("last_run_date") == today


def _mark_sent(today):
    st = _load_state()
    st["last_run_date"] = today
    _save_state(st)


# =====================================================
# Descarga de earnings (API oficial Investing filtrada)
# =====================================================

def fetch_weekly_earnings(week_start: datetime) -> List[Dict[str, Any]]:
    """
    Investing.com API (endpoint interno)
    Filtrado:
      - Estados Unidos -> country[]=5
      - Impacto 3 estrellas -> importance[]=3
      - Rango lunes–viernes

    Devuelve lista de dicts: date, company, ticker, eps, revenue, time
    """

    week_end = week_start + timedelta(days=4)

    payload = {
        "country[]": ["5"],        # Estados Unidos
        "importance[]": ["3"],     # Impacto 3 estrellas
        "dateFrom": week_start.strftime("%Y-%m-%d"),
        "dateTo": week_end.strftime("%Y-%m-%d"),
        "currentTab": "earnings",
        "limit_from": "0"
    }

    try:
        r = requests.post(API_URL, headers=HEADERS, data=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.error(f"earnings_weekly | Error descargando: {e}")
        return []

    results = []
    for item in data.get("data", []):
        # Campos reales del JSON:
        # date, name, symbol, actualEps, estimatedEps, actualRevenue, estimatedRevenue, time
        results.append({
            "date": item.get("date", ""),
            "company": item.get("name", ""),
            "ticker": item.get("symbol", ""),
            "eps": f"{item.get('actualEps', '--')} / {item.get('estimatedEps', '--')}",
            "revenue": f"{item.get('actualRevenue', '--')} / {item.get('estimatedRevenue', '--')}",
            "time": item.get("time", "—")
        })

    logger.info(f"earnings_weekly | {len(results)} earnings filtrados (USA, impacto 3).")
    return results


# =====================================================
# Construcción del mensaje
# =====================================================

def _build_calendar_text(earnings: List[Dict[str, Any]], week_start: datetime) -> str:

    week_end = week_start + timedelta(days=4)

    if not earnings:
        return (
            "📊 *Resultados empresariales de la semana*\n\n"
            f"No hay resultados entre {week_start:%d/%m} y {week_end:%d/%m} "
            "bajo los filtros (Estados Unidos, impacto 3)."
        )

    # Ordenar por fecha
    earnings_sorted = sorted(earnings, key=lambda x: (x["date"], x["company"]))

    lines = []
    lines.append("📊 *Resultados empresariales de la semana*")
    lines.append(f"Semana del {week_start:%d/%m} al {week_end:%d/%m}.\n")

    last_date = None
    for e in earnings_sorted:
        d = datetime.strptime(e["date"], "%Y-%m-%d")
        label = d.strftime("%A %d/%m").capitalize()
        if label != last_date:
            lines.append(f"🗓 *{label}*")
            last_date = label

        lines.append(
            f" • {e['company']} ({e['ticker']}) — BPA: {e['eps']} | "
            f"Ingresos: {e['revenue']} | {e['time']}"
        )

    return "\n".join(lines)


def _build_professional_note(earnings: List[Dict[str, Any]], week_start: datetime) -> str:

    if not earnings:
        return (
            "\nNo se esperan publicaciones de resultados relevantes en Estados Unidos "
            "con impacto significativo durante esta semana."
        )

    compact = "\n".join(
        f"{e['date']} — {e['company']} — {e['eps']} — {e['revenue']}"
        for e in earnings
    )

    prompt = (
        "Redacta un párrafo profesional en español, sin emojis ni tono promocional. "
        "Analiza brevemente la relevancia semanal del siguiente calendario de "
        "resultados empresariales:\n\n" + compact
    )

    try:
        note = call_gpt_mini(prompt).strip()
        return "\n" + note
    except:
        return "\nSemana con resultados relevantes que pueden influir en la volatilidad."


# =====================================================
# Ejecutar earnings semanales
# =====================================================

def run_weekly_earnings(force=False):

    simulate_tomorrow = os.getenv("EARNINGS_SIMULATE_TOMORROW", "0").lower() in ("1", "true", "yes")

    now = datetime.utcnow() + timedelta(hours=TZ_OFFSET)
    if simulate_tomorrow:
        now += timedelta(days=1)

    today = now.strftime("%Y-%m-%d")

    if not force and _already_sent(today):
        logger.info("earnings_weekly | Ya se envió hoy.")
        return

    # Semana (lunes–viernes)
    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    earnings = fetch_weekly_earnings(week_start)

    msg = _build_calendar_text(earnings, week_start)
    msg += _build_professional_note(earnings, week_start)

    send_telegram_message(msg)
    _mark_sent(today)
