import os
import json
import logging
import time as _time
import random as _random
import re
from datetime import datetime, timedelta, time
from typing import List, Dict
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_FILE = "econ_calendar_state.json"
TZ = ZoneInfo("Europe/Madrid")

TRANSLATION_CACHE_FILE = "econ_translation_cache.json"
DAILY_CACHE_DIR = "econ_daily_cache"

CME_BASE = "https://www.cmegroup.com/education/events/economic-releases-calendar.html"
CME_PLAIN_HTML = "https://www.cmegroup.com/education/events/economic-releases-calendar.plain.html"
CME_PLAIN_JSON = "https://www.cmegroup.com/education/events/economic-releases-calendar.plain.json"


# ----------------------------
# Estado 1 envío/día
# ----------------------------
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
    return _load_state().get("sent_day") == day_key

def _mark_sent(day_key: str):
    st = _load_state()
    st["sent_day"] = day_key
    _save_state(st)


# ----------------------------
# Caché traducciones
# ----------------------------
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


# ----------------------------
# Caché diaria datos
# ----------------------------
def _cache_path(day_key: str) -> str:
    if not os.path.exists(DAILY_CACHE_DIR):
        try:
            os.makedirs(DAILY_CACHE_DIR, exist_ok=True)
        except:
            pass
    return os.path.join(DAILY_CACHE_DIR, f"econ_{day_key}.json")

def _save_daily_cache(day_key: str, df: pd.DataFrame):
    try:
        p = _cache_path(day_key)
        cols = [c for c in ["date", "time", "event", "importance", "actual", "forecast", "previous"] if c in df.columns]
        out = df[cols].copy().fillna("")
        out.to_json(p, orient="records", force_ascii=False)
        logger.info(f"[econ] daily cache saved -> {p} rows={len(out)}")
    except Exception as e:
        logger.warning(f"[econ] daily cache save failed: {e}")

def _load_daily_cache(day_key: str) -> pd.DataFrame:
    try:
        p = _cache_path(day_key)
        if not os.path.exists(p):
            return pd.DataFrame()
        df = pd.read_json(p, orient="records", dtype=False)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.warning(f"[econ] daily cache load failed: {e}")
        return pd.DataFrame()


# ----------------------------
# Importancia (CME no trae ⭐)
# ----------------------------
def _infer_importance(name: str) -> str:
    n = (name or "").lower()
    high = ["cpi", "consumer price", "pce", "nonfarm", "payroll", "fomc", "fed", "rate", "gdp", "ism", "ppi", "retail sales"]
    medium = ["jobless", "claims", "housing", "durable", "confidence", "sentiment", "philly", "empire", "income", "spending", "trade"]
    if any(k in n for k in high): return "high"
    if any(k in n for k in medium): return "medium"
    return "low"

def _stars(imp: str) -> int:
    imp = (imp or "").lower()
    if "high" in imp or "3" in imp: return 3
    if "medium" in imp or "2" in imp: return 2
    return 1


# ----------------------------
# Traducción nombres (reglas + IA opcional)
# ----------------------------
def _translate_event_name(ev_name: str) -> str:
    if not isinstance(ev_name, str) or not ev_name.strip():
        return ""
    s = " ".join(ev_name.strip().split())
    n = s.lower()
    if "jobless" in n and "claims" in n: return "Solicitudes semanales de subsidio por desempleo"
    if "nonfarm" in n or "payroll" in n: return "Nóminas no agrícolas (NFP)"
    if "unemployment" in n: return "Tasa de desempleo"
    if "cpi" in n or "consumer price" in n: return "IPC (inflación)"
    if "pce" in n: return "PCE (inflación preferida por la Fed)"
    if "gdp" in n: return "PIB (GDP)"
    if "retail sales" in n: return "Ventas minoristas"
    if "ppi" in n: return "PPI (precios productor)"
    if "ism" in n and "manufact" in n: return "ISM manufacturero"
    if "ism" in n and ("services" in n or "non-manufact" in n): return "ISM servicios"
    if "fomc" in n or ("fed" in n and "meeting" in n): return "Fed / FOMC (evento)"
    return s

def _gpt_translate_event_name(raw: str) -> str:
    raw_clean = " ".join((raw or "").strip().split())
    if not raw_clean:
        return ""
    cache = _load_translation_cache()
    key = raw_clean.lower()
    if cache.get(key):
        return cache[key]
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return raw_clean
    try:
        out = call_gpt_mini(
            "Eres traductor/editor macro. Devuelve SOLO la traducción al español, corta y natural. Sin comillas.",
            f"Traduce este evento macro al español claro (mantén siglas como Fed, FOMC, IPC, PCE, NFP): {raw_clean}",
            max_tokens=40
        ).strip()
    except Exception:
        out = ""
    if not out:
        out = raw_clean
    cache[key] = out
    _save_translation_cache(cache)
    return out

def _translate_event_name_smart(raw: str) -> str:
    raw_clean = " ".join((raw or "").strip().split())
    if not raw_clean:
        return ""
    rule = _translate_event_name(raw_clean)
    if rule.strip().lower() == raw_clean.strip().lower():
        return _gpt_translate_event_name(raw_clean)
    return rule


# ----------------------------
# Agrupación agenda
# ----------------------------
def _bucket_event(ev_name: str) -> str:
    n = (ev_name or "").lower()
    if "cpi" in n or "consumer price" in n: return "Inflación: IPC"
    if "pce" in n: return "Inflación: PCE (Fed)"
    if "jobless" in n or "unemployment" in n or "payroll" in n or "nonfarm" in n: return "Empleo"
    if "gdp" in n: return "Crecimiento: PIB"
    if "ism" in n or "pmi" in n or "manufact" in n: return "Actividad"
    if "fed" in n or "fomc" in n: return "Fed: eventos"
    return "Otros"

def _group_agenda(events: List[Dict]) -> List[Dict]:
    groups = {}
    for ev in events:
        raw = (ev.get("event") or "").strip()
        bucket = _bucket_event(raw)
        dt = ev.get("datetime")
        stars = int(ev.get("stars", 1))
        ex = _translate_event_name_smart(raw) or raw
        if bucket not in groups:
            groups[bucket] = {"datetime": dt, "stars": stars, "label": bucket, "examples": [ex] if ex else []}
        else:
            if dt and groups[bucket]["datetime"] and dt < groups[bucket]["datetime"]:
                groups[bucket]["datetime"] = dt
            if stars > groups[bucket]["stars"]:
                groups[bucket]["stars"] = stars
            if ex and ex not in groups[bucket]["examples"]:
                groups[bucket]["examples"].append(ex)

    out = []
    for g in groups.values():
        ex = g["examples"][:2]
        suffix = (": " + " / ".join(ex)) if ex else ""
        out.append({"datetime": g["datetime"], "stars": g["stars"], "label": g["label"] + suffix})
    out.sort(key=lambda x: x["datetime"] or datetime.max)
    return out


# ----------------------------
# Macro brief IA
# ----------------------------
def _make_macro_brief(events: List[Dict]) -> str:
    if not events:
        return ""
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return (
            "Sesión marcada por referencias macro capaces de mover expectativas de tipos. "
            "Si sorprenden al alza, presión para la renta variable y apoyo al USD/yields; "
            "si salen más suaves, alivio para el riesgo y para los bonos."
        )

    lines = []
    for e in events:
        dt = e.get("datetime")
        hr = dt.strftime("%H:%M") if dt else ""
        stars = "⭐" * int(e.get("stars", 1))
        name = _translate_event_name_smart(e.get("event", "")) or e.get("event", "")
        lines.append(f"- {stars} {hr} — {name}")

    system_prompt = (
        "Eres analista macro senior en un desk institucional (estilo Bloomberg/CNBC) "
        "y escribes para un canal de Telegram en español. Tono humano, directo y con criterio."
    )
    user_prompt = (
        "Redacta un 'Macro Brief' en 2 a 4 frases.\n"
        "Reglas: no enumeres eventos/horas; agrupa lo repetido; condicionales claros; "
        "conecta con Fed/tipos, yields, USD y renta variable; no inventes cifras; prohibido inglés.\n\n"
        "Contexto:\n" + "\n".join(lines)
    )

    try:
        out = call_gpt_mini(system_prompt, user_prompt, max_tokens=200).strip()
    except Exception:
        out = ""

    return out or (
        "Sesión marcada por referencias macro capaces de mover expectativas de tipos. "
        "Si sorprenden al alza, presión para la renta variable y apoyo al USD/yields; "
        "si salen más suaves, alivio para el riesgo y para los bonos."
    )


# ----------------------------
# Construcción mensaje
# ----------------------------
def _build_message(events, date_ref: datetime, source_status: str = "OK") -> str:
    fecha = date_ref.strftime("%a %d/%m").replace(".", "")

    if source_status == "BLOCKED":
        return (
            f"📅 Calendario económico — {fecha}\n\n"
            "⚠️ CME (modo plain) no devolvió datos hoy. "
            "Si hay caché, se usa; si no, no puedo listar eventos con fiabilidad."
        )

    if events == "HOLIDAY":
        return (
            f"📅 Calendario económico — {fecha}\n\n"
            "🎌 Hoy es festivo en Estados Unidos.\n"
            "No hay referencias macroeconómicas relevantes."
        )

    if not events:
        return f"📅 Calendario económico — {fecha}\n\nHoy no hay datos macro relevantes en EE. UU."

    brief = _make_macro_brief(events)
    agenda = _group_agenda(events)

    lines = [f"🧠 Macro Brief (EE. UU.) — {fecha}\n", brief, "\nAgenda clave:"]
    for a in agenda:
        dt = a.get("datetime")
        hr = dt.strftime("%H:%M") if dt else ""
        stars = "⭐" * int(a.get("stars", 1))
        lines.append(f"{stars} {hr} — {a.get('label','')}".strip())
    return "\n".join(lines)


# ----------------------------
# Parse CME plain
# ----------------------------
def _clean_time_str(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "00:00"
    low = s.lower()
    if low in ["tbd", "all day", "na", "n/a", "--", "--:--"]:
        return "00:00"

    s2 = s.replace(".", "").strip()
    s2 = re.sub(r"\s+", " ", s2)

    m = re.match(r"^(\d{1,2}):(\d{2})\s*(am|pm)$", s2, re.IGNORECASE)
    if m:
        hh = int(m.group(1)); mm = int(m.group(2)); ap = m.group(3).lower()
        if ap == "pm" and hh != 12: hh += 12
        if ap == "am" and hh == 12: hh = 0
        return f"{hh:02d}:{mm:02d}"

    m2 = re.match(r"^(\d{1,2}):(\d{2})$", s2)
    if m2:
        return f"{int(m2.group(1)):02d}:{int(m2.group(2)):02d}"

    return "00:00"


def _safe_request_cme_plain(start: datetime, end: datetime) -> pd.DataFrame:
    if end <= start:
        end = start + timedelta(days=1)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }

    last_err = None
    df_final = pd.DataFrame()

    for attempt in range(3):
        try:
            _time.sleep(0.4 + _random.random() * 0.8)

            r = requests.get(CME_PLAIN_HTML, headers=headers, timeout=25)
            r.raise_for_status()
            html = r.text or ""
            if len(html) < 1500:
                raise ValueError("CME plain HTML demasiado corto")

            tables = pd.read_html(html)  # requiere lxml
            if not tables:
                raise ValueError("CME plain no contiene tablas parseables")

            # elegir tabla que tenga columnas tipo "Time" y "Release"
            best = None
            for t in tables:
                cols = [str(c).strip().lower() for c in t.columns]
                if any("time" in c for c in cols) and any(("release" in c) or ("event" in c) for c in cols):
                    best = t
                    break
            if best is None:
                best = tables[0]

            df = best.copy()
            # map columns
            col_map = {}
            for c in df.columns:
                cl = str(c).strip().lower()
                if "time" in cl:
                    col_map[c] = "time"
                elif "release" in cl or "event" in cl:
                    col_map[c] = "event"
                elif "date" in cl:
                    col_map[c] = "date"
            df = df.rename(columns=col_map)

            for col in ["date", "time", "event"]:
                if col not in df.columns:
                    df[col] = ""

            df["event"] = df["event"].astype(str).str.strip()
            df["time"] = df["time"].astype(str).apply(_clean_time_str)

            # parse date
            dt_date = pd.to_datetime(df["date"].astype(str), errors="coerce", infer_datetime_format=True)
            df["date"] = dt_date.dt.strftime("%Y-%m-%d")

            df["datetime"] = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
            df = df.dropna(subset=["datetime"])

            df = df[(df["datetime"] >= start) & (df["datetime"] < end)].sort_values("datetime")

            df["importance"] = df["event"].apply(_infer_importance)
            df["actual"] = ""
            df["forecast"] = ""
            df["previous"] = ""

            df_final = df[["date", "time", "event", "importance", "actual", "forecast", "previous", "datetime"]].copy()
            logger.info(f"[econ] CME plain parsed rows_in_range={len(df_final)}")
            break

        except Exception as e:
            last_err = e
            logger.error(f"[econ] CME plain exception attempt {attempt+1}/3: {e}")
            _time.sleep(1.0 + attempt * 1.2 + _random.random() * 0.8)

    if df_final.empty:
        logger.error(f"[econ] CME plain returned EMPTY after retries: {last_err}")

    return df_final


def _is_holiday(df: pd.DataFrame) -> bool:
    if df.empty:
        return False
    for ev in df["event"].astype(str).str.lower():
        if "holiday" in ev or "market holiday" in ev or "thanksgiving" in ev or "christmas" in ev:
            return True
    return False


def _process_events(df: pd.DataFrame) -> List[Dict]:
    if df.empty:
        return []
    df = df.copy()
    df["stars"] = df["importance"].apply(_stars)
    df = df[df["stars"] >= 2]
    if df.empty:
        return []
    df = df.sort_values(["stars", "datetime"], ascending=[False, True]).head(6).sort_values("datetime")

    out = []
    for _, r in df.iterrows():
        out.append({"datetime": r["datetime"], "event": r["event"], "stars": int(r["stars"]),
                    "actual": "", "forecast": "", "previous": ""})
    return out


# ----------------------------
# MAIN ENTRY
# ----------------------------
def run_econ_calendar(force: bool = False, force_tomorrow: bool = False):
    now = datetime.now(TZ)
    day_key = now.strftime("%Y-%m-%d")

    if not force and not force_tomorrow and _already_sent(day_key):
        logger.info("econ_calendar: ya enviado hoy.")
        return

    if force_tomorrow:
        start = datetime.combine(now.date() + timedelta(days=1), time.min)
        end = start + timedelta(days=1)
        title_date = start
        cache_key = start.strftime("%Y-%m-%d")
    else:
        start = datetime.combine(now.date(), time.min)
        end = start + timedelta(days=1)
        title_date = now
        cache_key = day_key

    df = _safe_request_cme_plain(start, end)

    source_status = "OK"
    if df.empty:
        cached = _load_daily_cache(cache_key)
        if not cached.empty:
            logger.warning(f"[econ] Using cached data for {cache_key} (CME plain empty).")
            cached = cached.copy()
            for col in ["date", "time", "event", "importance", "actual", "forecast", "previous"]:
                if col not in cached.columns:
                    cached[col] = ""
            cached["datetime"] = pd.to_datetime(
                cached["date"].astype(str) + " " + cached["time"].astype(str),
                errors="coerce"
            )
            df = cached.dropna(subset=["datetime"]).sort_values("datetime")
        else:
            source_status = "BLOCKED"

    if not df.empty and source_status == "OK":
        _save_daily_cache(cache_key, df)

    if source_status == "BLOCKED":
        msg = _build_message([], title_date, source_status="BLOCKED")
        send_telegram_message(msg)
        if not force and not force_tomorrow:
            _mark_sent(day_key)
        return

    if _is_holiday(df):
        msg = _build_message("HOLIDAY", title_date)
        send_telegram_message(msg)
        if not force and not force_tomorrow:
            _mark_sent(day_key)
        return

    events = _process_events(df)
    msg = _build_message(events, title_date)
    send_telegram_message(msg)

    if not force and not force_tomorrow:
        _mark_sent(day_key)
