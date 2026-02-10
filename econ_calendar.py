import os
import json
import requests
from datetime import datetime
from typing import List, Dict

CME_CALENDAR_PAGE = "https://www.cmegroup.com/education/events/economic-releases-calendar.html"
CME_EVENTS_API = "https://www.cmegroup.com/services/economic-release-events"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =========================
# CME FETCH (browser-like)
# =========================
def fetch_cme_events(date: str) -> List[Dict]:
    session = requests.Session()

    # 1) Warm-up page (cookies / akamai / consent)
    session.get(
        CME_CALENDAR_PAGE,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        },
        timeout=25,
    )

    payload = {
        "startDate": date,
        "endDate": date,
        "countries": ["United States"],
        "impact": ["Market Mover", "Merits Extra Attention"],
    }

    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.cmegroup.com",
        "Referer": CME_CALENDAR_PAGE,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "X-Requested-With": "XMLHttpRequest",
    }

    resp = session.post(
        CME_EVENTS_API,
        data=json.dumps(payload),
        headers=headers,
        timeout=25,
    )

    if resp.status_code == 403:
        raise RuntimeError("CME bloquea la IP (403). Render muy probable.")

    resp.raise_for_status()
    return resp.json().get("events", [])


# =========================
# GPT INTERPRETATION
# =========================
def interpret_with_gpt(events: List[Dict], date: str) -> str:
    if not events:
        return (
            f"**Agenda macro – Estados Unidos ({date})**\n\n"
            "No se publican datos macroeconómicos relevantes hoy.\n\n"
            "**Sesgo esperado:** Neutral"
        )

    events_text = "\n".join(
        f"- {e.get('eventName','')} ({e.get('eventTime','')})"
        for e in events
    )

    prompt = f"""
Eres un analista macro profesional (estilo Bloomberg).

Fecha: {date}
País: Estados Unidos

Eventos:
{events_text}

Tareas:
1. Resume los eventos en español claro.
2. Explica por qué importan para mercado.
3. Da un sesgo de riesgo: Alcista / Bajista / Neutral.
NO menciones consenso ni datos previos.
"""

    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        },
        timeout=40,
    )

    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# =========================
# TELEGRAM
# =========================
def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[econ] Telegram no configurado, imprimo mensaje:\n")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        },
        timeout=20,
    )


# =========================
# MAIN ENTRY
# =========================
def run_econ_calendar():
    today = datetime.now().strftime("%Y-%m-%d")

    print(f"[econ] Descargando CME para {today}")
    events = fetch_cme_events(today)

    print(f"[econ] Eventos encontrados: {len(events)}")
    message = interpret_with_gpt(events, today)

    send_telegram(message)
    print("[econ] OK enviado")
