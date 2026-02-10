import os
import json
import requests
from datetime import datetime
from openai import OpenAI


# =====================
# CONFIG
# =====================
CME_EVENTS_URL = "https://www.cmegroup.com/services/economic-release-events"
TIMEOUT = 25

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# =====================
# CME FETCH
# =====================
def fetch_cme_events(date_yyyy_mm_dd: str) -> list[dict]:
    """
    Descarga eventos CME (USA) para una fecha concreta.
    Devuelve lista de eventos crudos.
    """
    payload = {
        "startDate": date_yyyy_mm_dd,
        "endDate": date_yyyy_mm_dd,
        "countries": ["United States"],
        "impact": ["Market Mover", "Merits Extra Attention"],
    }

    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0",
    }

    resp = requests.post(
        CME_EVENTS_URL,
        data=json.dumps(payload),
        headers=headers,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()

    data = resp.json()
    return data.get("events", [])


# =====================
# GPT BLOOMBERG STYLE
# =====================
def gpt_calendar_es(events: list[dict], date_label_es: str) -> str:
    """
    Traduce + lista eventos + interpretación Bloomberg.
    TODO en español.
    """
    if not OPENAI_API_KEY:
        return f"📅 **Agenda macro — {date_label_es}**\n\n⚠️ OPENAI_API_KEY no configurada."

    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""
Vas a generar un mensaje para Telegram en CASTELLANO (España) a partir de eventos macroeconómicos crudos de CME.

Requisitos OBLIGATORIOS:
1) NO incluir consenso ni dato previo.
2) Traducir TODOS los nombres de eventos al español.
3) Formato exacto de lista:
   • HH:MM — Nombre del evento traducido | Impacto: Alto / Medio
4) Después de la lista, añade una interpretación estilo Bloomberg (máx 8 líneas).
   - Impacto probable en: S&P / Nasdaq, bonos USA (10Y) y USD.
   - Cierra con: "Sesgo de riesgo: Alcista / Neutral / Bajista".
5) Si no hay eventos relevantes, indícalo claramente.

Fecha: {date_label_es}

Eventos crudos (JSON):
{json.dumps(events, ensure_ascii=False)}
"""

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": "Eres un analista macro institucional. Respondes en español (España)."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )

    return resp.choices[0].message.content.strip()


# =====================
# TELEGRAM
# =====================
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[econ] Telegram no configurado, imprimo mensaje:\n")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    requests.post(url, json=payload, timeout=15)


# =====================
# MAIN
# =====================
def run_econ_calendar():
    today = datetime.now().strftime("%Y-%m-%d")
    date_label_es = datetime.now().strftime("%A %d de %B").capitalize()

    print(f"[econ] Descargando CME para {today}…")
    events = fetch_cme_events(today)

    print(f"[econ] Eventos recibidos: {len(events)}")
    message = gpt_calendar_es(events, date_label_es)

    send_telegram(message)
    print("[econ] OK enviado.")


if __name__ == "__main__":
    run_econ_calendar()
