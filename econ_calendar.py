import os
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional


# =========================
# Config
# =========================
CME_CALENDAR_PAGE = "https://www.cmegroup.com/education/events/economic-releases-calendar.html"
CME_EVENTS_API    = "https://www.cmegroup.com/services/economic-release-events"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# Estado para evitar duplicados (1 envío/día)
STATE_FILE = "econ_calendar_state.json"

TZ = ZoneInfo("Europe/Madrid")


# =========================
# State helpers (dedupe)
# =========================
def _load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except:
        return {}

def _save_state(d: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except:
        pass

def _already_sent(target_date: str) -> bool:
    st = _load_state()
    return st.get("sent_for_date") == target_date

def _mark_sent(target_date: str) -> None:
    st = _load_state()
    st["sent_for_date"] = target_date
    st["sent_at"] = datetime.now(TZ).isoformat()
    _save_state(st)


# =========================
# CME fetch (browser-like)
# =========================
def _fetch_cme_events(target_date: str) -> List[Dict[str, Any]]:
    """
    target_date: 'YYYY-MM-DD' (Europe/Madrid, solo para el rango de consulta)
    """
    session = requests.Session()

    # 1) Warm-up para cookies / WAF
    session.get(
        CME_CALENDAR_PAGE,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=25,
    )

    payload = {
        "startDate": target_date,
        "endDate": target_date,
        "countries": ["United States"],
        # Estos son los que ves en la web:
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

    # CME puede bloquear IPs de datacenter (Render) => 403
    if resp.status_code == 403:
        raise requests.HTTPError("403 Forbidden (WAF/Datacenter block)", response=resp)

    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return data.get("events", []) or []


# =========================
# OpenAI (interpretación)
# =========================
def _interpret_with_openai(events: List[Dict[str, Any]], target_date: str) -> str:
    if not OPENAI_API_KEY:
        # Sin OpenAI: fallback mínimo en ES
        if not events:
            return (
                f"**AGENDA MACRO – EE.UU. ({target_date})**\n\n"
                "No se publican datos macroeconómicos relevantes hoy.\n\n"
                "**Sesgo esperado:** Neutral"
            )
        lines = []
        for e in events:
            name = e.get("eventName") or e.get("name") or "Evento"
            time = e.get("eventTime") or e.get("time") or ""
            impact = e.get("impact") or ""
            lines.append(f"- {name} ({time}) {('— ' + impact) if impact else ''}".strip())
        return (
            f"**AGENDA MACRO – EE.UU. ({target_date})**\n\n"
            + "\n".join(lines)
            + "\n\n**Sesgo esperado:** Neutral"
        )

    # Prepara lista “limpia” (sin consenso/previo)
    compact = []
    for e in events:
        compact.append({
            "time": e.get("eventTime") or e.get("time") or "",
            "event": e.get("eventName") or e.get("name") or "",
            "impact": e.get("impact") or "",
            "country": e.get("country") or "United States",
        })

    prompt = f"""
Eres un analista macro profesional (estilo Bloomberg) para un canal de trading.

Objetivo:
- Escribir en ESPAÑOL, claro, directo, sin jerga innecesaria.
- Dar primero una LISTA de eventos (hora + evento + impacto).
- Luego una interpretación breve de cada evento (1-2 frases por evento).
- Finalmente un "Sesgo de riesgo" (Alcista / Bajista / Neutral) y por qué en 2-3 líneas.
- NO menciones "consenso" ni "previo" ni valores numéricos (aunque existan).
- Si un evento no es relevante para mercados, dilo.

Fecha objetivo: {target_date}
País: Estados Unidos

Eventos (JSON):
{json.dumps(compact, ensure_ascii=False)}
""".strip()

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
        timeout=45,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# =========================
# Telegram send
# =========================
def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[econ] Telegram no configurado (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID). Imprimo mensaje:\n")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=25,
    )
    resp.raise_for_status()


# =========================
# Public API (compatible con tu main)
# =========================
def run_econ_calendar(force: bool = False, force_tomorrow: bool = False) -> None:
    """
    Compatible con tu main.py:
      run_econ_calendar(force=True/False, force_tomorrow=True/False)

    - En modo normal: envía 1 vez/día (dedupe por fecha objetivo).
    - En force=True: ignora dedupe.
    - En force_tomorrow=True: calcula fecha objetivo = mañana (Europe/Madrid).
    """
    now = datetime.now(TZ)
    target = now.date() + (timedelta(days=1) if force_tomorrow else timedelta(days=0))
    target_date = target.strftime("%Y-%m-%d")

    # Dedupe: si Render ejecuta 20 veces, solo 1 envío
    if (not force) and _already_sent(target_date):
        print(f"[econ] Ya enviado para {target_date}. Skip.")
        return

    # Descarga CME
    try:
        print(f"[econ] Descargando CME para {target_date} (USA)")
        events = _fetch_cme_events(target_date)
        print(f"[econ] CME eventos: {len(events)}")
    except requests.HTTPError as e:
        # No rompas cronjob: manda aviso claro
        status = getattr(e.response, "status_code", None)
        if status == 403:
            msg = (
                f"**AGENDA MACRO – EE.UU. ({target_date})**\n\n"
                "⚠️ CME está bloqueando la petición desde el servidor (403).\n"
                "Esto suele pasar con IPs de datacenter (Render).\n\n"
                "**Sesgo esperado:** Neutral (sin datos)\n"
            )
            _send_telegram(msg)
            _mark_sent(target_date)
            return
        raise
    except Exception as e:
        msg = (
            f"**AGENDA MACRO – EE.UU. ({target_date})**\n\n"
            f"⚠️ Error obteniendo CME: {e}\n\n"
            "**Sesgo esperado:** Neutral (sin datos)\n"
        )
        _send_telegram(msg)
        _mark_sent(target_date)
        return

    # Interpreta (OpenAI) y envía
    try:
        text = _interpret_with_openai(events, target_date)
    except Exception as e:
        # fallback si OpenAI falla
        print(f"[econ] OpenAI fallo: {e}")
        if not events:
            text = (
                f"**AGENDA MACRO – EE.UU. ({target_date})**\n\n"
                "No se publican datos macroeconómicos relevantes hoy.\n\n"
                "**Sesgo esperado:** Neutral"
            )
        else:
            lines = []
            for ev in events:
                name = ev.get("eventName") or ev.get("name") or "Evento"
                time = ev.get("eventTime") or ev.get("time") or ""
                impact = ev.get("impact") or ""
                lines.append(f"- {name} ({time}) {('— ' + impact) if impact else ''}".strip())
            text = (
                f"**AGENDA MACRO – EE.UU. ({target_date})**\n\n"
                + "\n".join(lines)
                + "\n\n**Sesgo esperado:** Neutral"
            )

    _send_telegram(text)
    _mark_sent(target_date)
    print(f"[econ] OK enviado para {target_date} (force={force}, force_tomorrow={force_tomorrow})")
