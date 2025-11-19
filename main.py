# main.py
import os
import logging
from datetime import datetime
import pytz
import pkg_resources  # lo usa investpy por debajo

from buenos_dias import run_buenos_dias
from econ_calendar import run_econ_calendar

# --------------------------------------------------------------------
# LOGGING
# --------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s"
)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------
# CONFIG (variables de entorno)
# --------------------------------------------------------------------
LOCAL_TZ = os.getenv("LOCAL_TZ", "Europe/Madrid")

# 1 = fuerza el envío aunque esté fuera de franja
FORCE_MORNING = os.getenv("FORCE_MORNING", "0") == "1"
FORCE_ECON = os.getenv("FORCE_ECON", "0") == "1"


def get_now_local():
    """Devuelve datetime actual en la zona horaria configurada."""
    tz = pytz.timezone(LOCAL_TZ)
    return datetime.now(tz)


def is_between(now, start_hour, end_hour):
    """
    True si la hora actual está entre start_hour (incluida)
    y end_hour (excluida). Solo se usa para decidir si toca enviar o no.
    """
    return start_hour <= now.hour < end_hour


def main():
    now = get_now_local()
    logger.info("INFO:__main__:Ejecutando main.py...")

    # ---------------------------------------------------------------
    # 1) MENSAJE DE BUENOS DÍAS / PREMARKET
    #    - Ventana: 9h–11h (hora LOCAL_TZ)
    #    - Si FORCE_MORNING=1 => se envía siempre que se lance el cron
    # ---------------------------------------------------------------
    if FORCE_MORNING or is_between(now, 9, 11):
        if FORCE_MORNING:
            logger.info("INFO:__main__:FORCE_MORNING=1 -> enviando 'Buenos días' sin restricciones.")
        else:
            logger.info("INFO:__main__:Dentro de franja 9–11h para 'Buenos días'.")
        try:
            run_buenos_dias()
        except Exception as e:
            logger.exception("ERROR:buenos_dias:⚠️ Error al enviar mensaje de buenos días: %s", e)
    else:
        logger.info("INFO:__main__:Fuera de franja 9–11h para 'Buenos días', no se envía.")

    # ---------------------------------------------------------------
    # 2) CALENDARIO ECONÓMICO USA (2–3 estrellas)
    #    - Ventana: 10:30–12h (hora LOCAL_TZ)
    #    - Si FORCE_ECON=1 => se envía siempre que se lance el cron
    # ---------------------------------------------------------------
    # Nota: la ventana es 10–12; como el cron lo pondrás a las 10:30 y 12:00
    # entra en ese rango sin problema.
    if FORCE_ECON or is_between(now, 10, 12):
        if FORCE_ECON:
            logger.info("INFO:__main__:FORCE_ECON=1 -> enviando calendario sin restricciones.")
        else:
            logger.info("INFO:__main__:Dentro de franja 10–12h para 'Calendario económico'.")
        try:
            logger.info("INFO:__main__:Obteniendo calendario económico USA...")
            # ⚠️ IMPORTANTE: ya NO pasamos ningún parámetro 'force'
            run_econ_calendar()
        except Exception as e:
            logger.exception("ERROR:econ_calendar:⚠️ Error al obtener calendario económico: %s", e)
    else:
        logger.info("INFO:__main__:Fuera de franja 10–12h para 'Calendario económico', no se envía.")


if __name__ == "__main__":
    main()
