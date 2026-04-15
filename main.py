# === main.py ===
# Orquestador InvestX (Render cron)
#
# AJUSTE (pedido):
# - Earnings semanales: SOLO 1 vez por semana, SOLO los lunes,
#   y SOLO en la pasada de las 10:30 (hora local Madrid) o posterior dentro de esa hora.
# - Noticias: SOLO 2 disparos al día (13:30 y 21:30 hora local Madrid), L-V.
# - TODO lo demás: EXACTAMENTE IGUAL.
#
# NUEVO (pedido):
# - En festivos USA (NYSE cerrado) aunque sea L-V:
#   -> NO enviar: Premarket, Calendario económico, Market Close
#   -> SÍ enviar: Noticias y Earnings (sin cambios)

import os
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from us_market_calendar import is_nyse_trading_day

from premarket import run_premarket_morning
from econ_calendar import run_econ_calendar
from news_es import run_news_once
from earnings_weekly import run_weekly_earnings
from market_close import run_market_close
from insider_trading import run_daily_insider
from congressional_trades import run_congressional_trades


# ---------------------------
# Configuración de franjas
# ---------------------------
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "1"))

MORNING_START_HOUR = int(os.getenv("MORNING_START_HOUR", "10"))
MORNING_END_HOUR   = int(os.getenv("MORNING_END_HOUR", "11"))

ECON_START_HOUR = int(os.getenv("ECON_START_HOUR", "11"))
ECON_END_HOUR   = int(os.getenv("ECON_END_HOUR", "13"))

# Flags de forzado
FORCE_MORNING  = os.getenv("FORCE_MORNING", "0").lower() in ("1", "true", "yes")
FORCE_ECON     = os.getenv("FORCE_ECON", "0").lower() in ("1", "true", "yes")

# 🔴 NUEVO: forzar calendario de MAÑANA aunque sea finde/festivo
ECON_FORCE_TOMORROW = os.getenv("ECON_FORCE_TOMORROW", "0").strip().lower() in ("1", "true", "yes")

FORCE_NEWS = any(
    os.getenv(var, "0").strip().lower() in ("1", "true", "yes")
    for var in ("FORCE_NEWS", "NEWS_FORCE", "news_force")
)

FORCE_EARNINGS  = os.getenv("FORCE_EARNINGS",  "0").strip().lower() in ("1", "true", "yes")
CLOSE_FORCE     = os.getenv("CLOSE_FORCE",     "0").strip().lower() in ("1", "true", "yes")
FORCE_INSIDER   = os.getenv("FORCE_INSIDER",   "0").strip().lower() in ("1", "true", "yes")
FORCE_CONGRESS  = os.getenv("FORCE_CONGRESS",  "0").strip().lower() in ("1", "true", "yes")


# ======================================================
# STATE para evitar duplicados de EARNINGS (1 vez/semana)
# ======================================================
EARNINGS_STATE_FILE = "earnings_weekly_state.json"

def _load_earnings_state():
    if not os.path.exists(EARNINGS_STATE_FILE):
        return {}
    try:
        with open(EARNINGS_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def _save_earnings_state(d):
    try:
        with open(EARNINGS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except:
        pass

def _earnings_week_key(dt_local: datetime) -> str:
    iso_year, iso_week, _ = dt_local.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"

def _earnings_already_sent_this_week(dt_local: datetime) -> bool:
    st = _load_earnings_state()
    return st.get("sent_week") == _earnings_week_key(dt_local)

def _mark_earnings_sent(dt_local: datetime):
    st = _load_earnings_state()
    st["sent_week"] = _earnings_week_key(dt_local)
    _save_earnings_state(st)


def main():
    now = datetime.now(ZoneInfo("Europe/Madrid"))
    print(f"{now} | INFO | __main__: Ejecutando main.py...")

    hour = now.hour
    minute = now.minute
    weekday = now.weekday()  # 0=lunes, 6=domingo

    try:
        nyse_open_today = is_nyse_trading_day(now)
    except Exception as e:
        print(f"WARNING | __main__: Fallo al evaluar calendario NYSE ({e}). Continuando sin filtro.")
        nyse_open_today = True

    INSIDER_HOUR   = 10
    INSIDER_MINUTE = 15

    CONGRESS_HOUR   = 14
    CONGRESS_MINUTE = 30

    PREMARKET_HOUR = 10
    PREMARKET_MINUTE = 30

    EARNINGS_HOUR = 10
    EARNINGS_MINUTE = 30

    ECON_HOUR = 11
    ECON_MINUTE = 30

    NEWS_HOUR_1 = 13
    NEWS_HOUR_2 = 21
    NEWS_MINUTE = 30

    # ======================================================
    # 0) INSIDER TRADING (L-V 10:15, operaciones de los últimos 2-3 días)
    # ======================================================
    if FORCE_INSIDER:
        run_daily_insider(force=True)
    else:
        if weekday < 5 and hour == INSIDER_HOUR and minute >= INSIDER_MINUTE:
            run_daily_insider(force=False)

    # ======================================================
    # 0b) CONGRESISTAS USA (L-V 14:30, declaraciones recientes)
    # ======================================================
    if FORCE_CONGRESS:
        run_congressional_trades(force=True)
    else:
        if weekday < 5 and hour == CONGRESS_HOUR and minute >= CONGRESS_MINUTE:
            run_congressional_trades(force=False)

    # ======================================================
    # 1) PREMARKET
    # ======================================================
    if FORCE_MORNING:
        run_premarket_morning(force=True)
    else:
        if weekday < 5 and nyse_open_today and hour == PREMARKET_HOUR and minute >= PREMARKET_MINUTE:
            run_premarket_morning(force=False)

    # ======================================================
    # 2) EARNINGS (SIN CAMBIOS)
    # ======================================================
    if FORCE_EARNINGS:
        run_weekly_earnings(force=True)
    else:
        if weekday == 0:
            if hour == EARNINGS_HOUR and minute >= EARNINGS_MINUTE:
                if not _earnings_already_sent_this_week(now):
                    run_weekly_earnings(force=False)
                    _mark_earnings_sent(now)

    # ======================================================
    # 3) CALENDARIO ECONÓMICO
    # ======================================================
    if ECON_FORCE_TOMORROW:
        print("INFO | __main__: ECON_FORCE_TOMORROW=1 -> enviando calendario de mañana.")
        run_econ_calendar(force=True, force_tomorrow=True)

    elif FORCE_ECON:
        run_econ_calendar(force=True)

    else:
        if weekday < 5 and nyse_open_today and hour == ECON_HOUR and minute >= ECON_MINUTE:
            run_econ_calendar(force=False)

    # ======================================================
    # 4) NOTICIAS (SIN CAMBIOS)
    # ======================================================
    if FORCE_NEWS:
        run_news_once(force=True)
    else:
        if weekday < 5:
            if ((hour == NEWS_HOUR_1 and minute >= NEWS_MINUTE) or
                (hour == NEWS_HOUR_2 and minute >= NEWS_MINUTE)):
                run_news_once(force=False)

    # ======================================================
    # 5) MARKET CLOSE (SIN CAMBIOS)
    # ======================================================
    if CLOSE_FORCE:
        run_market_close(force=True)
    else:
        if weekday < 5 and nyse_open_today and hour == 22 and minute >= 30:
            run_market_close(force=False)


if __name__ == "__main__":
    main()
