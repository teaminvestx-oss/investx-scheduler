# === main.py ===
# Orquestador InvestX (Render cron)

import os
from datetime import datetime, timedelta

import pkg_resources  # lo usa investpy por debajo, NO BORRAR

from premarket import run_premarket_morning
from econ_calendar import run_econ_calendar
from news_es import run_news_once          # Noticias
from earnings_weekly import run_weekly_earnings   # Earnings semanales


# ---------------------------
# Configuración de franjas
# ---------------------------
# Offset horario respecto a UTC (para Madrid normalmente 1 en horario normal)
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "1"))

# Franja "Buenos días" / premarket
MORNING_START_HOUR = int(os.getenv("MORNING_START_HOUR", "10"))
MORNING_END_HOUR   = int(os.getenv("MORNING_END_HOUR", "11"))

# Franja calendario económico
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


def main():
    # Hora "local" aplicando offset
    now = datetime.utcnow() + timedelta(hours=TZ_OFFSET)
    print(f"{now} | INFO | __main__: Ejecutando main.py...")

    hour = now.hour
    weekday = now.weekday()  # 0=lunes, 6=domingo

    # ======================================================
    # 📌 BLOQUE 1 — “Buenos días / Premarket”
    # ======================================================
    within_morning_window = MORNING_START_HOUR <= hour < MORNING_END_HOUR

    if FORCE_MORNING:
        print("INFO | __main__: FORCE_MORNING=1 -> enviando 'Buenos días' siempre.")
        run_premarket_morning(force=True)
    else:
        if weekday < 5 and within_morning_window:
            print(f"INFO | __main__: Dentro de franja {MORNING_START_HOUR}-{MORNING_END_HOUR}h para 'Buenos días'.")
            run_premarket_morning(force=False)
        else:
            print(f"INFO | __main__: Fuera de franja para 'Buenos días' (hora={hour}, weekday={weekday}). No se envía.")

    # ======================================================
    # 📌 BLOQUE 2 — Earnings semanales (lunes 10–11h)
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
            # El propio módulo controla que solo se envíe una vez al día
            run_weekly_earnings(force=False)
        else:
            print(
                "INFO | __main__: No es lunes o fuera de franja para earnings semanales "
                f"(hora={hour}, weekday={weekday}). No se envían."
            )

    # ======================================================
    # 📌 BLOQUE 3 — Calendario económico (11–13h)
    # ======================================================

    within_econ_window = ECON_START_HOUR <= hour < ECON_END_HOUR

    if FORCE_ECON:
        print("INFO | __main__: FORCE_ECON=1 -> enviando calendario económico sin restricciones.")
        run_econ_calendar(force=True)
    else:
        if weekday < 5 and within_econ_window:
            print(f"INFO | __main__: Dentro de franja {ECON_START_HOUR}-{ECON_END_HOUR}h -> calendario económico.")
            run_econ_calendar(force=False)
        else:
            print(
                f"INFO | __main__: Fuera de franja para 'Calendario económico' "
                f"(hora={hour}, weekday={weekday}). No se envía."
            )

    # ======================================================
    # 📌 BLOQUE 4 — Noticias (franjas internas del módulo)
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


if __name__ == "__main__":
    main()
