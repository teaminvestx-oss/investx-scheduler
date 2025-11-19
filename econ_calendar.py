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
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# =====================================
# TELEGRAM (con troceo por longitud)
# =====================================
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[ERROR] Faltan TELEGRAM_TOKEN / CHAT_ID para enviar mensaje.")
        return

    max_len = 3900  # margen bajo los 4096 de Telegram
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


# =====================================
# CALENDARIO ECONÓMICO (USA, 2–3⭐)
# =====================================
def get_calendar_df():
    today = dt.date.today()
    from_date = today.strftime("%d/%m/%Y")
    to_date = today.strftime("%d/%m/%Y")

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

    # Solo importancia media/alta (≈ 2–3 estrellas)
    df = df[df["importance"].isin(["medium", "high"])]

    if df.empty:
        return None

    # Eliminar duplicados (a veces investing repite eventos)
    df = df.drop_duplicates(subset=["event", "date", "time"], keep="first")

    # Ordenar: high primero, luego medium, por fecha y hora
    df["imp_rank"] = df["importance"].map({"high": 0, "medium": 1}).fillna(2)
    df = df.sort_values(by=["imp_rank", "date", "time"])

    # Limitar número de eventos para no saturar
    df = df.head(8)

    return df


# =====================================
# CONSTRUIR LISTA PLANA DE EVENTOS
# =====================================
def build_plain_events(df):
    """
    Devuelve:
    - events: lista de dicts con la info de cada evento
    - plain: texto plano con todos los eventos para pasárselo a la IA
    """
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

        importance = ev["importance"]
        if importance == "high":
            stars = "⭐⭐⭐"
        elif importance == "medium":
            stars = "⭐⭐"
        else:
            stars = ""

        line = (
            f"{stars} {ev['date']} {ev['time']} – {ev['event']} | "
            f"actual={ev['actual']} | forecast={ev['forecast']} | previous={ev['previous']}"
        )
        lines.append(line)

    plain = "\n".join(lines)
    return events, plain


# =====================================
# INTERPRETACIÓN GLOBAL CON IA
# =====================================
def interpret_calendar(plain_events: str) -> str:
    """
    Pide a GPT que haga un resumen estilo InvestX:
    - 1–2 líneas sobre el foco principal (ej. FOMC, tipos, etc.)
    - Varias líneas tipo '• 18:00 – dato | impacto...'
    - Cierre con '👉 Clave del día: ...'
    """
    if not client or not plain_events.strip():
        return ""

    system_prompt = (
        "Eres un analista macro que prepara un resumen para un canal de trading llamado InvestX. "
        "Recibirás una lista de eventos del calendario económico de Estados Unidos con hora, nombre y datos. "
        "Tu objetivo es escribir un RESUMEN BREVE en español, claro y directo, sin mencionar IA ni modelos. "
        "Formato deseado (ejemplo de estilo):\n\n"
        "FOMC y discurso de FOMC Member Williams | Muy relevante para mercado; pistas sobre futura política "
        "monetaria pueden generar volatilidad en índices y divisa, especialmente el USD.\n\n"
        "• 18:00 – Subasta de bonos a 20 años y balance presupuestario | Resultados influyen en rentabilidad "
        "de bonos y percepción fiscal, afectando a mercados de renta fija y dólar.\n\n"
        "👉 Clave del día: Publicación de minutos del FOMC a las 19:00, foco principal para anticipar movimientos "
        "en Fed, índices USA y USD.\n\n"
        "Instrucciones clave:\n"
        "- Máximo 8–10 líneas en total.\n"
        "- Puedes agrupar varios datos similares en una misma línea (ej. varios datos de vivienda).\n"
        "- Si hay FOMC, tipos, inflación o empleo, destácalos claramente.\n"
        "- Termina SIEMPRE con una línea '👉 Clave del día: ...' comentando el evento más importante.\n"
        "- No uses HTML ni negritas: solo texto plano.\n"
        "- Tono profesional pero cercano, sin exageraciones."
    )

    user_prompt = (
        "Estos son los eventos macroeconómicos de hoy en Estados Unidos (2–3 estrellas de importancia):\n\n"
        f"{plain_events}\n\n"
        "Escribe el resumen siguiendo exactamente el estilo descrito."
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
        return text
    except Exception as e:
        print("Error interpretando calendario económico:", e)
        return ""


# =====================================
# FALLBACK: LISTADO SIMPLE SIN IA
# =====================================
def build_simple_list(events):
    lines = []
    for ev in events:
        importance = ev["importance"]
        if importance == "high":
            stars = "⭐⭐⭐"
        elif importance == "medium":
            stars = "⭐⭐"
        else:
            stars = ""

        date = ev["date"]
        time = ev["time"]
        name = ev["event"]
        actual = ev["actual"]
        forecast = ev["forecast"]
        previous = ev["previous"]

        value_parts = []
        if actual and actual.lower() != "none":
            value_parts.append(f"Actual: {actual}")
        if forecast and forecast.lower() != "none":
            value_parts.append(f"Previsión: {forecast}")
        if previous and previous.lower() != "none":
            value_parts.append(f"Anterior: {previous}")

        values_line = " | ".join(value_parts) if value_parts else "Sin datos numéricos disponibles."

        line = f"{stars} {time} – {name} | {values_line}"
        lines.append(line)

    return "\n".join(lines).strip()


# =====================================
# FUNCIÓN PRINCIPAL
# =====================================
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
        msg = "📭 No hay eventos económicos relevantes en USA (2–3⭐) para hoy."
        print("[INFO]", msg)
        send_telegram(msg)
        return

    events, plain = build_plain_events(df)
    interpretation = interpret_calendar(plain)

    today = dt.date.today().strftime("%d/%m")
    header = f"({today}) – EE. UU. (2–3⭐)\n\n"

    if interpretation:
        body = interpretation
    else:
        # Si algo va mal con la IA, mandamos solo el listado sin comentarios genéricos
        body = build_simple_list(events)

    msg = header + body
    print("[INFO] Enviando calendario económico...")
    send_telegram(msg)
