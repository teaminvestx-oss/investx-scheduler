import os
import datetime as dt
import investpy
import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ---------------------------
#  SEND TO TELEGRAM
# ---------------------------
def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=20)
        if r.status_code != 200:
            print("[WARN] Error Telegram:", r.text)
        return r.status_code == 200
    except Exception as e:
        print("[WARN] Error enviando a Telegram:", e)
        return False

# ---------------------------
#  GET ECONOMIC CALENDAR
# ---------------------------
def get_calendar_df():
    today = dt.date.today()
    tomorrow = today + dt.timedelta(days=1)

    from_date_str = today.strftime("%d/%m/%Y")
    to_date_str = tomorrow.strftime("%d/%m/%Y")

    try:
        df = investpy.economic_calendar(
            countries=["united states"],
            from_date=from_date_str,
            to_date=to_date_str
        )
    except Exception as e:
        raise RuntimeError(f"Error al obtener calendario de investpy: {e}")

    if df is None or df.empty:
        return None

    if "importance" not in df.columns:
        raise RuntimeError("La respuesta de investpy no tiene columna 'importance'.")

    # 🔹 QUEDARME SOLO CON LOS EVENTOS DE HOY
    df = df[df["date"] == from_date_str]

    if df.empty:
        return None

    # Filtrar solo importancia media/alta
    df = df[df["importance"].isin(["medium", "high"])]

    if df.empty:
        return None

    # Eliminar duplicados
    df = df.drop_duplicates(subset=["event", "date", "time"], keep="first")

    # Ordenar por importancia
    df["imp_rank"] = df["importance"].map({"high": 0, "medium": 1}).fillna(2)
    df = df.sort_values(by=["imp_rank", "date", "time"])

    # Limitar número de eventos
    df = df.head(8)

    return df

# ---------------------------
#  INTERPRETACIÓN IA EVENTO
# ---------------------------
def interpret_event(event_name: str):
    """Interpretación usando ChatGPT Mini, natural, sin decir 'IA' en ningún sitio."""
    import openai
    openai.api_key = OPENAI_API_KEY

    prompt = f"""
Eres analista macroeconómico. Explica en 1 frase clara y natural por qué este evento es importante para los mercados: {event_name}.
Tono profesional pero sencillo. No menciones IA.
"""

    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
        )
        return resp.choices[0].message["content"].strip()
    except Exception as e:
        print("[WARN] Error interpretando evento:", e)
        return "Dato relevante que puede influir en mercados dependiendo de contexto macro."

# ---------------------------
#  BUILD FINAL MESSAGE
# ---------------------------
def build_calendar_message(df):
    if df is None or df.empty:
        return "⚠️ No hay eventos relevantes en el calendario económico de hoy."

    today_str = dt.date.today().strftime("%d/%m/%Y")

    msg = f"<b>📅 Calendario económico — {today_str}</b>\n"

    for _, row in df.iterrows():
        title = row["event"]
        time = row["time"]
        star = "⭐⭐⭐" if row["importance"] == "high" else "⭐⭐"
        actual = row.get("actual", "")
        forecast = row.get("forecast", "")
        previous = row.get("previous", "")

        msg += f"\n{star} <b>{title}</b>\n"
        msg += f"⏰ {time}\n"

        # Números si existen
        if actual not in ["-", "", None]:
            msg += f"<b>Actual:</b> {actual} | "
        if forecast not in ["-", "", None]:
            msg += f"<b>Previsión:</b> {forecast} | "
        if previous not in ["-", "", None]:
            msg += f"<b>Anterior:</b> {previous}"

        msg += "\n💬 " + interpret_event(title) + "\n"

    return msg

# ---------------------------
#  MAIN EXECUTION
# ---------------------------
def run_econ_calendar(force=False):
    """
    force=True = se envía siempre, aunque esté fuera de la franja (para ejecuciones manuales)
    """

    df = get_calendar_df()

    msg = build_calendar_message(df)

    send_telegram(msg)

    return True
