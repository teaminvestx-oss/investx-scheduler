# === econ_calendar.py ===
import os
import datetime as dt
import requests
import investpy
from openai import OpenAI

# ======================================================
#  ENV VARS (usamos tus nombres sin cambiarlos)
# ======================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("INVESTX_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

missing = []
if not TELEGRAM_TOKEN:
    missing.append("TELEGRAM_TOKEN/INVESTX_TOKEN")
if not TELEGRAM_CHAT_ID:
    missing.append("TELEGRAM_CHAT_ID/CHAT_ID")
if not OPENAI_API_KEY:
    missing.append("OPENAI_API_KEY")

if missing:
    raise RuntimeError("Faltan env vars: " + ", ".join(missing))

client = OpenAI(api_key=OPENAI_API_KEY)


# ======================================================
#  FUNCIÓN: enviar mensaje a Telegram
# ======================================================

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)


# ======================================================
#  FUNCIÓN: obtener calendario de Investing filtrado
# ======================================================

def fetch_investing_calendar(from_date, to_date):
    """
    Obtiene el calendario económico filtrado:
    - País: Estados Unidos
    - Impacto: 2 y 3 estrellas
    """

    try:
        df = investpy.economic_calendar(
            countries=["united states"],
            from_date=from_date.strftime("%d/%m/%Y"),
            to_date=to_date.strftime("%d/%m/%Y")
        )
    except Exception as e:
        return None, f"Error obteniendo calendario: {str(e)}"

    # Filtrar impacto 2 y 3
    df = df[df["impact"].isin(["medium", "high"])]

    if df.empty:
        return [], None

    return df.to_dict("records"), None


# ======================================================
#  FUNCIÓN: generar resumen con ChatGPT MINI
# ======================================================

def ai_summarize(text: str) -> str:
    """
    Usa gpt-4o-mini (barato) para hacer un resumen.
    """
    try:
        resp = client.responses.create(
            model="gpt-4o-mini",
            input=f"Resume este calendario económico de forma clara y útil para trading:\n\n{text}"
        )
        return resp.output_text
    except Exception as e:
        return f"No fue posible generar resumen AI: {str(e)}"


# ======================================================
#  FUNCIÓN PRINCIPAL
# ======================================================

def run_econ_calendar():
    today = dt.date.today()
    weekday = today.weekday()  # lunes=0, domingo=6

    # -----------------------------
    # LUNES → semana completa
    # -----------------------------
    if weekday == 0:
        from_date = today
        to_date = today + dt.timedelta(days=6)
        header = "📅 *Calendario Económico — Semana Completa (USA)*"
    # -----------------------------
    # MARTES–VIERNES → solo hoy
    # -----------------------------
    elif 1 <= weekday <= 4:
        from_date = to_date = today
        header = f"📅 *Calendario Económico — {today.strftime('%d/%m/%Y')} (USA)*"
    else:
        # Sábado o domingo → no hay calendario, pero mandamos mensaje mínimo
        send_telegram_message("⏳ Cron ejecutado (fin de semana). No hay calendario económico.")
        return

    # Obtener calendario
    data, err = fetch_investing_calendar(from_date, to_date)

    if err:
        send_telegram_message(f"⚠️ Error obteniendo calendario: {err}")
        return

    if not data:
        send_telegram_message(f"{header}\n\nNo hay eventos de impacto 2–3⭐️ en USA.")
        return

    # Formato básico
    lines = []
    for ev in data:
        lines.append(
            f"• *{ev['date']} {ev['time']}* — {ev['event']} "
            f"({ev['impact']}) → Actual: {ev['actual']} | Previo: {ev['previous']} | Est.: {ev['forecast']}"
        )

    raw_text = "\n".join(lines)

    # Resumen AI
    summary = ai_summarize(raw_text)

    final_msg = (
        f"{header}\n\n"
        f"*Resumen AI*\n{summary}\n\n"
        f"*Detalle completo:*\n{raw_text}"
    )

    send_telegram_message(final_msg)
