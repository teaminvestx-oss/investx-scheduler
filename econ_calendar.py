# econ_calendar.py

import os
import datetime as dt
import requests
from openai import OpenAI
import investpy  # usa Investing.com por debajo


# ========= CONFIG (ENV VARS) =========

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # id del canal InvestX (ej: -100xxxx)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or not OPENAI_API_KEY:
    raise RuntimeError("Faltan env vars: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID u OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)


# ========= UTILIDAD TELEGRAM =========

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    r = requests.post(url, json=payload)
    r.raise_for_status()
    return r.json()


# ========= CALENDARIO ECONÓMICO (INVESTING → USA 2–3⭐) =========

def fetch_investing_calendar(start_date: dt.date, end_date: dt.date):
    """
    Usa investpy.economic_calendar (fuente Investing.com) para obtener eventos
    entre start_date y end_date (ambos inclusive).
    """
    start_str = start_date.strftime("%d/%m/%Y")
    end_str = end_date.strftime("%d/%m/%Y")

    df = investpy.news.economic_calendar(
        from_date=start_str,
        to_date=end_str
    )

    events = []
    for _, row in df.iterrows():
        events.append({
            "date": str(row.get("date", "")),        # formato 'YYYY-MM-DD'
            "time": str(row.get("time", "")),        # 'HH:MM'
            "country": str(row.get("country", "")),  # 'United States', etc.
            "event": str(row.get("event", "")),
            # importance: 'low' / 'medium' / 'high'
            "importance": str(row.get("importance", "")).lower(),
        })

    return events


def filter_events_usa_2_3_stars(events):
    """Filtra SOLO Estados Unidos + importancia medium/high (≈ 2–3⭐)."""
    filtered = []

    for ev in events:
        country_raw = (ev.get("country") or "").strip().lower()
        if country_raw not in ["united states", "united states of america", "usa", "us", "estados unidos"]:
            continue

        importance = (ev.get("importance") or "").strip().lower()
        if importance not in ["medium", "high"]:
            continue

        # medium → 2⭐, high → 3⭐
        stars = 2 if importance == "medium" else 3
        ev["stars"] = stars
        filtered.append(ev)

    return filtered


def build_events_text(events):
    lines = []
    for ev in events:
        date = ev.get("date", "")
        time = ev.get("time", "")
        event_name = ev.get("event", "")
        stars = ev.get("stars", "")

        impact_text = f"{stars}⭐" if stars else ""
        line = f"{date} {time} | {event_name} | {impact_text}"
        lines.append(line)

    return "\n".join(lines)


def summarize_events_calendar(raw_text: str, mode: str, today: dt.date) -> str:
    if mode == "weekly":
        system_prompt = (
            "Eres InvestX, analista institucional. "
            "Tienes el calendario económico SOLO de Estados Unidos y SOLO de importancia 2–3 estrellas. "
            "Haz un resumen SEMANAL para un canal de Telegram de trading:\n\n"
            "- Agrupa por día (Lunes, Martes, etc.).\n"
            "- Destaca hora, tipo de dato y por qué importa para índices USA y USD.\n"
            "- Máximo 8–10 bullets.\n"
            "- Termina con sección '📌 Claves InvestX' con 2–3 ideas clave."
        )
        header = "📆 *Resumen calendario económico de la semana – EE. UU. (2–3⭐)*\n\n"
    else:
        system_prompt = (
            "Eres InvestX, analista institucional. "
            "Haz un resumen DIARIO del calendario económico SOLO de EE. UU. y SOLO de importancia 2–3 estrellas.\n\n"
            "- Haz 3–6 bullets.\n"
            "- Destaca hora, dato y posible impacto en índices USA y USD.\n"
            "- Termina con una línea '👉 Clave del día:' con el catalizador principal."
        )
        header = f"📆 *Calendario económico para hoy ({today.strftime('%d/%m')}) – EE. UU. (2–3⭐)*\n\n"

    user_prompt = f"Eventos filtrados (USA 2–3⭐):\n\n{raw_text}"

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",   # MINI para calendarios (barato y suficiente)
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    summary = resp.choices[0].message.content.strip()
    return header + summary


def run_econ_calendar(mode: str):
    """
    mode = 'weekly' → semana completa desde hoy
    mode = 'daily'  → solo hoy

    Siempre envía algún mensaje a Telegram:
    - resumen (si hay eventos)
    - o mensaje de “no hay eventos relevantes”.
    """
    today = dt.date.today()

    if mode == "weekly":
        start_date = today
        end_date = today + dt.timedelta(days=6)
    else:
        start_date = end_date = today

    events = fetch_investing_calendar(start_date, end_date)
    events = filter_events_usa_2_3_stars(events)

    if not events:
        if mode == "weekly":
            msg = "📆 Esta semana no hay eventos relevantes (2–3⭐) en EE. UU."
        else:
            msg = "📆 Hoy no hay eventos relevantes (2–3⭐) en EE. UU."
        send_telegram_message(msg)
        return

    raw_text = build_events_text(events)
    msg = summarize_events_calendar(raw_text, mode, today)
    send_telegram_message(msg)
