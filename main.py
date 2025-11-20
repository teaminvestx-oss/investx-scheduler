# main.py
import os
import logging
from datetime import datetime, time
from dateutil import tz
import pkg_resources  # lo usa investpy por debajo, NO borrar

from econ_calendar import run_econ_calendar
from buenos_dias import run_buenos_dias

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s:%(funcName)s: %(message)s",
)

logger = logging.getLogger(__name__)

LOCAL_TZ = os.getenv("LOCAL_TZ", "Europe/Madrid")
FORCE_ECON = os.getenv("FORCE_ECON", "0")
FORCE_MORNING = os.getenv("FORCE_MORNING", "0")


def now_local():
    tzinfo = tz.gettz(LOCAL_TZ)
    return datetime.now(tzinfo)


def in_morning_window(dt: datetime) -> bool:
    """Franja Buenos días: 09:00–11:00."""
    return time(9, 0) <= dt.time() <= time(11, 0)


def in_econ_window(dt: datetime) -> bool:
    """Franja calendario económico: 10:30–13:00."""
    t = dt.time()
    # 10:30 <= hora < 13:00
    return (t >= time(10, 30)) and (t < time(13, 0))


def main():
    logger.info("Ejecutando main.py...")
    current = now_local()
    logger.info("Hora local: %s (%s)", current, LOCAL_TZ)

    # ----------------------
    # 1) Mensaje Buenos días
    # ----------------------
    try:
        if FORCE_MORNING == "1":
            logger.info("FORCE_MORNING=1 -> enviando 'Buenos días' sin restricciones.")
            run_buenos_dias(force=True)
        else:
            if in_morning_window(current):
                logger.info("Dentro de la franja 9–11h -> comprobando 'Buenos días'.")
                run_buenos_dias(force=False)
            else:
                logger.info("Fuera de franja 9–11h para 'Buenos días', no se envía.")
    except Exception as e:
        logger.exception("Error al ejecutar 'Buenos días': %s", e)

    # --------------------------
    # 2) Calendario económico USA
    # --------------------------
    try:
        if FORCE_ECON == "1":
            logger.info("FORCE_ECON=1 -> enviando calendario económico sin restricciones (ignora franja y 'una vez al día').")
            run_econ_calendar(force=True)
        else:
            if in_econ_window(current):
                logger.info("Dentro de la franja 10:30–13h -> intentando enviar calendario (1 vez al día).")
                # run_econ_calendar(force=False) internamente ya controla si se envió hoy
                run_econ_calendar(force=False)
            else:
                logger.info("Fuera de franja 10:30–13h -> no se envía calendario económico.")
    except Exception as e:
        logger.exception("Error al ejecutar calendario económico: %s", e)


if __name__ == "__main__":
    main()
