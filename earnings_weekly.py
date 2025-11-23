# === earnings_weekly.py ===
# Earnings semanales desde Investing.com usando endpoint filtrado
# - Filtra por Estados Unidos (country=5)
# - Impacto = 3 estrellas
# - Semana L-V
# - Formato profesional en español

import os
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

import requests
from bs4 import BeautifulSoup

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
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://es.investing.com/earnings-calendar/",
}

API_URL = "https://es.investing.com/earnings-calendar/Service/getCalendarFilteredData"


# =====================================================
# Estado (solo 1 envío por día)
# =====================================================

def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _already_sent(today_str: str) -> bool:
    return _load_state().get("last_run_date") == today_str


def _mark_sent(today_str: str) -> None:
    state = _load_state()
    state["last_run_date"] = today_str
    _save_state(state)


# =====================================================
# Descarga de earnings (API Investing + parseo HTML)
# =====================================================

def fetch_weekly_earnings(week_start: datetime) -> List[Dict[str, Any]]:
    """
    Investing.com API interna:
      - country[] = 5 (USA)
      - importance[] = 3 (impacto 3 estrellas)
      - dateFrom/dateTo = semana L-V

    Devuelve lista de dicts con:
      date, company, eps, revenue, time
    """

    week_end = week_start + timedelta(days=4)

    payload = {
        "country[]": ["5"],        # Estados Unidos
        "importance[]": ["3"],     # Impacto 3 estrellas
        "dateFrom": week_start.strftime("%Y-%m-%d"),
        "dateTo": week_end.strftime("%Y-%m-%d"),
        "currentTab": "earnings",
        "limit_from": "0",
    }

    try:
        resp = requests.post(API_URL, headers=HEADERS, data=payload, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.error(f"earnings_weekly | Error HTTP/JSON Investing: {e}")
        return []

    rows_html = raw.get("data", [])
    if isinstance(rows_html, str):
        rows_html = [rows_html]

    earnings: List[Dict[str, Any]] = []

    for row_html in rows_html:
        # Cada elemento de rows_html es un <tr> en HTML
        try:
            soup = BeautifulSoup(row_html, "html.parser")
            tds = soup.find_all("td")
            if len(tds) < 6:
                continue

            # Estructura típica del calendario de resultados:
            # 0: fecha
            # 1: empresa (nombre + link)
            # 2: BPA / Previsión
            # 3: Ingresos / Previsión
            # 4: Cap. mercado
            # 5: Hora
            date_text = tds[0].get_text(strip=True)
            company = tds[1].get_text(strip=True)
            eps_text = tds[2].get_text(strip=True)
            rev_text = tds[3].get_text(strip=True)
            time_text = tds[5].get_text(strip=True) or "—"

            # La fecha suele venir como "24.11.2025"
            try:
                parsed_date = datetime.strptime(date_text, "%d.%m.%Y")
                date_iso = parsed_date.strftime("%Y-%m-%d")
            except Exception:
                date_iso = week_start.strftime("%Y-%m-%d")

            earnings.append(
                {
                    "date": date_iso,
                    "company": company,
                    "eps": eps_text,
                    "revenue": rev_text,
                    "time": time_text,
                }
            )
        except Exception as e:
            logger.error(f"earnings_weekly | Error parseando fila HTML: {e}")
            continue

    logger.info(
        f"earnings_weekly | Investing (USA, impacto 3): {len(earnings)} resultados "
        f"entre {week_start.date()} y {week_end.date()}."
    )
    return earnings


# =====================================================
# Construcción de mensajes
# =====================================================

def _build_calendar_text(earnings: List[Dict[str, Any]], week_start: datetime) -> str:
    week_end = week_start + timedelta(days=4)

    if not earnings:
        return (
            "📊 *Resultados empresariales de la semana*\n\n"
            f"No hay resultados entre {week_start:%d/%m} y {week_end:%d/%m} "
            "bajo los filtros (Estados Unidos, impacto 3)."
        )

    earnings_sorted = sorted(earnings, key=lambda x: (x["date"], x["company"]))

    lines: List[str] = []
    lines.append("📊 *Resultados empresariales de la semana*")
    lines.append(f"Semana del {week_start:%d/%m} al {week_end:%d/%m}.\n")

    last_date = None
    for e in earnings_sorted:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d")
            date_label = d.strftime("%A %d/%m").capitalize()
        except Exception:
            date_label = e["date"]

        if date_label != last_date:
            lines.append(f"🗓 *{date_label}*")
            last_date = date_label

        lines.append(
            f" • {e['company']} — BPA: {e['eps']} | "
            f"Ingresos: {e['revenue']} | {e['time']}"
        )

    return "\n".join(lines)


def _build_professional_note(earnings: List[Dict[str, Any]], week_start: datetime) -> str:
    week_end = week_start + timedelta(days=4)

    if not earnings:
        return (
            "\nEsta semana no se esperan publicaciones de resultados corporativos "
            "de alto impacto en Estados Unidos."
        )

    compact = "\n".join(
        f"{e['date']} — {e['company']} — BPA {e['eps']} — Ingresos {e['revenue']}"
        for e in earnings
    )

    prompt = (
        "Redacta un párrafo profesional, claro y conciso en español, sin emojis y "
        "sin mencionar que eres una IA. Resume la relevancia semanal del siguiente "
        "calendario de resultados empresariales (Estados Unidos, impacto 3):\n\n"
        f"{compact}"
    )

    try:
        note = call_gpt_mini(prompt)
        return "\n" + (note or "").strip()
    except Exception as e:
        logger.error(f"earnings_weekly | Error generando nota profesional: {e}")
        return (
            "\nLos resultados previstos para esta semana pueden influir en la volatilidad "
            "de los principales índices estadounidenses y en los sectores más expuestos."
        )


# =====================================================
# Ejecución principal
# =====================================================

def run_weekly_earnings(force: bool = False) -> None:
    simulate_tomorrow = (
        os.getenv("EARNINGS_SIMULATE_TOMORROW", "0").strip().lower() in ("1", "true", "yes")
    )

    now = datetime.utcnow() + timedelta(hours=TZ_OFFSET)
    if simulate_tomorrow:
        now += timedelta(days=1)

    today_str = now.strftime("%Y-%m-%d")

    logger.info(
        f"earnings_weekly | run_weekly_earnings(force={force}, simulate_tomorrow={simulate_tomorrow}, "
        f"today={today_str})"
    )

    if not force and _already_sent(today_str):
        logger.info("earnings_weekly | Ya se envió hoy. No se repite.")
        return

    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    earnings = fetch_weekly_earnings(week_start)

    calendar_text = _build_calendar_text(earnings, week_start)
    professional_note = _build_professional_note(earnings, week_start)

    final_message = f"{calendar_text}\n{professional_note}"

    send_telegram_message(final_message)
    _mark_sent(today_str)
    logger.info("earnings_weekly | Mensaje enviado correctamente.")
