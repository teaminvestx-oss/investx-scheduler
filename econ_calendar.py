# === econ_calendar.py ===
# Calendario económico (USA) para InvestX
# Fuente principal: CME Group Economic Release Calendar (web)
# - Sin APIs de pago
# - Con control anti-duplicados para no spamear si Render ejecuta varias veces
# - Opcional: resumen estilo Bloomberg usando call_gpt_mini (si está en utils)

import os
import re
import json
import time as _time
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from utils import send_telegram_message, call_gpt_mini  # asumimos que ya lo tienes así

# -----------------------
# Config
# -----------------------
TZ = ZoneInfo("Europe/Madrid")
STATE_FILE = "econ_calendar_state.json"

CME_PAGE_URL = "https://www.cmegroup.com/education/events/economic-releases-calendar.html"

# Si quieres “solo USA”, dejamos un filtro por país/flag si viene en los datos
ONLY_USA = os.getenv("ECON_ONLY_USA", "1").strip().lower() in ("1", "true", "yes")

# Impact filter (si la fuente lo trae): Market Mover / Merits Extra Attention / Other Key Indicator
# En CME web lo ves como filtros; aquí lo dejamos flexible:
IMPACT_ALLOW = set(
    x.strip().lower()
    for x in os.getenv("ECON_IMPACT_ALLOW", "market mover,merits extra attention").split(",")
    if x.strip()
)

# Anti-spam: solo 1 envío por "target_date" (hoy o mañana)
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

def _already_sent(target_key: str) -> bool:
    st = _load_state()
    return st.get("sent_key") == target_key

def _mark_sent(target_key: str) -> None:
    st = _load_state()
    st["sent_key"] = target_key
    st["sent_at"] = datetime.now(TZ).isoformat()
    _save_state(st)

def _safe_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 25) -> str:
    # Headers “normales” (no para esconder nada), solo para evitar respuestas raras por missing UA
    h = {
        "User-Agent": "Mozilla/5.0 (InvestX Econ Calendar Bot; +https://investx.ai)",
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        "Connection": "close",
    }
    if headers:
        h.update(headers)
    r = requests.get(url, headers=h, timeout=timeout)
    r.raise_for_status()
    return r.text

def _safe_get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 25) -> Any:
    h = {
        "User-Agent": "Mozilla/5.0 (InvestX Econ Calendar Bot; +https://investx.ai)",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        "Connection": "close",
    }
    if headers:
        h.update(headers)
    r = requests.get(url, headers=h, timeout=timeout)
    r.raise_for_status()
    return r.json()

# -----------------------
# CME fetcher (heurístico)
# -----------------------
def _extract_candidate_urls(html: str) -> List[str]:
    """
    Busca URLs candidatas en el HTML que suenan a endpoints internos:
    - EconomicReleaseCalendar
    - event-calendar / calendar
    - json
    """
    urls = set()

    # URLs absolutas
    for m in re.findall(r'https?://[^\s"\'<>]+', html):
        if any(k in m.lower() for k in ("economic", "calendar", "event-calendar", "economicreleasecalendar", ".json")):
            urls.add(m)

    # URLs relativas tipo /something.json o /CmeWS/...
    for m in re.findall(r'/(?:[A-Za-z0-9\-_./]+)', html):
        ml = m.lower()
        if any(k in ml for k in ("economic", "calendar", "event-calendar", "economicreleasecalendar", ".json", "cnews", "cmews", "mvc")):
            # construye absoluta
            urls.add("https://www.cmegroup.com" + m)

    # limpia basuras
    clean = []
    for u in urls:
        if u.startswith("https://www.cmegroup.com"):
            clean.append(u.split("#")[0])
    return sorted(set(clean))

def _try_parse_events_from_json_payload(payload: Any, target_day: date) -> List[Dict[str, Any]]:
    """
    Intenta normalizar “algo” a lista de eventos con campos básicos.
    Como no garantizamos el shape exacto, hacemos heurística.
    """
    events: List[Dict[str, Any]] = []

    def to_dt(val: Any) -> Optional[datetime]:
        if val is None:
            return None
        if isinstance(val, (int, float)):
            # epoch ms/sec heurístico
            if val > 10_000_000_000:
                return datetime.fromtimestamp(val / 1000, tz=TZ)
            return datetime.fromtimestamp(val, tz=TZ)
        if isinstance(val, str):
            s = val.strip()
            # ISO
            try:
                # intenta parse simple
                return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(TZ)
            except Exception:
                pass
        return None

    def walk(obj: Any):
        if isinstance(obj, dict):
            # posible evento
            keys = {k.lower() for k in obj.keys()}
            if any(k in keys for k in ("event", "eventname", "title", "name")) and any(k in keys for k in ("date", "datetime", "release", "time", "timestamp", "releasedate")):
                # intenta montar evento
                name = obj.get("eventName") or obj.get("event") or obj.get("title") or obj.get("name")
                dval = obj.get("date") or obj.get("dateTime") or obj.get("datetime") or obj.get("releaseDate") or obj.get("timestamp") or obj.get("time")
                dt = to_dt(dval)

                country = obj.get("country") or obj.get("countryName") or obj.get("locale") or obj.get("region")
                impact = obj.get("impact") or obj.get("importance") or obj.get("impactLevel")

                actual = obj.get("actual") or obj.get("act")
                forecast = obj.get("forecast") or obj.get("consensus") or obj.get("cons")
                previous = obj.get("previous") or obj.get("prev")

                if dt is not None and dt.date() == target_day:
                    events.append({
                        "name": str(name) if name is not None else "Evento",
                        "dt": dt,
                        "country": str(country) if country is not None else "",
                        "impact": str(impact) if impact is not None else "",
                        "actual": actual,
                        "forecast": forecast,
                        "previous": previous,
                    })

            for v in obj.values():
                walk(v)

        elif isinstance(obj, list):
            for it in obj:
                walk(it)

    walk(payload)

    # filtro USA si aplica
    if ONLY_USA:
        filtered = []
        for e in events:
            c = (e.get("country") or "").lower()
            # acepta "US", "United States", etc.
            if ("united states" in c) or (c.strip() == "us") or ("usa" in c):
                filtered.append(e)
        events = filtered

    # impacto allow si viene
    if IMPACT_ALLOW:
        tmp = []
        for e in events:
            imp = (e.get("impact") or "").lower()
            if not imp:
                tmp.append(e)  # si no viene, no lo descartamos
            elif any(a in imp for a in IMPACT_ALLOW):
                tmp.append(e)
        events = tmp

    # ordena por hora
    events.sort(key=lambda x: x["dt"])
    return events

def _fetch_cme_events(target_day: date) -> List[Dict[str, Any]]:
    """
    Estrategia:
    1) Descarga HTML de la página CME
    2) Extrae URLs candidatas a JSON/endpoints
    3) Prueba varias y busca eventos del target_day
    """
    html = _safe_get(CME_PAGE_URL)

    candidates = _extract_candidate_urls(html)

    # Prioriza URLs con pinta de datos
    def score(u: str) -> int:
        ul = u.lower()
        s = 0
        if "economicreleasecalendar" in ul: s += 5
        if "calendar" in ul: s += 3
        if "event" in ul: s += 2
        if ul.endswith(".json") or ".json?" in ul: s += 3
        if "mvc" in ul or "cmews" in ul: s += 2
        return s

    candidates = sorted(candidates, key=score, reverse=True)

    last_err: Optional[Exception] = None

    for u in candidates[:30]:  # no queremos 200 llamadas
        try:
            # intenta JSON directo
            if ".json" in u.lower() or "calendar" in u.lower() or "economic" in u.lower():
                payload = _safe_get_json(u)
                ev = _try_parse_events_from_json_payload(payload, target_day)
                if ev:
                    return ev
        except Exception as e:
            last_err = e
            continue

    # Si no encontró nada, devuelve vacío
    if last_err:
        print(f"[econ] CME fetch failed (no usable endpoint found). Last error: {last_err}")
    return []

# -----------------------
# Formatting + GPT rewrite
# -----------------------
def _format_header(target_day: date) -> str:
    # “Mon 09/02”
    dt = datetime(target_day.year, target_day.month, target_day.day, tzinfo=TZ)
    dow = dt.strftime("%a")  # Mon/Tue...
    return f"📅 Calendario económico — {dow} {dt.strftime('%d/%m')}"

def _format_events_plain(events: List[Dict[str, Any]]) -> str:
    lines = []
    for e in events:
        t = e["dt"].strftime("%H:%M")
        name = e["name"]
        prev = e.get("previous")
        cons = e.get("forecast")
        act = e.get("actual")

        extras = []
        if cons not in (None, "", "—", "-"):
            extras.append(f"Cons: {cons}")
        if prev not in (None, "", "—", "-"):
            extras.append(f"Prev: {prev}")
        if act not in (None, "", "—", "-"):
            extras.append(f"Act: {act}")

        tail = f" ({' | '.join(extras)})" if extras else ""
        lines.append(f"• {t} — {name}{tail}")
    return "\n".join(lines)

def _bloomberg_rewrite(header: str, body_plain: str) -> str:
    """
    Si tienes call_gpt_mini operativo, lo usamos para un texto estilo Bloomberg.
    Si falla, devolvemos el plain.
    """
    try:
        prompt = f"""
Eres un redactor financiero tipo Bloomberg. Escribe un mensaje corto para Telegram (máx 700 caracteres),
en español, basado SOLO en estos eventos. No inventes datos.

Título: {header}

Eventos (lista):
{body_plain}

Formato deseado:
- 1 línea de título
- 2–6 bullets con lo más relevante (impacto/tema)
- Si no hay eventos, dilo de forma clara.
"""
        txt = call_gpt_mini(prompt).strip()
        if txt:
            return txt
    except Exception as e:
        print(f"[econ] GPT rewrite failed: {e}")
    return f"{header}\n\n{body_plain}"

# -----------------------
# Public entry point
# -----------------------
def run_econ_calendar(force: bool = False, force_tomorrow: bool = False) -> None:
    """
    force=True  -> ignora anti-duplicados (pero sigue intentando enviar 1 mensaje coherente)
    force_tomorrow=True -> target_day = mañana (para pruebas manuales)
    """
    now = datetime.now(TZ)
    target_day = (now.date() + timedelta(days=1)) if force_tomorrow else now.date()
    target_key = f"econ:{target_day.isoformat()}"

    if (not force) and _already_sent(target_key):
        print(f"[econ] SKIP: ya enviado para {target_key}")
        return

    # reintentos suaves
    last_err: Optional[Exception] = None
    events: List[Dict[str, Any]] = []

    for attempt in range(1, 4):
        try:
            events = _fetch_cme_events(target_day)
            last_err = None
            break
        except Exception as e:
            last_err = e
            print(f"[econ] CME exception attempt {attempt}/3: {e}")
            _time.sleep(0.8 * attempt)

    header = _format_header(target_day)

    if not events:
        # mensaje “sin eventos”
        msg_plain = f"{header}\n\nHoy no hay datos macro relevantes en EE. UU."
        # opcional: que GPT lo deje bonito (pero ya es corto)
        try:
            msg = _bloomberg_rewrite(header, "No hay publicaciones macro relevantes en EE. UU.")
        except Exception:
            msg = msg_plain

        send_telegram_message(msg)
        _mark_sent(target_key)
        if last_err:
            print(f"[econ] CME returned EMPTY after retries (with error): {last_err}")
        else:
            print("[econ] CME returned EMPTY (no events for target day).")
        return

    body_plain = _format_events_plain(events)
    msg = _bloomberg_rewrite(header, body_plain)

    send_telegram_message(msg)
    _mark_sent(target_key)
    print(f"[econ] SENT OK for {target_key} ({len(events)} events).")
