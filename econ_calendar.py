# =====================================================
# econ_calendar.py — InvestX v4.2 (Macro Brief PRO + Español total)
# Fuente: FINNHUB (sustituye investpy por bloqueos)
# Lógica: 1 envío/día (igual) + festivos (igual) + filtro 2-3⭐ (igual) + máx 6 (igual)
# NUEVO:
# - Macro Brief IA estilo CNBC/Bloomberg SIEMPRE en español
# - Agenda agrupada + “detalle humano” (sin repetir CPI 4 veces)
# - Traducción/adaptación de nombres (no mezcla inglés/español)
# - Verificación de OPENAI_API_KEY (si falta, fallback digno)
# MEJORAS:
# - FIX: call_gpt_mini(system_prompt, user_prompt, ...)
# - Traducción "instantánea": reglas + fallback IA para eventos no cubiertos
# - Caché persistente de traducciones (evita gastar tokens repetidos)
#
# FIX MÍNIMO PARA QUE DEVUELVA DATOS:
# - Timezone Europe/Madrid (evita desfase UTC que te pedía domingo cuando era lunes)
# - _safe_request robusto: reintentos + logs
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

# Timezone explícita (clave si el servidor corre en UTC)
TZ = ZoneInfo("Europe/Madrid")

# Caché de traducciones (para no llamar a IA cada día por los mismos nombres)
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
# REQUEST SAFE A FINNHUB (sustituye investpy bloqueado)
# Devuelve DF compatible con tu pipeline: date/time/event/importance/actual/forecast/previous/datetime
# =====================================================
def _safe_request(country, start: datetime, end: datetime):
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        logger.error("[econ] FINNHUB_API_KEY no configurada")
        return pd.DataFrame()

    if end <= start:
        end = start + timedelta(days=1)

    # Finnhub usa rango por fecha ISO
    f = start.strftime("%Y-%m-%d")
    t = end.strftime("%Y-%m-%d")

    url = "https://finnhub.io/api/v1/calendar/economic"
    params = {"from": f, "to": t, "token": api_key}

    df = None
    last_err = None

    logger.info(f"[econ] finnhub request country={country} from={f} to={t}")

    # 3 intentos: por robustez (timeouts / rate / fallos puntuales)
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=12)
            r.raise_for_status()
            data = r.json()

            items = data.get("economicCalendar", []) or []
            logger.info(f"[econ] attempt {attempt+1}/3 -> items={len(items)}")

            rows = []
            for ev in items:
                ts = ev.get("time")
                if not ts:
                    continue

                # Finnhub devuelve epoch (segundos). Convertimos a TZ Madrid
                dt = datetime.fromtimestamp(int(ts), TZ)

                rows.append(
                    {
                        "date": dt.strftime("%d/%m/%Y"),
                        "time": dt.strftime("%H:%M"),
                        "event": ev.get("event", "") or "",
                        "importance": ev.get("impact", "") or "",  # low/medium/high
                        "actual": ev.get("actual", "") or "",
                        "forecast": ev.get("forecast", "") or "",
                        "previous": ev.get("previous", "") or "",
                        "datetime": dt,
                    }
                )

            df = pd.DataFrame(rows)
            if df is None:
                df = pd.DataFrame()

            if not df.empty:
                df = df.sort_values("datetime")
                logger.info(
                    f"[econ] after parse -> rows={len(df)} sample_event={df.iloc[0]['event'] if len(df) else 'n/a'}"
                )
                break

        except Exception as e:
            last_err = e
            logger.error(f"[econ] finnhub exception attempt {attempt+1}/3: {e}")

        _time.sleep(0.8 + _random.random() * 0.8)

    if df is None or df.empty:
        if last_err:
            logger.error(f"[econ] finnhub returned EMPTY after retries (with error): {last_err}")
        else:
            logger.warning("[econ] finnhub returned EMPTY after retries (NO exception). Possible no events.")
        return pd.DataFrame()

    # Normalizamos columnas por si falta alguna
    for col in ["date", "time", "event", "importance", "actual", "forecast", "previous", "datetime"]:
        if col not in df.columns:
            df[col] = ""

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
    """
    Traduce/adapta los nombres más comunes a español entendible.
    Regla rápida; si no hay match, devuelve el original.
    """
    if not isinstance(ev_name, str) or not ev_name.strip():
        return ""

    s = " ".join(ev_name.strip().split())
    n = s.lower()

    # Política
    if ("president" in n or "u.s. president" in n) and ("speaks" in n or "speech" in n):
        if "trump" in n:
            return "El presidente Trump ofrece un discurso"
        return "El presidente de EE. UU. ofrece un discurso"

    # Empleo (claims)
    if "initial jobless claims" in n or ("jobless" in n and "claims" in n):
        return "Solicitudes semanales de subsidio por desempleo"

    # Empleo (NFP / paro / salarios)
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

    # Inflación - CPI
    if "core cpi" in n:
        return "IPC subyacente (sin energía ni alimentos)"
    if "cpi" in n:
        if "mom" in n:
            return "IPC (mensual)"
        if "yoy" in n:
            return "IPC (interanual)"
        return "IPC (índice de precios al consumidor)"

    # Inflación - PCE
    if "pce" in n:
        return "PCE (inflación preferida por la Fed)"

    # Actividad - Philly Fed
    if "philadelphia fed" in n and ("manufacturing" in n or "index" in n):
        return "Índice manufacturero de la Fed de Filadelfia"

    # Genéricos comunes
    if "manufacturing" in n and "index" in n:
        return "Índice manufacturero"

    return s


def _gpt_translate_event_name(raw: str) -> str:
    """
    Traducción IA SOLO si la regla no cubre el evento.
    Usa caché persistente para evitar llamadas repetidas.
    """
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
    """
    Traducción inteligente:
    - Primero reglas (rápido y consistente)
    - Si no cambia nada (inglés), fallback IA + caché
    """
    if not isinstance(raw, str) or not raw.strip():
        return ""

    raw_clean = " ".join(raw.strip().split())
    rule_es = _translate_event_name(raw_clean)

    # Si la regla no cambió el texto, usamos IA para traducir
    if rule_es.strip().lower() == raw_clean.strip().lower():
        return _gpt_translate_event_name(raw_clean)

    return rule_es


# =====================================================
# AGRUPACIÓN DE AGENDA (evita duplicados + mantiene detalle)
# =====================================================
def _bucket_event(ev_name: str) -> str:
    n = _normalize_event_name(ev_name)

    # Inflación
    if "core cpi" in n or ("cpi" in n and "core" in n):
        return "Inflación: IPC e IPC subyacente"
    if "cpi" in n or "inflation" in n:
        return "Inflación: IPC e IPC subyacente"
    if "pce" in n:
        return "Inflación: PCE (Fed)"

    # Empleo
    if "jobless" in n or "unemployment" in n or "payroll" in n or "nonfarm" in n:
        return "Empleo"
    if "average hourly earnings" in n or ("hourly" in n and "earnings" in n):
        return "Empleo"

    # Actividad
    if "philadelphia fed" in n:
        return "Actividad: Fed de Filadelfia"
    if "manufacturing" in n or "ism" in n or "pmi" in n:
        return "Actividad"

    # Fed / discursos
    if ("speaks" in n or "speech" in n) and ("fed" in n or "fomc" in n or "chair" in n or "member" in n):
        return "Fed: discursos"

    # Política
    if "president" in n and ("speaks" in n or "speech" in n):
        return "Política: declaraciones"

    # (Mantenemos 'Otros' como fallback)
    return "Otros"


def _group_agenda(events: List[Dict]) -> List[Dict]:
    """
    Agrupa eventos y conserva 1-2 ejemplos (traducidos) del nombre original.
    Mantiene hora mínima del grupo y máxima importancia (stars).
    """
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
                "examples": [example_es] if example_es else [],
            }
        else:
            # Hora: la más temprana
            if dt and groups[bucket]["datetime"] and dt < groups[bucket]["datetime"]:
                groups[bucket]["datetime"] = dt
            # Estrellas: la más alta
            if stars > groups[bucket]["stars"]:
                groups[bucket]["stars"] = stars
            # Ejemplos: únicos (máximo 2)
            if example_es and example_es not in groups[bucket]["examples"]:
                groups[bucket]["examples"].append(example_es)

    out = []
    for g in groups.values():
        ex = g["examples"][:2]
        suffix = ""
        if ex:
            suffix = ": " + " / ".join(ex)
        out.append({"datetime": g["datetime"], "stars": g["stars"], "label": g["label"] + suffix})

    out.sort(key=lambda x: x["datetime"] or datetime.max)
    return out


# =====================================================
# MACRO BRIEF IA (estilo CNBC/Bloomberg) — SIEMPRE EN ESPAÑOL
# + Verifica OPENAI_API_KEY (si falta → fallback digno)
# =====================================================
def _make_macro_brief(events: List[Dict]) -> str:
    if not events:
        return ""

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning("OPENAI_API_KEY no configurada. Macro Brief irá por fallback.")
        return (
            "Sesión marcada por datos macro capaces de mover expectativas de tipos. "
            "Si sorprenden al alza, presión para la renta variable y apoyo al USD/yields; "
            "si salen más suaves, alivio para el riesgo y para los bonos."
        )

    # Contexto para IA (pero sin obligar a listar)
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
        "- Agrupa mentalmente lo repetido (IPC general y subyacente, etc.).\n"
        "- Usa condicionales claros: si sale por encima / por debajo de lo previsto.\n"
        "- Conecta con: expectativas de la Fed/tipos, yields, USD y renta variable.\n"
        "- No inventes resultados ni cifras que no estén en el contexto.\n"
        "- Prohibido escribir en inglés.\n\n"
        "Contexto de eventos (solo para que entiendas el día):\n"
        f"{event_block}\n"
    )

    try:
        out = call_gpt_mini(system_prompt, user_prompt, max_tokens=200).strip()
    except Exception as e:
        logger.warning(f"call_gpt_mini falló: {e}")
        out = ""

    # Si sale accidentalmente en inglés, lo traducimos
    eng_hits = 0
    low = out.lower() if isinstance(out, str) else ""
    for w in ["markets", "ahead", "yields", "dollar", "stocks", "brace", "inflation", "fed", "rates"]:
        if w in low:
            eng_hits += 1

    if out and eng_hits >= 3:
        try:
            tr_system = "Eres un editor senior. Traduce y adapta al español claro sin añadir información."
            tr_user = "Traduce al español claro (máx 4 frases), sin añadir información:\n" + out
            out = call_gpt_mini(tr_system, tr_user, max_tokens=240).strip()
        except Exception as e:
            logger.warning(f"Traducción falló: {e}")

    if not out:
        out = (
            "Sesión marcada por datos macro capaces de mover expectativas de tipos. "
            "Si sorprenden al alza, presión para la renta variable y apoyo al USD/yields; "
            "si salen más suaves, alivio para el riesgo y para los bonos."
        )

    return out


# =====================================================
# CREAR MENSAJE FINAL (Macro Brief arriba + agenda agrupada y entendible)
# =====================================================
def _build_message(events: List[Dict], date_ref: datetime) -> str:
    fecha = date_ref.strftime("%a %d/%m").replace(".", "")

    # Caso: festividad
    if events == "HOLIDAY":
        return (
            f"📅 Calendario económico — {fecha}\n\n"
            f"🎌 Hoy es festivo en Estados Unidos.\n"
            f"No hay referencias macroeconómicas relevantes."
        )

    # Caso: no eventos
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

        item = f"{stars} {hr} — {label}".strip()
        lines.append(item)

    return "\n".join(lines)


# =====================================================
# FUNCIÓN PRINCIPAL
# =====================================================
def run_econ_calendar(force: bool = False, force_tomorrow: bool = False):

    now = datetime.now(TZ)
    day_key = now.strftime("%Y-%m-%d")

    # Control 1 vez al día
    if not force and not force_tomorrow:
        if _already_sent(day_key):
            logger.info("econ_calendar: ya enviado hoy.")
            return

    # Rangos
    if force_tomorrow:
        start = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=TZ)
        end = start + timedelta(days=1)
        title_date = start
    else:
        start = datetime.combine(now.date(), time.min, tzinfo=TZ)
        end = start + timedelta(days=1)
        title_date = now

    # Descarga (Finnhub)
    df = _safe_request(DEFAULT_COUNTRY, start, end)

    # Detectar festividad
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
