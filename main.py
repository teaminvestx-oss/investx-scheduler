# === main.py ===
import os
import datetime as dt
from econ_calendar import run_econ_calendar
from premarket import run_premarket_morning


# =========================================================
# UTILIDAD GENERAL: CONTROL HORARIO + FLAG DIARIO + FORZAR
# =========================================================
def should_send_in_window(start_hour: int, end_hour: int,
                          flag_file: str,
                          force_env_var: str | None,
                          label: str) -> bool:
    """
    Devuelve True si:
    - estamos en la franja [start_hour, end_hour), y
    - aún no se ha enviado hoy (según flag_file)
    SALVO que force_env_var esté a "1" -> en ese caso SIEMPRE True
      (para ejecuciones manuales).
    """
    # Modo forzado (para pruebas manuales)
    if force_env_var and os.getenv(force_env_var) == "1":
        print(f"[INFO] {label} forzado por {force_env_var}, se envía siempre.")
        return True

    now = dt.datetime.now()
    current_hour = now.hour

    if not (start_hour <= current_hour < end_hour):
        print(f"[INFO] Fuera de franja {start_hour}-{end_hour} para {label}: {current_hour}h")
        return False

    today_str = now.strftime("%Y-%m-%d")

    if os.path.exists(flag_file):
        try:
            with open(flag_file, "r") as f:
                last_sent = f.read().strip()
                if last_sent == today_str:
                    print(f"[INFO] {label} ya se envió hoy (según {flag_file}), no se repite.")
                    return False
        except Exception as e:
            print(f"[WARN] Error leyendo {flag_file}: {e}")

    # Marcamos como enviado hoy
    try:
        with open(flag_file, "w") as f:
            f.write(today_str)
    except Exception as e:
        print(f"[WARN] No se pudo escribir {flag_file}: {e}")

    print(f"[INFO] Aprobado envío para {label}.")
    return True


# =========================================================
# MAIN
# =========================================================
def main():
    print("[INFO] Ejecutando main.py...")

    today = dt.date.today()
    weekday = today.weekday()  # 0=lunes, 6=domingo

    force_morning = os.getenv("FORCE_MORNING") == "1"
    force_econ = os.getenv("FORCE_ECON") == "1"

    # Fin de semana: solo permitimos envío si está forzado
    if weekday >= 5 and not (force_morning or force_econ):
        print("[INFO] Fin de semana y sin flags de fuerza: no se envía nada.")
        return

    # 1) Buenos días + premarket (9–11h, o forzado)
    if should_send_in_window(
        start_hour=9,
        end_hour=11,
        flag_file="premarket_sent_today.txt",
        force_env_var="FORCE_MORNING",
        label="Buenos días / premarket",
    ):
        print("[INFO] Enviando mensaje de buenos días (premarket)...")
        run_premarket_morning()
    else:
        print("[INFO] No toca 'Buenos días' ahora (ni forzado).")

    # 2) Calendario económico (11–13h, o forzado)
    if should_send_in_window(
        start_hour=11,
        end_hour=13,
        flag_file="econ_sent_today.txt",
        force_env_var="FORCE_ECON",
        label="Calendario económico",
    ):
        print("[INFO] Enviando calendario económico...")
        run_econ_calendar()
    else:
        print("[INFO] No toca calendario ahora (ni forzado).")


if __name__ == "__main__":
    main()
