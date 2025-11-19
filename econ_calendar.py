# === econ_calendar.py ===
import os
import requests
import datetime as dt
import investpy
from openai import OpenAI

# =====================================
# ENV VARS
# =====================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("INVESTX_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def env_ok() -> bool:
    missing = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN/INVESTX_TOKEN")
    if not CHAT_ID:
        missing.append("CHAT_ID/TELEGRAM_CHAT_ID")

    if missing:
        print("Faltan env vars:", ", ".join(missing))
        return False
    return True


# =====================================
# TELEGRAM
# =====================================
def send_telegram(msg: str):
    if not env_ok():
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, data=payload, timeout=15)
        if resp.status_code >= 400:
            print(f"[WARN] Error Telegram HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        print("Error enviando Telegram:", e)


# =====================================
# CALENDARIO ECONÓMICO (USA, 2–3⭐)
# =====================================
def get_calendar():
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

    if "importance" not in df.columns:
        raise RuntimeError("La respuesta de investpy no tiene columna 'importance'.")

    # Solo importancia media/alta (≈ 2–3⭐)
    df = df[df["importance"].isin(["medium", "high"])]

    if df.empty:
        return None

    return df


# =====================================
# FORMATEO EVENTOS + TEXTO PARA IA
# =====================================
def format_events_for_ai(df):
    """
    Devuelve:
    - lista de dicts (eventos "limpios")
    - texto plano para la IA (una línea por evento)
    """
    events = []
    plain_lines = []

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

        plain_line = (
            f"{ev['event']} | {ev['date']} {ev['time']} | "
            f"importance={ev['importance']} | "
            f"actual={ev['actual']} | forecast={ev['forecast']} | previous={ev['previous']}"
        )
        plain_lines.append(plain_line)

    return events, "\n".join(plain_lines)


# =====================================
# JUSTIFICACIONES POR EVENTO (TONO NATURAL)
# =====================================
def get_justifications(plain_events: str, n_events: int):
    """
    Pide al modelo una justificación corta por evento.
    Devuelve una lista de strings (longitud n_events).
    Si no hay API key o hay error, devuelve justificaciones genéricas.
    """
    default = ["Dato relevante que puede generar movimientos en mercado USA."] * n_events

    if not client or not plain_events.strip():
        return default

    system_prompt = (
        "Vas a recibir un listado de eventos macroeconómicos de Estados Unidos. "
        "Cada línea incluye nombre del dato, fecha, hora, importancia y valores "
        "previos/previsión. Devuelve exactamente UNA línea de justificación por evento, "
        "en el mismo orden, sin numerar y sin viñetas. "
        "Cada línea debe ser una frase corta (máx. 20 palabras) en español, "
        "explicando por qué el dato es relevante o qué suele implicar "
        "para la bolsa USA o el dólar. "
        "No menciones que eres un modelo ni hables de 'IA' ni 'analista'."
    )

    user_prompt = (
        "Eventos macroeconómicos de hoy (Estados Unidos):\n\n"
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

        # Ajustamos al número de eventos
        if len(lines) < n_events:
            # Rellenamos con genéricas si faltan
            lines += default[len(lines):]
        elif len(lines) > n_events:
            lines = lines[:n_events]

        return lines

    except Exception as e:
        print("Error pidiendo justificaciones al modelo:", e)
        return default


# =====================================
# CONSTRUIR MENSAJE FINAL
# =====================================
def build_message(events, justifications):
    """
    Construye el HTML final para Telegram:
    - ⭐⭐ / ⭐⭐⭐
    - nombre en negrita
    - hora + valores
    - línea de justificación debajo
    """
    today = dt.date.today().strftime("%d/%m/%Y")
    lines = [f"📊 <b>Calendario económico (USA)</b>\n📆 Hoy — {today}\n"]

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

        # Construimos la línea de valores de forma inteligente
        value_parts = []
        if actual and actual.lower() != "none":
            value_parts.append(f"📉 Actual: {actual}")
        if forecast and forecast.lower() != "none":
            value_parts.append(f"📈 Previsión: {forecast}")
        if previous and previous.lower() != "none":
            value_parts.append(f"Anterior: {previous}")

        if value_parts:
            values_line = " | ".join(value_parts)
        else:
            values_line = "Sin datos numéricos disponibles."

        block = (
            f"\n{stars} <b>{name}</b>\n"
            f"🕒 {date} — {time}\n"
            f"{values_line}\n"
            f"💬 {just}\n"
        )

        lines.append(block)

    return "\n".join(lines).strip()


# =====================================
# FUNCIÓN PRINCIPAL
# =====================================
def run_econ_calendar():
    if not env_ok():
        return

    try:
        df = get_calendar()
    except Exception as e:
        send_telegram(f"⚠️ Error al obtener calendario económico:\n{e}")
        return

    if df is None or df.empty:
        send_telegram("📭 <b>No hay eventos económicos relevantes en USA (2–3⭐).</b>")
        return

    events, plain = format_events_for_ai(df)
    justifications = get_justifications(plain, len(events))
    msg = build_message(events, justifications)

    send_telegram(msg)
