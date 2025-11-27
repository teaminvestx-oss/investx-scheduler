# =====================================================
# econ_calendar.py — InvestX v4.0 (FINAL, ESTABLE)
# Calendario USA diario/semanal + resumen IA + control 1 envío
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

    # Reducimos a máximo 6 eventos, por prioridad temporal
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
# RESUMEN FINAL DE IA
# =====================================================
def _make_summary(events: List[Dict]) -> str:
    if not events:
        return ""

    prompt = (
        "Eres analista macro. Resume en 1 frase clara (máx 150 caracteres) "
        "la clave del día para índices USA y el USD. Eventos:\n"
    )

    for ev in events:
        prompt += f"- {ev['event']}\n"

    try:
        out = call_gpt_mini(prompt, max_tokens=60).strip()
        return out
    except:
        return "Los datos macro de hoy marcarán el tono del mercado en EE. UU."


# =====================================================
# CREAR MENSAJE FINAL
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

    # Construcción normal
    lines = [f"📅 Calendario económico — {fecha}\n"]

    for ev in events:
        hr = ev["datetime"].strftime("%H:%M")
        stars = "⭐" * ev["stars"]

        block = (
            f"{stars} {hr} — {ev['event']}\n"
            f"   Actual: {ev['actual']} | Previsión: {ev['forecast']} | Anterior: {ev['previous']}"
        )
        lines.append(block)

    summary = _make_summary(events)
    lines.append(f"\n👉 Clave del día: {summary}")

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
        # Diariamente → solo hoy
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
