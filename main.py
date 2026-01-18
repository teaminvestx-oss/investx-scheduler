# === main.py ===
# Orquestador InvestX (Render cron)
#
# AJUSTE (pedido):
# - Earnings semanales: SOLO 1 vez por semana, SOLO los lunes,
#   y SOLO en la pasada de las 10:30 (hora local Madrid) o posterior dentro de esa hora.
#   (tolerancia natural: si el cron se retrasa, minute >= 30 sigue entrando)
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

import pkg_resources  # lo usa investpy por debajo, NO BORRAR

from zoneinfo import ZoneInfo
from us_market_calendar import is_nyse_trading_day  # NUEVO helper

from premarket import run_premarket_morning
from econ_calendar import run_econ_calendar
from news_es import run_news_once          # Noticias
from earnings_weekly import run_weekly_earnings   # Earnings semanales
from market_close import run_market_close         # Market Close


# ---------------------------
# Configuración de franjas
# ---------------------------
# Offset horario respecto a UTC (para Madrid normalmente 1 en horario normal)
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "1"))

# Estas franjas las dejamos por compatibilidad con earnings/noticias (no gobiernan el disparo de earnings ya)
MORNING_START_HOUR = int(os.getenv("MORNING_START_HOUR", "10"))
MORNING_END_HOUR   = int(os.getenv("MORNING_END_HOUR", "11"))

ECON_START_HOUR = int(os.getenv("ECON_START_HOUR", "11"))
ECON_END_HOUR   = int(os.getenv("ECON_END_HOUR", "13"))

# Flags de forzado desde variables de entorno
FORCE_MORNING  = os.getenv("FORCE_MORNING", "0").lower() in ("1", "true", "yes")
FORCE_ECON     = os.getenv("FORCE_ECON", "0").lower() in ("1", "true", "yes")

# Variantes para noticias
FORCE_NEWS = any(
    os.getenv(var, "0").strip().lower() in ("1", "true", "yes")
    for var in ("FORCE_NEWS", "NEWS_FORCE", "news_force")
)

# Forzado de earnings semanales
FORCE_EARNINGS = os.getenv("FORCE_EARNINGS", "0").strip().lower() in ("1", "true", "yes")

# Forzado de cierre de mercado (para pruebas)
CLOSE_FORCE = os.getenv("CLOSE_FORCE", "0").strip().lower() in ("1", "true", "yes")


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
    # Hora local REAL de Madrid (evita problemas de cambios horarios)
    now = datetime.now(ZoneInfo("Europe/Madrid"))
    print(f"{now} | INFO | __main__: Ejecutando main.py...")

    hour = now.hour
    minute = now.minute
    weekday = now.weekday()  # 0=lunes, 6=domingo

    # NUEVO: ¿NYSE abre hoy? (se calcula 1 vez por ejecución)
    try:
        nyse_open_today = is_nyse_trading_day(now)
    except Exception as e:
        # Fail-safe: si falla el calendario, NO bloqueamos (se comporta como antes).
        print(f"WARNING | __main__: Fallo al evaluar calendario NYSE ({e}). Continuando sin filtro de festivos.")
        nyse_open_today = True

    # Hora única de disparo para PREMARKET (10:30 local)
    PREMARKET_HOUR = 10
    PREMARKET_MINUTE = 30

    # Hora única de disparo para EARNINGS (lunes, 10:30 local, con tolerancia por retraso)
    EARNINGS_HOUR = 10
    EARNINGS_MINUTE = 30

    # Hora única de disparo para CALENDARIO (11:30 local)
    ECON_HOUR = 11
    ECON_MINUTE = 30

    # Noticias: 2 disparos al día (13:30 y 21:30 local)
    NEWS_HOUR_1 = 13
    NEWS_HOUR_2 = 21
    NEWS_MINUTE = 30

    # ======================================================
    # 1) "Buenos días / Premarket" -> SOLO una vez al día
    #    NUEVO: solo si NYSE abre hoy (si es festivo USA, se bloquea)
    # ======================================================
    if FORCE_MORNING:
        print("INFO | __main__: FORCE_MORNING=1 -> enviando 'Buenos días' siempre.")
        run_premarket_morning(force=True)
    else:
        if (
            weekday < 5
            and nyse_open_today
            and hour == PREMARKET_HOUR
            and minute >= PREMARKET_MINUTE
        ):
            print(
                f"INFO | __main__: Activando 'Buenos días' en "
                f"{PREMARKET_HOUR:02d}:{PREMARKET_MINUTE:02d}+ (>= minuto)."
            )
            run_premarket_morning(force=False)
        else:
            print(
                "INFO | __main__: 'Buenos días' NO enviado "
                f"(weekday={weekday}, hour={hour}, minute={minute}, nyse_open_today={nyse_open_today})."
            )

    # ======================================================
    # 2) Earnings semanales -> SOLO lunes, SOLO 10:30+ y SOLO 1 vez/semana
    #    (SIN CAMBIOS: se envía aunque sea festivo NYSE)
    # ======================================================
    if FORCE_EARNINGS:
        print("INFO | __main__: FORCE_EARNINGS=1 -> enviando earnings semanales sin restricciones.")
        run_weekly_earnings(force=True)
    else:
        if weekday == 0:
            within_earnings_trigger = (hour == EARNINGS_HOUR and minute >= EARNINGS_MINUTE)

            if within_earnings_trigger:
                if _earnings_already_sent_this_week(now):
                    print(
                        "INFO | __main__: Earnings semanales YA enviados esta semana "
                        f"({ _earnings_week_key(now) }). No se reenvían."
                    )
                else:
                    print(
                        f"INFO | __main__: Lunes {EARNINGS_HOUR:02d}:{EARNINGS_MINUTE:02d}+ "
                        "-> enviando earnings semanales (1 vez/semana)."
                    )
                    run_weekly_earnings(force=False)
                    _mark_earnings_sent(now)
            else:
                print(
                    "INFO | __main__: Earnings semanales NO enviados "
                    f"(lunes pero fuera del disparo 10:30+, hour={hour}, minute={minute})."
                )
        else:
            print(
                "INFO | __main__: No es lunes -> no se evalúan earnings semanales "
                f"(weekday={weekday})."
            )

    # ======================================================
    # 3) Calendario económico -> SOLO una vez al día (11:30)
    #    NUEVO: solo si NYSE abre hoy (si es festivo USA, se bloquea)
    # ======================================================
    if FORCE_ECON:
        print("INFO | __main__: FORCE_ECON=1 -> enviando calendario económico sin restricciones.")
        run_econ_calendar(force=True)
    else:
        if (
            weekday < 5
            and nyse_open_today
            and hour == ECON_HOUR
            and minute >= ECON_MINUTE
        ):
            print(
                f"INFO | __main__: Activando 'Calendario económico' en "
                f"{ECON_HOUR:02d}:{ECON_MINUTE:02d}+ (>= minuto)."
            )
            run_econ_calendar(force=False)
        else:
            print(
                "INFO | __main__: 'Calendario económico' NO enviado "
                f"(weekday={weekday}, hour={hour}, minute={minute}, nyse_open_today={nyse_open_today})."
            )

    # ======================================================
    # 4) Noticias -> SOLO 13:30 y 21:30 (hora local), L-V
    #    (SIN CAMBIOS: se envía aunque sea festivo NYSE)
    # ======================================================
    if FORCE_NEWS:
        print("INFO | __main__: FORCE_NEWS=1 -> enviando noticias sin restricciones.")
        run_news_once(force=True)
    else:
        if weekday < 5:
            if ((hour == NEWS_HOUR_1 and minute >= NEWS_MINUTE) or
                (hour == NEWS_HOUR_2 and minute >= NEWS_MINUTE)):
                print(
                    f"INFO | __main__: Activando 'Noticias' en {hour:02d}:{NEWS_MINUTE:02d}+ (>= minuto)."
                )
                run_news_once(force=False)
            else:
                print(
                    "INFO | __main__: Noticias NO enviadas "
                    f"(weekday={weekday}, hour={hour}, minute={minute})."
                )
        else:
            print(f"INFO | __main__: Fin de semana (weekday={weekday}) -> no se evalúan noticias.")

    # ======================================================
    # 5) Market Close USA -> SOLO última ejecución del día (noche)
    #    NUEVO: solo si NYSE abre hoy (si es festivo USA, se bloquea)
    # ======================================================
    # Para evitar problemas si Render arranca con retraso, usamos minute >= 30
    if CLOSE_FORCE:
        print("INFO | __main__: CLOSE_FORCE=1 -> enviando Market Close sin restricciones.")
        run_market_close(force=True)
    else:
        if weekday < 5 and nyse_open_today and hour == 22 and minute >= 30:
            print("INFO | __main__: Última pasada del día (>=22:30) -> enviando Market Close.")
            run_market_close(force=False)
        else:
            print(
                "INFO | __main__: Market Close NO enviado "
                f"(weekday={weekday}, hour={hour}, minute={minute}, nyse_open_today={nyse_open_today})."
            )


if __name__ == "__main__":
    main()
