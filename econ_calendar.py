# =====================================================
# econ_calendar.py — InvestX v4.1 (Macro Brief PRO, estable)
# Calendario USA diario/semanal + Macro Brief IA (estilo CNBC/Bloomberg, en español)
# + control 1 envío + agenda agrupada (evita duplicados tipo CPI/Core CPI)
# =====================================================

import os
import json
import logging
from datetime import datetime, timedelta, time
from typing import List, Dict

import pandas as pd
import investpy

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_FILE = "econ_calendar_state.json"
DEFAULT_COUNTRY = "United States"


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


# =====================================================
# REQUEST SAFE A INVESTPY (arregla error rango)
# =====================================================
def _safe_request(country, start: datetime, end: datetime):
    if end <= start:
        end = start + timedelta(days=1)

    f = start.strftime("%d/%m/%Y")
    t = end.strftime("%d/%m/%Y")

    try:
        df = investpy.economic_calendar(
            from_date=f,
            to_date=t,
            countries=[country]
        )
    except Exception as e:
        logger.error(f"Error investpy: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    # Normalizamos columnas
    for col in ["date", "time", "event", "importance", "actual", "forecast", "previous"]:
        if col not in df.columns:
            df[col] = ""

    df["datetime"] = pd.to_datetime(df["date"] + " " + df["time"], errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values("datetime")

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
# AGRUPACIÓN DE AGENDA (evita duplicados tipo CPI/Core CPI)
# =====================================================
def _normalize_event_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    return " ".join(name.strip().split()).lower()


def _bucket_event(ev_name: str) -> str:
    n = _normalize_event_name(ev_name)

    # Inflación
    if "cpi" in n or "inflation" in n:
        return "Inflación (CPI/Core CPI)"
    if "pce" in n:
        return "Inflación (PCE)"

    # Empleo
    if "jobless" in n or "unemployment" in n or "payroll" in n or "nonfarm" in n:
        return "Empleo (mercado laboral)"

    # Actividad / crecimiento
    if "manufacturing" in n or "ism" in n or "pmi" in n or "philadelphia fed" in n:
        return "Actividad (PMI/ISM/Fed regional)"

    # Fed / discursos
    if "fed" in n and ("speech" in n or "speaks" in n or "chair" in n):
        return "Fed (discursos)"

    # Política / declaraciones
    if "president" in n and ("speaks" in n or "speech" in n):
        return "Política (declaraciones)"

    # Default
    return ev_name.strip() if isinstance(ev_name, str) else ""


def _group_agenda(events: List[Dict]) -> List[Dict]:
    """
    Agrupa eventos repetidos/relacionados para que la agenda sea entendible.
    Mantiene hora mínima del grupo y máxima importancia (stars).
    """
    if not events:
        return []

    groups = {}
    for ev in events:
        bucket = _bucket_event(ev.get("event", ""))
        dt = ev.get("datetime")
        stars = int(ev.get("stars", 1))

        if bucket not in groups:
            groups[bucket] = {"datetime": dt, "stars": stars, "label": bucket}
        else:
            # Hora: la más temprana
            if dt and groups[bucket]["datetime"] and dt < groups[bucket]["datetime"]:
                groups[bucket]["datetime"] = dt
            # Estrellas: la más alta
            if stars > groups[bucket]["stars"]:
                groups[bucket]["stars"] = stars

    out = list(groups.values())
    out = sorted(out, key=lambda x: x["datetime"] or datetime.max)
    return out


# =====================================================
# MACRO BRIEF IA (estilo CNBC/Bloomberg) — SIEMPRE EN ESPAÑOL
# =====================================================
def _make_macro_brief(events: List[Dict]) -> str:
    if not events:
        return ""

    event_names = [e.get("event", "") for e in events if e.get("event")]
    event_block = "\n".join([f"- {n}" for n in event_names])

    prompt = (
        "Eres analista macro senior en un desk institucional (estilo Bloomberg/CNBC).\n"
        "Con los eventos de EE. UU. de hoy, redacta un 'Macro Brief' en español, claro y entendible.\n"
        "Máximo 3 frases.\n"
        "Reglas:\n"
        "- NO enumeres eventos ni horas.\n"
        "- Agrupa mentalmente eventos repetidos (ej: CPI/Core CPI).\n"
        "- Usa condicionales (si sale más alto/si sale más débil).\n"
        "- Enfócate en impacto sobre: Fed/tipos, bonos (yields), dólar (USD) y renta variable.\n"
        "- Prohibido escribir en inglés.\n\n"
        "Eventos:\n"
        f"{event_block}\n"
    )

    try:
        out = call_gpt_mini(prompt, max_tokens=120).strip()
    except:
        out = ""

    # Cinturón y tirantes: si aun así sale en inglés, traducimos sin añadir info
    if out and any(w in out.lower() for w in ["markets", "ahead", "yields", "dollar", "stocks", "brace", "inflation", "fed"]):
        try:
            tr_prompt = (
                "Traduce al español neutro y claro (máx 3 frases), sin añadir información:\n"
                f"{out}"
            )
            out = call_gpt_mini(tr_prompt, max_tokens=140).strip()
        except:
            pass

    if not out:
        out = (
            "Hoy el mercado ajusta expectativas de tipos en función de los datos macro; "
            "atención al impacto en USD, yields y renta variable."
        )

    return out


# =====================================================
# CREAR MENSAJE FINAL (Macro Brief arriba + agenda agrupada)
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

    # Macro brief IA
    brief = _make_macro_brief(events)

    # Agenda agrupada (evita CPI x4)
    agenda = _group_agenda(events)

    lines = [f"🧠 Macro Brief — {fecha} (EE. UU.)\n", brief, "\nAgenda clave:"]

    for a in agenda:
        dt = a.get("datetime")
        hr = dt.strftime("%H:%M") if dt else ""
        stars = "⭐" * int(a.get("stars", 1))
        label = a.get("label", "")
        lines.append(f"{stars} {hr} — {label}".strip())

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

    # Descarga
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
