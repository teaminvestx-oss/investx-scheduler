# =====================================================
# econ_calendar.py — InvestX v4.2 (Macro Brief PRO + Español total)
# Fuente: investpy (igual)
# Lógica: 1 envío/día (igual) + festivos (igual) + filtro 2-3⭐ (igual) + máx 6 (igual)
# NUEVO:
# - Macro Brief IA estilo CNBC/Bloomberg SIEMPRE en español
# - Agenda agrupada + “detalle humano” (sin repetir CPI 4 veces)
# - Traducción/adaptación de nombres (no mezcla inglés/español)
# - Verificación de OPENAI_API_KEY (si falta, fallback digno)
# ROBUSTEZ NUEVA (INVESTPY):
# - Reintentos con backoff (por vacíos/bloqueos intermitentes)
# - No decir "no hay datos" si la fuente falla: mensaje "fuente no disponible"
# - No tirar eventos por horas raras (All Day/Tentative/--:--) -> 00:00
# - Filtro de importancia más tolerante + fallback si cambia el formato
# =====================================================

import os
import json
import logging
import time as _time
import random as _random
from datetime import datetime, timedelta, time
from typing import List, Dict, Tuple, Optional

import pandas as pd
import investpy

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_FILE = "econ_calendar_state.json"
DEFAULT_COUNTRY = "United States"

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
# HELPERS: parsing hora + status fuente
# =====================================================
def _clean_time(s: str) -> str:
    """
    investpy a veces devuelve time como 'All Day', 'Tentative', '--:--', vacío...
    Para no perder el evento, lo normalizamos a '00:00'.
    """
    if s is None:
        return "00:00"
    x = str(s).strip()
    low = x.lower()
    if low in ["", "all day", "tentative", "tbd", "--:--", "na", "n/a", "null"]:
        return "00:00"
    # algunas veces viene "All Day " con espacios o similar
    if "all day" in low or "tentative" in low:
        return "00:00"
    return x


def _source_unavailable_message(date_ref: datetime) -> str:
    fecha = date_ref.strftime("%a %d/%m").replace(".", "")
    return (
        f"📅 Calendario económico — {fecha}\n\n"
        "⚠️ Hoy no puedo obtener el calendario macro (fuente sin respuesta o bloqueada).\n"
        "En cuanto vuelva la conexión, lo publico con normalidad."
    )


# =====================================================
# REQUEST SAFE A INVESTPY (arregla error rango + robustez)
# =====================================================
def _safe_request(country, start: datetime, end: datetime) -> pd.DataFrame:
    if end <= start:
        end = start + timedelta(days=1)

    f = start.strftime("%d/%m/%Y")
    t = end.strftime("%d/%m/%Y")

    df: Optional[pd.DataFrame] = None
    last_err: Optional[Exception] = None

    # 3 intentos (vacíos/bloqueos intermitentes)
    for attempt in range(3):
        try:
            df = investpy.economic_calendar(
                from_date=f,
                to_date=t,
                countries=[country]
            )
            if df is None:
                df = pd.DataFrame()

            # Si viene vacío, reintenta (muchas veces es intermitente)
            if not df.empty:
                break
        except Exception as e:
            last_err = e
            logger.error(f"Error investpy (attempt {attempt+1}/3): {e}")

        # backoff suave
        _time.sleep(0.8 + _random.random() * 0.8)

    if df is None or df.empty:
        if last_err:
            logger.error(f"investpy vacío tras reintentos: {last_err}")
        return pd.DataFrame()

    # Normalizamos columnas
    for col in ["date", "time", "event", "importance", "actual", "forecast", "previous"]:
        if col not in df.columns:
            df[col] = ""

    # Limpiamos hora para no perder eventos
    df["time"] = df["time"].astype(str).apply(_clean_time)

    # Parse datetime
    df["datetime"] = pd.to_datetime(
        df["date"].astype(str) + " " + df["time"].astype(str),
        errors="coerce"
    )

    # Quitamos solo lo realmente roto
    df = df.dropna(subset=["datetime"]).sort_values("datetime")

    return df


# =====================================================
# IMPORTANCIA → ESTRELLAS (más tolerante)
# =====================================================
def _stars(imp: str) -> int:
    if imp is None:
        return 1

    s = str(imp).strip().lower()

    # formatos típicos
    if "high" in s or "3" in s or "★★★" in s or "bull3" in s:
        return 3
    if "medium" in s or "2" in s or "★★" in s or "bull2" in s:
        return 2

    # si llega algo raro/no vacío, preferimos no perder el día por cambios de formato
    if s and s not in ["low", "1", "★", "bull1"]:
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
# FILTRADO PRINCIPAL (con fallback si el filtro mata todo)
# =====================================================
def _process_events(df: pd.DataFrame) -> List[Dict]:
    if df.empty:
        return []

    df = df.copy()
    df["stars"] = df["importance"].apply(_stars)

    # Solo 2 y 3 estrellas
    df2 = df[df["stars"] >= 2].copy()

    # Fallback: si había eventos pero ninguno pasó el filtro, no publiques "no hay datos"
    if df2.empty and not df.empty:
        df2 = df.copy()
        # elevamos el mínimo a 2 para mantener coherencia del canal
        df2["stars"] = df2["stars"].clip(lower=2)

    # Reducimos a máximo 6 eventos
    df2 = df2.sort_values(["stars", "datetime"], ascending=[False, True]).head(6)
    df2 = df2.sort_values("datetime")

    events = []
    for _, r in df2.iterrows():
        events.append(
            {
                "datetime": r["datetime"],
                "event": r["event"],
                "stars": int(r["stars"]),
                "actual": r.get("actual", "") or "",
                "forecast": r.get("forecast", "") or "",
                "previous": r.get("previous", "") or "",
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
                "examples": [example_es] if example_es else []
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
        out.append({
            "datetime": g["datetime"],
            "stars": g["stars"],
            "label": g["label"] + suffix
        })

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
def _build_message(events, date_ref: datetime) -> str:
    fecha = date_ref.strftime("%a %d/%m").replace(".", "")

    # Caso: festividad
    if events == "HOLIDAY":
        return (
            f"📅 Calendario económico — {fecha}\n\n"
            f"🎌 Hoy es festivo en Estados Unidos.\n"
            f"No hay referencias macroeconómicas relevantes."
        )

    # Caso: no eventos (reales)
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

    now = datetime.now()
    day_key = now.strftime("%Y-%m-%d")

    # Control 1 vez al día
    if not force and not force_tomorrow:
        if _already_sent(day_key):
            logger.info("econ_calendar: ya enviado hoy.")
            return

    # Rangos
    if force_tomorrow:
        start = datetime.combine(now.date() + timedelta(days=1), time.min)
        end = start + timedelta(days=1)
        title_date = start
    else:
        start = datetime.combine(now.date(), time.min)
        end = start + timedelta(days=1)
        title_date = now

    # Descarga (investpy robusto)
    df = _safe_request(DEFAULT_COUNTRY, start, end)

    # Si la fuente falla/vacía -> NO digas "no hay datos"
    if df.empty:
        msg = _source_unavailable_message(title_date)
        send_telegram_message(msg)
        if not force and not force_tomorrow:
            _mark_sent(day_key)
        return

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
