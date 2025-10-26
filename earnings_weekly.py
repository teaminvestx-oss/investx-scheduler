# earnings_weekly.py — InvestX (agenda completa + ⭐ watchlist + hora España)
# Fuente: FMP /stable/earnings-calendar
# Publica SOLO 1 vez cada lunes (ventana configurable). FORCE=1 ignora ventana/lock.
# Muestra TODA la agenda, marcando con ⭐ los tickers de tu watchlist.
# Hora: convierte ET -> Europe/Madrid según la FECHA concreta (maneja DST).

import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Tuple
import requests

# ========= ENV =========
CHAT_ID    = os.getenv("CHAT_ID", "").strip()
BOT_TOKEN  = os.getenv("INVESTX_TOKEN", "").strip()

# Sanitizar posible whitespace en la API key
_raw_key = (os.getenv("FMP_API_KEY") or "").strip()
FMP_API_KEY = "".join(ch for ch in _raw_key if ch.isalnum())
FMP_URL     = "https://financialmodelingprep.com/stable/earnings-calendar"

LOCAL_TZ  = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))
NY_TZ     = ZoneInfo("America/New_York")  # referencia ET

WATCHLIST_PRIORITY  = [s.strip().upper() for s in (os.getenv("WATCHLIST_PRIORITY") or "").replace(";", ",").split(",") if s.strip()]
WATCHLIST_SECONDARY = [s.strip().upper() for s in (os.getenv("WATCHLIST_SECONDARY") or "").replace(";", ",").split(",") if s.strip()]

H1 = int(os.getenv("EARNINGS_MORNING_FROM_H", "12"))
H2 = int(os.getenv("EARNINGS_MORNING_TO_H",   "14"))  # inclusivo

FORCE = os.getenv("EARNINGS_FORCE", "0").lower() in {"1","true","yes","y"}

# Candado semanal para no repetir
LOCK_PATH = os.getenv("EARNINGS_LOCK_PATH", "/tmp/investx_earnings.lock")

# ========= TELEGRAM =========
def _post(text: str):
    if not (BOT_TOKEN and CHAT_ID):
        return
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30
    ).raise_for_status()

# ========= TIEMPO / RANGO =========
def is_run_window(now_local: datetime) -> bool:
    # 0 = Lunes
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

# ========= FETCH =========
def fetch_earnings(start: str, end: str) -> List[Dict]:
    if not FMP_API_KEY:
        return []
    params = {"from": start, "to": end, "apikey": FMP_API_KEY}
    r = requests.get(FMP_URL, params=params, timeout=30)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        return []  # si 401/403/etc., no publicamos nada
    data = r.json()
    if not isinstance(data, list):
        return []
    # Normalizar campos mínimos
    out: List[Dict] = []
    for d in data:
        sym = (d.get("symbol") or "").upper()
        name = d.get("company") or d.get("companyName") or d.get("name") or sym
        out.append({
            "symbol": sym,
            "company": name,
            "date": d.get("date") or d.get("dateCalendar") or "",
            "time_raw": normalize_time_raw(d.get("time") or d.get("hour") or ""),
        })
    return out

# ========= HORA (ET -> Madrid) =========
_TZ_TOKENS = re.compile(r"\b(edt|est|et|eastern|time|zone)\b", re.IGNORECASE)

def normalize_time_raw(raw: str) -> str:
    t = (raw or "").strip()
    t = _TZ_TOKENS.sub("", t)  # quitar 'EST', 'ET', etc.
    t = t.replace("-", " ").replace("_", " ").lower()
    t = " ".join(t.split())
    return t

_time_re = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)

def parse_et_time_to_local(date_str: str, time_raw: str) -> str | None:
    """
    Intenta interpretar 'time_raw' como hora ET (numérica) en la fecha dada,
    y devuelve la hora en Madrid como 'HH:MMh'. Si no se puede, devuelve None.
    """
    if not date_str or not time_raw:
        return None
    m = _time_re.search(time_raw)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or "0")
    ampm = (m.group(3) or "").lower()

    if ampm in ("am", "pm"):
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0

    # construir dt en ET según la fecha concreta
    try:
        dt = datetime.fromisoformat(date_str)
    except Exception:
        return None
    dt_et = datetime(dt.year, dt.month, dt.day, hour, minute, tzinfo=NY_TZ)
    dt_local = dt_et.astimezone(LOCAL_TZ)
    return f"{dt_local:%H:%M}h"

def fallback_session_time_to_local(date_str: str, time_raw: str) -> str | None:
    """
    Si solo tenemos 'bmo'/'amc'/similares, usar 09:30 ET y 16:00 ET (NYSE) y convertir a Madrid.
    """
    if not date_str:
        return None
    t = (time_raw or "").lower()
    if any(k in t for k in ("bmo", "pre", "premarket", "pre market", "before", "before open", "before market open")):
        base_h, base_m = 9, 30
    elif any(k in t for k in ("amc", "post", "postmarket", "post market", "after", "after close", "after market close", "after hours", "after hour")):
        base_h, base_m = 16, 0
    else:
        return None
    try:
        dt = datetime.fromisoformat(date_str)
    except Exception:
        return None
    dt_et = datetime(dt.year, dt.month, dt.day, base_h, base_m, tzinfo=NY_TZ)
    dt_local = dt_et.astimezone(LOCAL_TZ)
    return f"{dt_local:%H:%M}h"

def best_local_time(date_str: str, time_raw: str) -> str:
    """
    Prioridad:
      1) hora exacta numérica -> ET->Madrid
      2) sesión (bmo/amc) -> hora estimada ET->Madrid
      3) TBD
    """
    if not time_raw:
        return "⏰ TBD"
    exact = parse_et_time_to_local(date_str, time_raw)
    if exact:
        return exact
    est = fallback_session_time_to_local(date_str, time_raw)
    if est:
        return est
    return "⏰ TBD"

# ========= DEDUP + AGRUPACIÓN =========
def prefer_better_time(existing: Dict, candidate: Dict) -> Dict:
    """
    Si hay duplicados del mismo (fecha, símbolo), preferimos el que tenga hora más informativa.
    Score: exacta(2) > estimada por sesión(1) > TBD(0)
    """
    def score(rec: Dict) -> int:
        t = best_local_time(rec.get("date") or "", rec.get("time_raw") or "")
        if t == "⏰ TBD":
            return 0
        # Si viene de parse numérico nos da exacta; si era solo sesión será estimada.
        # Heurística: si 'time_raw' tiene dígitos, la tratamos como exacta.
        return 2 if _time_re.search(rec.get("time_raw") or "") else 1

    return candidate if score(candidate) > score(existing) else existing

def group_by_day(rows: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Agrupa por fecha con deduplicado por (fecha, símbolo).
    Mantiene la mejor entrada por ticker en cada día.
    """
    per_day: Dict[str, Dict[str, Dict]] = {}
    for r in rows:
        d = r.get("date") or ""
        if not d:
            continue
        sym = r.get("symbol") or ""
        bucket = per_day.setdefault(d, {})
        if sym in bucket:
            bucket[sym] = prefer_better_time(bucket[sym], r)
        else:
            bucket[sym] = r

    # convertir a listas ordenadas por símbolo
    grouped: Dict[str, List[Dict]] = {}
    for d, by_sym in per_day.items():
        items = list(by_sym.values())
        items.sort(key=lambda x: (x.get("symbol") or ""))
        grouped[d] = items

    # ordenar por fecha ascendente
    return dict(sorted(grouped.items(), key=lambda kv: kv[0]))

# ========= PRESENTACIÓN =========
def in_watchlist(sym: str, pri: set, sec: set) -> bool:
    return (sym in pri) or (sym in sec)

def build_message(grouped: Dict[str, List[Dict]], start: str, end: str, pri: set, sec: set) -> str:
    lines: list[str] = []
    lines.append("📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>")
    lines.append(f"Semana 📆 {start} → {end}\n")
    lines.append("<b>Agenda por día</b>:")

    DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    for d, items in grouped.items():
        dt = datetime.fromisoformat(d)
        lines.append(f"\n<u>{DIAS_ES[dt.weekday()]} {d}</u>")
        for it in items:
            sym  = (it.get("symbol") or "").upper()
            name = it.get("company") or sym
            time_local = best_local_time(d, it.get("time_raw") or "")
            mark = "⭐" if in_watchlist(sym, pri, sec) else "•"
            # Ejemplo: ⭐ TSLA — Tesla Inc.  22:00h
            lines.append(f"{mark} <b>{sym}</b> — <i>{name}</i>  {time_local}")
    return "\n".join(lines)

def no_relevant_msg(start: str, end: str) -> str:
    return ("📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>\n"
            f"Semana 📆 {start} → {end}\n\n"
            "⚠️ No hay resultados en el calendario para esta semana.")

# ========= MAIN =========
def main():
    if not (BOT_TOKEN and CHAT_ID):
        return

    now_local = datetime.now(LOCAL_TZ)

    # Solo lunes, una vez, salvo FORCE
    if not FORCE:
        if not is_run_window(now_local):
            return
        week_tag = current_iso_week_tag(now_local)
        if read_lock() == week_tag:
            return

    start, end = monday_to_friday_range(now_local)
    data = fetch_earnings(start, end)

    grouped = group_by_day(data)
    if not grouped:
        _post(no_relevant_msg(start, end))
        if not FORCE and now_local.weekday() == 0:
            write_lock(current_iso_week_tag(now_local))
        return

    pri, sec = set(WATCHLIST_PRIORITY), set(WATCHLIST_SECONDARY)
    _post(build_message(grouped, start, end, pri, sec))

    if not FORCE and now_local.weekday() == 0:
        write_lock(current_iso_week_tag(now_local))

if __name__ == "__main__":
    main()
