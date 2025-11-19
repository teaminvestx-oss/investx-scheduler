# === main.py ===
import os
import datetime as dt
from econ_calendar import run_econ_calendar


# =========================================================
# FUNCIÓN PARA CONTROLAR HORARIO Y EVITAR DUPLICADOS
# =========================================================
def should_send(today_id: str = "econ_sent_today.txt") -> bool:
    now = dt.datetime.now()
    current_hour = now.hour

    # 1. FRANJA HORARIA (11:00–13:00)
    if not (11 <= current_hour < 13):
        print(f"[INFO] Fuera de franja: {current_hour}h, no se envía.")
        return False

    # 2. EVITAR REPETIR ENVÍO EN EL MISMO DÍA
    today_str = now.strftime("%Y-%m-%d")

    if os.path.exists(today_id):
        try:
            with open(today_id, "r") as f:
                last_sent = f.read().strip()
                if last_sent == today_str:
                    print("[INFO] El calendario ya se envió hoy, no se repite.")
                    return False
        except Exception as e:
            print(f"[WARN] Error leyendo {today_id}: {e}")

    # MARCAMOS COMO ENVIADO HOY
    try:
        with open(today_id, "w") as f:
            f.write(today_str)
    except Exception as e:
        print(f"[WARN] No se pudo escribir {today_id}: {e}")

    print("[INFO] Aprobado: se enviará el calendario.")
    return True


# =========================================================
# MAIN
# =========================================================
def main():
    print("[INFO] Ejecutando main.py...")

    if not should_send():
        print("[INFO] No toca enviar ahora.")
        return

    print("[INFO] Enviando calendario económico...")
    run_econ_calendar()


if __name__ == "__main__":
    main()
