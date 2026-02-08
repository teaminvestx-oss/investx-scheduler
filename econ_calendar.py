# =====================================================
# econ_calendar.py — InvestX v4.2 (Macro Brief PRO + Español total)
# Fuente: FMP (Financial Modeling Prep) — STABLE endpoint ✅
# Lógica: 1 envío/día + festivos + filtro 2-3⭐ + máx 6 + agenda agrupada
# Firma run_econ_calendar compatible con main.py (force, force_tomorrow) ✅
# =====================================================

import os
import json
import logging
import time as _time
import random as _random
from datetime import datetime, timedelta, time
from typing import List, Dict
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_FILE = "econ_calendar_state.json"
DEFAULT_COUNTRY = "United States"

TZ = ZoneInfo("Europe/Madrid")
TRANSLATION_CACHE_FILE = "econ_translation_cache.json"


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
# REQUEST SAFE A FMP (STABLE) ✅
# =====================================================
def _safe_request(country, start: datetime, end: datetime):
    if end <= start:
        end = start + timedelta(days=1)

    api_key = os.getenv("FMP_API_KEY", "").strip()
    if not api_key:
        logger.error("[econ] FMP_API_KEY no configurada -> devolviendo vacío.")
        return pd.DataFrame()

    f = start.strftime("%Y-%m-%d")
    t = end.strftime("%Y-%m-%d")

    # ✅ endpoint correcto (no /api/v3/)
    url = "https://financialmodelingprep.com/stable/economic-calendar"

    df = None
    last_err = None

    logger.info(f"[econ] FMP(STABLE) request from={f} to={t} country={country}")

    for attempt in range(3):
        try:
            # IMPORTANTE: NO pasamos country aquí (a veces provoca 403/filtrado raro).
            params = {"from": f, "to": t, "apikey": api_key}

            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()

            data = r.json()
            if not isinstance(data, list):
                data = []

            logger.info(f"[econ] attempt {attempt+1}/3 -> items={len(data)}")

            df = pd.DataFrame(data) if data else pd.DataFrame()
            if df is not None and not df.empty:
                break

        except Exception as e:
            last_err = e
            logger.error(f"[econ] FMP exception attempt {attempt+1}/3: {e}")

        _time.sleep(0.8 + _random.random() * 0.8)

    if df is None or df.empty:
        if last_err:
            logger.error(f"[econ] FMP returned EMPTY after retries (with error): {last_err}")
        else:
            logger.warning("[econ] FMP returned EMPTY after retries (NO exception).")
        return pd.DataFrame()

    # Normalización
    colmap = {
        "date": "date",
        "event": "event",
        "country": "country",
        "actual": "actual",
        "previous": "previous",
        "forecast": "forecast",
        "estimate": "forecast",
        "importance": "importance",
        "impact": "importance",
    }
    for src, dst in colmap.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]

    for col in ["date", "time", "event", "importance", "actual", "forecast", "previous", "country"]:
        if col not in df.columns:
            df[col] = ""

    # parse fecha/hora
    def _split_date_time(x):
        s = str(x).strip()
        if not s:
            return "", "00:00"
        try:
            dt = pd.to_datetime(s, errors="coerce")
            if pd.isna(dt):
                return s[:10], "00:00"
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
        except:
            return s[:10], "00:00"

    dates, times = [], []
    for v in df["date"].tolist():
        d, tm = _split_date_time(v)
        dates.append(d)
        times.append(tm)

    df["date"] = dates
    df["time"] = df["time"].astype(str).str.strip()
    df.loc[df["time"].isin(["", "nan", "None"]), "time"] = pd.Series(times).astype(str)

    def _clean_time(x):
        s = str(x).strip()
        low = s.lower()
        if low in ["", "all day", "tentative", "tbd", "--:--", "na", "n/a", "null", "none", "nan"]:
            return "00:00"
        return s[:5] if len(s) >= 5 else s

    df["time"] = df["time"].apply(_clean_time)

    # filtrar US localmente (FMP puede traer varios países)
    c = df["country"].astype(str).str.lower()
    us_mask = (
        c.isin(["united states", "united states of america", "us", "usa", "united states (us)"])
        | c.str.contains("united states", na=False)
    )
    if us_mask.any():
        df = df[us_mask]
    else:
        # si FMP viene con country vacío, no lo eliminamos todo
        logger.warning("[econ] No se detectó country=US en payload; se mantiene sin filtro estricto.")

    df["datetime"] = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values("datetime")

    logger.info(f"[econ] after parse -> rows={len(df)} sample_event={df.iloc[0]['event'] if len(df) else 'n/a'}")
    return df


# =====================================================
# IMPORTANCIA → ESTRELLAS
# =====================================================
def _stars(imp: str) -> int:
    if not isinstance(imp, str):
        return 1
    imp = imp.lower()
    if "high" in imp or "3" in imp:
        return 3
    if "medium" in imp or "2" in imp:
        return 2
    return 1


# =====================================================
# DETECTAR FESTIVIDAD
# =====================================================
def _is_holiday(df: pd.DataFrame) -> bool:
    if df.empty:
        return False
    for ev in df["event"].astype(str).str.lower():
        if "holiday" in ev or "festividad" in ev or "thanksgiving" in ev:
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

    events = []
    for _, r in df.iterrows():
        events.append(
            {
                "datetime": r["datetime"],
                "event": r["event"],
                "stars": int(r["stars"]),
                "actual": r["actual"] or "",
                "forecast": r["forecast"] or "",
                "previous": r["previous"] or "",
            }
        )
    return events


# =====================================================
# TRADUCCIÓN / ADAPTACIÓN (reglas rápidas)
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

    if ("president" in n or "u.s. president" in n) and ("speaks" in n or "speech" in n):
        if "trump" in n:
            return "El presidente Trump ofrece un discurso"
        return "El presidente de EE. UU. ofrece un discurso"

    if "initial jobless claims" in n or ("jobless" in n and "claims" in n):
        return "Solicitudes semanales de subsidio por desempleo"

    if "nonfarm payrolls" in n or "non-farm payrolls" in n:
        return "Nóminas no agrícolas (NFP)"
    if "unemployment rate" in n:
        return "Tasa de desempleo"
    if "average hourly earnings" in n:
        if "mom" in n:
            return "Salario medio por hora (mensual)"
        if "yoy" in n:
            return "Salario medio por hora (interanual)"
        return "Salario medio por hora"

    if "core cpi" in n:
        return "IPC subyacente (sin energía ni alimentos)"
    if "cpi" in n:
        if "mom" in n:
            return "IPC (mensual)"
        if "yoy" in n:
            return "IPC (interanual)"
        return "IPC (índice de precios al consumidor)"

    if "pce" in n:
        return "PCE (inflación preferida por la Fed)"

    if "philadelphia fed" in n and ("manufacturing" in n or "index" in n):
        return "Índice manufacturero de la Fed de Filadelfia"

    if "manufacturing" in n and "index" in n:
        return "Índice manufacturero"

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
        "- Si es un discurso, dilo como 'Discurso de X (FOMC)' si aparece el nombre.\n"
        "- No añadas datos que no estén.\n\n"
        f"Evento: {raw_clean}"
    )

    try:
        out = call_gpt_mini(system_prompt, user_prompt, max_tokens=40).strip()
    except Exception as e:
        logger.warning(f"GPT translate falló: {e}")
        out = ""

    if not out or out.lower() == raw_clean.lower():
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

    if "core cpi" in n or ("cpi" in n and "core" in n) or "cpi" in n or "inflation" in n:
        return "Inflación: IPC e IPC subyacente"
    if "pce" in n:
        return "Inflación: PCE (Fed)"

    if "jobless" in n or "unemployment" in n or "payroll" in n or "nonfarm" in n:
        return "Empleo"
    if "average hourly earnings" in n or ("hourly" in n and "earnings" in n):
        return "Empleo"

    if "philadelphia fed" in n:
        return "Actividad: Fed de Filadelfia"
    if "manufacturing" in n or "ism" in n or "pmi" in n:
        return "Actividad"

    if ("speaks" in n or "speech" in n) and ("fed" in n or "fomc" in n or "chair" in n or "member" in n):
        return "Fed: discursos"

    if "president" in n and ("speaks" in n or "speech" in n):
        return "Política: declaraciones"

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
            "Sesión marcada por datos macro capaces de mover expectativas de tipos. "
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
        "- Usa condicionales claros: si sale por encima / por debajo de lo previsto.\n"
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
            "Sesión marcada por datos macro capaces de mover expectativas de tipos. "
            "Si sorprenden al alza, presión para la renta variable y apoyo al USD/yields; "
            "si salen más suaves, alivio para el riesgo y para los bonos."
        )

    return out


# =====================================================
# MENSAJE FINAL
# =====================================================
def _build_message(events, date_ref: datetime) -> str:
    fecha = date_ref.strftime("%a %d/%m").replace(".", "")

    if events == "HOLIDAY":
        return (
            f"📅 Calendario económico — {fecha}\n\n"
            f"🎌 Hoy es festivo en Estados Unidos.\n"
            f"No hay referencias macroeconómicas relevantes."
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
# FUNCIÓN PRINCIPAL (COMPATIBLE CON main.py) ✅
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
    else:
        start = datetime.combine(now.date(), time.min)
        end = start + timedelta(days=1)
        title_date = now

    df = _safe_request(DEFAULT_COUNTRY, start, end)

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
