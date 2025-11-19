import os
import datetime as dt

import requests
import investpy
from openai import OpenAI

# ======================
#  CONFIG ENV VARS
# ======================
TELEGRAM_TOKEN = os.getenv("INVESTX_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# país fijo: USA
COUNTRY = "united states"

# cliente OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)


# ======================
#  TELEGRAM
# ======================
def send_telegram_message(text: str):
    """Envía mensaje de texto al canal de Telegram en HTML."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[ERROR] Falta TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en env vars.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if not resp.ok:
            print("[WARN] Error Telegram HTTP", resp.status_code, resp.text)
    except Exception as e:
        print("[ERROR] Excepción enviando a Telegram:", e)


# ======================
#  IA MINI PARA RESUMIR
# ======================
def call_gpt_mini(prompt: str) -> str:
    """Llama al modelo mini para un resumen corto."""
    if not OPENAI_API_KEY:
        print("[WARN] No hay OPENAI_API_KEY, devolviendo mensaje genérico.")
        return "Dato relevante que puede mover índices USA o el dólar."

    try:
        response = client.responses.create(
            model="gpt-5.1-mini",
            input=prompt,
        )
        # Extraer texto
        output = response.output[0].content[0].text
        return output.strip()
    except Exception as e:
        print("[WARN] Error llamando a OpenAI:", e)
        return "Dato relevante que puede mover índices USA o el dólar."


# ======================
#  OBTENER CALENDARIO
# ======================
def get_calendar_df():
    """Devuelve el calendario de HOY de investpy para USA, impacto 2–3⭐."""

    today = dt.date.today()
    tomorrow = today + dt.timedelta(days=1)

    from_str = today.strftime("%d/%m/%Y")
    to_str = tomorrow.strftime("%d/%m/%Y")  # siempre > from_str

    print(f"[INFO] Rango econ_calendar: {from_str} -> {to_str}")

    try:
        df = investpy.economic_calendar(
            countries=[COUNTRY],
            from_date=from_str,
            to_date=to_str,
        )
    except Exception as e:
        raise RuntimeError(f"Error al obtener calendario de investpy: {e}")

    if df is None or df.empty:
        return None

    # Solo eventos de HOY
    df = df[df["date"] == from_str]
    if df.empty:
        return None

    # Filtrar importancia 2–3 (media/alta)
    imp = df["importance"].astype(str).str.lower()
    df = df[imp.isin(["2", "3", "medium", "high"])]
    if df.empty:
        return None

    # Quitar duplicados
    df = df.drop_duplicates(subset=["event", "date", "time"], keep="first")

    # Ordenar por importancia y hora
    def rank_imp(x: str) -> int:
        x = x.lower()
        if x in ("3", "high"):
            return 0
        if x in ("2", "medium"):
            return 1
        return 2

    df["imp_rank"] = df["importance"].astype(str).apply(rank_imp)
    df = df.sort_values(by=["imp_rank", "date", "time"])

    # Limitar para no pasarnos del límite de Telegram
    df = df.head(8)

    return df


# ======================
#  INTERPRETACIÓN EVENTO
# ======================
def interpret_event_short(title: str) -> str:
    """Una frase corta explicando por qué importa el dato (sin mencionar IA)."""
    prompt = (
        "Resume en UNA sola frase, clara y concreta, por qué este dato económico "
        "puede ser importante para los mercados (índices USA y el dólar). "
        "No menciones IA ni que estás analizando nada técnico.\n\n"
        f"Dato: {title}"
    )

    txt = call_gpt_mini(prompt)
    if len(txt) > 220:
        txt = txt[:217].rstrip() + "..."
    return txt


# ======================
#  CONSTRUIR MENSAJE
# ======================
def build_calendar_message(df):
    if df is None or df.empty:
        return "📅 Hoy no hay eventos económicos relevantes en EE. UU. (2–3⭐)."

    fecha = df["date"].iloc[0]
    lines = []
    lines.append(f"📅 <b>Calendario económico USA — {fecha}</b>")
    lines.append("")

    for _, row in df.iterrows():
        title = str(row.get("event", "")).strip()
        time = str(row.get("time", "")).strip()
        imp = str(row.get("importance", "")).lower()

        star = "⭐⭐⭐" if imp in ("3", "high") else "⭐⭐"

        actual = row.get("actual", "")
        forecast = row.get("forecast", "")
        previous = row.get("previous", "")

        block = []

        block.append(f"{star} <b>{title}</b>")
        if time:
            block.append(f"🕒 {time}")

        nums = []
        if actual not in ("", None, "-", "nan"):
            nums.append(f"Actual: {actual}")
        if forecast not in ("", None, "-", "nan"):
            nums.append(f"Previsión: {forecast}")
        if previous not in ("", None, "-", "nan"):
            nums.append(f"Anterior: {previous}")
        if nums:
            block.append(" | ".join(nums))

        comentario = interpret_event_short(title)
        block.append(f"💬 {comentario}")

        lines.append("\n".join(block))
        lines.append("")

    msg = "\n".join(lines).strip()
    if len(msg) > 3800:
        msg = msg[:3790].rstrip() + "\n\n[Mensaje recortado por longitud.]"

    return msg


# ======================
#  ENTRYPOINT PÚBLICO
# ======================
def run_econ_calendar():
    print("[INFO] Obteniendo calendario económico USA...")

    try:
        df = get_calendar_df()
    except Exception as e:
        err = f"⚠️ Error al obtener calendario económico:\n{e}"
        print("[ERROR]", err)
        send_telegram_message(err)
        return

    msg = build_calendar_message(df)
    send_telegram_message(msg)
    print("[INFO] Calendario económico enviado.")
