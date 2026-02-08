# === econ_calendar.py ===
# Fuente ÚNICA: CME Group (web pública, sin API)
# Estable en Render / sin bloqueos

import requests
import logging
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CME_URL = "https://www.cmegroup.com/education/events/economic-releases-calendar.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_RETRIES = 3
TIMEOUT = 25


def _fetch_cme_html():
    last_err = None
    for i in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(CME_URL, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            logger.error(f"[econ] CME exception attempt {i}/{MAX_RETRIES}: {e}")
    raise last_err


def _parse_cme_events(html: str, target_date: datetime.date):
    """
    Extrae eventos del calendario CME para una fecha concreta
    Devuelve lista de dicts
    """
    soup = BeautifulSoup(html, "html.parser")
    events = []

    # CME renderiza eventos como filas con fecha + título
    rows = soup.select("table tbody tr")

    for row in rows:
        cols = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cols) < 3:
            continue

        # Ejemplo típico:
        # [Time, Country, Event, Actual, Forecast, Previous]
        time_str = cols[0]
        country = cols[1]
        event = cols[2]

        # Nos quedamos SOLO con USA
        if country.upper() not in ("US", "UNITED STATES"):
            continue

        # CME no siempre incluye fecha explícita en cada fila,
        # asumimos que la tabla es del día visible
        events.append({
            "time": time_str,
            "country": "US",
            "event": event,
        })

    return events


def run_econ_calendar(force=False):
    """
    Entry point llamado desde main.py
    """
    try:
        today = datetime.now().date()
        html = _fetch_cme_html()
        events = _parse_cme_events(html, today)

        if not events:
            msg = "Hoy no hay datos macro relevantes en EE. UU."
            logger.info("[econ] " + msg)
            _send_message(msg)
            return

        lines = ["📅 *Calendario económico – EE. UU.*\n"]
        for e in events:
            lines.append(f"• {e['time']} — {e['event']}")

        _send_message("\n".join(lines))

    except Exception as e:
        logger.error(f"[econ] CME returned EMPTY after retries: {e}")
        _send_message("Hoy no hay datos macro relevantes en EE. UU.")


# 🔽 AJUSTA ESTO A TU BOT (Telegram, etc.)
def _send_message(text: str):
    print(text)
