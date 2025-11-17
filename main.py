# main.py

import datetime as dt
from econ_calendar import run_econ_calendar, send_telegram_message

def main():
    """
    - Si es lunes  → resumen semanal.
    - Si es mar–vie → resumen diario.
    - Si es sábado/domingo → manda mensaje de que no hay calendario, para que
      cuando ejecutes a mano veas que el cron funciona.
    """

    today = dt.date.today()
    weekday = today.weekday()  # 0 = lunes, 1 = martes, ..., 6 = domingo

    if weekday == 0:
        # Lunes → semanal
        run_econ_calendar(mode="weekly")
    elif weekday in (1, 2, 3, 4):
        # Martes a viernes → diario
        run_econ_calendar(mode="daily")
    else:
        # Sábado / Domingo → mensaje corto para pruebas
        send_telegram_message("📆 Hoy es fin de semana: no hay calendario USA, pero el cron está OK.")

if __name__ == "__main__":
    main()
