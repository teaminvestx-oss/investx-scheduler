# === earnings_weekly.py ===
# Resumen semanal de resultados empresariales (earnings)
# - Se envía solo una vez al día gracias a un state file
# - Pensado para ejecutarse los lunes entre 10-11h desde main.py

import os
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_FILE = "earnings_weekly_state.json"

# Offset horario respecto a UTC (para alinear con main.py / Madrid)
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "1"))


# ============================
# Gestión de estado (solo 1 envío/día)
# ============================

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


def _already_sent_today(today_str: str) -> bool:
    state = _load_state()
    last_date = state.get("last_run_date")
    return last_date == today_str


def _mark_sent(today_str: str) -> None:
    state = _load_state()
    state["last_run_date"] = today_str
    _save_state(state)


# ============================
# OBTENCIÓN DE EARNINGS —
#   Aquí conectas tu API o scraper
# ============================

def fetch_weekly_earnings(week_start: datetime) -> List[Dict[str, Any]]:
    """
    Devuelve una lista de earnings planificados para la semana [week_start, week_start+6].
    Estructura recomendada de cada item:
      {
        "date": "2025-11-24",
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "time": "After Close"
      }

    ✳️ IMPORTANTE: Implementa aquí tu propia lógica de obtención de datos
    (API de resultados, scraping, fichero local, etc.)
    """
    # TEMPORAL — para evitar crash
    return []


# ============================
# Construcción de textos
# ============================

def _build_earnings_text(earnings: List[Dict[str, Any]], week_start: datetime) -> str:
    """Construye el texto con el calendario de earnings de la semana."""
    week_end = week_start + timedelta(days=4)

    if not earnings:
        return (
            "📊 *Resultados empresariales de la semana*\n\n"
            f"No hay resultados empresariales relevantes entre "
            f"{week_start.strftime('%d/%m')} y {week_end.strftime('%d/%m')}."
        )

    earnings_sorted = sorted(
        earnings, key=lambda e: (e.get("date", ""), e.get("ticker", ""))
    )

    lines = []
    lines.append("📊 *Resultados empresariales de la semana*")
    lines.append(f"Del {week_start.strftime('%d/%m')} al {week_end.strftime('%d/%m')}.\n")

    current_date = None

    for e in earnings_sorted:
        date_str = e.get("date", "")
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            date_label = d.strftime("%a %d/%m")
        except:
            date_label = date_str

        if date_label != current_date:
            lines.append(f"🗓 *{date_label}*")
            current_date = date_label

        ticker = e.get("ticker", "")
        company = e.get("company", "")
        time_str = e.get("time", "") or "Horario no especificado"

        if company:
            lines.append(f" • {ticker} ({company}) — {time_str}")
        else:
            lines.append(f" • {ticker} — {time_str}")

    return "\n".join(lines)


def _build_ai_summary(earnings: List[Dict[str, Any]], week_start: datetime) -> str:
    """Genera un resumen corto con IA de todos los earnings de la semana."""
    week_end = week_start + timedelta(days=4)

    if not earnings:
        return (
            "Esta semana no hay resultados empresariales relevantes en el calendario, "
            "por lo que no se esperan grandes catalizadores por beneficios."
        )

    compact = "\n".join(
        f"{e.get('date')} — {e.get('ticker')} ({e.get('company')}) — {e.get('time')}"
        for e in earnings
    )

    prompt = (
        "Quiero un resumen financiero en *2-3 frases* sobre los resultados empresariales "
        "de esta semana. Resume sectores principales, si es una semana fuerte o débil, "
        "y qué puede implicar para traders swing.\n\n"
        f"Semana {week_start.strftime('%d/%m')} - {week_end.strftime('%d/%m')}.\n\n"
        f"Calendario:\n{compact}"
    )

    try:
        out = call_gpt_mini(prompt)
        return out.strip()
    except Exception as e:
        logger.error(f"earnings_weekly | Error generando resumen IA: {e}")
        return "Resumen IA no disponible por un error técnico."


# ============================
# BLOQUE PRINCIPAL — ESTE ES EL COMPLETO
# ============================

def run_weekly_earnings(force: bool = False) -> None:
    """
    Envío del resumen semanal de earnings.
    - force=True: envía siempre e ignora estado.
    - EARNINGS_SIMULATE_TOMORROW=1 → se usa “hoy + 1 día”
      para obtener la semana siguiente aunque hoy sea domingo.
    """

    # -------------------------------
    # VARIABLES DE ENTORNO
    # -------------------------------
    SIMULATE_TOMORROW = os.getenv(
        "EARNINGS_SIMULATE_TOMORROW", "0"
    ).strip().lower() in ("1", "true", "yes")

    # Fecha real
    now_utc = datetime.utcnow()
    now_local = now_utc + timedelta(hours=TZ_OFFSET)

    # Simulación de mañana → semana siguiente
    if SIMULATE_TOMORROW:
        logger.info("earnings_weekly | SIMULATE_TOMORROW=1 → usando fecha simulada (hoy + 1).")
        now_local = now_local + timedelta(days=1)

    today_str = now_local.strftime("%Y-%m-%d")

    logger.info(
        f"earnings_weekly | Ejecutando run_weekly_earnings("
        f"force={force}, simulate_tomorrow={SIMULATE_TOMORROW}) "
        f"fecha base = {now_local}"
    )

    # -------------------------------
    # CONTROL SOLO UNA VEZ AL DÍA
    # -------------------------------
    if not force and _already_sent_today(today_str):
        logger.info("earnings_weekly | Ya se envió hoy. No se repite.")
        return

    # -------------------------------
    # DEFINICIÓN DE LA SEMANA
    # -------------------------------
    week_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    logger.info(f"earnings_weekly | Semana desde {week_start.date()}")

    # -------------------------------
    # OBTENER EARNINGS
    # -------------------------------
    earnings = fetch_weekly_earnings(week_start)

    # -------------------------------
    # CONSTRUIR MENSAJE
    # -------------------------------
    text_calendar = _build_earnings_text(earnings, week_start)
    text_summary = _build_ai_summary(earnings, week_start)

    final_message = (
        f"{text_calendar}\n\n"
        "🤖 *Resumen IA*\n"
        f"{text_summary}"
    )

    # -------------------------------
    # ENVIAR MENSAJE
    # -------------------------------
    try:
        send_telegram_message(final_message)
        logger.info("earnings_weekly | Enviado correctamente.")
        _mark_sent(today_str)
    except Exception as e:
        logger.error(f"earnings_weekly | Error al enviar Telegram: {e}")
