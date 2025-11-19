import os
import subprocess
import logging
from datetime import datetime

from dateutil import tz
import pkg_resources  # lo usa investpy por debajo, no borrar

from econ_calendar import run_econ_calendar


# ----------------- LOGGING -----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s:%(message)s",
)
logger = logging.getLogger(__name__)


# ----------------- UTILIDADES -----------------
def get_now_local():
    """Devuelve datetime con la zona horaria indicada en LOCAL_TZ (por defecto Europe/Madrid)."""
    local_tz_name = os.getenv("LOCAL_TZ", "Europe/Madrid")
    local_tz = tz.gettz(local_tz_name)
    return datetime.now(local_tz)


def in_hour_window(now: datetime, start_hour: int, end_hour: int) -> bool:
    """Devuelve True si now.hour está en [start_hour, end_hour)."""
    return start_hour <= now.hour < end_hour


def run_buenos_dias_script():
    """
    Lanza el fichero buenos_dias.py como script independiente,
    igual que hacía el cron antiguo (no tocamos buenos_dias.py).
    """
    logger.info("Buenos días: lanzando script 'buenos_dias.py' con subprocess...")
    try:
        subprocess.run(["python", "buenos_dias.py"], check=True)
        logger.info("Buenos días: script ejecutado correctamente.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Buenos días: error al ejecutar buenos_dias.py: {e}")


# ----------------- MAIN -----------------
def main():
    logger.info("Ejecutando main.py...")

    now = get_now_local()
    weekday = now.weekday()  # 0 = lunes, 6 = domingo

    # Flags de forzado desde variables de entorno
    force_morning = os.getenv("FORCE_MORNING", "0") == "1"
    force_econ = os.getenv("FORCE_ECON", "0") == "1"

    # -------- 1) Mensaje de Buenos días / premarket --------
    # Ventana normal: lunes-viernes, 9h-11h
    if force_morning:
        logger.info("FORCE_MORNING=1 -> enviando 'Buenos días' sin restricciones de hora.")
        run_buenos_dias_script()
    else:
        if 0 <= weekday <= 4 and in_hour_window(now, 9, 11):
            logger.info("Dentro de franja 9–11h para 'Buenos días', se envía.")
            run_buenos_dias_script()
        else:
            logger.info("Fuera de franja 9–11h para 'Buenos días', no se envía.")

    # -------- 2) Calendario económico USA --------
    # Ventana normal: lunes-viernes, 10h-13h
    if force_econ:
        logger.info("FORCE_ECON=1 -> enviando calendario económico sin restricciones.")
        try:
            logger.info("econ_calendar: Obteniendo calendario económico USA...")
            run_econ_calendar()  # sin parámetro 'force'
            logger.info("econ_calendar: Calendario económico enviado.")
        except Exception as e:
            logger.error(f"econ_calendar: ⚠️ Error al obtener/enviar calendario económico: {e}")
    else:
        if 0 <= weekday <= 4 and in_hour_window(now, 10, 13):
            logger.info("Dentro de franja 10–13h para calendario económico, se envía.")
            try:
                logger.info("econ_calendar: Obteniendo calendario económico USA...")
                run_econ_calendar()
                logger.info("econ_calendar: Calendario económico enviado.")
            except Exception as e:
                logger.error(f"econ_calendar: ⚠️ Error al obtener/enviar calendario económico: {e}")
        else:
            logger.info("Fuera de franja 10–13h para calendario económico, no se envía.")


if __name__ == "__main__":
    main()
