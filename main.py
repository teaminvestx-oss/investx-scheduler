import logging
import os
from datetime import datetime
from econ_calendar import run_econ_calendar

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def dentro_franja(hora_inicio, hora_fin):
    """Comprueba si hora actual está entre dos horas dadas."""
    ahora = datetime.now().hour
    return hora_inicio <= ahora < hora_fin


def main():
    logger.info("Ejecutando main.py...")

    FORCE_ECON = int(os.getenv("FORCE_ECON", "0"))

    # -------------------------
    # ENVÍO DEL CALENDARIO
    # -------------------------
    if FORCE_ECON == 1:
        logger.info("FORCE_ECON=1 → enviando calendario sin restricciones.")
        run_econ_calendar(force=True)
    else:
        # Franja 10:30–12:00
        if dentro_franja(10, 13):
            logger.info("Dentro de franja 10–13 → Enviando calendario.")
            run_econ_calendar(force=False)
        else:
            logger.info("Fuera de franja → No se envía calendario económico.")


if __name__ == "__main__":
    main()
