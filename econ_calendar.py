# =====================================================
# econ_calendar.py — InvestX (CME + Macro Brief IA ES)
# Fuente: CME Group (web pública)
#
# - 1 envío/día (state)
# - force / force_tomorrow compatible con tu main.py
# - Macro Brief IA estilo Bloomberg SIEMPRE en español (call_gpt_mini)
# - Traducción/adaptación nombres + caché persistente
# - Agenda agrupada (evita repetir IPC 4 veces)
# - Fallback digno si CME falla o no hay eventos
# =====================================================

import os
import json
import logging
import time as _time
import random as _random
import re
from datetime import datetime, timedelta, time
from typing import List, Dict, Any
from zoneinfo import ZoneInfo

import requests

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None  # fallback a regex

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_FILE = "econ_calendar_state.json"
TRANSLATION_CACHE_FILE = "econ_translation_cache.json"

TZ = ZoneInfo("Europe/Madrid")

# CME página pública
CME_URL = "https://www.cmegroup.com/education/events/economic-releases-calendar.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MAX_RETRIES = 3
TIMEOUT = 25


# ================================
# ESTADO DE ENVÍO (solo 1 vez)
# ================================
def _load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def _save_state(d):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except:
        pass


def _already_sent(day_key: str) -> bool:
    st = _load_state()
    return st.get("sent_day") == day_key


def _mark_sent(day_key: str):
    st = _load_state()
    st["sent_day"] = day_key
    _save_state(st)


# ================================
# CACHÉ DE TRADUCCIÓN
# ================================
def _load_translation_cache() -> Dict[str, str]:
    if not os.path.exists(TRANSLATION_CACHE_FILE):
        return {}
    try:
        with open(TRANSLATION_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def _save_translation_cache(d: Dict[str, str]):
    try:
        with open(TRANSLATION_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except:
        pass


# =====================================================
# FETCH CME
# =====================================================
def _fetch_cme_html() -> str:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(CME_URL, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.text or ""
        except Exception as e:
            last_err = e
            logger.error(f"[econ] CME html exception attempt {attempt}/{MAX_RETRIES}: {e}")
            _time.sleep(0.8 + _random.random() * 0.8)
    raise last_err


# =====================================================
# PARSE CME -> eventos normalizados
# Objetivo: lista de dicts con:
#   datetime, event, stars(2-3), forecast, previous
#
# CME puede cambiar HTML; hacemos:
#  1) Intento JSON embebido (si aparece)
#  2) Intento tabla HTML (si aparece)
#  3) Fallback vacío
# =====================================================
def _parse_time_to_dt(date_ref: datetime, hhmm: str) -> datetime:
    # hhmm esperado "08:30" etc. Si no, 00:00
    try:
        hhmm = (hhmm or "").strip()
        m = re.match(r"^(\d{1,2}):(\d{2})$", hhmm)
        if not m:
            return datetime.combine(date_ref.date(), time(0, 0), tzinfo=TZ)
        h = int(m.group(1))
        mi = int(m.group(2))
        return datetime.combine(date_ref.date(), time(h, mi), tzinfo=TZ)
    except:
        return datetime.combine(date_ref.date(), time(0, 0), tzinfo=TZ)


def _stars_from_event_name(name: str) -> int:
    """
    CME no trae 'importance' estándar.
    Asignamos heurística:
    - 3⭐: IPC/CPI, PCE, NFP, Unemployment, FOMC/Fed, GDP, ISM, Retail Sales, CPI Core, Core PCE
    - 2⭐: Jobless claims, PMI, Philly Fed, Durable Goods, Housing, Confidence, Treasury auctions (si salen)
    - 1⭐: resto
    """
    n = (name or "").lower()
    three = [
        "cpi", "consumer price", "pce", "nonfarm", "payroll", "unemployment",
        "fomc", "fed rate", "interest rate", "gdp", "ism", "retail sales",
        "core cpi", "core pce", "ppi"
    ]
    two = [
        "jobless", "claims", "pmi", "philadelphia", "philly", "durable goods",
        "housing", "confidence", "sentiment", "treasury", "auction", "t-bill", "t-note", "t-bond"
    ]
    if any(k in n for k in three):
        return 3
    if any(k in n for k in two):
        return 2
    return 1


def _extract_json_like(html: str) -> List[Dict[str, Any]]:
    """
    Algunos sitios embeben datos en JSON dentro de scripts.
    Buscamos patrones típicos de arrays con campos como 'title','time','country'.
    Si no encontramos, devolvemos [].
    """
    out: List[Dict[str, Any]] = []

    # Heurística: buscar bloques que parezcan JSON con "country":"US" o "United States"
    # No garantizado; es solo intento.
    candidates = re.findall(r"\{[^{}]{50,2000}\}", html)
    for c in candidates[:400]:
        if ("\"US\"" not in c and "United States" not in c and "\"country\"" not in c):
            continue
        # Intento muy prudente: no parseamos todo, solo extraemos campos simples con regex
        country = None
        title = None
        ttime = None
        fc = ""
        pv = ""
        m_country = re.search(r"\"country\"\s*:\s*\"([^\"]+)\"", c)
        if m_country:
            country = m_country.group(1)
        m_title = re.search(r"\"(title|event|name)\"\s*:\s*\"([^\"]+)\"", c)
        if m_title:
            title = m_title.group(2)
        m_time = re.search(r"\"time\"\s*:\s*\"([0-9]{1,2}:[0-9]{2})\"", c)
        if m_time:
            ttime = m_time.group(1)

        if country and title and ttime:
            out.append({"country": country, "event": title, "time": ttime, "forecast": fc, "previous": pv})

    return out


def _parse_table(html: str) -> List[Dict[str, Any]]:
    if not BeautifulSoup:
        return []
    soup = BeautifulSoup(html, "html.parser")

    events = []
    rows = soup.select("table tbody tr")
    for row in rows:
        cols = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if len(cols) < 3:
            continue

        # Muy habitual: Time | Country | Event | Actual | Forecast | Previous...
        ttime = (cols[0] or "").strip()
        country = (cols[1] or "").strip()
        event = (cols[2] or "").strip()

        # Forecast / Previous si están
        forecast = cols[4].strip() if len(cols) >= 5 else ""
        previous = cols[5].strip() if len(cols) >= 6 else ""

        if not event:
            continue

        events.append({
            "country": country,
            "event": event,
            "time": ttime,
            "forecast": forecast,
            "previous": previous
        })

    return events


def _get_cme_events_for_day(date_ref: datetime) -> List[Dict]:
    html = _fetch_cme_html()

    # 1) intentar JSON-like
    raw = _extract_json_like(html)

    # 2) si no, intentar tabla HTML
    if not raw:
        raw = _parse_table(html)

    # 3) normalizar
    out = []
    for r in raw:
        country = (r.get("country") or "").strip()
        # Aceptamos "US", "United States", "USA"
        country_low = country.lower()
        if not (country_low in ("us", "usa", "united states", "united states of america")):
            continue

        ev = (r.get("event") or "").strip()
        if not ev:
            continue

        ttime = (r.get("time") or "").strip()
        dt = _parse_time_to_dt(date_ref, ttime)

        stars = _stars_from_event_name(ev)

        out.append({
            "datetime": dt,
            "event": ev,
            "stars": int(stars),
            "forecast": (r.get("forecast") or "").strip(),
            "previous": (r.get("previous") or "").strip(),
        })

    # Orden y límite: igual filosofía (2-3⭐, máx 6)
    out = [e for e in out if e["stars"] >= 2]
    out.sort(key=lambda x: (-x["stars"], x["datetime"]))
    out = out[:6]
    out.sort(key=lambda x: x["datetime"])
    return out


# =====================================================
# TRADUCCIÓN / ADAPTACIÓN (reglas + IA + caché)
# =====================================================
def _normalize_event_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    return " ".join(name.strip().split()).lower()


def _translate_event_name_rules(ev_name: str) -> str:
    if not isinstance(ev_name, str) or not ev_name.strip():
        return ""

    s = " ".join(ev_name.strip().split())
    n = s.lower()

    # Empleo
    if "initial jobless claims" in n or ("jobless" in n and "claims" in n):
        return "Solicitudes semanales de subsidio por desempleo"
    if "nonfarm payroll" in n or "non-farm payroll" in n or "payrolls" in n:
        return "Nóminas no agrícolas (NFP)"
    if "unemployment" in n and "rate" in n:
        return "Tasa de desempleo"
    if "average hourly earnings" in n:
        return "Salario medio por hora"

    # Inflación
    if "core cpi" in n:
        return "IPC subyacente (sin energía ni alimentos)"
    if "cpi" in n or "consumer price" in n:
        return "IPC (índice de precios al consumidor)"
    if "pce" in n:
        return "PCE (inflación preferida por la Fed)"
    if "ppi" in n or "producer price" in n:
        return "IPP (precios de producción)"

    # Actividad
    if "gdp" in n:
        return "PIB"
    if "ism" in n:
        return "ISM (actividad)"
    if "pmi" in n:
        return "PMI (actividad)"
    if "retail sales" in n:
        return "Ventas minoristas"
    if "philadelphia" in n:
        return "Índice manufacturero Fed de Filadelfia"

    # Fed
    if "fomc" in n:
        return "Fed (FOMC)"
    if "fed" in n and ("speech" in n or "speaks" in n):
        return "Fed: discursos"

    return s


def _gpt_translate_event_name(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return ""

    raw_clean = " ".join(raw.strip().split())
    key = raw_clean.lower()

    cache = _load_translation_cache()
    if key in cache and cache[key]:
        return cache[key]

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return raw_clean

    system_prompt = (
        "Eres traductor/editor macro. Devuelve SOLO la traducción al español, "
        "corta y natural para un canal de trading. Sin comillas."
    )
    user_prompt = (
        "Traduce este nombre de evento macro al español claro.\n"
        "- Mantén siglas útiles (Fed, FOMC, IPC, PCE, NFP, PMI).\n"
        "- Si es una subasta, dilo como 'Subasta del Tesoro USA (10 años)' etc.\n"
        "- Si es un discurso, dilo como 'Discurso de X (Fed)' si aparece el nombre.\n"
        "- No añadas datos que no estén.\n\n"
        f"Evento: {raw_clean}"
    )

    try:
        out = call_gpt_mini(system_prompt, user_prompt, max_tokens=40).strip()
    except Exception as e:
        logger.warning(f"GPT translate falló: {e}")
        out = ""

    if not out:
        out = raw_clean

    cache[key] = out
    _save_translation_cache(cache)
    return out


def _translate_event_name_smart(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return ""
    raw_clean = " ".join(raw.strip().split())
    rule_es = _translate_event_name_rules(raw_clean)
    if rule_es.strip().lower() == raw_clean.strip().lower():
        return _gpt_translate_event_name(raw_clean)
    return rule_es


# =====================================================
# AGRUPACIÓN AGENDA (evita duplicados)
# =====================================================
def _bucket_event(ev_name: str) -> str:
    n = _normalize_event_name(ev_name)

    # Inflación
    if "core cpi" in n or "cpi" in n or "consumer price" in n:
        return "Inflación: IPC"
    if "pce" in n:
        return "Inflación: PCE (Fed)"
    if "ppi" in n or "producer price" in n:
        return "Inflación: IPP"

    # Empleo
    if "jobless" in n or "claims" in n:
        return "Empleo: jobless claims"
    if "payroll" in n or "nonfarm" in n or "unemployment" in n or "earnings" in n:
        return "Empleo"

    # Actividad
    if "gdp" in n:
        return "Actividad: PIB"
    if "ism" in n or "pmi" in n:
        return "Actividad: ISM/PMI"
    if "retail sales" in n:
        return "Actividad: consumo"
    if "philadelphia" in n or "philly" in n:
        return "Actividad: Fed de Filadelfia"

    # Fed
    if "fomc" in n or ("fed" in n and ("speech" in n or "speaks" in n)):
        return "Fed"

    return "Otros"


def _group_agenda(events: List[Dict]) -> List[Dict]:
    if not events:
        return []

    groups: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        raw_name = (ev.get("event") or "").strip()
        bucket = _bucket_event(raw_name)
        dt = ev.get("datetime")
        stars = int(ev.get("stars", 1))

        example_es = _translate_event_name_smart(raw_name) or raw_name

        if bucket not in groups:
            groups[bucket] = {
                "datetime": dt,
                "stars": stars,
                "label": bucket,
                "examples": [example_es] if example_es else []
            }
        else:
            if dt and groups[bucket]["datetime"] and dt < groups[bucket]["datetime"]:
                groups[bucket]["datetime"] = dt
            if stars > groups[bucket]["stars"]:
                groups[bucket]["stars"] = stars
            if example_es and example_es not in groups[bucket]["examples"]:
                groups[bucket]["examples"].append(example_es)

    out = []
    for g in groups.values():
        ex = g["examples"][:2]
        suffix = (": " + " / ".join(ex)) if ex else ""
        out.append({
            "datetime": g["datetime"],
            "stars": g["stars"],
            "label": g["label"] + suffix
        })

    out.sort(key=lambda x: x["datetime"] or datetime.max.replace(tzinfo=TZ))
    return out


# =====================================================
# MACRO BRIEF IA — SIEMPRE ESPAÑOL
# =====================================================
def _make_macro_brief(events: List[Dict]) -> str:
    if not events:
        return ""

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning("OPENAI_API_KEY no configurada. Macro Brief irá por fallback.")
        return (
            "Sesión marcada por referencias macro capaces de mover expectativas de tipos. "
            "Si sorprenden al alza, presión para la renta variable y apoyo al USD/yields; "
            "si salen más suaves, alivio para el riesgo y para los bonos."
        )

    # Contexto para IA
    lines = []
    for e in events:
        dt = e.get("datetime")
        hr = dt.strftime("%H:%M") if dt else ""
        stars = "⭐" * int(e.get("stars", 1))
        evn_raw = e.get("event", "")
        evn_es = _translate_event_name_smart(evn_raw) or evn_raw
        fc = e.get("forecast", "")
        pv = e.get("previous", "")

        extra = []
        if fc:
            extra.append(f"previsión: {fc}")
        if pv:
            extra.append(f"anterior: {pv}")

        tail = f" ({' | '.join(extra)})" if extra else ""
        lines.append(f"- {stars} {hr} — {evn_es}{tail}")

    event_block = "\n".join(lines)

    system_prompt = (
        "Eres analista macro senior en un desk institucional (estilo Bloomberg) "
        "y escribes para un canal de Telegram en español. "
        "Tono humano, directo y con criterio; cero relleno."
    )

    user_prompt = (
        "Redacta un 'Macro Brief' en 2 a 4 frases.\n"
        "Objetivo: que se entienda rápido qué puede mover hoy el mercado.\n\n"
        "Reglas:\n"
        "- No enumeres eventos ni horas (eso va debajo en la agenda).\n"
        "- Puedes mencionar 1 dato por su nombre si es protagonista (ej: IPC, empleo, Fed).\n"
        "- Agrupa mentalmente lo repetido.\n"
        "- Usa condicionales claros: si sale por encima / por debajo de lo previsto.\n"
        "- Conecta con: expectativas de la Fed/tipos, yields, USD y renta variable.\n"
        "- No inventes resultados ni cifras.\n"
        "- Prohibido escribir en inglés.\n\n"
        "Contexto de eventos (solo para que entiendas el día):\n"
        f"{event_block}\n"
    )

    try:
        out = call_gpt_mini(system_prompt, user_prompt, max_tokens=200).strip()
    except Exception as e:
        logger.warning(f"call_gpt_mini falló: {e}")
        out = ""

    if not out:
        out = (
            "Sesión marcada por referencias macro capaces de mover expectativas de tipos. "
            "Si sorprenden al alza, presión para la renta variable y apoyo al USD/yields; "
            "si salen más suaves, alivio para el riesgo y para los bonos."
        )

    return out


# =====================================================
# MENSAJE FINAL (Brief + Agenda agrupada)
# =====================================================
def _build_message(events: List[Dict], date_ref: datetime) -> str:
    fecha = date_ref.strftime("%a %d/%m").replace(".", "")

    if not events:
        return (
            f"📅 Calendario económico — {fecha}\n\n"
            "Hoy no hay datos macro relevantes en EE. UU."
        )

    brief = _make_macro_brief(events)
    agenda = _group_agenda(events)

    lines = [f"🧠 Macro Brief (EE. UU.) — {fecha}\n", brief, "\nAgenda clave:"]

    for a in agenda:
        dt = a.get("datetime")
        hr = dt.strftime("%H:%M") if dt else ""
        stars = "⭐" * int(a.get("stars", 1))
        label = a.get("label", "")
        lines.append(f"{stars} {hr} — {label}".strip())

    return "\n".join(lines)


# =====================================================
# FUNCIÓN PRINCIPAL (COMPATIBLE CON TU MAIN)
# =====================================================
def run_econ_calendar(force: bool = False, force_tomorrow: bool = False):
    """
    Compatible con main.py:
      run_econ_calendar(force=True, force_tomorrow=True)
    """
    now = datetime.now(TZ)
    day_key = now.strftime("%Y-%m-%d")

    # Control 1 vez al día (solo para hoy; mañana también se controla por day_key de hoy)
    if not force and not force_tomorrow:
        if _already_sent(day_key):
            logger.info("econ_calendar: ya enviado hoy.")
            return

    # Fecha objetivo
    if force_tomorrow:
        target = now + timedelta(days=1)
        title_date = target
    else:
        target = now
        title_date = now

    try:
        events = _get_cme_events_for_day(target)
    except Exception as e:
        logger.error(f"[econ] CME fetch/parse failed: {e}")
        events = []

    msg = _build_message(events, title_date)
    send_telegram_message(msg)

    if not force and not force_tomorrow:
        _mark_sent(day_key)
