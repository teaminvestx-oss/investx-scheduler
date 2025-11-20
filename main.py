import os
import logging
import datetime
import pkg_resources   # requerido por investpy, NO BORRAR

from econ_calendar import run_econ_calendar
from premarket import run_buenos_dias   # ✔️ ESTA ES LA FUNCIÓN REAL

# ---------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------

TZ_OFFSET = 1  # GMT+1 España
NOW = datetime.datetime.utcnow() + datetime.timedelta(hours=TZ_OFFSET)

HOUR = NOW.hour
TODAY = NOW.strftime("%Y-%m-%d")

# Variables de entorno
FORCE_ECON = os.environ.get("FORCE_ECON", "0") in ("1", "true", "TRUE")
FORCE_BUENOSDIAS = os.environ.get("FORCE_BUENOSDIAS", "0") in ("1", "true", "TRUE")

# Archivos para evitar envíos duplicados
ECON_SENT_FILE = "/tmp/econ_sent.txt"
BUENOS_SENT_FILE = "/tmp/buenos_sent.txt"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# FUNCIONES AUXILIARES
# ---------------------------------------------------------

def already_sent(file_path: str) -> bool:
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r") as f:
            date = f.read().strip()
        return date == TODAY
    except:
        return False

def mark_sent(file_path: str):
    with open(file_path, "w") as f:
        f.write(TODAY)

# ---------------------------------------------------------
# EJECUCIÓN PRINCIPAL
# ---------------------------------------------------------

def main():
    logger.info("Ejecutando main.py…")
    logger.info(f"Fecha/hora España: {NOW}")

    # ======================================================
    # 1) BLOQUE BUENOS DÍAS (PREMARKET)
    # ======================================================
    if 10 <= HOUR < 11 or FORCE_BUENOSDIAS:
        logger.info("Bloque 'Buenos días' dentro de franja o forzado.")

        if not FORCE_BUENOSDIAS and already_sent(BUENOS_SENT_FILE):
            logger.info("Buenos días YA enviado hoy -> no se repite.")
        else:
            try:
                run_buenos_dias()
                mark_sent(BUENOS_SENT_FILE)
                logger.info("Buenos días enviado correctamente.")
            except Exception as e:
                logger.error(f"Error ejecutando Buenos días: {e}")

    else:
        logger.info("Fuera de franja 10–11h para 'Buenos días', no se envía.")

    # ======================================================
    # 2) BLOQUE CALENDARIO ECONÓMICO
    # ======================================================
    if 12 <= HOUR < 13 or FORCE_ECON:
        logger.info("Bloque 'Calendario económico' dentro de franja o forzado.")

        if not FORCE_ECON and already_sent(ECON_SENT_FILE):
            logger.info("Calendario económico YA enviado hoy -> no se repite.")
        else:
            try:
                run_econ_calendar(force=FORCE_ECON)
                mark_sent(ECON_SENT_FILE)
                logger.info("Calendario económico enviado correctamente.")
            except Exception as e:
                logger.error(f"Error ejecutando calendario económico: {e}")

    else:
        logger.info("Fuera de franja 12–13h para 'Calendario económico', no se envía.")

# ---------------------------------------------------------
# ENTRYPOINT
# ---------------------------------------------------------

if __name__ == "__main__":
    main()
