# === econ_calendar.py ===
# InvestX - Calendario económico (CME) + resumen estilo Bloomberg (ChatGPT)
#
# ✅ Fuente: CME Group (NO pago, NO investpy, NO finnhub, NO FMP)
# ✅ Filtros: SOLO USA + Impactos altos (Market Mover, Merits Extra Attention)
# ✅ Control anti-duplicados (1 envío por día objetivo)
# ✅ Soporta "mañana" (force_tomorrow=True o ENV ECON_FORCE_TOMORROW=1)
# ✅ Si hay OPENAI_API_KEY -> genera texto estilo Bloomberg
# ✅ Si no hay OPENAI_API_KEY -> genera resumen “fallback” estructurado
#
# Uso desde main.py:
#   from econ_calendar import run_econ_calendar
#   run_econ_calendar(force=False)                 # normal (hoy)
#   run_econ_calendar(force=True)                  # fuerza aunque se haya enviado
#   run_econ_calendar(force=True, force_tomorrow=True)  # fuerza mañana
#
# ENV opcionales:
#   OPENAI_API_KEY, OPENAI_MODEL (por defecto gpt-4o-mini)
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
#   ECON_FORCE_TOMORROW=1
#   ECON_COUNTRY=US
#   ECON_IMPACTS="Market Mover,Merits Extra Attention"
#   ECON_MAX_EVENTS=12

import os
import json
import time
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional

import requests


# -----------------------------
# Config
# -----------------------------
TZ = ZoneInfo("Europe/Madrid")

CME_EVENTS_URL = "https://www.cmegroup.com/CmeWS/mvc/EconomicRelease/EventList"

ECON_COUNTRY = os.getenv("ECON_COUNTRY", "US").strip()  # CME espera "US"
ECON_IMPACTS = [s.strip() for s in os.getenv(
    "ECON_IMPACTS", "Market Mover,Merits Extra Attention"
).split(",") if s.strip()]

ECON_MAX_EVENTS = int(os.getenv("ECON_MAX_EVENTS", "12"))

STATE_FILE = "econ_calendar_state.json"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (InvestX EconBot)",
    "Accept": "application/json",
}


# -----------------------------
# State (anti-duplicados)
# -----------------------------
def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _save_state(d: Dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass

def _already_sent_for(target_date_iso: str) -> bool:
    st = _load_state()
    return st.get("sent_for_date") == target_date_iso

def _mark_sent(target_date_iso: str) -> None:
    st = _load_state()
    st["sent_for_date"] = target_date_iso
    st["sent_at"] = datetime.now(TZ).isoformat()
    _save_state(st)


# -----------------------------
# CME fetch
# -----------------------------
def _fetch_cme_events(from_date_iso: str, to_date_iso: str) -> List[Dict[str, Any]]:
    """
    from_date_iso / to_date_iso: 'YYYY-MM-DD'
    """
    params = {
        "fromDate": from_date_iso,
        "toDate": to_date_iso,
        "country": ECON_COUNTRY,
        "impact": ",".join(ECON_IMPACTS),
    }
    r = requests.get(CME_EVENTS_URL, headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    data = r.json() or {}
    events = data.get("events", []) or []
    return events

def _safe_str(x: Any) -> str:
    return "" if x is None else str(x)

def _normalize_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Intento de normalizar campos típicos de CME:
      - eventTime (string)
      - event (nombre)
      - impact
      - actual / consensus / previous (a veces llegan como strings)
    """
    out: List[Dict[str, Any]] = []
    for e in events:
        out.append({
            "time": _safe_str(e.get("eventTime") or e.get("time") or ""),
            "event": _safe_str(e.get("event") or e.get("eventName") or e.get("title") or ""),
            "impact": _safe_str(e.get("impact") or ""),
            "actual": _safe_str(e.get("actual") or e.get("act") or ""),
            "consensus": _safe_str(e.get("consensus") or e.get("forecast") or e.get("cons") or ""),
            "previous": _safe_str(e.get("previous") or e.get("prev") or ""),
            "unit": _safe_str(e.get("unit") or ""),
        })
    # orden: primero por hora si viene algo como "10:00 AM CT" (no siempre parseable)
    return out


# -----------------------------
# Telegram
# -----------------------------
def _send_telegram(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[econ] Telegram no configurado (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID). Imprimo mensaje:\n")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()


# -----------------------------
# Bloomberg-style copy (OpenAI)
# -----------------------------
def _openai_bloomberg_text(target_date_label: str, events: List[Dict[str, Any]]) -> Optional[str]:
    """
    Devuelve texto si hay OPENAI_API_KEY, si no -> None
    """
    if not OPENAI_API_KEY:
        return None

    # compactamos eventos para prompt
    compact = []
    for e in events[:ECON_MAX_EVENTS]:
        compact.append({
            "time": e["time"],
            "event": e["event"],
            "impact": e["impact"],
            "actual": e["actual"],
            "consensus": e["consensus"],
            "previous": e["previous"],
        })

    system = (
        "Eres un redactor financiero estilo Bloomberg/WSJ, conciso y profesional. "
        "Escribes en español para un canal de trading. "
        "No inventes datos (si falta ACT/CONS/PREV, indícalo como '—'). "
        "Formato: título + bullets claros. "
        "Incluye una línea final 'Sesgo de riesgo' (Risk-on / Risk-off / Neutral) basado SOLO en la agenda (no en precio)."
    )

    user = {
        "date": target_date_label,
        "country": "Estados Unidos",
        "filters": {
            "impact": ECON_IMPACTS
        },
        "events": compact
    }

    # Chat Completions (simple, estable con requests)
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENAI_MODEL,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)}
        ],
    }

    r = requests.post(url, headers=headers, json=payload, timeout=40)
    r.raise_for_status()
    data = r.json()
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return text.strip() if text else None


# -----------------------------
# Fallback copy (sin OpenAI)
# -----------------------------
def _fallback_text(target_date_label: str, events: List[Dict[str, Any]]) -> str:
    lines = []
    lines.append(f"📅 Calendario económico — {target_date_label} (EE. UU.)")
    lines.append(f"Filtros CME: Impacto = {', '.join(ECON_IMPACTS)}")
    lines.append("")
    if not events:
        lines.append("Hoy no hay datos macro relevantes en EE. UU. (según filtros CME).")
        return "\n".join(lines)

    for e in events[:ECON_MAX_EVENTS]:
        act = e["actual"] if e["actual"] else "—"
        cons = e["consensus"] if e["consensus"] else "—"
        prev = e["previous"] if e["previous"] else "—"
        t = e["time"] if e["time"] else "Hora —"
        lines.append(f"• {t} | {e['event']} | ACT {act} | CONS {cons} | PREV {prev} | {e['impact']}")
    lines.append("")
    lines.append("Sesgo de riesgo: Neutral (agenda sin sorpresa previa).")
    return "\n".join(lines)


# -----------------------------
# Main runner
# -----------------------------
def run_econ_calendar(force: bool = False, force_tomorrow: bool = False) -> None:
    """
    Ejecuta envío del calendario económico (CME).
    - force: ignora anti-duplicados
    - force_tomorrow: usa fecha de mañana (Madrid)
    """
    # Compatibilidad con ENV
    env_force_tomorrow = os.getenv("ECON_FORCE_TOMORROW", "0").strip().lower() in ("1", "true", "yes")
    if env_force_tomorrow:
        force_tomorrow = True

    now = datetime.now(TZ)
    target_dt = (now + timedelta(days=1)) if force_tomorrow else now
    target_date = target_dt.date()  # date obj
    target_iso = target_date.isoformat()  # YYYY-MM-DD
    target_label = target_date.strftime("%a %d/%m").title()

    if (not force) and _already_sent_for(target_iso):
        print(f"[econ] Ya enviado para {target_iso}. No reenvío.")
        return

    # Fetch con reintentos simples
    last_err = None
    raw_events: List[Dict[str, Any]] = []
    for attempt in range(1, 4):
        try:
            raw_events = _fetch_cme_events(target_iso, target_iso)
            last_err = None
            break
        except Exception as e:
            last_err = e
            print(f"[econ] CME exception attempt {attempt}/3: {e}")
            time.sleep(1.2)

    if last_err is not None:
        print(f"[econ] CME returned EMPTY after retries (with error): {last_err}")
        # aun así, mandamos mensaje de “sin datos” para que no se quede colgado
        events_norm: List[Dict[str, Any]] = []
    else:
        events_norm = _normalize_events(raw_events)

    # Generación de texto (OpenAI si hay key)
    text = _openai_bloomberg_text(target_label, events_norm)
    if not text:
        text = _fallback_text(target_label, events_norm)

    # Envío
    _send_telegram(text)

    # Marca estado
    if not force:
        _mark_sent(target_iso)

    print(f"[econ] OK enviado para {target_iso} (force={force}, force_tomorrow={force_tomorrow}).")
