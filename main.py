# === main.py ===

import os
from datetime import datetime, timedelta
from pathlib import Path
import pkg_resources

from premarket import run_premarket_morning
from econ_calendar import run_econ_calendar
from news_es import run_news_once
from earnings_weekly import run_weekly_earnings
from market_close import run_market_close


# -----------------------------------------------------
# SOLO DOS VARIABLES DE ENTORNO
# -----------------------------------------------------

TZ_OFFSET = int(os.getenv("TZ_OFFSET", "1"))
CLOSE_FORCE = os.getenv("CLOSE_FORCE", "0") == "1"

MARKERS_DIR = Path("/tmp")


def _close_marker(now_local):
    return MARKERS_DIR / f"market_close_sent_{now_local.strftime('%Y-%m-%d')}.flag"


def should_run_market_close(now_local, force=False):
    if force:
        print("[MARKET_CLOSE] FORZADO = True, enviando cierre.")
        return True

    # Ejecutar SOLO en la última pasada del cron: 22:30 hora Madrid
    if not (now_local.hour == 22 and now_local.minute >= 30):
        print("[MARKET_CLOSE] No es 22:30, no se ejecuta.")
        return False

    marker = _close_marker(now_local)
    if marker.exists():
        print("[MARKET_CLOSE] Ya se envió hoy, no repetir.")
        return False

    return True


def mark_close_sent(now_local):
    marker = _close_marker(now_local)
    marker.write_text("sent")
    print(f"[MARKET_CLOSE] Marcado como enviado: {marker}")


# -----------------------------------------------------
# MAIN
# -----------------------------------------------------

def main():
    now_utc = datetime.utcnow()
    now_local = now_utc + timedelta(hours=TZ_OFFSET)
    print(f"[MAIN] now_local={now_local}")

    # ------------- PREMARKET 10:15 y 10:30 -------------
    if now_local.hour == 10 and now_local.minute in [15, 30]:
        try:
            run_premarket_morning()
        except Exception as e:
            print(f"[PREMARKET ERROR] {e}")

    # ------------- ECON CALENDAR FORZADO -------------
    if os.getenv("ECON_FORCE", "0") == "1":
        run_econ_calendar()

    # ------------- NEWS FORZADO -------------
    if os.getenv("NEWS_FORCE", "0") == "1":
        run_news_once(force=True)

    # ------------- EARNINGS FORZADO -------------
    if os.getenv("EARNINGS_FORCE", "0") == "1":
        run_weekly_earnings(force=True)

    # ------------- CIERRE MERCADO 22:30 -------------
    if should_run_market_close(now_local, force=CLOSE_FORCE):
        try:
            run_market_close(force=CLOSE_FORCE)
            if not CLOSE_FORCE:
                mark_close_sent(now_local)
        except Exception as e:
            print(f"[MARKET_CLOSE ERROR] {e}")


if __name__ == "__main__":
    main()
