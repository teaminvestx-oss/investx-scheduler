# =====================================================
# econ_calendar.py — InvestX v4.2 (Macro Brief PRO + Español total)
# Fuente: CME Economic Releases Calendar (web pública) ✅
# URL: https://www.cmegroup.com/education/events/economic-releases-calendar.html
#
# Lógica (igual):
# - 1 envío/día + festivos
# - filtro 2-3⭐ + máx 6
# - Macro Brief IA SIEMPRE en español (fallback si falta OPENAI_API_KEY)
# - Agenda agrupada + “detalle humano”
# - Traducción/adaptación de nombres + caché persistente
# - Timezone Europe/Madrid
# Robustez:
# - Scrape estable (pd.read_html) + reintentos
# - Caché diaria: si CME cae, se usa el último resultado guardado
# - Si no hay datos y no hay caché: se avisa (NO se miente)
# =====================================================

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

CME_URL = "https://www.cmegroup.com/education/events/economic-releases-calendar.html"


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


# ================================
# CACHÉ DIARIA (RAW)
# ================================
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
        if df is None:
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.warning(f"[econ] daily cache load failed: {e}")
        return pd.DataFrame()


# =====================================================
# IMPORTANCIA HEURÍSTICA (CME no trae ⭐)
# =====================================================
def _infer_importance(event_name: str) -> str:
    n = (event_name or "").lower()

    high = [
        "cpi", "consumer price", "inflation", "pce", "core pce",
        "nonfarm", "payroll", "employment situation", "jobs report",
        "fomc", "fed", "interest rate", "powell",
        "gdp", "gross domestic product",
        "ism manufacturing", "ism non-manufacturing", "ism services",
        "ppi", "producer price",
        "retail sales"  # a veces mueve mucho
    ]
    medium = [
        "jobless", "claims",
        "housing starts", "building permits",
        "existing home", "new home",
        "durable goods",
        "consumer confidence", "sentiment",
        "philly fed", "empire state",
        "core retail",
        "personal income", "personal spending",
        "trade balance",
        "business inventories"
    ]

    if any(k in n for k in high):
        return "high"
    if any(k in n for k in medium):
        return "medium"
    return "low"

def _stars(imp: str) -> int:
    imp = (imp or "").lower()
    if "high" in imp or "3" in imp:
        return 3
    if "medium" in imp or "2" in imp:
        return 2
    return 1


# =====================================================
# PARSE FECHA/HORA (CME)
# =====================================================
def _clean_time_str(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "00:00"

    low = s.lower()
    if low in ["tbd", "all day", "na", "n/a", "--", "--:--"]:
        return "00:00"

    # Normaliza "8:30 a.m." / "8:30 AM" / "08:30"
    s2 = s.replace(".", "").replace("a m", "am").replace("p m", "pm")
    s2 = re.sub(r"\s+", " ", s2).strip()

    m = re.match(r"^(\d{1,2}):(\d{2})\s*(am|pm)$", s2, re.IGNORECASE)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        ap = m.group(3).lower()
        if ap == "pm" and hh != 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0
        return f"{hh:02d}:{mm:02d}"

    m2 = re.match(r"^(\d{1,2}):(\d{2})$", s2)
    if m2:
        return f"{int(m2.group(1)):02d}:{int(m2.group(2)):02d}"

    return "00:00"


# =====================================================
# REQUEST + SCRAPE CME (robusto)
# =====================================================
def _safe_request_cme(start: datetime, end: datetime) -> pd.DataFrame:
    if end <= start:
        end = start + timedelta(days=1)

    df_final = pd.DataFrame()
    last_err = None

    logger.info(f"[econ] CME request date_range={start.date()}->{end.date()}")

    # jitter suave
    _time.sleep(0.4 + _random.random() * 0.8)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }

    for attempt in range(3):
        try:
            r = requests.get(CME_URL, headers=headers, timeout=25)
            r.raise_for_status()

            html = r.text or ""
            if len(html) < 2000:
                raise ValueError("CME HTML demasiado corto (posible error/transitorio)")

            # pd.read_html necesita lxml
            tables = pd.read_html(html)
            logger.info(f"[econ] CME tables found: {len(tables)}")

            # Buscamos una tabla que contenga columnas tipo Release/Event y Time/Date
            candidates = []
            for t in tables:
                cols = [str(c).strip().lower() for c in t.columns]
                if any("release" in c or "event" in c for c in cols) and any("time" in c for c in cols):
                    candidates.append(t)

            if not candidates:
                # fallback: la primera con al menos 3 columnas
                candidates = [t for t in tables if len(t.columns) >= 3]

            if not candidates:
                raise ValueError("No se detectaron tablas parseables en CME")

            # Unimos candidates y normalizamos nombres
            df = pd.concat(candidates, ignore_index=True)

            # Normaliza nombres de columnas
            cols_map = {}
            for c in df.columns:
                cl = str(c).strip().lower()
                if "release" in cl or "event" in cl:
                    cols_map[c] = "event"
                elif "time" in cl:
                    cols_map[c] = "time"
                elif "date" in cl:
                    cols_map[c] = "date"
            df = df.rename(columns=cols_map)

            # Asegura columnas mínimas
            for col in ["date", "time", "event"]:
                if col not in df.columns:
                    df[col] = ""

            # Limpieza
            df["event"] = df["event"].astype(str).str.strip()
            df["time"] = df["time"].astype(str).apply(_clean_time_str)

            # La fecha puede venir en formatos variados; intentamos parse general
            df["date_raw"] = df["date"].astype(str).str.strip()
            # pd.to_datetime con inferencia; luego formateamos YYYY-MM-DD
            dt_date = pd.to_datetime(df["date_raw"], errors="coerce", infer_datetime_format=True)
            df["date"] = dt_date.dt.strftime("%Y-%m-%d")

            # Crea datetime y filtra rango
            df["datetime"] = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
            df = df.dropna(subset=["datetime"])

            df = df[(df["datetime"] >= start) & (df["datetime"] < end)]

            # Añade campos esperados por tu pipeline
            df["importance"] = df["event"].apply(_infer_importance)
            df["actual"] = ""
            df["forecast"] = ""
            df["previous"] = ""

            df = df.sort_values("datetime")
            df_final = df[["date", "time", "event", "importance", "actual", "forecast", "previous", "datetime"]].copy()

            logger.info(f"[econ] CME parsed rows_in_range={len(df_final)}")
            break

        except Exception as e:
            last_err = e
            logger.error(f"[econ] CME exception attempt {attempt+1}/3: {e}")
            _time.sleep(1.0 + attempt * 1.2 + _random.random() * 0.8)

    if df_final.empty:
        logger.error(f"[econ] CME returned EMPTY after retries: {last_err}")
    return df_final


# =====================================================
# DETECTAR FESTIVIDAD
# =====================================================
def _is_holiday(df: pd.DataFrame) -> bool:
    if df.empty:
        return False
    for ev in df["event"].astype(str).str.lower():
        if "holiday" in ev or "market holiday" in ev or "christmas" in ev or "thanksgiving" in ev:
            return True
    return False


# =====================================================
# FILTRADO PRINCIPAL
# =====================================================
def _process_events(df: pd.DataFrame) -> List[Dict]:
    if df.empty:
        return []

    df = df.copy()
    df["stars"] = df["importance"].apply(_stars)

    df = df[df["stars"] >= 2]
    if df.empty:
        return []

    df = df.sort_values(["stars", "datetime"], ascending=[False, True]).head(6)
    df = df.sort_values("datetime")

    out = []
    for _, r in df.iterrows():
        out.append(
            {
                "datetime": r["datetime"],
                "event": r["event"],
                "stars": int(r["stars"]),
                "actual": "",
                "forecast": "",
                "previous": "",
            }
        )
    return out


# =====================================================
# TRADUCCIÓN / ADAPTACIÓN (reglas rápidas + IA opcional)
# =====================================================
def _normalize_event_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    return " ".join(name.strip().split()).lower()

def _translate_event_name(ev_name: str) -> str:
    if not isinstance(ev_name, str) or not ev_name.strip():
        return ""
    s = " ".join(ev_name.strip().split())
    n = s.lower()

    if "jobless" in n and "claims" in n:
        return "Solicitudes semanales de subsidio por desempleo"
    if "nonfarm" in n or "payroll" in n:
        return "Nóminas no agrícolas (NFP)"
    if "unemployment" in n:
        return "Tasa de desempleo"
    if "cpi" in n or "consumer price" in n:
        return "IPC (inflación)"
    if "pce" in n:
        return "PCE (inflación preferida por la Fed)"
    if "fomc" in n or ("fed" in n and "meeting" in n):
        return "Fed / FOMC (decisión o evento)"
    if "gdp" in n:
        return "PIB (GDP)"
    if "retail sales" in n:
        return "Ventas minoristas"
    if "ppi" in n:
        return "PPI (precios productor)"
    if "ism" in n and "manufact" in n:
        return "ISM manufacturero"
    if "ism" in n and ("services" in n or "non-manufact" in n):
        return "ISM servicios"
    if "philly" in n:
        return "Índice manufacturero Fed de Filadelfia"

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
    rule_es = _translate_event_name(raw_clean)
    if rule_es.strip().lower() == raw_clean.strip().lower():
        return _gpt_translate_event_name(raw_clean)
    return rule_es


# =====================================================
# AGRUPACIÓN DE AGENDA
# =====================================================
def _bucket_event(ev_name: str) -> str:
    n = _normalize_event_name(ev_name)

    if "cpi" in n or "consumer price" in n:
        return "Inflación: IPC"
    if "pce" in n:
        return "Inflación: PCE (Fed)"
    if "inflation" in n:
        return "Inflación"

    if "jobless" in n or "unemployment" in n or "payroll" in n or "nonfarm" in n:
        return "Empleo"

    if "gdp" in n:
        return "Crecimiento: PIB"

    if "ism" in n or "pmi" in n or "manufact" in n:
        return "Actividad"

    if "fed" in n or "fomc" in n:
        return "Fed: eventos"

    return "Otros"

def _group_agenda(events: List[Dict]) -> List[Dict]:
    if not events:
        return []

    groups = {}
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
        suffix = ""
        if ex:
            suffix = ": " + " / ".join(ex)
        out.append({
            "datetime": g["datetime"],
            "stars": g["stars"],
            "label": g["label"] + suffix
        })

    out.sort(key=lambda x: x["datetime"] or datetime.max)
    return out


# =====================================================
# MACRO BRIEF IA (si no hay OPENAI_API_KEY -> fallback)
# =====================================================
def _make_macro_brief(events: List[Dict]) -> str:
    if not events:
        return ""

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
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
        evn_raw = e.get("event", "")
        evn_es = _translate_event_name_smart(evn_raw) or evn_raw
        lines.append(f"- {stars} {hr} — {evn_es}")

    event_block = "\n".join(lines)

    system_prompt = (
        "Eres analista macro senior en un desk institucional (estilo Bloomberg/CNBC) "
        "y escribes para un canal de Telegram en español. "
        "Tono humano, directo y con criterio; cero relleno."
    )

    user_prompt = (
        "Redacta un 'Macro Brief' con personalidad (no robótico), en 2 a 4 frases.\n"
        "Objetivo: que se entienda rápido qué puede mover hoy el mercado.\n\n"
        "Reglas:\n"
        "- No enumeres eventos ni horas (eso va debajo en la agenda).\n"
        "- Puedes mencionar 1 dato por su nombre si es el protagonista (ej: IPC, empleo, Fed).\n"
        "- Agrupa mentalmente lo repetido.\n"
        "- Usa condicionales claros: si sale por encima / por debajo de lo esperado.\n"
        "- Conecta con: expectativas de la Fed/tipos, yields, USD y renta variable.\n"
        "- No inventes resultados ni cifras.\n"
        "- Prohibido escribir en inglés.\n\n"
        "Contexto de eventos:\n"
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
# MENSAJE FINAL (NO MIENTE SI LA FUENTE FALLA)
# =====================================================
def _build_message(events, date_ref: datetime, source_status: str = "OK") -> str:
    fecha = date_ref.strftime("%a %d/%m").replace(".", "")

    if source_status == "BLOCKED":
        return (
            f"📅 Calendario económico — {fecha}\n\n"
            "⚠️ Fuente CME temporalmente no disponible. "
            "Si hay caché reciente, se usa; si no, hoy no puedo listar eventos con fiabilidad."
        )

    if events == "HOLIDAY":
        return (
            f"📅 Calendario económico — {fecha}\n\n"
            "🎌 Hoy es festivo en Estados Unidos.\n"
            "No hay referencias macroeconómicas relevantes."
        )

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
# FUNCIÓN PRINCIPAL (compatible con main.py)
# =====================================================
def run_econ_calendar(force: bool = False, force_tomorrow: bool = False):
    now = datetime.now(TZ)
    day_key = now.strftime("%Y-%m-%d")

    if not force and not force_tomorrow:
        if _already_sent(day_key):
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

    # 1) intentar CME
    df = _safe_request_cme(start, end)

    source_status = "OK"

    # 2) si CME falla, usar caché
    if df.empty:
        cached = _load_daily_cache(cache_key)
        if not cached.empty:
            logger.warning(f"[econ] Using cached data for {cache_key} (CME empty/unavailable).")
            cached = cached.copy()
            for col in ["date", "time", "event", "importance", "actual", "forecast", "previous"]:
                if col not in cached.columns:
                    cached[col] = ""
            cached["datetime"] = pd.to_datetime(
                cached["date"].astype(str) + " " + cached["time"].astype(str),
                errors="coerce"
            )
            cached = cached.dropna(subset=["datetime"]).sort_values("datetime")
            df = cached
        else:
            source_status = "BLOCKED"

    # 3) guardar caché si tenemos algo
    if not df.empty and source_status == "OK":
        try:
            _save_daily_cache(cache_key, df)
        except:
            pass

    # 4) construir mensaje
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
