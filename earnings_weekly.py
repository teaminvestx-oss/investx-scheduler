# === earnings_weekly.py ===
# Earnings semanales desde Investing.com usando endpoint filtrado
# - País: Estados Unidos (country=5)
# - Impacto: 3 estrellas
# - Semana L-V desde la fecha base
# - Formato profesional en español para Telegram

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

API_URL = "https://es.investing.com/earnings-calendar/Service/getCalendarFilteredData"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://es.investing.com/earnings-calendar/",
    "Accept-Language": "es-ES,es;q=0.9",
}

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
# Descarga de earnings: un día concreto (USA, impacto 3)
# =====================================================

def _fetch_day_from_investing(day: datetime) -> List[Dict[str, Any]]:
    """
    Descarga el calendario de resultados de Investing.com para un día concreto,
    filtrando por:
      - country[] = 5 (Estados Unidos)
      - importance[] = 3 (impacto fuerte)

    Devuelve lista de dicts:
      - date (YYYY-MM-DD)
      - company
      - eps  (texto "BPA / Previsión")
      - revenue (texto "Ingresos / Previsión")
      - time (hora / icono)
    """

    date_str = day.strftime("%Y-%m-%d")

    payload = {
        "country[]": ["5"],       # Estados Unidos
        "importance[]": ["3"],    # Impacto 3 estrellas
        "dateFrom": date_str,
        "dateTo": date_str,
        "currentTab": "earnings",
        "limit_from": "0",
    }

    try:
        resp = requests.post(API_URL, headers=HEADERS, data=payload, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        logger.error(f"earnings_weekly | Error HTTP/JSON Investing para {date_str}: {e}")
        return []

    blocks_html = raw.get("data", [])
    if isinstance(blocks_html, str):
        blocks_html = [blocks_html]

    earnings: List[Dict[str, Any]] = []

    for block_html in blocks_html:
        soup = BeautifulSoup(block_html, "html.parser")
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue

            # Estructura típica:
            # 0: país (bandera / texto fecha según formato)
            # 1: empresa
            # 2: BPA / Previsión
            # 3: Ingresos / Previsión
            # 4: Cap. mercado
            # 5: Hora
            company = tds[1].get_text(strip=True)
            if not company:
                continue

            eps_text = tds[2].get_text(strip=True) or "--"
            rev_text = tds[3].get_text(strip=True) or "--"
            time_text = tds[5].get_text(strip=True) or "—"

            earnings.append(
                {
                    "date": date_str,
                    "company": company,
                    "eps": eps_text,
                    "revenue": rev_text,
                    "time": time_text,
                }
            )

    logger.info(
        f"earnings_weekly | Investing {date_str} (USA, impacto 3): "
        f"{len(earnings)} resultados."
    )
    return earnings


# =====================================================
# Semana completa (L-V)
# =====================================================

def fetch_weekly_earnings(week_start: datetime) -> List[Dict[str, Any]]:
    """
    Obtiene los earnings de la semana [week_start, week_start+4] (lunes a viernes).
    Para cada día hace una llamada independiente al endpoint filtrado.
    """
    earnings: List[Dict[str, Any]] = []

    for i in range(5):
        day = week_start + timedelta(days=i)
        day_list = _fetch_day_from_investing(day)
        earnings.extend(day_list)

    logger.info(
        f"earnings_weekly | Total semana Investing (USA, impacto 3): "
        f"{len(earnings)} resultados."
    )
    return earnings


# =====================================================
# Construcción de texto principal
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
            date_label = d.strftime("%A %d/%m").capitalize()  # Monday 24/11, etc.
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


# =====================================================
# Párrafo profesional IA
# =====================================================

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
    """
    Envía al canal de Telegram el resumen semanal de resultados empresariales.

    - Por defecto solo 1 envío al día (control STATE_FILE).
    - Si force=True ignora el control y envía siempre.
    - Si EARNINGS_SIMULATE_TOMORROW=1, toma como base 'hoy + 1 día'
      para poder forzar un domingo la semana siguiente.
    """

    simulate_tomorrow = (
        os.getenv("EARNINGS_SIMULATE_TOMORROW", "0").strip().lower()
        in ("1", "true", "yes")
    )

    now = datetime.utcnow() + timedelta(hours=TZ_OFFSET)
    if simulate_tomorrow:
        now += timedelta(days=1)

    today_str = now.strftime("%Y-%m-%d")

    logger.info(
        f"earnings_weekly | run_weekly_earnings(force={force}, "
        f"simulate_tomorrow={simulate_tomorrow}, today={today_str})"
    )

    if not force and _already_sent(today_str):
        logger.info("earnings_weekly | Ya se envió hoy. No se repite.")
        return

    # Semana que comienza en la fecha base (habitualmente lunes)
    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    earnings = fetch_weekly_earnings(week_start)

    calendar_text = _build_calendar_text(earnings, week_start)
    professional_note = _build_professional_note(earnings, week_start)

    final_message = f"{calendar_text}\n{professional_note}"

    send_telegram_message(final_message)
    _mark_sent(today_str)
    logger.info("earnings_weekly | Mensaje enviado correctamente.")
