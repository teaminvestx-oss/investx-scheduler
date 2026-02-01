# =====================================================
# econ_calendar.py — InvestX v4.3 FINAL
# Fuente: investpy
# =====================================================

import os
import json
import logging
import time as _time
import random as _random
from datetime import datetime, timedelta, time
from typing import List, Dict, Optional, Tuple

from zoneinfo import ZoneInfo  # <-- CLAVE: timezone correcta

import pandas as pd
import investpy

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_FILE = "econ_calendar_state.json"
DEFAULT_COUNTRY = "United States"
TZ = ZoneInfo("Europe/Madrid")

TRANSLATION_CACHE_FILE = "econ_translation_cache.json"


# ================================
# ESTADO DE ENVÍO
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
    return _load_state().get("sent_day") == day_key


def _mark_sent(day_key: str):
    st = _load_state()
    st["sent_day"] = day_key
    _save_state(st)


# ================================
# HELPERS
# ================================
def _clean_time(s: str) -> str:
    if s is None:
        return "00:00"
    low = str(s).strip().lower()
    if low in ["", "all day", "tentative", "tbd", "--:--", "na", "n/a"]:
        return "00:00"
    if "all day" in low or "tentative" in low:
        return "00:00"
    return str(s).strip()


def _source_unavailable_message(date_ref: datetime) -> str:
    fecha = date_ref.strftime("%a %d/%m").replace(".", "")
    return (
        f"📅 Calendario económico — {fecha}\n\n"
        "⚠️ Hoy no puedo obtener el calendario macro (fuente sin respuesta o bloqueada).\n"
        "En cuanto vuelva la conexión, lo publico con normalidad."
    )


# =====================================================
# INVESTPY ROBUSTO + FLAG ERROR REAL
# =====================================================
def _safe_request(country, start: datetime, end: datetime) -> Tuple[pd.DataFrame, bool]:
    if end <= start:
        end = start + timedelta(days=1)

    f = start.strftime("%d/%m/%Y")
    t = end.strftime("%d/%m/%Y")

    df: Optional[pd.DataFrame] = None
    last_err: Optional[Exception] = None

    for attempt in range(3):
        try:
            df = investpy.economic_calendar(from_date=f, to_date=t, countries=[country])
            if df is None:
                df = pd.DataFrame()
            if not df.empty:
                break
        except Exception as e:
            last_err = e
            logger.error(f"investpy error attempt {attempt+1}: {e}")

        _time.sleep(0.8 + _random.random() * 0.8)

    if df is None or df.empty:
        return pd.DataFrame(), last_err is not None

    for col in ["date", "time", "event", "importance", "actual", "forecast", "previous"]:
        if col not in df.columns:
            df[col] = ""

    df["time"] = df["time"].astype(str).apply(_clean_time)
    df["datetime"] = pd.to_datetime(
        df["date"].astype(str) + " " + df["time"].astype(str),
        errors="coerce"
    )

    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    return df, False


# =====================================================
# IMPORTANCIA
# =====================================================
def _stars(imp: str) -> int:
    if imp is None:
        return 1
    s = str(imp).lower()
    if "high" in s or "3" in s:
        return 3
    if "medium" in s or "2" in s:
        return 2
    if s:
        return 2
    return 1


# =====================================================
# FESTIVOS
# =====================================================
def _is_holiday(df: pd.DataFrame) -> bool:
    if df.empty:
        return False
    return df["event"].astype(str).str.lower().str.contains("holiday|thanksgiving").any()


# =====================================================
# PROCESADO EVENTOS
# =====================================================
def _process_events(df: pd.DataFrame) -> List[Dict]:
    if df.empty:
        return []

    df = df.copy()
    df["stars"] = df["importance"].apply(_stars)

    df2 = df[df["stars"] >= 2]
    if df2.empty:
        df2 = df.copy()
        df2["stars"] = 2

    df2 = df2.sort_values(["stars", "datetime"], ascending=[False, True]).head(6)
    df2 = df2.sort_values("datetime")

    return [
        {
            "datetime": r["datetime"],
            "event": r["event"],
            "stars": int(r["stars"]),
            "actual": r.get("actual", ""),
            "forecast": r.get("forecast", ""),
            "previous": r.get("previous", ""),
        }
        for _, r in df2.iterrows()
    ]


# =====================================================
# MACRO BRIEF (fallback incluido)
# =====================================================
def _make_macro_brief(events: List[Dict]) -> str:
    if not events:
        return ""

    if not os.getenv("OPENAI_API_KEY"):
        return (
            "Sesión marcada por referencias macro capaces de mover expectativas de tipos. "
            "Datos más fuertes presionarían a la renta variable y apoyarían al USD y los yields; "
            "lecturas más suaves aliviarían el riesgo."
        )

    return call_gpt_mini(
        "Eres analista macro senior. Escribe en español, 2–4 frases, estilo Bloomberg.",
        "Hoy el foco macro puede alterar expectativas de tipos y flujos de riesgo.",
        max_tokens=150
    ).strip()


# =====================================================
# MENSAJE FINAL
# =====================================================
def _build_message(events, date_ref: datetime) -> str:
    fecha = date_ref.strftime("%a %d/%m").replace(".", "")

    if events == "HOLIDAY":
        return f"📅 Calendario económico — {fecha}\n\n🎌 Hoy es festivo en EE. UU."

    if not events:
        return f"📅 Calendario económico — {fecha}\n\nHoy no hay datos macro relevantes en EE. UU."

    brief = _make_macro_brief(events)

    lines = [f"🧠 Macro Brief (EE. UU.) — {fecha}\n", brief, "\nAgenda clave:"]
    for e in events:
        hr = e["datetime"].strftime("%H:%M")
        stars = "⭐" * e["stars"]
        lines.append(f"{stars} {hr} — {e['event']}")

    return "\n".join(lines)


# =====================================================
# FUNCIÓN PRINCIPAL
# =====================================================
def run_econ_calendar(force: bool = False, force_tomorrow: bool = False):

    now = datetime.now(TZ)
    day_key = now.strftime("%Y-%m-%d")

    if not force and not force_tomorrow and _already_sent(day_key):
        return

    if force_tomorrow:
        start = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=TZ)
        end = start + timedelta(days=1)
        title_date = start
    else:
        start = datetime.combine(now.date(), time.min, tzinfo=TZ)
        end = start + timedelta(days=1)
        title_date = now

    df, had_error = _safe_request(DEFAULT_COUNTRY, start, end)

    if df.empty:
        msg = (
            _source_unavailable_message(title_date)
            if had_error
            else _build_message([], title_date)
        )
        send_telegram_message(msg)
        if not force and not force_tomorrow:
            _mark_sent(day_key)
        return

    if _is_holiday(df):
        send_telegram_message(_build_message("HOLIDAY", title_date))
    else:
        events = _process_events(df)
        send_telegram_message(_build_message(events, title_date))

    if not force and not force_tomorrow:
        _mark_sent(day_key)
