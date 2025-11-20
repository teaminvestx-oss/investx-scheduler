# main.py — InvestX Scheduler (versión estable)

import os
import logging
from datetime import datetime, time, date

import pkg_resources  # requerido por investpy, NO BORRAR

# === IMPORTS CORRECTOS ===

from econ_calendar import run_econ_calendar
from premarket import main as run_buenos_dias   # <-- ESTE ES EL FIX REAL


# === CONFIG LOGGING ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s:%(funcName)s: %(message)s",
)

log = logging.getLogger(__name__)


# === CONFIGURACIÓN HORARIOS ===
MORNING_START = time(9, 0)
MORNING_END   = time(11, 0)

# ECON CALENDAR – se lanzará a las 12:30 (tu cron)
ECON_START = time(12, 0)
ECON_END   = time(14, 0)

# === VARIABLES DE FORZADO ===
FORCE_ECON    = os.environ.get("FORCE_ECON", "0") in ("1", "true", "TRUE")
FORCE_MORNING = os.environ.get("FORCE_MORNING", "0") in ("1", "true", "TRUE")

STATE_FILE = "/opt/render/project/src/.state_investx"
if not os.path.exists(STATE_FILE):
    with open(STATE_FILE, "w") as f:
        f.write("")


# === FUNCIONES UTILIDAD ===
def already_sent(tag: str) -> bool:
    """Comprueba si hoy ya se envió algo identificado como 'tag'."""
    today = date.today().isoformat()
    key = f"{tag}:{today}"
    with open(STATE_FILE, "r") as f:
        content = f.read().splitlines()
    return key in content


def mark_sent(tag: str):
    today = date.today().isoformat()
    key = f"{tag}:{today}"
    with open(STATE_FILE, "a") as f:
        f.write(key + "\n")


def is_in_range(start: time, end: time) -> bool:
    now = datetime.now().time()
    return start <= now <= end


# ============================================================
# ======================== MAIN ===============================
# ============================================================

def main():
    log.info("Ejecutando main.py...")

    now = datetime.now().time()

    # ================================
    # 1) MENSAJE DE "BUENOS DÍAS"
    # ================================
    if FORCE_MORNING:
        log.info("FORCE_MORNING=1 → enviando 'Buenos días' SIN restricciones.")
        run_buenos_dias()
        return

    if is_in_range(MORNING_START, MORNING_END):
        if not already_sent("morning"):
            log.info("Dentro de la franja 9–11h → enviando 'Buenos días'...")
            run_buenos_dias()
            mark_sent("morning")
        else:
            log.info("'Buenos días' ya fue enviado hoy.")
    else:
        log.info("Fuera de franja 9–11h → 'Buenos días' NO se envía.")


    # ================================
    # 2) CALENDARIO ECONÓMICO
    # ================================
    if FORCE_ECON:
        log.info("FORCE_ECON=1 → enviando calendario SIN restricciones.")
        run_econ_calendar(force=True)
        return

    if is_in_range(ECON_START, ECON_END):
        if not already_sent("econ_calendar"):
            log.info("Dentro de franja económica → enviando calendario...")
            run_econ_calendar(force=False)
            mark_sent("econ_calendar")
        else:
            log.info("Calendario económico ya enviado hoy.")
    else:
        log.info("Fuera de franja del calendario económico → no se envía.")


# ============================================================

if __name__ == "__main__":
    main()
