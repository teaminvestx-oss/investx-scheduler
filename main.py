# main.py
import os
import datetime as dt
import pkg_resources  # lo usa investpy por debajo

from econ_calendar import run_econ_calendar
from buenos_dias import run_buenos_dias   # OJO: aquí el nombre debe coincidir con el archivo

def now_local():
    """Devuelve hora local aproximada usando LOCAL_TZ (solo para la lógica de franjas)."""
    # Si quieres algo más fino, ya lo afinamos, pero esto te vale para la lógica de 9-11 / 11-13
    offset = int(os.getenv("LOCAL_OFFSET_HOURS", "1"))  # por defecto +1 (Madrid invierno)
    return dt.datetime.utcnow() + dt.timedelta(hours=offset)

def main():
    print("INFO:__main__:Ejecutando main.py...")
    now = now_local()
    weekday = now.weekday()  # 0 = lunes
    hour = now.hour

    force_econ = os.getenv("FORCE_ECON", "0") == "1"
    force_morning = os.getenv("FORCE_MORNING", "0") == "1"

    # --- Buenos días / premarket ---
    if force_morning or (weekday < 5 and 9 <= hour < 11):
        print(f"INFO:__main__:FORCE_MORNING={int(force_morning)} -> enviando buenos días.")
        run_buenos_dias(force=force_morning)
    else:
        print(f"INFO:__main__:Fuera de franja 9-11h para 'Buenos días'.")

    # --- Calendario económico USA ---
    if force_econ or (weekday < 5 and 11 <= hour < 13):
        print(f"INFO:__main__:FORCE_ECON={int(force_econ)} -> enviando calendario.")
        run_econ_calendar(force=force_econ)
    else:
        print("INFO:__main__:No toca calendario ahora.")

if __name__ == "__main__":
    main()
