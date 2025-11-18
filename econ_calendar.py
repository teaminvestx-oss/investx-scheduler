# === econ_calendar.py ===
import os
import datetime as dt
import requests
import investpy
from openai import OpenAI

# ======================================================
#  ENV VARS (usamos tus nombres: INVESTX_TOKEN, CHAT_ID)
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
#  TELEGRAM
# ======================================================

def send_telegram_message(text: str):
    """Envía un mensaje de texto simple al canal de Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)


# ======================================================
#  CALENDARIO ECONÓMICO (Investing → investpy)
# ======================================================

def fetch_investing_calendar(start_date: dt.date, end_date: dt.date):
    """
    Obtiene calendario económico de Investing (vía investpy) para USA,
    entre start_date y end_date.
    Luego filtramos por importancia media/alta (≈ 2–3⭐).

    FIX: si por lo que sea end_date <= start_date, forzamos end_date = start_date + 1 día
    para evitar el error ERR#0032 de investpy.
    """

    # Cinturón de seguridad por si algo raro pasa con fechas
    if end_date <= start_date:
        end_date = start_date + dt.timedelta(days=1)

    from_str = start_date.strftime("%d/%m/%Y")
    to_str = end_date.strftime("%d/%m/%Y")

    try:
        df = investpy.economic_calendar(
            countries=["united states"],
            from_date=from_str,
            to_date=to_str
        )
    except Exception as e:
        return None, f"Error al obtener calendario de investpy: {e}"

    if "importance" not in df.columns:
        return None, "La respuesta de investpy no tiene columna 'importance'."

    # Solo medium / high (≈ 2–3 estrellas)
    df = df[df["importance"].isin(["medium", "high"])]

    if df.empty:
        return [], None

    events = df.to_dict("records")
    return events, None


def build_events_text(events):
    """Convierte los eventos en texto plano para pasarlo al modelo."""
    lines = []
    for ev in events:
        date = str(ev.get("date", ""))
        time = str(ev.get("time", ""))
        event_name = str(ev.get("event", ""))
        importance = str(ev.get("importance", ""))
        actual = str(ev.get("actual", ""))
        forecast = str(ev.get("forecast", ""))
        previous = str(ev.get("previous", ""))

        if importance == "medium":
            stars = "2⭐"
        elif importance == "high":
            stars = "3⭐"
        else:
            stars = ""

        line = (
            f"{date} {time} | {event_name} | {stars} "
            f"(act: {actual}, est: {forecast}, prev: {previous})"
        )
        lines.append(line)

    return "\n".join(lines)


# ======================================================
#  RESUMEN CON GPT-4.1-MINI
# ======================================================

def summarize_events_calendar(raw_text: str, mode: str, today: dt.date) -> str:
    if mode == "weekly":
        system_prompt = (
            "Eres InvestX, analista institucional. "
            "Tienes el calendario económico SOLO de Estados Unidos y SOLO de importancia media/alta "
            "(2–3 estrellas). Haz un resumen SEMANAL para un canal de Telegram:\n\n"
            "- Agrupa por día (Lunes, Martes, etc.).\n"
            "- Indica hora, dato y por qué es relevante para índices USA y el USD.\n"
            "- Máximo 8–10 viñetas.\n"
            "- Termina SIEMPRE con una sección '📌 Claves InvestX' con 2–3 ideas clave."
        )
        header = "📆 *Resumen calendario económico de la semana – EE. UU. (2–3⭐)*\n\n"
    else:
        system_prompt = (
            "Eres InvestX, analista institucional. "
            "Tienes el calendario económico SOLO de Estados Unidos y SOLO de importancia media/alta "
            "(2–3 estrellas). Haz un resumen DIARIO para un canal de Telegram:\n\n"
            "- Haz 3–6 viñetas.\n"
            "- Indica hora, dato y posible impacto en índices USA y el USD.\n"
            "- Termina SIEMPRE con una línea '👉 Clave del día:' con el catalizador principal."
        )
        header = f"📆 *Calendario económico para hoy ({today.strftime('%d/%m')}) – EE. UU. (2–3⭐)*\n\n"

    user_prompt = f"Estos son los eventos filtrados (USA, importancia media/alta):\n\n{raw_text}"

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    summary = resp.choices[0].message.content.strip()
    return header + summary


# ======================================================
#  FUNCIÓN PRINCIPAL (sin parámetros)
# ======================================================

def run_econ_calendar():
    """
    Decide internamente si genera resumen semanal o diario:

    - Lunes   → semanal (hoy + 6 días)
    - Mar–Vie → diario (hoy → hoy+1, y luego resumimos solo lo relevante)
    - Sáb/Dom → mensaje corto de “cron OK”
    """
    today = dt.date.today()
    weekday = today.weekday()  # 0 = lunes ... 6 = domingo

    # Fin de semana: no hay calendario, pero confirmamos que el cron corre
    if weekday >= 5:
        send_telegram_message("📆 Hoy es fin de semana: no hay calendario USA, pero el cron está OK.")
        return

    if weekday == 0:
        # LUNES → SEMANAL
        mode = "weekly"
        start_date = today
        end_date = today + dt.timedelta(days=6)
    else:
        # MAR–VIE → DIARIO
        mode = "daily"
        start_date = today
        end_date = today + dt.timedelta(days=1)

    # 1) Obtener eventos
    events, err = fetch_investing_calendar(start_date, end_date)
    if err:
        send_telegram_message(f"⚠️ Error al obtener calendario económico: {err}")
        return

    if not events:
        if mode == "weekly":
            msg = "📆 Esta semana no hay eventos relevantes (2–3⭐) en EE. UU."
        else:
            msg = "📆 Hoy no hay eventos relevantes (2–3⭐) en EE. UU."
        send_telegram_message(msg)
        return

    # 2) Construir texto base
    raw_text = build_events_text(events)

    # 3) Resumen AI
    final_msg = summarize_events_calendar(raw_text, mode, today)

    # 4) Enviar a Telegram
    send_telegram_message(final_msg)
