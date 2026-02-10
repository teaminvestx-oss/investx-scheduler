# === econ_calendar.py ===
# InvestX - Economic Calendar (CME)
# - Bootstrap cookies (GET page) + POST /services/economic-release-events
# - Spanish output + Bloomberg-style interpretation via OpenAI API
# - Telegram via ENV: INVESTX_TOKEN + CHAT_ID (compat with TELEGRAM_* too)
# - Anti-duplicate daily sending (state file)
#
# NOTE:
# CME puede bloquear IPs de datacenter (Render) con 403. En ese caso,
# el script NO revienta: envía aviso + (opcional) interpretación "sin datos".

from __future__ import annotations

import os
import json
import time
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Tuple

import requests


# -----------------------------
# CONFIG
# -----------------------------
TZ = ZoneInfo("Europe/Madrid")

CME_PAGE_URL = "https://www.cmegroup.com/education/events/economic-releases-calendar.html"
CME_EVENTS_URL = "https://www.cmegroup.com/services/economic-release-events"

# Filtros por defecto (equivalente a lo que estás usando en la web)
DEFAULT_COUNTRY = os.getenv("ECON_COUNTRY", "United States")  # en web sale "United States"
DEFAULT_IMPACTS = os.getenv("ECON_IMPACTS", "Market Mover,Merits Extra Attention").strip()

# Ventana / rango
DAYS_FORWARD = int(os.getenv("ECON_DAYS_FORWARD", "1"))  # por defecto solo el día elegido

# Retries / timeouts
HTTP_TIMEOUT = int(os.getenv("ECON_HTTP_TIMEOUT", "25"))
HTTP_RETRIES = int(os.getenv("ECON_HTTP_RETRIES", "3"))
RETRY_SLEEP = float(os.getenv("ECON_RETRY_SLEEP", "1.5"))

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# Telegram
STATE_FILE = "econ_calendar_state.json"


# -----------------------------
# Telegram sender (compat)
# -----------------------------
def _send_telegram(message: str) -> None:
    token = (
        os.getenv("INVESTX_TOKEN")
        or os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("TELEGRAM_TOKEN")
    )
    chat_id = (
        os.getenv("CHAT_ID")
        or os.getenv("TELEGRAM_CHAT_ID")
        or os.getenv("TELEGRAM_CHAT")
    )

    if not token or not chat_id:
        print("[econ] Telegram no configurado (INVESTX_TOKEN/CHAT_ID o TELEGRAM_*). Imprimo mensaje:")
        print(message)
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        requests.post(url, json=payload, timeout=20).raise_for_status()
    except Exception as e:
        print(f"[econ] ERROR enviando Telegram: {e}")
        print(message)


# -----------------------------
# State (anti-duplicate)
# -----------------------------
def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_state(d: Dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass

def _already_sent(day_key: str) -> bool:
    st = _load_state()
    return st.get("last_sent_day") == day_key

def _mark_sent(day_key: str) -> None:
    st = _load_state()
    st["last_sent_day"] = day_key
    st["last_sent_at"] = datetime.now(TZ).isoformat()
    _save_state(st)


# -----------------------------
# OpenAI (Bloomberg-style)
# -----------------------------
def _openai_bloomberg_summary_es(day_label: str, events: List[Dict[str, Any]]) -> str:
    """
    Devuelve texto en español:
    1) Lista de eventos (sin prev/consenso)
    2) Interpretación tipo Bloomberg (riesgo / sesgo)
    """
    # Si no hay key, devolvemos plantilla sin IA
    if not OPENAI_API_KEY:
        lines = [f"**AGENDA MACRO - EE.UU. ({day_label})**", ""]
        if not events:
            lines.append("- **Eventos:** No se reportan eventos significativos para el día.")
            lines.append("")
            lines.append("**Lectura rápida:** Sesgo neutral por falta de referencias macro concretas.")
            return "\n".join(lines)

    # Reducimos payload para no meter ruido
    compact = []
    for e in events[:30]:
        compact.append({
            "time": e.get("time"),
            "event": e.get("event"),
            "country": e.get("country"),
            "impact": e.get("impact"),
        })

    sys = (
        "Eres el analista macro de un canal financiero (estilo Bloomberg) para traders. "
        "Escribes SIEMPRE en español neutro, conciso y accionable. "
        "NO inventes datos. No incluyas consenso ni dato previo. "
        "Formato:\n"
        "1) Título\n"
        "2) Lista de eventos con hora + evento + etiqueta de impacto\n"
        "3) Interpretación (2-5 bullets) sobre posible volatilidad y sesgo (risk-on/off/neutral)\n"
        "4) Nota final muy corta (gestión de riesgo)\n"
    )

    user = {
        "day_label": day_label,
        "events": compact,
        "instructions": "Traduce los nombres de eventos al español si vienen en inglés.",
    }

    try:
        # Chat Completions simple (compatible)
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": OPENAI_MODEL,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": sys},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
            },
            timeout=35,
        )
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"]["content"].strip()
        return text
    except Exception as e:
        # fallback sin IA
        lines = [f"**AGENDA MACRO - EE.UU. ({day_label})**", ""]
        if not events:
            lines.append("- **Eventos:** No se reportan eventos significativos para el día.")
            lines.append("")
            lines.append("**Lectura rápida:** Sesgo neutral por falta de referencias macro concretas.")
        else:
            lines.append("**Eventos:**")
            for ev in events:
                lines.append(f"- {ev.get('time','--:--')} — {ev.get('event','(evento)')} ({ev.get('impact','Impacto')})")
            lines.append("")
            lines.append("**Lectura rápida:** Posible aumento de volatilidad alrededor de los horarios clave.")
        lines.append("")
        lines.append(f"_OpenAI no disponible (error: {e})._")
        return "\n".join(lines)


# -----------------------------
# CME fetch (bootstrap + POST)
# -----------------------------
def _browser_headers() -> Dict[str, str]:
    # Imitación razonable (Safari/Chrome)
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
        ),
        "Accept": "*/*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": "https://www.cmegroup.com",
        "Referer": CME_PAGE_URL,
        "Connection": "keep-alive",
    }

def _bootstrap_session(sess: requests.Session) -> None:
    # 1) GET a la página -> cookies + seteo inicial
    sess.get(CME_PAGE_URL, headers=_browser_headers(), timeout=HTTP_TIMEOUT)

def _build_cme_payload(day_from: date, day_to: date) -> str:
    """
    CME espera Content-Type text/plain; en Safari suele ir un JSON en texto.
    Payload "mínimo" replicando filtros:
    - country: United States
    - impact: Market Mover, Merits Extra Attention
    - rango fechas
    """
    impacts = [x.strip() for x in DEFAULT_IMPACTS.split(",") if x.strip()]

    payload = {
        "country": DEFAULT_COUNTRY,
        "impacts": impacts,
        "fromDate": day_from.isoformat(),
        "toDate": day_to.isoformat(),
        "timezone": "America/Chicago",  # CME muestra CT
        "language": "en",               # el backend suele devolver en inglés; luego traducimos con IA
    }
    return json.dumps(payload, ensure_ascii=False)

def fetch_cme_events(day_local: date) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Devuelve (events, error_string)
    events: lista normalizada con keys: time, event, country, impact
    """
    day_from = day_local
    day_to = day_local + timedelta(days=max(0, DAYS_FORWARD - 1))

    sess = requests.Session()
    headers = _browser_headers()

    last_err: Optional[str] = None

    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            _bootstrap_session(sess)

            body = _build_cme_payload(day_from, day_to)

            resp = sess.post(
                CME_EVENTS_URL,
                headers={**headers, "Content-Type": "text/plain;charset=UTF-8"},
                data=body,
                timeout=HTTP_TIMEOUT,
            )

            # Si CME bloquea por WAF, suele ser 403
            if resp.status_code == 403:
                last_err = "403_FORBIDDEN"
                raise requests.HTTPError("403 Client Error: Forbidden", response=resp)

            resp.raise_for_status()

            data = resp.json()

            raw_events = data.get("events") or []
            out: List[Dict[str, Any]] = []

            for ev in raw_events:
                # Campos típicos (pueden variar)
                # Intentamos ser tolerantes:
                time_str = ev.get("time") or ev.get("releaseTime") or ev.get("eventTime") or "--:--"
                title = ev.get("event") or ev.get("title") or ev.get("name") or ""
                country = ev.get("country") or ev.get("countryName") or DEFAULT_COUNTRY
                impact = ev.get("impact") or ev.get("impactLabel") or ev.get("importance") or ""

                out.append({
                    "time": str(time_str),
                    "event": str(title),
                    "country": str(country),
                    "impact": str(impact),
                })

            return out, None

        except Exception as e:
            last_err = str(e)
            print(f"[econ] CME exception attempt {attempt}/{HTTP_RETRIES}: {e}")
            time.sleep(RETRY_SLEEP)

    return [], last_err


# -----------------------------
# Public entrypoint
# -----------------------------
def run_econ_calendar(force: bool = False, force_tomorrow: bool = False) -> None:
    """
    - force=False: respeta anti-duplicados (1 envío/día)
    - force=True: envía aunque ya se haya enviado hoy
    - force_tomorrow=True: usa mañana (Europe/Madrid) como fecha objetivo
    """
    now = datetime.now(TZ)
    target = (now.date() + timedelta(days=1)) if force_tomorrow else now.date()

    day_key = target.isoformat()
    if (not force) and _already_sent(day_key):
        print(f"[econ] Ya enviado para {day_key}. Skipping.")
        return

    print(f"[econ] Descargando CME para {day_key} (USA)")

    events, err = fetch_cme_events(target)

    # Construimos etiqueta día para título
    # Ej: "2026-02-10"
    day_label = target.strftime("%Y-%m-%d")

    if err:
        # Mensaje claro, sin tumbar cron
        warn_lines = [
            f"**AGENDA MACRO - EE.UU. ({day_label})**",
            "",
        ]

        if "403" in err or "Forbidden" in err or err == "403_FORBIDDEN":
            warn_lines += [
                "⚠️ **CME está bloqueando la petición desde el servidor (403).**",
                "Esto suele pasar con **IPs de datacenter** (Render) o reglas anti-bot.",
                "",
            ]
        elif "Read timed out" in err or "timeout" in err.lower():
            warn_lines += [
                "⚠️ **Timeout al consultar CME desde el servidor.**",
                "Puede ser temporal o un bloqueo intermitente.",
                "",
            ]
        else:
            warn_lines += [
                f"⚠️ **Error consultando CME:** `{err}`",
                "",
            ]

        # Aun así, generamos “lectura” sin datos (IA o plantilla)
        text = _openai_bloomberg_summary_es(day_label, events)
        message = "\n".join(warn_lines) + text

        _send_telegram(message)
        _mark_sent(day_key)
        print(f"[econ] OK enviado (con error CME) para {day_key} (force={force}, force_tomorrow={force_tomorrow}).")
        return

    # Caso OK (con eventos o vacío)
    text = _openai_bloomberg_summary_es(day_label, events)
    _send_telegram(text)
    _mark_sent(day_key)
    print(f"[econ] OK enviado para {day_key} (force={force}, force_tomorrow={force_tomorrow}).")
