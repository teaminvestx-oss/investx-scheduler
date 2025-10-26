# earnings_weekly.py — InvestX
# Agenda semanal completa + ⭐ watchlist + nombre empresa + hora España (sin "TBD")
# - Fuente base: /stable/earnings-calendar (1 sola llamada)
# - Enriquecimiento SOLO watchlist (por defecto) con /api/v3/profile y /api/v3/earning_calendar
# - Conversión horaria ET -> Europe/Madrid por fecha (maneja DST)
# - Publica 1 vez cada lunes (ventana configurable) con lock; FORCE=1 ignora ventana/lock

import os, re, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Tuple
import requests

# ========= ENV =========
CHAT_ID    = os.getenv("CHAT_ID", "").strip()
BOT_TOKEN  = os.getenv("INVESTX_TOKEN", "").strip()
_raw_key   = (os.getenv("FMP_API_KEY") or "").strip()
FMP_API_KEY = "".join(ch for ch in _raw_key if ch.isalnum())

LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))
NY_TZ    = ZoneInfo("America/New_York")

# Ventana del lunes (hora local)
H1 = int(os.getenv("EARNINGS_MORNING_FROM_H", "12"))
H2 = int(os.getenv("EARNINGS_MORNING_TO_H",   "14"))  # inclusivo

FORCE = os.getenv("EARNINGS_FORCE", "0").lower() in {"1","true","yes","y"}

# Enriquecer solo watchlist (para ahorrar llamadas)
ENRICH_WATCHLIST_ONLY = os.getenv("ENRICH_WATCHLIST_ONLY", "1").lower() in {"1","true","yes","y"}
ENRICH_SLOWDOWN_MS    = int(os.getenv("ENRICH_SLOWDOWN_MS", "150"))  # delay entre llamadas v3

# Watchlist
WATCHLIST_PRIORITY  = [s.strip().upper() for s in (os.getenv("WATCHLIST_PRIORITY") or "").replace(";", ",").split(",") if s.strip()]
WATCHLIST_SECONDARY = [s.strip().upper() for s in (os.getenv("WATCHLIST_SECONDARY") or "").replace(";", ",").split(",") if s.strip()]
WL_SET = set(WATCHLIST_PRIORITY + WATCHLIST_SECONDARY)

# Candado semanal
LOCK_PATH = os.getenv("EARNINGS_LOCK_PATH", "/tmp/investx_earnings.lock")

# Endpoints FMP
FMP_STABLE_CAL = "https://financialmodelingprep.com/stable/earnings-calendar"
FMP_V3_PROFILE = "https://financialmodelingprep.com/api/v3/profile"
FMP_V3_CAL     = "https://financialmodelingprep.com/api/v3/earning_calendar"

# ========= TELEGRAM =========
def _post(text: str):
    if not (BOT_TOKEN and CHAT_ID): return
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30
    ).raise_for_status()

# ========= TIEMPO / RANGO =========
def is_run_window(now_local: datetime) -> bool:
    return (now_local.weekday() == 0) and (H1 <= now_local.hour <= H2)

def monday_to_friday_range(today_local: datetime) -> Tuple[str, str]:
    d = today_local.date()
    monday = d - timedelta(days=d.weekday())
    friday = monday + timedelta(days=4)
    return monday.isoformat(), friday.isoformat()

def current_iso_week_tag(now_local: datetime) -> str:
    y, w, _ = now_local.isocalendar()
    return f"{y}-W{w:02d}"

def read_lock() -> str | None:
    try:
        with open(LOCK_PATH, "r", encoding="utf-8") as f:
            return (f.read() or "").strip()
    except Exception:
        return None

def write_lock(tag: str) -> None:
    try:
        with open(LOCK_PATH, "w", encoding="utf-8") as f:
            f.write(tag)
    except Exception:
        pass

# ========= FETCH BASE (stable) =========
def fetch_base(start: str, end: str) -> List[Dict]:
    if not FMP_API_KEY: return []
    r = requests.get(FMP_STABLE_CAL, params={"from": start, "to": end, "apikey": FMP_API_KEY}, timeout=30)
    try: r.raise_for_status()
    except requests.HTTPError: return []
    js = r.json()
    if not isinstance(js, list): return []
    out=[]
    for d in js:
        out.append({
            "symbol": (d.get("symbol") or "").upper(),
            "company": (d.get("company") or d.get("companyName") or d.get("name") or "").strip(),
            "date": d.get("date") or d.get("dateCalendar") or "",
            "time_raw": normalize_time_raw(d.get("time") or d.get("hour") or ""),
        })
    return out

# ========= ENRIQUECIMIENTO (v3) =========
_name_cache: Dict[str, str] = {}
_time_cache: Dict[Tuple[str,str], str] = {}  # (symbol, date) -> time_raw

def enrich_symbol_name(sym: str) -> str | None:
    """/api/v3/profile/{sym} -> company name"""
    if sym in _name_cache: return _name_cache[sym]
    if not FMP_API_KEY: return None
    url = f"{FMP_V3_PROFILE}/{sym}"
    r = requests.get(url, params={"apikey": FMP_API_KEY}, timeout=30)
    try: r.raise_for_status()
    except requests.HTTPError: return None
    js = r.json()
    name = None
    if isinstance(js, list) and js:
        name = (js[0].get("companyName") or js[0].get("company") or js[0].get("name") or "").strip() or None
    _name_cache[sym] = name or ""
    return name

def enrich_symbol_time(sym: str, date_str: str) -> str | None:
    """/api/v3/earning_calendar?symbol=... -> time/time_raw para ese símbolo; toma el evento con misma fecha si existe, o el primero."""
    key = (sym, date_str)
    if key in _time_cache: return _time_cache[key] or None
    if not FMP_API_KEY: return None
    r = requests.get(FMP_V3_CAL, params={"symbol": sym, "from": date_str, "to": date_str, "apikey": FMP_API_KEY}, timeout=30)
    try: r.raise_for_status()
    except requests.HTTPError:
        _time_cache[key] = ""
        return None
    js = r.json()
    traw = None
    if isinstance(js, list) and js:
        # buscar exacta coincidencia de fecha; si no, coger el primer registro
        rec = None
        for x in js:
            if (x.get("date") or x.get("dateCalendar")) == date_str:
                rec = x; break
        if rec is None: rec = js[0]
        traw = normalize_time_raw(rec.get("time") or rec.get("hour") or "")
    _time_cache[key] = traw or ""
    return traw

# ========= HORA (ET -> Madrid) =========
_TZ_TOKENS = re.compile(r"\b(edt|est|et|eastern|time|zone)\b", re.IGNORECASE)
def normalize_time_raw(raw: str) -> str:
    t = (raw or "").strip()
    if not t: return ""
    t = _TZ_TOKENS.sub("", t)
    t = t.replace("-", " ").replace("_", " ").lower()
    t = " ".join(t.split())
    return t

_time_re = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)

def parse_et_time_to_local(date_str: str, time_raw: str) -> str | None:
    if not date_str or not time_raw: return None
    m = _time_re.search(time_raw)
    if not m: return None
    hour = int(m.group(1)); minute = int(m.group(2) or "0"); ampm = (m.group(3) or "").lower()
    if ampm:
        if ampm == "pm" and hour != 12: hour += 12
        if ampm == "am" and hour == 12: hour = 0
    try: d = datetime.fromisoformat(date_str)
    except Exception: return None
    dt_et = datetime(d.year, d.month, d.day, hour, minute, tzinfo=NY_TZ)
    dt_local = dt_et.astimezone(LOCAL_TZ)
    return f"{dt_local:%H:%M}h"

def fallback_session_time_to_local(date_str: str, time_raw: str) -> str | None:
    if not date_str: return None
    t = (time_raw or "")
    t_low = t.lower()
    if any(k in t_low for k in ("bmo","pre","premarket","pre market","before","before open","before market open")):
        base_h, base_m = 9, 30
    elif any(k in t_low for k in ("amc","post","postmarket","post market","after","after close","after market close","after hours","after hour")):
        base_h, base_m = 16, 0
    else:
        return None
    try: d = datetime.fromisoformat(date_str)
    except Exception: return None
    dt_et = datetime(d.year, d.month, d.day, base_h, base_m, tzinfo=NY_TZ)
    dt_local = dt_et.astimezone(LOCAL_TZ)
    return f"{dt_local:%H:%M}h"

def best_local_time(date_str: str, time_raw: str) -> str | None:
    if not time_raw: return None
    exact = parse_et_time_to_local(date_str, time_raw)
    if exact: return exact
    est = fallback_session_time_to_local(date_str, time_raw)
    if est: return est
    return None

# ========= DEDUP + AGRUPACIÓN =========
def score_time_info(date_str: str, time_raw: str) -> int:
    if not time_raw: return 0
    if _time_re.search(time_raw): return 2  # numérica exacta
    if fallback_session_time_to_local(date_str, time_raw): return 1
    return 0

def group_by_day(rows: List[Dict]) -> Dict[str, List[Dict]]:
    # dedup por (fecha, símbolo) quedándonos con el que tenga mejor time_raw
    per_day: Dict[str, Dict[str, Dict]] = {}
    for r in rows:
        d = r.get("date") or ""
        if not d: continue
        sym = r.get("symbol") or ""
        bucket = per_day.setdefault(d, {})
        if sym in bucket:
            old = bucket[sym]
            if score_time_info(d, r.get("time_raw") or "") > score_time_info(d, old.get("time_raw") or ""):
                bucket[sym] = r
        else:
            bucket[sym] = r
    grouped: Dict[str, List[Dict]] = {}
    for d, bysym in per_day.items():
        items = list(bysym.values())
        items.sort(key=lambda x: (x.get("symbol") or ""))
        grouped[d] = items
    # ordenar por fecha ascendente
    return dict(sorted(grouped.items(), key=lambda kv: kv[0]))

# ========= PRESENTACIÓN =========
def build_message(grouped: Dict[str, List[Dict]], start: str, end: str) -> str:
    lines: List[str] = []
    lines.append("🗓️ <b>EARNINGS WEEKLY PREVIEW | InvestX</b>")
    lines.append(f"Semana 📆 {start} → {end}\n")
    lines.append("<b>Agenda por día</b>:")

    DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    for d, items in grouped.items():
        dt = datetime.fromisoformat(d)
        lines.append(f"\n<u>{DIAS_ES[dt.weekday()]} {d}</u>")
        for it in items:
            sym   = (it.get("symbol") or "").upper()
            name  = it.get("company") or ""
            t_raw = it.get("time_raw") or ""
            t_loc = best_local_time(d, t_raw)  # None si no hay hora
            mark  = "⭐" if sym in WL_SET else "•"
            # Si no hay nombre, mostramos solo el ticker entre paréntesis
            if name:
                base = f"{mark} <b>{name}</b> ({sym})"
            else:
                base = f"{mark} <b>{sym}</b>"
            # Solo añadimos hora si existe
            line = f"{base}  🕓 {t_loc}" if t_loc else base
            lines.append(line)
    return "\n".join(lines)

def no_relevant_msg(start: str, end: str) -> str:
    return ("🗓️ <b>EARNINGS WEEKLY PREVIEW | InvestX</b>\n"
            f"Semana 📆 {start} → {end}\n\n"
            "⚠️ No hay resultados en el calendario para esta semana.")

# ========= MAIN =========
def main():
    if not (BOT_TOKEN and CHAT_ID and FMP_API_KEY):
        return

    now_local = datetime.now(LOCAL_TZ)

    # Solo lunes dentro de ventana, una vez por semana (salvo FORCE)
    if not FORCE:
        if not is_run_window(now_local):
            return
        week_tag = current_iso_week_tag(now_local)
        if read_lock() == week_tag:
            return

    start, end = monday_to_friday_range(now_local)

    # 1) Base una sola llamada
    data = fetch_base(start, end)
    if not data:
        _post(no_relevant_msg(start, end))
        if not FORCE and now_local.weekday() == 0:
            write_lock(current_iso_week_tag(now_local))
        return

    # 2) Enriquecer (por defecto solo watchlist)
    symbols_to_enrich = {r["symbol"] for r in data if (r["symbol"] in WL_SET)} if ENRICH_WATCHLIST_ONLY else {r["symbol"] for r in data}

    # Enriquecer nombres
    for sym in symbols_to_enrich:
        nm = enrich_symbol_name(sym)
        if nm:
            for r in data:
                if r["symbol"] == sym and not r.get("company"):
                    r["company"] = nm
        time.sleep(ENRICH_SLOWDOWN_MS / 1000.0)

    # Enriquecer horas (por símbolo y por cada fecha concreta en esa semana)
    # Mapa: symbol -> fechas presentes
    per_sym_dates: Dict[str, set] = {}
    for r in data:
        if r["symbol"] in symbols_to_enrich:
            per_sym_dates.setdefault(r["symbol"], set()).add(r["date"])

    for sym, dates in per_sym_dates.items():
        for d in sorted(dates):
            traw = enrich_symbol_time(sym, d)
            if traw:
                for r in data:
                    if r["symbol"] == sym and r["date"] == d and not r.get("time_raw"):
                        r["time_raw"] = traw
            time.sleep(ENRICH_SLOWDOWN_MS / 1000.0)

    # 3) Agrupar y presentar
    grouped = group_by_day(data)
    msg = build_message(grouped, start, end)
    _post(msg)

    # 4) Lock semanal
    if not FORCE and now_local.weekday() == 0:
        write_lock(current_iso_week_tag(now_local))

if __name__ == "__main__":
    main()
