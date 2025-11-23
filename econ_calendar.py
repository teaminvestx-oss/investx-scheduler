# =====================================================
# econ_calendar.py — InvestX v3.0 (FINAL, ESTABLE)
# Calendario USA diario / semanal + resumen IA + control de envíos
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

# Archivo para controlar "solo 1 envío por día"
STATE_FILE = "econ_calendar_state.json"

DEFAULT_COUNTRY = os.environ.get("ECON_COUNTRY", "united states")

# ================
# ESTADO DIARIO
# ================

def _load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def _save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except:
        pass

def _already_sent_today(day_key: str) -> bool:
    state = _load_state()
    return state.get("sent_day") == day_key

def _mark_sent_today(day_key: str):
    state = _load_state()
    state["sent_day"] = day_key
    _save_state(state)


# =====================================================
# CORRECCIÓN DEFINITIVA DEL RANGO FECHAS (NO MÁS ERR#0032)
# =====================================================

def _safe_investpy_request(country: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
    """
    Siempre garantiza end_date > start_date
    """
    if end_date <= start_date:
        end_date = start_date + timedelta(days=1)

    f = start_date.strftime("%d/%m/%Y")
    t = end_date.strftime("%d/%m/%Y")

    logger.info(f"econ_calendar: solicitando rango {f} -> {t}")

    df = investpy.economic_calendar(
        from_date=f,
        to_date=t,
        countries=[country.title()]
    )

    if df is None or df.empty:
        logger.info("econ_calendar: no hay datos para el rango")
        return pd.DataFrame()

    # Normalizar columnas
    for col in ["date", "time", "event", "importance", "actual", "forecast", "previous", "country"]:
        if col not in df.columns:
            df[col] = ""

    # Convertir datetime real
    df["datetime"] = pd.to_datetime(df["date"] + " " + df["time"], errors="coerce")
    df = df.dropna(subset=["datetime"])
    df = df.sort_values("datetime")

    return df


# =====================================================
# IMPORTANCIA + PALABRAS CLAVE
# =====================================================

def _importance_to_stars(imp: str) -> int:
    if not isinstance(imp, str):
        return 1
    imp = imp.lower()
    if "high" in imp or "3" in imp:
        return 3
    if "medium" in imp or "2" in imp:
        return 2
    return 1

KEYWORDS_PRIORITY = [
    "fed", "fomc", "interest", "rate", "trump",
    "nonfarm", "payroll", "cpi", "inflation", "pce",
    "gdp", "retail", "ism", "manufacturing", "services",
    "housing", "building permits",
    "oil", "inventories"
]


def _normalize_title(t: str) -> str:
    import re
    t = t.lower()
    t = re.sub(r"\(.*?\)", "", t)
    t = " ".join(t.split())
    return t


# =====================================================
# FILTRADO / AGRUPACIÓN
# =====================================================

def _filter_and_group(df: pd.DataFrame) -> List[Dict]:
    if df.empty:
        return []

    df = df.copy()
    df["stars"] = df["importance"].apply(_importance_to_stars)
    df = df[df["stars"] >= 2]     # solo 2 y 3 estrellas

    if df.empty:
        return []

    df["title_norm"] = df["event"].apply(_normalize_title)

    # Agrupación por títulos parecidos
    grouped = []
    for _, g in df.groupby("title_norm"):
        g = g.sort_values(["stars", "datetime"], ascending=[False, True])
        grouped.append(g.iloc[0])

    df = pd.DataFrame(grouped)

    # Prioridad real
    def is_priority(ev, stars):
        ev = ev.lower()
        if stars == 3:
            return True
        return any(k in ev for k in KEYWORDS_PRIORITY)

    df["priority"] = df.apply(lambda r: is_priority(r["event"], r["stars"]), axis=1)

    # Orden final
    df = df.sort_values(["priority", "stars", "datetime"], ascending=[False, False, True])

    # Reducimos a 6
    df = df.head(6)
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
# IA – INTERPRETACIÓN
# =====================================================

def _interpret(ev: Dict) -> str:
    prompt = f"""
Eres analista macro en un canal de trading. Resume el IMPACTO POTENCIAL PARA ÍNDICES USA Y EL USD:

Evento: {ev['event']}
Actual: {ev['actual']}
Previsión: {ev['forecast']}
Anterior: {ev['previous']}

Reglas:
- SOLO 1–3 líneas, muy cortas.
- Tono profesional.
- Máximo 220 caracteres.
- Sin mencionar IA ni "este dato".
    """
    try:
        txt = call_gpt_mini(prompt, max_tokens=80)
        return txt.strip()
    except:
        return "Dato relevante para índices USA y el USD."


# =====================================================
# MENSAJE FINAL
# =====================================================

def _build_message(events: List[Dict], title_date: datetime) -> str:
    if not events:
        return "📅 Hoy no hay datos macro relevantes en EE. UU. o no se han encontrado eventos válidos."

    fecha = title_date.strftime("%a %d/%m").replace(".", "")

    lines = [f"📅 Calendario económico — {fecha}\n"]

    for ev in events:
        hr = ev["datetime"].strftime("%H:%M")
        stars = "⭐" * ev["stars"]

        inter = _interpret(ev)

        block = (
            f"{stars} {hr} — {ev['event']}\n"
            f"   Actual: {ev['actual']} | Previsión: {ev['forecast']} | Anterior: {ev['previous']}\n"
            f"   {inter}"
        )
        lines.append(block)

    # Resumen final
    resumen_prompt = "Resume en 1 frase (máx 160 caracteres) cuál es la CLAVE del día para índices USA y USD:"
    resumen_prompt += "\n".join(f"- {e['event']}" for e in events)

    try:
        resumen = call_gpt_mini(resumen_prompt, max_tokens=50)
    except:
        resumen = "Los datos macro de hoy marcarán el tono para índices USA y el USD."

    lines.append(f"\n👉 Clave del día: {resumen}")

    msg = "\n".join(lines)
    return msg if len(msg) < 3900 else msg[:3900]


# =====================================================
# FUNCIÓN PRINCIPAL
# =====================================================

def run_econ_calendar(force: bool = False, force_tomorrow: bool = False):
    """
    Lógica final:
    - Lunes -> semana completa
    - Otros días -> solo hoy
    - force=True: ignora estado diario
    - force_tomorrow=True: envía solo "mañana"
    """

    now = datetime.now()
    weekday = now.weekday()  # 0=lunes

    # Control 1 envío por día
    day_key = now.strftime("%Y-%m-%d")
    if not force and not force_tomorrow:
        if _already_sent_today(day_key):
            logger.info("econ_calendar: ya enviado hoy.")
            return

    # ===========================
    # Selección del rango
    # ===========================

    if force_tomorrow:
        start = datetime.combine(now.date() + timedelta(days=1), time.min)
        end = start + timedelta(days=1)
        title_date = start
    else:
        if weekday == 0:
            # LUNES → SEMANA ENTERA
            start = datetime.combine(now.date(), time.min)
            end = start + timedelta(days=5)
            title_date = now
        else:
            # DÍA NORMAL
            start = datetime.combine(now.date(), time.min)
            end = start + timedelta(days=1)
            title_date = now

    # ===========================
    # Descarga de datos
    # ===========================

    try:
        df = _safe_investpy_request(DEFAULT_COUNTRY, start, end)
    except Exception as e:
        send_telegram_message(f"⚠️ Error al obtener calendario económico: {e}")
        return

    events = _filter_and_group(df)
    msg = _build_message(events, title_date)

    # Enviar
    send_telegram_message(msg)

    if not force and not force_tomorrow:
        _mark_sent_today(day_key)
