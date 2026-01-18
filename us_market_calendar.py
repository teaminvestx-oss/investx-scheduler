# us_market_calendar.py
import os
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import pandas_market_calendars as mcal

NY_TZ = ZoneInfo("America/New_York")
MADRID_TZ = ZoneInfo("Europe/Madrid")

NYSE = mcal.get_calendar("NYSE")


def is_nyse_trading_day(dt_madrid: datetime) -> bool:
    """
    Devuelve True si NYSE abre el día evaluado.
    Permite simular días futuros con la env var:
      NYSE_DAY_OFFSET = 0  (default)
      NYSE_DAY_OFFSET = 1  (mañana)
      NYSE_DAY_OFFSET = 2  (pasado mañana)
    """

    # Offset de simulación (en días)
    try:
        day_offset = int(os.getenv("NYSE_DAY_OFFSET", "0"))
    except ValueError:
        day_offset = 0

    # Aplicamos offset SOLO al calendario
    dt_simulated = dt_madrid + timedelta(days=day_offset)

    # Convertimos a fecha NY
    dt_ny = dt_simulated.replace(tzinfo=MADRID_TZ).astimezone(NY_TZ)
    d: date = dt_ny.date()

    # Consultamos calendario NYSE
    sched = NYSE.schedule(start_date=d, end_date=d)
    return not sched.empty
