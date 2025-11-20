# ================================
# econ_calendar.py  —  InvestX v3.0
# ================================

import os
import logging
import datetime as dt
import pandas as pd
from openai import OpenAI
import investpy
from utils import send_telegram_message

logger = logging.getLogger(__name__)


# =======================================================
# CONFIG
# =======================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Palabras clave para incluir eventos aunque importance = 1
KEYWORDS = [
    "fomc",
    "fed",
    "powell",
    "balance sheet",
    "trump",

    "cpi",
    "inflation",
    "pce",
    "core",

    "nonfarm",
    "payroll",
    "nfp",

    "jobless",
    "claims",
    "unemployment",

    "gdp",
    "manufacturing",
    "ism",
    "pmi",

    "housing starts",
    "building permits",
]

COUNTRY = "united states"
MAX_MESSAGES = 4   # evitar spam en telegram


# =======================================================
# FILTRO DE IMPORTANCIA + KEYWORDS
# =======================================================

def event_is_relevant(row):
    """
    Devuelve True si el evento debe entrar:
    - importance >= 2
    - O contiene palabras clave de interés
    """
    title = str(row.get("event", "")).lower()

    # Regla principal
    if int(row.get("importance", 0)) >= 2:
        return True

    # Palabras clave
    for k in KEYWORDS:
        if k in title:
            return True

    return False


# =======================================================
# FORMATEO DE EVENTOS
# =======================================================

def format_event(row):
    hora = row.get("time", "").strip()
    evento = row.get("event", "")
    prev = row.get("previous", "")
    forecast = row.get("forecast", "")
    actual = row.get("actual", "")

    text = f"⏰ <b>{hora}</b> — {evento}"

    extra = []
    if forecast not in (None, "", "nan"):
        extra.append(f"📊 Prev: {forecast}")
    if actual not in (None, "", "nan"):
        extra.append(f"📈 Actual: {actual}")
    if prev not in (None, "", "nan"):
        extra.append(f"📉 Ant: {prev}")

    if extra:
        text += "\n" + " • ".join(extra)

    return text


# =======================================================
# RESUMEN BREVE VIA IA
# =======================================================

def summarize_events_with_ai(events_text):
    if not client or not events_text.strip():
        return ""

    prompt = (
        "Haz un resumen MUY BREVE (2-3 frases) en español sobre los eventos "
        "macroeconómicos del día. No menciones que eres IA. Explica "
        "por qué estos datos pueden importar al mercado hoy."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "Eres un analista macro profesional."},
                {"role": "user", "content": prompt + "\n\nEventos:\n" + events_text},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error("Error IA resumen: %s", e)
        return ""


# =======================================================
# EJECUCIÓN PRINCIPAL
# =======================================================

def run_econ_calendar(force=False):

    logger.info("Obteniendo calendario económico USA...")

    today = dt.date.today()
    today_str = today.strftime("%d/%m/%Y")

    try:
        df = investpy.news.economic_calendar(
            from_date=today.strftime("%d/%m/%Y"),
            to_date=today.strftime("%d/%m/%Y"),
        )
    except Exception as e:
        logger.error("❌ Error al obtener calendario económico: %s", e)
        return

    if df is None or df.empty:
        send_telegram_message("📅 Hoy no hay datos macro relevantes en EE. UU.")
        return

    # Filtrar país
    df = df[df["country"].str.lower() == COUNTRY]

    # Ordenar por fecha + hora
    try:
        df["datetime"] = pd.to_datetime(df["date"] + " " + df["time"], errors="coerce")
        df = df.sort_values("datetime")
    except:
        pass

    # Filtrar eventos importantes
    df = df[df.apply(event_is_relevant, axis=1)]

    if df.empty:
        send_telegram_message("📅 Hoy no hay referencias macro importantes en EE. UU.")
        return

    # ==== FORMATEAR TEXTO ====

    events_blocks = []
    for _, row in df.iterrows():
        events_blocks.append(format_event(row))

    all_events_text = "\n\n".join(events_blocks)

    # ==== RESUMEN BREVE ====
    resumen = summarize_events_with_ai(all_events_text)

    final_text = (
        f"📅 <b>Agenda macro USA — {today_str}</b>\n\n"
        + all_events_text
    )

    if resumen:
        final_text += "\n\n<b>Resumen clave:</b>\n" + resumen

    # ==== TROCEO EN 4 MENSAJES MÁX ====
    chunks = []
    max_len = 3800
    txt = final_text
    while len(txt) > max_len:
        part = txt[:max_len]
        chunks.append(part)
        txt = txt[max_len:]
    chunks.append(txt)

    for i, c in enumerate(chunks[:MAX_MESSAGES], start=1):
        send_telegram_message(c)

    logger.info("econ_calendar: Calendario económico enviado.")
