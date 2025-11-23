# === earnings_weekly.py ===
# Resumen semanal de resultados empresariales (earnings)
# Fuente: Yahoo Finance (web scraping del calendario de resultados)
# - Gratis, sin API key
# - Español, tono profesional
# - Control de 1 envío/día + simulación "mañana"

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


# =====================================================
# Estado (solo 1 envío diario)
# =====================================================

def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"earnings_weekly | Error cargando estado: {e}")
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"earnings_weekly | Error guardando estado: {e}")


def _already_sent(today_str: str) -> bool:
    return _load_state().get("last_run_date") == today_str


def _mark_sent(today_str: str) -> None:
    state = _load_state()
    state["last_run_date"] = today_str
    _save_state(state)


# =====================================================
# Scraping Yahoo Finance (earnings calendar)
# =====================================================

YF_BASE_URL = "https://finance.yahoo.com/calendar/earnings"
YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


def _fetch_yahoo_earnings_for_day(day: datetime) -> List[Dict[str, Any]]:
    """
    Descarga el calendario de resultados de Yahoo Finance para un día concreto.
    Devuelve una lista de dicts con:
      - date: "YYYY-MM-DD"
      - ticker
      - company
      - time: "Before Open" | "After Close" | "Horario no especificado"
    """

    date_str = day.strftime("%Y-%m-%d")
    params = {
        "day": date_str,
        "offset": "0",
        "size": "100",  # máx. filas por página
    }

    try:
        resp = requests.get(YF_BASE_URL, params=params, headers=YF_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"earnings_weekly | Error HTTP al consultar Yahoo para {date_str}: {e}")
        return []

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")

        if not table:
            logger.warning(f"earnings_weekly | No se encontró tabla de earnings en Yahoo ({date_str}).")
            return []

        tbody = table.find("tbody")
        if not tbody:
            logger.warning(f"earnings_weekly | Tabla sin tbody en Yahoo ({date_str}).")
            return []

        rows = tbody.find_all("tr")
        results: List[Dict[str, Any]] = []

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue

            ticker = cells[0].get_text(strip=True)
            company = cells[1].get_text(strip=True)
            time_raw = cells[2].get_text(strip=True)

            if not ticker:
                continue

            t_lower = (time_raw or "").lower()
            if "before" in t_lower:
                time_label = "Before Open"
            elif "after" in t_lower:
                time_label = "After Close"
            else:
                time_label = "Horario no especificado"

            results.append(
                {
                    "date": date_str,
                    "ticker": ticker,
                    "company": company or ticker,
                    "time": time_label,
                }
            )

        logger.info(
            f"earnings_weekly | Yahoo devolvió {len(results)} earnings para {date_str}."
        )
        return results

    except Exception as e:
        logger.error(f"earnings_weekly | Error parseando HTML de Yahoo ({date_str}): {e}")
        return []


def fetch_weekly_earnings(week_start: datetime) -> List[Dict[str, Any]]:
    """
    Obtiene los earnings de la semana [week_start, week_start+4] (L-V)
    haciendo 1 petición al calendario de Yahoo por día.
    """
    earnings: List[Dict[str, Any]] = []

    for i in range(5):  # Lunes → Viernes
        day = week_start + timedelta(days=i)
        day_list = _fetch_yahoo_earnings_for_day(day)
        earnings.extend(day_list)

    logger.info(
        f"earnings_weekly | Yahoo total semana: {len(earnings)} resultados "
        f"desde {week_start.date()} hasta {(week_start + timedelta(days=4)).date()}."
    )
    return earnings


# =====================================================
# Construcción de texto
# =====================================================

def _build_calendar_text(earnings: List[Dict[str, Any]], week_start: datetime) -> str:
    """Construye el texto del calendario semanal de resultados."""
    week_end = week_start + timedelta(days=4)

    if not earnings:
        return (
            "📊 *Resultados empresariales de la semana*\n\n"
            f"No hay resultados empresariales previstos entre "
            f"{week_start.strftime('%d/%m')} y {week_end.strftime('%d/%m')}."
        )

    earnings_sorted = sorted(earnings, key=lambda e: (e["date"], e["ticker"]))

    lines: List[str] = []
    lines.append("📊 *Resultados empresariales de la semana*")
    lines.append(
        f"Semana del {week_start.strftime('%d/%m')} al {week_end.strftime('%d/%m')}.\n"
    )

    current_date_label = None

    for e in earnings_sorted:
        date_str = e.get("date")
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            date_label = d.strftime("%a %d/%m")
        except Exception:
            date_label = date_str or "Fecha desconocida"

        if date_label != current_date_label:
            lines.append(f"🗓 *{date_label}*")
            current_date_label = date_label

        ticker = e.get("ticker", "")
        company = e.get("company", "") or ticker
        time_str = e.get("time", "") or "Horario no especificado"

        lines.append(f" • {ticker} ({company}) — {time_str}")

    return "\n".join(lines)


def _build_professional_note(earnings: List[Dict[str, Any]], week_start: datetime) -> str:
    """
    Párrafo profesional en español describiendo la relevancia semanal.
    Sin títulos tipo 'Resumen IA'.
    """
    week_end = week_start + timedelta(days=4)

    if not earnings:
        return (
            "\nDurante esta semana no hay publicaciones de resultados corporativos "
            "relevantes en el calendario, por lo que no se esperan catalizadores "
            "significativos derivados de beneficios empresariales."
        )

    compact = "\n".join(
        f"{e['date']} — {e['ticker']} ({e['company']}) — {e['time']}"
        for e in earnings
    )

    prompt = (
        "Redacta un párrafo profesional, claro y conciso en español, sin emojis y "
        "sin mencionar que eres una IA. El texto debe interpretar la relevancia "
        "semanal del siguiente calendario de resultados empresariales para un "
        "inversor de corto/medio plazo.\n\n"
        f"Semana del {week_start.strftime('%d/%m')} al {week_end.strftime('%d/%m')}.\n\n"
        f"Calendario de resultados:\n{compact}"
    )

    try:
        note = call_gpt_mini(prompt)
        if not note:
            raise ValueError("Respuesta vacía")
        return "\n" + note.strip()
    except Exception as e:
        logger.error(f"earnings_weekly | Error generando nota profesional: {e}")
        return (
            "\nEsta semana se concentran varias publicaciones de resultados corporativos "
            "que pueden influir en el sentimiento de mercado, especialmente en los "
            "valores directamente afectados y en sus sectores de referencia."
        )


# =====================================================
# Módulo principal
# =====================================================

def run_weekly_earnings(force: bool = False) -> None:
    """
    Envía el resumen semanal de resultados empresariales al canal de Telegram.

    - Solo se ejecuta una vez al día (control por STATE_FILE), salvo que force=True.
    - Si EARNINGS_SIMULATE_TOMORROW=1, se usa 'hoy + 1 día' como fecha base
      para calcular la semana; útil para forzar un domingo la semana siguiente.
    """

    simulate_tomorrow = (
        os.getenv("EARNINGS_SIMULATE_TOMORROW", "0").strip().lower()
        in ("1", "true", "yes")
    )

    now_utc = datetime.utcnow()
    now_local = now_utc + timedelta(hours=TZ_OFFSET)

    if simulate_tomorrow:
        logger.info(
            "earnings_weekly | EARNINGS_SIMULATE_TOMORROW=1 → usando fecha simulada (hoy + 1 día)."
        )
        now_local = now_local + timedelta(days=1)

    today_str = now_local.strftime("%Y-%m-%d")

    logger.info(
        f"earnings_weekly | run_weekly_earnings("
        f"force={force}, simulate_tomorrow={simulate_tomorrow}) "
        f"fecha base={today_str}"
    )

    if not force and _already_sent(today_str):
        logger.info("earnings_weekly | Ya se envió hoy, no se repite.")
        return

    # Semana que empieza en la fecha base (habitualmente lunes)
    week_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    earnings = fetch_weekly_earnings(week_start)
    calendar_text = _build_calendar_text(earnings, week_start)
    professional_note = _build_professional_note(earnings, week_start)

    final_message = f"{calendar_text}\n{professional_note}"

    try:
        send_telegram_message(final_message)
        logger.info("earnings_weekly | Mensaje enviado correctamente.")
        _mark_sent(today_str)
    except Exception as e:
        logger.error(f"earnings_weekly | Error enviando mensaje a Telegram: {e}")
