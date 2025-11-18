# === econ_calendar.py (versión simplificada) ===
import os
import datetime as dt
import requests
import investpy
from openai import OpenAI


# ============================================================================
# 1. Variables de entorno (usamos tus nombres habituales)
# ============================================================================

# Prioridad: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID, si no, INVESTX_TOKEN / CHAT_ID
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("INVESTX_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def _check_env():
    missing = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN o INVESTX_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID o CHAT_ID")
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")

    if missing:
        # No reventamos el proceso, solo devolvemos un mensaje de error
        return "⚠️ Faltan variables de entorno: " + ", ".join(missing)
    return None

client = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================================
# 2. Utilidad para enviar mensajes a Telegram
# ============================================================================

def send_telegram_message(text: str):
    """Envía un mensaje sencillo al canal de Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("No hay TELEGRAM_TOKEN/CHAT_ID configurados, no se puede enviar el mensaje.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")


# ============================================================================
# 3. Obtener calendario de Investing con investpy (solo USA, impacto medio/alto)
# ============================================================================

def fetch_us_calendar(start_date: dt.date, end_date: dt.date):
    """
    Devuelve lista de eventos USA con importancia media/alta (≈ 2–3⭐)
    entre start_date y end_date (ambos inclusive).
    """

    # Cinturón de seguridad por si las fechas vienen raras
    if end_date <= start_date:
        end_date = start_date + dt.timedelta(days=1)

    from_str = start_date.strftime("%d/%m/%Y")
    to_str = end_date.strftime("%d/%m/%Y")

    try:
        df = investpy.economic_calendar(
            countries=["united states"],
            from_date=from_str,
            to_date=to_str,
        )
    except Exception as e:
        return None, f"Error investpy: {e}"

    if "importance" not in df.columns:
        return None, "La respuesta del calendario no tiene columna 'importance'."

    # Solo medium / high
    df = df[df["importance"].isin(["medium", "high"])]

    if df.empty:
        return [], None

    events = df.to_dict("records")
    return events, None


def events_to_text(events):
    """Convierte la lista de eventos en texto plano para el modelo."""
    lines = []
    for ev in events:
        date = str(ev.get("date", ""))
        time = str(ev.get("time", ""))
        name = str(ev.get("event", ""))
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
            f"{date} {time} | {name} | {stars} "
            f"(act: {actual}, est: {forecast}, prev: {previous})"
        )
        lines.append(line)

    return "\n".join(lines)


# ============================================================================
# 4. Resumen con GPT-4.1-mini
# ============================================================================

def summarize_with_ai(raw_text: str, mode: str, today: dt.date) -> str:
    if client is None:
        # Si por lo que sea no hay API Key, mandamos el texto bruto
        header = "📆 *Calendario económico (sin resumen AI)*\n\n"
        return header + raw_text

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

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        summary = resp.choices[0].message.content.strip()
    except Exception as e:
        # Si falla la API, devolvemos el texto plano
        return header + f"⚠️ Error llamando al modelo: {e}\n\n" + raw_text

    return header + summary


# ============================================================================
# 5. Función principal: decidir semanal / diario y mandar a Telegram
# ============================================================================

def run_econ_calendar():
    """
    Lunes   → resumen semanal (hoy + 6 días)
    Mar–Vie → resumen diario (solo hoy, aunque para investpy pedimos hoy→mañana)
    Sáb/Dom → mensaje de que no hay calendario, pero el cron funciona
    """

    env_error = _check_env()
    if env_error:
        print(env_error)
        send_telegram_message(env_error)
        return

    today = dt.date.today()
    weekday = today.weekday()  # 0 = lunes ... 6 = domingo

    # Fines de semana
    if weekday >= 5:
        send_telegram_message("📆 Hoy es fin de semana: no hay calendario USA, pero el cron está OK.")
        return

    if weekday == 0:
        mode = "weekly"
        start_date = today
        end_date = today + dt.timedelta(days=6)
    else:
        mode = "daily"
        start_date = today
        end_date = today + dt.timedelta(days=1)  # para evitar problemas de rango en investpy

    # 1) Obtener eventos
    events, err = fetch_us_calendar(start_date, end_date)
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

    # 2) Texto base
    raw = events_to_text(events)

    # 3) Resumen con AI (o texto plano si falla)
    final_text = summarize_with_ai(raw, mode, today)

    # 4) Enviar a Telegram
    send_telegram_message(final_text)
