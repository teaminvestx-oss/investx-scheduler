# === main.py ===
import datetime as dt
from econ_calendar import run_econ_calendar, send_telegram_message

def main():
    today = dt.date.today()
    weekday = today.weekday()  # 0 = lunes, ..., 6 = domingo

    if weekday == 0:
        # Lunes → resumen semanal
        run_econ_calendar(mode="weekly")
    elif weekday in (1, 2, 3, 4):
        # Martes a viernes → resumen diario
        run_econ_calendar(mode="daily")
    else:
        # Sábado / domingo → mensaje corto (útil cuando le das a "Run now")
        send_telegram_message("📆 Hoy es fin de semana: no hay calendario USA, pero el cron está OK.")

if __name__ == "__main__":
    main()
