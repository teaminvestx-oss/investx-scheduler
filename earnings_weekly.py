# === earnings_weekly.py ===
# Earnings semanales vía yfinance (calendario de tickers de alto impacto USA)
# - Lista curada de ~60 compañías de gran capitalización
# - Semana L-V desde la fecha base
# - Formato profesional y minimalista para Telegram

import os
import json
import logging
from datetime import datetime, timedelta, date
from typing import List, Dict, Any, Optional

import yfinance as yf

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_FILE = "earnings_weekly_state.json"
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "1"))

# =====================================================
# Universo de tickers a seguir (alto impacto USA)
# =====================================================
TICKER_NAMES: Dict[str, str] = {
    # Mega-cap Tech
    "AAPL":  "Apple",
    "MSFT":  "Microsoft",
    "AMZN":  "Amazon",
    "NVDA":  "Nvidia",
    "GOOGL": "Alphabet (Google)",
    "META":  "Meta",
    "TSLA":  "Tesla",
    # Software / Cloud
    "ADBE":  "Adobe",
    "CRM":   "Salesforce",
    "NOW":   "ServiceNow",
    "ORCL":  "Oracle",
    "IBM":   "IBM",
    "CSCO":  "Cisco",
    "INTU":  "Intuit",
    "MSCI":  "MSCI",
    # Semis
    "AMD":   "AMD",
    "INTC":  "Intel",
    "AVGO":  "Broadcom",
    "QCOM":  "Qualcomm",
    "TXN":   "Texas Instruments",
    "MU":    "Micron",
    "AMAT":  "Applied Materials",
    "KLAC":  "KLA Corp",
    # Financieras
    "JPM":   "JPMorgan Chase",
    "BAC":   "Bank of America",
    "C":     "Citigroup",
    "GS":    "Goldman Sachs",
    "MS":    "Morgan Stanley",
    "WFC":   "Wells Fargo",
    "AXP":   "American Express",
    "V":     "Visa",
    "MA":    "Mastercard",
    "BLK":   "BlackRock",
    # Salud / Farma
    "JNJ":   "Johnson & Johnson",
    "LLY":   "Eli Lilly",
    "ABBV":  "AbbVie",
    "UNH":   "UnitedHealth",
    "PFE":   "Pfizer",
    "MRK":   "Merck",
    "BMY":   "Bristol-Myers Squibb",
    "AMGN":  "Amgen",
    # Energía
    "XOM":   "ExxonMobil",
    "CVX":   "Chevron",
    "COP":   "ConocoPhillips",
    "SLB":   "SLB (Schlumberger)",
    "OXY":   "Occidental Petroleum",
    # Bebidas / Consumo
    "STZ":   "Constellation Brands",
    "DEO":   "Diageo",
    "BUD":   "AB InBev",
    # Seguros / Financieras especializadas
    "PGR":   "Progressive",
    "TRV":   "Travelers",
    "ALL":   "Allstate",
    "CB":    "Chubb",
    "MET":   "MetLife",
    "PRU":   "Prudential",
    # Salud adicional
    "CVS":   "CVS Health",
    "HUM":   "Humana",
    "CI":    "Cigna",
    "ELV":   "Elevance Health",
    # Inmobiliario / Otros S&P 500
    "AMT":   "American Tower",
    "PLD":   "Prologis",
    "SPG":   "Simon Property Group",
    # Transporte / Logística
    "UPS":   "UPS",
    "FDX":   "FedEx",
    "DAL":   "Delta Air Lines",
    "UAL":   "United Airlines",
    # Media / Entretenimiento
    "NFLX":  "Netflix",
    "DIS":   "Walt Disney",
    "CMCSA": "Comcast",
    # Consumo discrecional / Retail
    "HD":    "Home Depot",
    "WMT":   "Walmart",
    "COST":  "Costco",
    "TGT":   "Target",
    "MCD":   "McDonald's",
    "NKE":   "Nike",
    "SBUX":  "Starbucks",
    # Consumo básico
    "PG":    "Procter & Gamble",
    "KO":    "Coca-Cola",
    "PEP":   "PepsiCo",
    "PM":    "Philip Morris",
    # Industriales / Defensa
    "BA":    "Boeing",
    "CAT":   "Caterpillar",
    "DE":    "Deere & Company",
    "GE":    "GE Aerospace",
    "HON":   "Honeywell",
    "RTX":   "RTX Corp",
    "LMT":   "Lockheed Martin",
    # Telecom
    "T":     "AT&T",
    "VZ":    "Verizon",
    # Otros relevantes
    "PYPL":  "PayPal",
    "BKNG":  "Booking Holdings",
    "UBER":  "Uber",
    "SPOT":  "Spotify",
    "COIN":  "Coinbase",
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
# Extracción de fecha de earnings desde yfinance
# =====================================================

def _extract_earnings_date(cal) -> Optional[date]:
    """
    Extrae la próxima fecha de earnings del objeto calendar de yfinance.
    Devuelve la fecha más cercana o None si no hay datos.

    yfinance puede devolver:
    - dict:      {"Earnings Date": [Timestamp, ...], ...}
    - DataFrame: "Earnings Date" como fila del índice (versiones recientes)
    """
    if cal is None:
        return None

    raw_dates = []

    if isinstance(cal, dict):
        raw = cal.get("Earnings Date")
        if raw is None:
            return None
        raw_dates = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    else:
        # DataFrame: en yfinance >= 0.2 "Earnings Date" suele estar en el índice
        try:
            if hasattr(cal, "index") and "Earnings Date" in cal.index:
                row = cal.loc["Earnings Date"]
                raw_dates = row.dropna().tolist() if hasattr(row, "dropna") else [row]
            elif hasattr(cal, "columns") and "Earnings Date" in cal.columns:
                raw_dates = cal["Earnings Date"].dropna().tolist()
        except Exception:
            return None

    found = []
    for d in raw_dates:
        try:
            if hasattr(d, "date"):
                found.append(d.date())
            elif hasattr(d, "to_pydatetime"):
                found.append(d.to_pydatetime().date())
            elif isinstance(d, str) and len(d) >= 10:
                from datetime import datetime as _dt
                found.append(_dt.strptime(d[:10], "%Y-%m-%d").date())
        except Exception:
            continue

    return min(found) if found else None


# =====================================================
# Fetch semanal vía yfinance (una llamada por ticker)
# =====================================================

def fetch_weekly_earnings(week_start: datetime) -> List[Dict[str, Any]]:
    """
    Obtiene los earnings de la semana [week_start, week_start+4] (lunes a viernes)
    consultando el calendario de yfinance para cada ticker de la lista curada.
    """
    week_dates: set[date] = set()
    for i in range(5):
        week_dates.add((week_start + timedelta(days=i)).date())

    earnings: List[Dict[str, Any]] = []

    for ticker_sym, company_name in TICKER_NAMES.items():
        try:
            t = yf.Ticker(ticker_sym)
            cal = t.calendar
            ed = _extract_earnings_date(cal)
            if ed and ed in week_dates:
                earnings.append({
                    "date": ed.isoformat(),
                    "company": company_name,
                    "eps": "--",
                    "revenue": "--",
                    "time": "—",
                })
        except Exception as e:
            logger.warning(f"earnings_weekly | {ticker_sym}: {e}")

    logger.info(
        f"earnings_weekly | Total semana yfinance (tickers curados): "
        f"{len(earnings)} resultados."
    )
    return earnings


# =====================================================
# Texto principal (formato minimalista)
# =====================================================

def _build_calendar_text(earnings: List[Dict[str, Any]], week_start: datetime) -> str:
    week_end = week_start + timedelta(days=4)

    if not earnings:
        return (
            "📊 *Resultados empresariales de la semana*\n"
            "(Estados Unidos · compañías de alto impacto)\n\n"
            f"No hay resultados entre {week_start:%d/%m} y {week_end:%d/%m} "
            "para las compañías seguidas."
        )

    earnings_sorted = sorted(earnings, key=lambda x: (x["date"], x["company"]))

    lines: List[str] = []
    lines.append("📊 *Resultados empresariales de la semana*")
    lines.append("(Estados Unidos · compañías de alto impacto)")
    lines.append(f"Semana del {week_start:%d/%m} al {week_end:%d/%m}\n")

    last_date = None
    for e in earnings_sorted:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d")
            date_label = d.strftime("%A %d/%m").capitalize()
        except Exception:
            date_label = e["date"]

        if date_label != last_date:
            if last_date is not None:
                lines.append("")
            lines.append(f"📅 *{date_label}*")
            last_date = date_label

        lines.append(f"• {e['company']}")

    return "\n".join(lines)


# =====================================================
# Párrafo profesional IA ("Lectura de la semana")
# =====================================================

def _build_professional_note(earnings: List[Dict[str, Any]], week_start: datetime) -> str:
    week_end = week_start + timedelta(days=4)

    if not earnings:
        return (
            "\n\n📌 *Lectura de la semana*\n"
            "Esta semana no se esperan publicaciones de resultados corporativos "
            "de alto impacto en Estados Unidos."
        )

    compact = "\n".join(
        f"{e['date']} — {e['company']}"
        for e in earnings
    )

    system_prompt = (
        "Eres un analista de mercados financieros que redacta comentarios breves "
        "y profesionales para un canal de inversión. Tu estilo es claro, directo y "
        "centrado en los mensajes clave para el inversor."
    )

    user_prompt = (
        "Resume de forma concisa la relevancia semanal del siguiente calendario "
        "de resultados empresariales en Estados Unidos (alto impacto). Indica qué días "
        "concentran más publicaciones y qué sectores o tipos de compañías pueden "
        "marcar el tono del mercado. Evita emojis y no menciones que eres una IA.\n\n"
        f"{compact}"
    )

    try:
        note = call_gpt_mini(system_prompt, user_prompt)
        texto = (note or "").strip()
    except Exception as e:
        logger.error(f"earnings_weekly | Error generando nota profesional: {e}")
        texto = (
            "Los resultados concentrados en varias compañías de gran capitalización "
            "pueden influir en la volatilidad de los índices estadounidenses y en "
            "los sectores más expuestos."
        )

    return "\n\n📌 *Lectura de la semana*\n" + texto


# =====================================================
# Ejecución principal
# =====================================================

def run_weekly_earnings(force: bool = False) -> None:
    """
    Envía al canal de Telegram el resumen semanal de resultados empresariales.

    - Por defecto solo 1 envío al día (control STATE_FILE).
    - Si force=True ignora el control y envía siempre.
    - Si EARNINGS_SIMULATE_TOMORROW=1, toma como base 'hoy + 1 día'.
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

    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    earnings = fetch_weekly_earnings(week_start)

    calendar_text = _build_calendar_text(earnings, week_start)
    professional_note = _build_professional_note(earnings, week_start)

    final_message = f"{calendar_text}{professional_note}"

    send_telegram_message(final_message)
    _mark_sent(today_str)
    logger.info("earnings_weekly | Mensaje enviado correctamente.")
