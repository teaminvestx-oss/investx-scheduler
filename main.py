# === main.py ===
import os
import datetime as dt
from econ_calendar import run_econ_calendar
from premarket import run_premarket_morning


# -----------------------------------------
# Utilidades para flags diarios
# -----------------------------------------
def _already_sent(flag_file: str) -> bool:
    today_str = dt.date.today().strftime("%Y-%m-%d")
    if os.path.exists(flag_file):
        try:
            with open(flag_file, "r") as f:
                last = f.read().strip()
                return last == today_str
        except Exception as e:
            print(f"[WARN] Error leyendo {flag_file}: {e}")
    return False


def _mark_sent(flag_file: str):
    today_str = dt.date.today().strftime("%Y-%m-%d")
    try:
        with open(flag_file, "w") as f:
            f.write(today_str)
    except Exception as e:
        print(f"[WARN] Error escribiendo {flag_file}: {e}")


# -----------------------------------------
# MAIN
# -----------------------------------------
def main():
    print("[INFO] Ejecutando main.py...")

    today = dt.date.today()
    weekday = today.weekday()  # 0=lunes, 6=domingo
    now = dt.datetime.now()
    hour = now.hour

    force_morning = os.getenv("FORCE_MORNING") == "1"
    force_econ = os.getenv("FORCE_ECON") == "1"

    # 1) BUENOS DÍAS / PREMARKET
    if force_morning:
        print("[INFO] FORCE_MORNING=1 -> enviando 'Buenos días' sin restricciones.")
        run_premarket_morning()
    else:
        # Solo lunes–viernes
        if weekday < 5:
            if 9 <= hour < 11:
                if not _already_sent("premarket_sent_today.txt"):
                    print("[INFO] Franja 9–11h y aún no enviado -> 'Buenos días'.")
                    run_premarket_morning()
                    _mark_sent("premarket_sent_today.txt")
                else:
                    print("[INFO] 'Buenos días' ya se envió hoy, no se repite.")
            else:
                print("[INFO] Fuera de franja 9–11h para 'Buenos días'.")
        else:
            print("[INFO] Fin de semana: sin 'Buenos días' (salvo FORCE_MORNING).")

    # 2) CALENDARIO ECONÓMICO
    if force_econ:
        print("[INFO] FORCE_ECON=1 -> enviando calendario sin restricciones.")
        run_econ_calendar()
    else:
        # Solo lunes–viernes
        if weekday < 5:
            if 11 <= hour < 13:
                if not _already_sent("econ_sent_today.txt"):
                    print("[INFO] Franja 11–13h y aún no enviado -> calendario económico.")
                    run_econ_calendar()
                    _mark_sent("econ_sent_today.txt")
                else:
                    print("[INFO] Calendario económico ya enviado hoy, no se repite.")
            else:
                print("[INFO] Fuera de franja 11–13h para calendario económico.")
        else:
            print("[INFO] Fin de semana: sin calendario (salvo FORCE_ECON).")


if __name__ == "__main__":
    main()
