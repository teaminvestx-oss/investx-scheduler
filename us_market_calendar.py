# us_market_calendar.py
from datetime import datetime, date, time
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal


NY_TZ = ZoneInfo("America/New_York")
NYSE = mcal.get_calendar("NYSE")


def is_nyse_trading_day(dt_madrid: datetime) -> bool:
    """
    Devuelve True si en la fecha correspondiente en NY hay sesión NYSE (no festivo, no fin de semana).
    """
    # Convertimos el "momento Madrid" a "fecha NY"
    dt_ny = dt_madrid.replace(tzinfo=ZoneInfo("Europe/Madrid")).astimezone(NY_TZ)
    d: date = dt_ny.date()

    # schedule requiere rango; miramos solo ese día
    sched = NYSE.schedule(start_date=d, end_date=d)
    return not sched.empty
