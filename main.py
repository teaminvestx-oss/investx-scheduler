# === main.py ===
# Orquestador InvestX (Render cron)

import os
from datetime import datetime, timedelta

import pkg_resources  # lo usa investpy por debajo, NO BORRAR

from premarket import run_premarket_morning
from econ_calendar import run_econ_calendar
from news_es import run_news_once          # Noticias
from earnings_weekly import run_weekly_earnings   # Earnings semanales
from market_close import run_market_close         # === MARKET CLOSE NUEVO ===


# ---------------------------
# Configuración de franjas
# ---------------------------
# Offset horario respecto a UTC (para Madrid normalmente 1 en horario normal)
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "1"))

# Estas franjas las dejamos por compatibilidad con earnings/noticias
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

# === MARKET CLOSE NUEVO ===
# Forzado de cierre de mercado (para pruebas)
CLOSE_FORCE = os.getenv("CLOSE_FORCE", "0").strip().lower() in ("1", "true", "yes")


def main():
    # Hora "local" aplicando offset (Madrid)
    now = datetime.utcnow() + timedelta(hours=TZ_OFFSET)
    print(f"{now} | INFO | __main__: Ejecutando main.py...")

    hour = now.hour
    minute = now.minute
    weekday = now.weekday()  # 0=lunes, 6=domingo

    # Render lanza a 9:15, 9:30, 10:15, 10:30, 21:15, 21:30 UTC
    # -> 10:15, 10:30, 11:15, 11:30, 22:15, 22:30 hora Madrid (TZ_OFFSET=1)

    # Hora única de disparo para PREMARKET (10:30 local)
    PREMARKET_HOUR = 10
    PREMARKET_MINUTE = 30

    # Hora única de disparo para CALENDARIO (11:30 local)
    ECON_HOUR = 11
    ECON_MINUTE = 30

    # ======================================================
    # 1) "Buenos días / Premarket" -> SOLO una vez al día
    # ======================================================
    if FORCE_MORNING:
        print("INFO | __main__: FORCE_MORNING=1 -> enviando 'Buenos días' siempre.")
        run_premarket_morning(force=True)
    else:
        if (
            weekday < 5
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
                f"(weekday={weekday}, hour={hour}, minute={minute})."
            )

    # ======================================================
    # 2) Earnings semanales (lunes 10–11h) -> se mantiene igual
    # ======================================================
    within_earnings_window = MORNING_START_HOUR <= hour < MORNING_END_HOUR

    if FORCE_EARNINGS:
        print("INFO | __main__: FORCE_EARNINGS=1 -> enviando earnings semanales sin restricciones.")
        run_weekly_earnings(force=True)
    else:
        if weekday == 0 and within_earnings_window:
            print(
                "INFO | __main__: Lunes y dentro de franja "
                f"{MORNING_START_HOUR}-{MORNING_END_HOUR}h -> evaluando earnings semanales."
            )
            run_weekly_earnings(force=False)
        else:
            print(
                "INFO | __main__: No es lunes o fuera de franja para earnings semanales "
                f"(hora={hour}, weekday={weekday}). No se envían."
            )

    # ======================================================
    # 3) Calendario económico -> SOLO una vez al día (11:30)
    # ======================================================
    if FORCE_ECON:
        print("INFO | __main__: FORCE_ECON=1 -> enviando calendario económico sin restricciones.")
        run_econ_calendar(force=True)
    else:
        if (
            weekday < 5
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
                f"(weekday={weekday}, hour={hour}, minute={minute})."
            )

    # ======================================================
    # 4) Noticias (franjas internas en news_es.run_news_once)
    # ======================================================
    if FORCE_NEWS:
        print("INFO | __main__: FORCE_NEWS=1 -> enviando noticias sin restricciones.")
        run_news_once(force=True)
    else:
        if weekday < 5:
            print("INFO | __main__: Evaluando envío de noticias (L-V).")
            run_news_once(force=False)
        else:
            print(f"INFO | __main__: Fin de semana (weekday={weekday}) -> no se evalúan noticias.")

    # ======================================================
    # 5) Market Close USA -> SOLO última ejecución del día (noche)
    # ======================================================
    # Última ejecución del cron: 22:30 hora Madrid (TZ_OFFSET=1)
    # Para evitar problemas si Render arranca con 1–2 min de retraso, usamos minute >= 30
    if CLOSE_FORCE:
        print("INFO | __main__: CLOSE_FORCE=1 -> enviando Market Close sin restricciones.")
        run_market_close(force=True)      # <- CORREGIDO
    else:
        if weekday < 5 and hour == 22 and minute >= 30:
            print("INFO | __main__: Última pasada del día (>=22:30) -> enviando Market Close.")
            run_market_close(force=False)  # <- CORREGIDO
        else:
            print(
                "INFO | __main__: Market Close NO enviado "
                f"(weekday={weekday}, hour={hour}, minute={minute})."
            )


if __name__ == "__main__":
    main()
