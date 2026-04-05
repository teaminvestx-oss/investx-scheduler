# === earnings_weekly.py ===
# Earnings semanales — fuente primaria: Yahoo Finance calendar (automático)
# Filtra por cobertura de analistas (epsestimate != null) = alto impacto
# Fallback: lista curada si Yahoo Finance no responde

import os
import re
import json
import logging
from datetime import datetime, timedelta, date
from typing import List, Dict, Any, Optional

import requests
import yfinance as yf

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_FILE = "earnings_weekly_state.json"
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "1"))

YF_TIMEOUT = int(os.getenv("EARNINGS_YF_TIMEOUT", "20"))

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
# Fuente primaria: Yahoo Finance calendar
# =====================================================

_YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _fetch_yf_calendar_day(target_date: date) -> List[Dict[str, Any]]:
    """
    Descarga el calendario de earnings de Yahoo Finance para un día.
    Filtra: solo US equities con estimación de EPS (cobertura de analistas = alto impacto).
    """
    date_str = target_date.isoformat()

    try:
        resp = requests.get(
            "https://finance.yahoo.com/calendar/earnings",
            params={"day": date_str},
            headers=_YF_HEADERS,
            timeout=YF_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"earnings | Yahoo Finance HTTP error {date_str}: {e}")
        return []

    # Extraer JSON embebido en __NEXT_DATA__
    try:
        match = re.search(
            r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            resp.text,
            re.DOTALL,
        )
        if not match:
            logger.warning(f"earnings | No __NEXT_DATA__ en Yahoo Finance para {date_str}")
            return []
        data = json.loads(match.group(1))
    except Exception as e:
        logger.warning(f"earnings | Error parseando __NEXT_DATA__ ({date_str}): {e}")
        return []

    # Navegar la estructura (puede variar según versión de Yahoo Finance)
    rows = []
    paths = [
        ["props", "pageProps", "state", "calendar", "earnings", "rows"],
        ["props", "pageProps", "earningsCalendar", "rows"],
        ["props", "pageProps", "calendars", "earnings", "rows"],
    ]
    for path in paths:
        try:
            node = data
            for key in path:
                node = node[key]
            if isinstance(node, list):
                rows = node
                break
        except (KeyError, TypeError):
            continue

    if not rows:
        logger.warning(f"earnings | Estructura inesperada en Yahoo Finance para {date_str}")
        return []

    results = []
    for row in rows:
        ticker = (row.get("ticker") or "").strip()
        company = (row.get("companyshortname") or ticker).strip()
        quote_type = (row.get("quoteType") or "").upper()
        eps_est = row.get("epsestimate")
        time_type = row.get("startdatetimetype") or "—"

        if not ticker:
            continue
        # Solo equities con cobertura de analistas (proxy de alto impacto)
        if quote_type and quote_type != "EQUITY":
            continue
        if eps_est is None:
            continue

        results.append({
            "date": date_str,
            "company": company,
            "eps": f"Est. {eps_est:.2f}" if isinstance(eps_est, (int, float)) else "--",
            "revenue": "--",
            "time": time_type,
        })

    logger.info(f"earnings | Yahoo Finance {date_str}: {len(results)} empresas con cobertura")
    return results


# =====================================================
# Fuente fallback: lista curada + yfinance calendar
# =====================================================

TICKER_NAMES: Dict[str, str] = {
    "AAPL": "Apple", "MSFT": "Microsoft", "AMZN": "Amazon",
    "NVDA": "Nvidia", "GOOGL": "Alphabet (Google)", "META": "Meta", "TSLA": "Tesla",
    "ADBE": "Adobe", "CRM": "Salesforce", "NOW": "ServiceNow", "ORCL": "Oracle",
    "IBM": "IBM", "CSCO": "Cisco", "INTU": "Intuit",
    "AMD": "AMD", "INTC": "Intel", "AVGO": "Broadcom", "QCOM": "Qualcomm",
    "TXN": "Texas Instruments", "MU": "Micron", "AMAT": "Applied Materials",
    "JPM": "JPMorgan Chase", "BAC": "Bank of America", "C": "Citigroup",
    "GS": "Goldman Sachs", "MS": "Morgan Stanley", "WFC": "Wells Fargo",
    "AXP": "American Express", "V": "Visa", "MA": "Mastercard", "BLK": "BlackRock",
    "PGR": "Progressive", "TRV": "Travelers", "CB": "Chubb", "MET": "MetLife",
    "JNJ": "Johnson & Johnson", "LLY": "Eli Lilly", "ABBV": "AbbVie",
    "UNH": "UnitedHealth", "PFE": "Pfizer", "MRK": "Merck", "CVS": "CVS Health",
    "HUM": "Humana", "CI": "Cigna", "ELV": "Elevance Health",
    "XOM": "ExxonMobil", "CVX": "Chevron", "COP": "ConocoPhillips",
    "STZ": "Constellation Brands", "KO": "Coca-Cola", "PEP": "PepsiCo",
    "PG": "Procter & Gamble", "PM": "Philip Morris", "WMT": "Walmart",
    "COST": "Costco", "TGT": "Target", "MCD": "McDonald's", "HD": "Home Depot",
    "NKE": "Nike", "SBUX": "Starbucks",
    "NFLX": "Netflix", "DIS": "Walt Disney", "CMCSA": "Comcast",
    "BA": "Boeing", "CAT": "Caterpillar", "GE": "GE Aerospace",
    "HON": "Honeywell", "RTX": "RTX Corp", "LMT": "Lockheed Martin",
    "UPS": "UPS", "FDX": "FedEx", "DAL": "Delta Air Lines",
    "T": "AT&T", "VZ": "Verizon",
    "PYPL": "PayPal", "BKNG": "Booking Holdings", "UBER": "Uber",
    "SPOT": "Spotify", "COIN": "Coinbase",
}


def _extract_earnings_date(cal) -> Optional[date]:
    if cal is None:
        return None

    raw_dates = []
    if isinstance(cal, dict):
        raw = cal.get("Earnings Date")
        if raw is None:
            return None
        raw_dates = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    else:
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
                found.append(datetime.strptime(d[:10], "%Y-%m-%d").date())
        except Exception:
            continue
    return min(found) if found else None


def _fetch_fallback_week(week_start: datetime) -> List[Dict[str, Any]]:
    """Lista curada + yfinance calendar como último recurso."""
    week_dates: set = set()
    for i in range(5):
        week_dates.add((week_start + timedelta(days=i)).date())

    earnings = []
    for ticker_sym, company_name in TICKER_NAMES.items():
        try:
            ed = _extract_earnings_date(yf.Ticker(ticker_sym).calendar)
            if ed and ed in week_dates:
                earnings.append({
                    "date": ed.isoformat(),
                    "company": company_name,
                    "eps": "--", "revenue": "--", "time": "—",
                })
        except Exception as e:
            logger.warning(f"earnings | fallback {ticker_sym}: {e}")

    logger.info(f"earnings | Fallback curado: {len(earnings)} resultados")
    return earnings


# =====================================================
# Fetch semanal (Yahoo Finance + fallback)
# =====================================================

def fetch_weekly_earnings(week_start: datetime) -> List[Dict[str, Any]]:
    """
    Obtiene los earnings L-V de la semana.
    Fuente primaria: Yahoo Finance calendar (automático, sin lista manual).
    Fallback: lista curada de ~70 tickers vía yfinance.
    """
    earnings: List[Dict[str, Any]] = []
    failed_days = 0

    for i in range(5):
        day = (week_start + timedelta(days=i)).date()
        day_results = _fetch_yf_calendar_day(day)
        if not day_results:
            failed_days += 1
        earnings.extend(day_results)

    if failed_days >= 3:
        logger.warning("earnings | Yahoo Finance falló ≥3 días, usando lista curada como fallback")
        return _fetch_fallback_week(week_start)

    logger.info(f"earnings | Total semana Yahoo Finance: {len(earnings)} empresas")
    return earnings


# =====================================================
# Texto principal (formato minimalista)
# =====================================================

def _build_calendar_text(earnings: List[Dict[str, Any]], week_start: datetime) -> str:
    week_end = week_start + timedelta(days=4)

    if not earnings:
        return (
            "📊 *Resultados empresariales de la semana*\n"
            "(Estados Unidos · alto impacto)\n\n"
            f"No hay resultados entre {week_start:%d/%m} y {week_end:%d/%m} "
            "bajo los filtros aplicados."
        )

    earnings_sorted = sorted(earnings, key=lambda x: (x["date"], x["company"]))

    lines: List[str] = []
    lines.append("📊 *Resultados empresariales de la semana*")
    lines.append("(Estados Unidos · alto impacto)")
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

    compact = "\n".join(f"{e['date']} — {e['company']}" for e in earnings)

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
        texto = (call_gpt_mini(system_prompt, user_prompt) or "").strip()
    except Exception as e:
        logger.error(f"earnings | Error nota profesional: {e}")
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
    simulate_tomorrow = (
        os.getenv("EARNINGS_SIMULATE_TOMORROW", "0").strip().lower()
        in ("1", "true", "yes")
    )

    now = datetime.utcnow() + timedelta(hours=TZ_OFFSET)
    if simulate_tomorrow:
        now += timedelta(days=1)

    today_str = now.strftime("%Y-%m-%d")

    logger.info(
        f"earnings | run_weekly_earnings(force={force}, "
        f"simulate_tomorrow={simulate_tomorrow}, today={today_str})"
    )

    if not force and _already_sent(today_str):
        logger.info("earnings | Ya se envió hoy. No se repite.")
        return

    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    earnings = fetch_weekly_earnings(week_start)

    calendar_text = _build_calendar_text(earnings, week_start)
    professional_note = _build_professional_note(earnings, week_start)

    send_telegram_message(f"{calendar_text}{professional_note}")
    _mark_sent(today_str)
    logger.info("earnings | Mensaje enviado correctamente.")
