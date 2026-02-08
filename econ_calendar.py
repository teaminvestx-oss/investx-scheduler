# === econ_calendar.py ===
# Calendario económico USA (FMP - endpoint STABLE)
# Diseñado para InvestX / Render cron
# - 1 envío diario
# - Filtro USA
# - Manejo robusto de errores (reintentos)
# - Formato consistente con el resto del sistema

import os
import json
import logging
import time as _time
import random as _random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from utils import send_telegram_message

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

STATE_FILE = "econ_calendar_state.json"
TZ = ZoneInfo("Europe/Madrid")


# --------------------------------------------------
# STATE (para evitar duplicados diarios)
# --------------------------------------------------
def _load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(d):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def _already_sent_today(today_str: str) -> bool:
    st = _load_state()
    return st.get("last_sent") == today_str


def _mark_sent(today_str: str):
    st = _load_state()
    st["last_sent"] = today_str
    _save_state(st)


# --------------------------------------------------
# REQUEST FMP (STABLE)
# --------------------------------------------------
def _safe_request(start: datetime, end: datetime) -> pd.DataFrame:
    if end <= start:
        end = start + timedelta(days=1)

    api_key = os.getenv("FMP_API_KEY", "").strip()
    if not api_key:
        logger.error("[econ] FMP_API_KEY no configurada")
        return pd.DataFrame()

    f = start.strftime("%Y-%m-%d")
    t = end.strftime("%Y-%m-%d")

    url = "https://financialmodelingprep.com/stable/economic-calendar"

    df = None
    last_err = None

    logger.info(f"[econ] FMP(STABLE) request from={f} to={t}")

    for attempt in range(3):
        try:
            params = {
                "from": f,
                "to": t,
                "apikey": api_key,
            }

            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()

            data = r.json()
            if not isinstance(data, list):
                data = []

            logger.info(f"[econ] attempt {attempt+1}/3 -> items={len(data)}")

            df = pd.DataFrame(data) if data else pd.DataFrame()
            if not df.empty:
                break

        except Exception as e:
            last_err = e
            logger.error(f"[econ] FMP exception attempt {attempt+1}/3: {e}")

        _time.sleep(0.8 + _random.random() * 0.8)

    if df is None or df.empty:
        logger.error(f"[econ] FMP returned EMPTY after retries: {last_err}")
        return pd.DataFrame()

    return df


# --------------------------------------------------
# NORMALIZACIÓN Y FILTROS
# --------------------------------------------------
def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    colmap = {
        "date": "date",
        "event": "event",
        "country": "country",
        "actual": "actual",
        "previous": "previous",
        "forecast": "forecast",
        "estimate": "forecast",
        "impact": "importance",
        "importance": "importance",
    }

    for src, dst in colmap.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]

    for col in ["date", "time", "event", "importance", "actual", "forecast", "previous", "country"]:
        if col not in df.columns:
            df[col] = ""

    def _split_date_time(x):
        s = str(x).strip()
        if not s:
            return "", "00:00"
        try:
            dt = pd.to_datetime(s, errors="coerce")
            if pd.isna(dt):
                return s[:10], "00:00"
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
        except Exception:
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
        s = str(x).strip().lower()
        if s in ["", "all day", "tentative", "tbd", "--:--", "na", "n/a", "null", "none", "nan"]:
            return "00:00"
        return s[:5]

    df["time"] = df["time"].apply(_clean_time)

    # Filtro USA
    c = df["country"].astype(str).str.lower()
    us_mask = (
        c.isin(["united states", "united states of america", "us", "usa"])
        | c.str.contains("united states", na=False)
    )
    df = df[us_mask]

    df["datetime"] = pd.to_datetime(
        df["date"].astype(str) + " " + df["time"].astype(str),
        errors="coerce"
    )

    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    return df


# --------------------------------------------------
# FORMATO MENSAJE
# --------------------------------------------------
def _build_message(df: pd.DataFrame, day: datetime) -> str:
    title = f"📅 Calendario económico — {day.strftime('%a %d/%m')}\n\n"

    if df.empty:
        return title + "Hoy no hay datos macro relevantes en EE. UU."

    lines = []
    for _, r in df.iterrows():
        t = r["time"]
        ev = r["event"]
        imp = str(r.get("importance", "")).lower()

        if "high" in imp or "3" in imp:
            dot = "🔴"
        elif "medium" in imp or "2" in imp:
            dot = "🟠"
        else:
            dot = "🟡"

        lines.append(f"{dot} {t} — {ev}")

    return title + "\n".join(lines)


# --------------------------------------------------
# ENTRY POINT
# --------------------------------------------------
def run_econ_calendar(force: bool = False):
    now = datetime.now(TZ)
    today_str = now.strftime("%Y-%m-%d")

    if not force and _already_sent_today(today_str):
        logger.info("[econ] Ya enviado hoy -> skip")
        return

    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    logger.info("[econ] Ejecutando calendario económico")

    raw = _safe_request(start, end)
    df = _normalize(raw)

    msg = _build_message(df, now)
    send_telegram_message(msg)

    _mark_sent(today_str)
    logger.info("[econ] Calendario económico enviado correctamente")
