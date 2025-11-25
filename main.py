# === main.py ===
# Orquestador InvestX (Render cron)

import os
from datetime import datetime, timedelta

import pkg_resources  # lo usa investpy por debajo, NO BORRAR

from premarket import run_premarket_morning
from econ_calendar import run_econ_calendar
from news_es import run_news_once          # Noticias
from earnings_weekly import run_weekly_earnings   # Earnings semanales
from market_close import run_market_close        # ⬅️ NUEVO IMPORT


# ---------------------------
# Configuración de franjas
# ---------------------------
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "1"))

# Franja "Buenos días" / premarket
MORNING_START_HOUR = int(os.getenv("MORNING_START_HOUR", "10"))
MORNING_END_HOUR   = int(os.getenv("MORNING_END_HOUR", "11"))

# Franja calendario económico
ECON_START_HOUR = int(os.getenv("ECON_START_HOUR", "11"))
ECON_END_HOUR   = int(os.getenv("ECON_END_HOUR", "13"))

# Flags de forzado
FORCE_MORNING  = os.getenv("FORCE_MORNING", "0").lower() in ("1", "true", "yes")
FORCE_ECON     = os.getenv("FORCE_ECON", "0").lower() in ("1", "true", "yes")
FORCE_EARNINGS = os.getenv("FORCE_EARNINGS", "0").lower() in ("1", "true", "yes")
FORCE_NEWS = any(
    os.getenv(var, "0").strip().lower() in ("1", "true", "yes")
    for var in ("FORCE_NEWS", "NEWS_FORCE", "news_force")
)

# ---- NUEVO ----
CLOSE_FORCE = os.getenv("CLOSE_FORCE", "0").lower() in ("1", "true", "yes")
# No necesitamos más variables: cierre siempre a 22:30 local Madrid


def main():
    # Hora "local" aplicando offset
    now = datetime.utcnow() + timedelta(hours=TZ_OFFSET)
    print(f"{now} | INFO | __main__: Ejecutando main.py...")

    hour = now.hour
    minute = now.minute
    weekday = now.weekday()  # 0=lunes


    # ======================================================
    # 1) "Buenos días / Premarket"
    # ======================================================
    within_morning_window = MORNING_START_HOUR <= hour < MORNING_END_HOUR

    if FORCE_MORNING:
        print("INFO | __main__: FORCE_MORNING=1 -> enviando 'Buenos días' siempre.")
        run_premarket_morning(force=True)
    else:
        if weekday < 5 and within_morning_window:
            print(f"INFO | __main__: Dentro de franja {MORNING_START_HOUR}-{MORNING_END_HOUR}h -> 'Buenos días'.")
            run_premarket_morning(force=False)
        else:
            print(f"INFO | __main__: Fuera de franja 'Buenos días' (hora={hour}, weekday={weekday}).")


    # ======================================================
    # 2) Earnings semanales (solo lunes 10–11h)
    # ======================================================
    within_earnings_window = MORNING_START_HOUR <= hour < MORNING_END_HOUR

    if FORCE_EARNINGS:
        print("INFO | __main__: FORCE_EARNINGS=1 -> enviando earnings semanales sin restricciones.")
        run_weekly_earnings(force=True)
    else:
        if weekday == 0 and within_earnings_window:
            print("INFO | __main__: Lunes dentro de franja -> Earnings semanales.")
            run_weekly_earnings(force=False)
        else:
            print(f"INFO | __main__: Earnings no enviados (hora={hour}, weekday={weekday}).")


    # ======================================================
    # 3) Calendario económico (11–13h)
    # ======================================================
    within_econ_window = ECON_START_HOUR <= hour < ECON_END_HOUR

    if FORCE_ECON:
        print("INFO | __main__: FORCE_ECON=1 -> enviando calendario económico sin restricciones.")
        run_econ_calendar(force=True)
    else:
        if weekday < 5 and within_econ_window:
            print(f"INFO | __main__: Dentro de franja {ECON_START_HOUR}-{ECON_END_HOUR}h -> Calendario económico.")
            run_econ_calendar(force=False)
        else:
            print(f"INFO | __main__: Calendario no enviado (hora={hour}, weekday={weekday}).")


    # ======================================================
    # 4) Noticias (control interno dentro del script)
    # ======================================================
    if FORCE_NEWS:
        print("INFO | __main__: FORCE_NEWS=1 -> noticias forzadas.")
        run_news_once(force=True)
    else:
        if weekday < 5:
            print("INFO | __main__: Evaluando envío de noticias (L-V).")
            run_news_once(force=False)
        else:
            print(f"INFO | __main__: Fin de semana -> no se evalúan noticias.")


    # ======================================================
    # 5) ⬅️ CIERRE MERCADO USA (22:30)
    # ======================================================

    # Solo enviamos cierre una vez por día y solo a 22:30 local Madrid.
    if CLOSE_FORCE or (hour == 22 and minute >= 30):
        print("INFO | __main__: Ejecutando cierre de mercado USA.")
        try:
            run_market_close(force=CLOSE_FORCE)
        except Exception as e:
            print(f"ERROR | __main__: Error en run_market_close: {e}")
    else:
        print(f"INFO | __main__: No es hora de cierre (hora={hour}:{minute}).")


if __name__ == "__main__":
    main()
