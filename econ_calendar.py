# === econ_calendar.py ===
import os
import requests
import datetime as dt
import investpy
from openai import OpenAI

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("INVESTX_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# ================================
# TELEGRAM (con troceo)
# ================================
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[ERROR] Faltan TELEGRAM_TOKEN / CHAT_ID para enviar mensaje.")
        return

    max_len = 3900  # margen bajo 4096
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [""]

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    for idx, chunk in enumerate(chunks, start=1):
        payload = {
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
        }
        try:
            r = requests.post(url, data=payload, timeout=20)
            if r.status_code >= 400:
                print(f"[WARN] Error Telegram HTTP {r.status_code} (chunk {idx}/{len(chunks)}): {r.text}")
        except Exception as e:
            print(f"[ERROR] Excepción enviando mensaje Telegram (chunk {idx}/{len(chunks)}): {e}")


# ================================
# CALENDARIO ECONÓMICO (USA, 2–3⭐)
# ================================
def get_calendar_df():
    today = dt.date.today()
    tomorrow = today + dt.timedelta(days=1)

    from_date = today.strftime("%d/%m/%Y")
    to_date = tomorrow.strftime("%d/%m/%Y")

    try:
        df = investpy.economic_calendar(
            countries=["united states"],
            from_date=from_date,
            to_date=to_date,
        )
    except Exception as e:
        raise RuntimeError(f"Error al obtener calendario de investpy: {e}")

    if df is None or df.empty:
        return None

    if "importance" not in df.columns:
        raise RuntimeError("La respuesta de investpy no tiene columna 'importance'.")

    # Solo importancia media/alta
    df = df[df["importance"].isin(["medium", "high"])]

    # Por si viene enorme: limitar a los 8 eventos más importantes (por hora y nombre)
    if len(df) > 8:
        df = df.sort_values(["importance", "date", "time"], ascending=[False, True, True]).head(8)

    if df.empty:
        return None

    return df


def format_events_for_ai(df):
    events = []
    lines = []

    for _, row in df.iterrows():
        ev = {
            "date": str(row.get("date", "")),
            "time": str(row.get("time", "")),
            "event": str(row.get("event", "")),
            "importance": str(row.get("importance", "")),
            "actual": str(row.get("actual", "")),
            "forecast": str(row.get("forecast", "")),
            "previous": str(row.get("previous", "")),
        }
        events.append(ev)

        line = (
            f"{ev['event']} | {ev['date']} {ev['time']} | "
            f"importance={ev['importance']} | "
            f"actual={ev['actual']} | forecast={ev['forecast']} | previous={ev['previous']}"
        )
        lines.append(line)

    return events, "\n".join(lines)


def get_justifications(plain_events: str, n_events: int):
    default = ["Dato relevante que puede mover mercado USA o el dólar."] * n_events

    if not client or not plain_events.strip():
        return default

    system_prompt = (
        "Vas a recibir un listado de eventos macroeconómicos de Estados Unidos. "
        "Cada línea incluye nombre del dato, fecha, hora, importancia y valores. "
        "Devuelve exactamente UNA línea de justificación por evento, en el mismo orden, "
        "sin numerar ni usar viñetas. Cada línea debe ser una frase corta (máx. 20 palabras) "
        "en español, explicando por qué el dato es relevante o qué suele implicar para "
        "la bolsa USA o el dólar. No menciones IA ni modelos."
    )

    user_prompt = (
        "Eventos macroeconómicos de hoy en Estados Unidos:\n\n"
        f"{plain_events}\n\n"
        "Devuélveme solo una frase por línea, en el mismo orden de los eventos."
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        if len(lines) < n_events:
            lines += default[len(lines):]
        elif len(lines) > n_events:
            lines = lines[:n_events]

        return lines
    except Exception as e:
        print("Error pidiendo justificaciones:", e)
        return default


def build_calendar_message(events, justifications):
    today_str = dt.date.today().strftime("%d/%m/%Y")
    out_lines = [f"📊 <b>Calendario económico (USA)</b>\n📆 Hoy — {today_str}\n"]

    for ev, just in zip(events, justifications):
        importance = ev["importance"]
        if importance == "medium":
            stars = "⭐⭐"
        elif importance == "high":
            stars = "⭐⭐⭐"
        else:
            stars = ""

        name = ev["event"]
        date = ev["date"]
        time = ev["time"]

        actual = ev["actual"]
        forecast = ev["forecast"]
        previous = ev["previous"]

        value_parts = []
        if actual and actual.lower() != "none":
            value_parts.append(f"📉 Actual: {actual}")
        if forecast and forecast.lower() != "none":
            value_parts.append(f"📈 Previsión: {forecast}")
        if previous and previous.lower() != "none":
            value_parts.append(f"Anterior: {previous}")

        values_line = " | ".join(value_parts) if value_parts else "Sin datos numéricos disponibles."

        block = (
            f"\n{stars} <b>{name}</b>\n"
            f"🕒 {date} — {time}\n"
            f"{values_line}\n"
            f"💬 {just}\n"
        )
        out_lines.append(block)

    return "\n".join(out_lines).strip()


# ================================
# FUNCIÓN PRINCIPAL
# ================================
def run_econ_calendar():
    print("[INFO] Obteniendo calendario económico USA...")
    try:
        df = get_calendar_df()
    except Exception as e:
        msg = f"⚠️ Error al obtener calendario económico:\n{e}"
        print("[ERROR]", msg)
        send_telegram(msg)
        return

    if df is None or df.empty:
        msg = "📭 <b>No hay eventos económicos relevantes en USA (2–3⭐) para hoy.</b>"
        print("[INFO]", msg)
        send_telegram(msg)
        return

    events, plain = format_events_for_ai(df)
    justifications = get_justifications(plain, len(events))
    msg = build_calendar_message(events, justifications)

    print("[INFO] Enviando calendario económico...")
    send_telegram(msg)
