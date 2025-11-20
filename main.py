import os
import logging
import datetime
import pkg_resources   # requerido por investpy, NO BORRAR

from econ_calendar import run_econ_calendar
from premarket import run_premarket_morning  # función real del premarket

# ---------------------------------------------------------
# CONFIGURACIÓN HORARIA
# ---------------------------------------------------------

# Offset sencillo para España (CET). Si quieres algo más fino, usamos pytz, pero esto vale.
TZ_OFFSET = 1  # GMT+1 España (ajústalo si cambias a verano/invierno con lógica extra)
NOW = datetime.datetime.utcnow() + datetime.timedelta(hours=TZ_OFFSET)

HOUR = NOW.hour           # hora actual en España aproximada
TODAY = NOW.strftime("%Y-%m-%d")

# ---------------------------------------------------------
# VARIABLES DE ENTORNO
# ---------------------------------------------------------

# Fuerza envío aunque esté fuera de franja o ya enviado
FORCE_ECON = os.environ.get("FORCE_ECON", "0").lower() in ("1", "true", "yes")
FORCE_MORNING = os.environ.get("FORCE_MORNING", "0").lower() in ("1", "true", "yes")

# Ficheros para evitar envíos duplicados en el mismo día
ECON_SENT_FILE = "/tmp/econ_sent.txt"
MORNING_SENT_FILE = "/tmp/morning_sent.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s:%(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# FUNCIONES AUXILIARES
# ---------------------------------------------------------

def already_sent(file_path: str) -> bool:
    """Devuelve True si en ese fichero está grabada la fecha de hoy."""
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r") as f:
            date_str = f.read().strip()
        return date_str == TODAY
    except Exception:
        return False


def mark_sent(file_path: str):
    """Marca en el fichero que hoy ya se ha enviado ese bloque."""
    try:
        with open(file_path, "w") as f:
            f.write(TODAY)
    except Exception as e:
        logger.warning("No se pudo marcar como enviado en %s: %s", file_path, e)


# ---------------------------------------------------------
# EJECUCIÓN PRINCIPAL
# ---------------------------------------------------------

def main():
    logger.info("Ejecutando main.py…")
    logger.info("Fecha/hora (offset +%sh): %s", TZ_OFFSET, NOW)

    # ======================================================
    # 1) BLOQUE BUENOS DÍAS (PREMARKET)
    #    - Franja normal: 10:00–11:00 (hora España)
    #    - Sólo una vez al día, salvo FORCE_MORNING=1
    # ======================================================
    if FORCE_MORNING or (10 <= HOUR < 11):
        logger.info("Bloque 'Buenos días' dentro de franja o forzado.")

        if not FORCE_MORNING and already_sent(MORNING_SENT_FILE):
            logger.info("'Buenos días' YA enviado hoy -> no se repite.")
        else:
            try:
                run_premarket_morning()
                mark_sent(MORNING_SENT_FILE)
                logger.info("'Buenos días' enviado correctamente.")
            except Exception as e:
                logger.error("Error ejecutando 'Buenos días': %s", e)
    else:
        logger.info("Fuera de franja 10–11h para 'Buenos días', no se envía.")

    # ======================================================
    # 2) BLOQUE CALENDARIO ECONÓMICO
    #    - Franja normal: 12:00–13:00 (hora España)
    #    - Sólo una vez al día, salvo FORCE_ECON=1
    # ======================================================
    if FORCE_ECON or (12 <= HOUR < 13):
        logger.info("Bloque 'Calendario económico' dentro de franja o forzado.")

        if not FORCE_ECON and already_sent(ECON_SENT_FILE):
            logger.info("Calendario económico YA enviado hoy -> no se repite.")
        else:
            try:
                # econ_calendar ya controla importancia, IA, etc.
                run_econ_calendar(force=FORCE_ECON)
                mark_sent(ECON_SENT_FILE)
                logger.info("Calendario económico enviado correctamente.")
            except Exception as e:
                logger.error("Error ejecutando calendario económico: %s", e)
    else:
        logger.info("Fuera de franja 12–13h para 'Calendario económico', no se envía.")


# ---------------------------------------------------------
# ENTRYPOINT
# ---------------------------------------------------------

if __name__ == "__main__":
    main()
