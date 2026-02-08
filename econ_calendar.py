# =====================================================
# econ_calendar.py — InvestX v4.2 (Macro Brief PRO + Español total)
# Fuente: investpy (Investing.com) — SCRAPING (puede fallar por bloqueos)
#
# Objetivo: 1 envío/día + festivos + filtro 2-3⭐ + máx 6
# Robustez:
# - Timezone Europe/Madrid
# - _safe_request con reintentos + limpieza de time
# - Caché diaria: si hoy Investing bloquea, usamos el último resultado guardado
# - Si no hay datos y no hay caché: se avisa (NO se miente con “no hay macro”)
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
import investpy

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_FILE = "econ_calendar_state.json"
DEFAULT_COUNTRY = "United States"
TZ = ZoneInfo("Europe/Madrid")

TRANSLATION_CACHE_FILE = "econ_translation_cache.json"

# Caché diaria de eventos (para aguantar bloqueos de Investing)
DAILY_CACHE_DIR = "econ_daily_cache"


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
# CACHÉ DIARIA (RAW EVENTS)
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
        # guardamos solo columnas relevantes
        cols = [c for c in ["date", "time", "event", "importance", "actual", "forecast", "previous"] if c in df.columns]
        out = df[cols].copy()

        # datetime no lo guardamos (lo recomputamos)
        out = out.fillna("")
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
# REQUEST SAFE A INVESTPY
# =====================================================
def _safe_request(country, start: datetime, end: datetime) -> pd.DataFrame:
    if end <= start:
        end = start + timedelta(days=1)

    f = start.strftime("%d/%m/%Y")
    t = end.strftime("%d/%m/%Y")

    df = None
    last_err = None

    logger.info(f"[econ] investpy request country={country} from={f} to={t}")

    # Jitter pequeño para evitar patrones “clavados” (no es bypass; reduce colisiones)
    _time.sleep(0.4 + _random.random() * 0.8)

    for attempt in range(3):
        try:
            df = investpy.economic_calendar(
                from_date=f,
                to_date=t,
                countries=[country]
            )
            if df is None:
                df = pd.DataFrame()

            logger.info(f"[econ] attempt {attempt+1}/3 -> rows={len(df)} cols={list(df.columns)[:8]}")

            if not df.empty:
                break

        except Exception as e:
            last_err = e
            logger.error(f"[econ] investpy exception attempt {attempt+1}/3: {e}")

        # backoff suave
        _time.sleep(1.0 + attempt * 1.2 + _random.random() * 0.8)

    if df is None or df.empty:
        if last_err:
            logger.error(f"[econ] investpy returned EMPTY after retries (with error): {last_err}")
        else:
            logger.warning("[econ] investpy returned EMPTY after retries (NO exception). Possible block/change/no events.")
        return pd.DataFrame()

    # Normalizamos columnas
    for col in ["date", "time", "event", "importance", "actual", "forecast", "previous"]:
        if col not in df.columns:
            df[col] = ""

    # Limpiar time
    def _clean_time(x):
        s = str(x).strip()
        low = s.lower()
        if low in ["", "all day", "tentative", "tbd", "--:--", "na", "n/a", "null", "none", "nan"]:
            return "00:00"
        if "all day" in low or "tentative" in low:
            return "00:00"
        # recorta HH:MM
        return s[:5] if len(s) >= 5 else s

    df["time"] = df["time"].apply(_clean_time)

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

    # Solo 2 y 3 estrellas
    df = df[df["stars"] >= 2]
    if df.empty:
        return []

    # Reducimos a máximo 6 eventos
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
        "- Puedes mencionar 1 dato por su nombre si es el protagonista.\n"
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
            "Sesión marcada por referencias macro capaces de mover expectativas de tipos. "
            "Si sorprenden al alza, presión para la renta variable y apoyo al USD/yields; "
            "si salen más suaves, alivio para el riesgo y para los bonos."
        )

    return out


# =====================================================
# MENSAJE FINAL (NO MIENTE SI HAY BLOQUEO)
# =====================================================
def _build_message(events, date_ref: datetime, source_status: str = "OK") -> str:
    fecha = date_ref.strftime("%a %d/%m").replace(".", "")

    if source_status == "BLOCKED":
        return (
            f"📅 Calendario económico — {fecha}\n\n"
            "⚠️ Fuente Investing temporalmente no disponible (bloqueo o cambios). "
            "Si hay caché reciente, se usará automáticamente; si no, hoy no puedo listar eventos con fiabilidad."
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

    # 1) intentar investpy
    df = _safe_request(DEFAULT_COUNTRY, start, end)

    # 2) si devuelve vacío: usar caché (si existe)
    source_status = "OK"
    if df.empty:
        cached = _load_daily_cache(cache_key)
        if not cached.empty:
            logger.warning(f"[econ] Using cached data for {cache_key} (investpy empty/blocked).")
            # recomputar datetime
            cached = cached.copy()
            for col in ["date", "time", "event", "importance", "actual", "forecast", "previous"]:
                if col not in cached.columns:
                    cached[col] = ""
            cached["datetime"] = pd.to_datetime(cached["date"].astype(str) + " " + cached["time"].astype(str), errors="coerce")
            cached = cached.dropna(subset=["datetime"]).sort_values("datetime")
            df = cached
        else:
            source_status = "BLOCKED"

    # 3) si tenemos df válido, guardar caché (para próximos bloqueos)
    if not df.empty and source_status == "OK":
        try:
            _save_daily_cache(cache_key, df)
        except:
            pass

    # 4) construir eventos + mensaje
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
