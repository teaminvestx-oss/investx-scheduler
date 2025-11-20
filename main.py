# main.py  — InvestX-Main
# Lanza:
#   - buenos_dias.py (premarket / mensaje de buenos días)
#   - econ_calendar.py (calendario económico USA)
#
# Controla:
#   - Franjas horarias (hora local)
#   - Variables de forzado:
#       FORCE_MORNING=1  → envía "Buenos días" siempre
#       FORCE_ECON=1     → envía calendario económico siempre
#
# NOTA: no toca nada del código interno de buenos_dias.py

import os
import subprocess
import logging
import datetime

from dateutil import tz
import pkg_resources  # lo usa investpy por debajo, no borrar

from econ_calendar import run_econ_calendar   # aquí sí tenemos función

# ---------- Configuración básica ----------

LOCAL_TZ = os.environ.get("LOCAL_TZ", "Europe/Madrid")

FORCE_MORNING = os.environ.get("FORCE_MORNING", "0").lower() in ("1", "true", "yes")
FORCE_ECON = os.environ.get("FORCE_ECON", "0").lower() in ("1", "true", "yes")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s:%(message)s",
)
log = logging.getLogger(__name__)


# ---------- Utilidades de tiempo ----------

def now_local() -> datetime.datetime:
    """Devuelve la hora local según LOCAL_TZ."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    return now_utc.astimezone(tz.gettz(LOCAL_TZ))


def in_window(start_hour: int, end_hour: int, current: datetime.datetime) -> bool:
    """
    Devuelve True si current.hour está dentro de [start_hour, end_hour),
    es decir, start_hour <= hora < end_hour.
    """
    return start_hour <= current.hour < end_hour


# ---------- Lanzador de buenos_dias.py ----------

def run_buenos_dias_cli(force: bool = False) -> None:
    """
    Ejecuta buenos_dias.py como script, sin depender de funciones internas.
    Si 'force' es True, añade un argumento --force (si lo quieres usar en el script).
    """
    cmd = ["python", "buenos_dias.py"]
    if force:
        cmd.append("--force")

    log.info("buenos_dias: Lanzando script: %s", " ".join(cmd))
    result = subprocess.run(cmd, check=False)

    if result.returncode != 0:
        log.error("buenos_dias: script terminó con código %s", result.returncode)
    else:
        log.info("buenos_dias: script ejecutado correctamente.")


# ---------- MAIN ----------

def main():
    ahora = now_local()
    log.info("Ejecutando main.py...")
    log.info("Hora local (%s): %s", LOCAL_TZ, ahora.strftime("%Y-%m-%d %H:%M:%S"))

    # ==============================
    # 1) Mensaje de BUENOS DÍAS
    # ==============================
    # Reglas:
    #   - Si FORCE_MORNING=1 → se envía siempre.
    #   - Si no, solo una vez en la franja 9–11h local.
    #     (si tu cron está a las 10:30 y 12:00 → solo 10:30 cae en 9–11)
    if FORCE_MORNING:
        log.info("FORCE_MORNING=1 → enviando 'Buenos días' sin restricciones.")
        run_buenos_dias_cli(force=True)
    else:
        if in_window(9, 11, ahora):
            log.info("Dentro de franja 9–11h → se envía 'Buenos días'.")
            run_buenos_dias_cli(force=False)
        else:
            log.info("Fuera de franja 9–11h para 'Buenos días'; no se envía.")

    # ==============================
    # 2) Calendario económico USA
    # ==============================
    # Reglas:
    #   - Si FORCE_ECON=1 → se envía siempre.
    #   - Si no, solo en la franja 11–13h local.
    #     (con cron 10:30 y 12:00 → solo 12:00 cae en 11–13)
    if FORCE_ECON:
        log.info("FORCE_ECON=1 → enviando calendario económico sin restricciones.")
        run_econ_calendar(force=True)
    else:
        if in_window(11, 13, ahora):
            log.info("Dentro de franja 11–13h → se envía calendario económico.")
            run_econ_calendar(force=False)
        else:
            log.info("Fuera de franja 11–13h; no se envía calendario económico.")


if __name__ == "__main__":
    main()
