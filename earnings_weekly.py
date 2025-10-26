# earnings_weekly.py — InvestX (fix selección de símbolos + nombre estable)
import os, re, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Tuple
import requests

CHAT_ID    = os.getenv("CHAT_ID", "").strip()
BOT_TOKEN  = os.getenv("INVESTX_TOKEN", "").strip()
_raw_key   = (os.getenv("FMP_API_KEY") or "").strip()
FMP_API_KEY = "".join(ch for ch in _raw_key if ch.isalnum())

LOCAL_TZ = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))
NY_TZ    = ZoneInfo("America/New_York")

H1 = int(os.getenv("EARNINGS_MORNING_FROM_H", "12"))
H2 = int(os.getenv("EARNINGS_MORNING_TO_H",   "14"))
FORCE = os.getenv("EARNINGS_FORCE", "0").lower() in {"1","true","yes","y"}
DEBUG = os.getenv("EARNINGS_DEBUG", "0").lower() in {"1","true","yes","y"}

ENRICH_WATCHLIST_ONLY = os.getenv("ENRICH_WATCHLIST_ONLY", "1").lower() in {"1","true","yes","y"}
ENRICH_SLOWDOWN_MS    = int(os.getenv("ENRICH_SLOWDOWN_MS", "150"))

WATCHLIST_PRIORITY  = [s.strip().upper() for s in (os.getenv("WATCHLIST_PRIORITY") or "").replace(";", ",").split(",") if s.strip()]
WATCHLIST_SECONDARY = [s.strip().upper() for s in (os.getenv("WATCHLIST_SECONDARY") or "").replace(";", ",").split(",") if s.strip()]
WL_SET = set(WATCHLIST_PRIORITY + WATCHLIST_SECONDARY)

LOCK_PATH = os.getenv("EARNINGS_LOCK_PATH", "/tmp/investx_earnings.lock")

# Endpoints
FMP_STABLE_CAL  = "https://financialmodelingprep.com/stable/earnings-calendar"
FMP_STABLE_SYM  = "https://financialmodelingprep.com/stable/search-symbol"
FMP_STABLE_NAME = "https://financialmodelingprep.com/stable/search-name"
FMP_V3_PROFILE  = "https://financialmodelingprep.com/api/v3/profile"
FMP_V3_ECAL     = "https://financialmodelingprep.com/api/v3/earning_calendar"

def _post(text: str):
    if not (BOT_TOKEN and CHAT_ID): return
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30
    ).raise_for_status()

def is_run_window(now_local: datetime) -> bool:
    return (now_local.weekday() == 0) and (H1 <= now_local.hour <= H2)

def monday_to_friday_range(today_local: datetime):
    d = today_local.date()
    monday = d - timedelta(days=d.weekday())
    friday = monday + timedelta(days=4)
    return monday.isoformat(), friday.isoformat()

def current_iso_week_tag(now_local: datetime) -> str:
    y, w, _ = now_local.isocalendar()
    return f"{y}-W{w:02d}"

def read_lock():
    try:
        with open(LOCK_PATH, "r", encoding="utf-8") as f:
            return (f.read() or "").strip()
    except Exception:
        return None

def write_lock(tag: str):
    try:
        with open(LOCK_PATH, "w", encoding="utf-8") as f:
            f.write(tag)
    except Exception:
        pass

def normalize_time_raw(raw: str) -> str:
    if not raw: return ""
    t = raw.strip()
    t = re.sub(r"\b(edt|est|et|eastern|time|zone)\b", "", t, flags=re.I)
    t = t.replace("-", " ").replace("_", " ").lower()
    return " ".join(t.split())

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
            "symbol": (d.get("symbol") or "").strip().upper(),
            "company": (d.get("company") or d.get("companyName") or d.get("name") or "").strip(),
            "date": (d.get("date") or d.get("dateCalendar") or "").strip(),
            "time_raw": normalize_time_raw(d.get("time") or d.get("hour") or ""),
        })
    return out

def parse_company_map(env_val: str | None) -> dict[str, str]:
    out = {}
    if not env_val: return out
    for p in [x.strip() for x in env_val.split(";") if x.strip()]:
        if "=" in p:
            k, v = p.split("=", 1)
            k = k.strip().upper(); v = v.strip()
            if k and v: out[k] = v
    return out

COMPANY_MAP = parse_company_map(os.getenv("COMPANY_MAP"))

# ===== Nombre (estable primero) =====
_name_cache: Dict[str, str] = {}

def enrich_symbol_name(sym: str) -> str | None:
    if sym in COMPANY_MAP: return COMPANY_MAP[sym]
    if sym in _name_cache: return _name_cache[sym] or None

    # 1) stable/search-symbol
    try:
        r = requests.get(FMP_STABLE_SYM, params={"query": sym, "limit": 1}, timeout=30)
        r.raise_for_status()
        js = r.json()
        if isinstance(js, list) and js:
            name = (js[0].get("name") or js[0].get("companyName") or "").strip()
            if name:
                _name_cache[sym] = name
                return name
    except Exception:
        pass

    # 2) stable/search-name
    try:
        r = requests.get(FMP_STABLE_NAME, params={"query": sym, "limit": 1}, timeout=30)
        r.raise_for_status()
        js = r.json()
        if isinstance(js, list) and js:
            name = (js[0].get("name") or js[0].get("companyName") or "").strip()
            if name:
                _name_cache[sym] = name
                return name
    except Exception:
        pass

    # 3) v3/profile (fallback)
    try:
        r = requests.get(f"{FMP_V3_PROFILE}/{sym}", params={"apikey": FMP_API_KEY}, timeout=30)
        r.raise_for_status()
        js = r.json()
        if isinstance(js, list) and js:
            name = (js[0].get("companyName") or js[0].get("company") or js[0].get("name") or "").strip()
            if name:
                _name_cache[sym] = name
                return name
    except Exception:
        pass

    _name_cache[sym] = ""
    return None

# ===== Hora semanal =====
_time_cache: Dict[Tuple[str,str,str], Dict[str,str]] = {}

def enrich_symbol_times_for_week(sym: str, start: str, end: str) -> Dict[str, str]:
    key = (sym, start, end)
    if key in _time_cache: return _time_cache[key]
    out={}
    try:
        r = requests.get(FMP_V3_ECAL, params={"symbol": sym, "from": start, "to": end, "apikey": FMP_API_KEY}, timeout=30)
        r.raise_for_status()
        js = r.json()
        if isinstance(js, list):
            for rec in js:
                dt = (rec.get("date") or rec.get("dateCalendar") or "").strip()
                tr = normalize_time_raw(rec.get("time") or rec.get("hour") or "")
                if dt and tr and start <= dt <= end:
                    out[dt] = tr
    except Exception:
        pass
    _time_cache[key] = out
    return out

# ===== Horario ET->Madrid =====
_time_re = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)
def parse_et_time_to_local(date_str: str, time_raw: str) -> str | None:
    m = _time_re.search(time_raw or "")
    if not (date_str and m): return None
    h = int(m.group(1)); mnt = int(m.group(2) or "0"); ampm = (m.group(3) or "").lower()
    if ampm == "pm" and h != 12: h += 12
    if ampm == "am" and h == 12: h = 0
    try: d = datetime.fromisoformat(date_str)
    except Exception: return None
    dt_et = datetime(d.year, d.month, d.day, h, mnt, tzinfo=NY_TZ)
    return datetime.strftime(dt_et.astimezone(LOCAL_TZ), "%H:%Mh")

def fallback_session_time_to_local(date_str: str, time_raw: str) -> str | None:
    t = (time_raw or "").lower()
    base = None
    if any(k in t for k in ("bmo","pre","premarket","pre market","before","before open","before market open")):
        base = (9,30)
    elif any(k in t for k in ("amc","post","postmarket","post market","after","after close","after market close","after hours","after hour")):
        base = (16,0)
    if not (date_str and base): return None
    try: d = datetime.fromisoformat(date_str)
    except Exception: return None
    dt_et = datetime(d.year, d.month, d.day, base[0], base[1], tzinfo=NY_TZ)
    return datetime.strftime(dt_et.astimezone(LOCAL_TZ), "%H:%Mh")

def best_local_time(date_str: str, time_raw: str) -> str | None:
    return parse_et_time_to_local(date_str, time_raw) or fallback_session_time_to_local(date_str, time_raw)

# ===== Dedup/Agrupar =====
def score_time(date_str: str, time_raw: str) -> int:
    if not time_raw: return 0
    if _time_re.search(time_raw): return 2
    if fallback_session_time_to_local(date_str, time_raw): return 1
    return 0

def group_by_day(rows: List[Dict]) -> Dict[str, List[Dict]]:
    per_day: Dict[str, Dict[str, Dict]] = {}
    for r in rows:
        d = r.get("date") or ""
        if not d: continue
        sym = (r.get("symbol") or "").strip().upper()
        bucket = per_day.setdefault(d, {})
        if sym in bucket:
            old = bucket[sym]
            if score_time(d, r.get("time_raw") or "") > score_time(d, old.get("time_raw") or ""):
                bucket[sym] = r
        else:
            r["symbol"] = sym  # normaliza
            bucket[sym] = r
    grouped = {}
    for d, bysym in per_day.items():
        items = list(bysym.values())
        items.sort(key=lambda x: x.get("symbol") or "")
        grouped[d] = items
    return dict(sorted(grouped.items(), key=lambda kv: kv[0]))

# ===== Presentación =====
def build_message(grouped: Dict[str, List[Dict]], start: str, end: str) -> str:
    lines = []
    lines.append("🗓️ <b>EARNINGS WEEKLY PREVIEW | InvestX</b>")
    lines.append(f"Semana 📆 {start} → {end}\n")
    lines.append("<b>Agenda por día</b>:")
    DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

    for d, items in grouped.items():
        dt = datetime.fromisoformat(d)
        lines.append(f"\n<u>{DIAS_ES[dt.weekday()]} {d}</u>")
        for it in items:
            sym  = (it.get("symbol") or "").upper()
            name = (it.get("company") or "").strip()
            tloc = best_local_time(d, it.get("time_raw") or "")
            mark = "⭐" if sym in WL_SET else "•"
            base = f"{mark} <b>{name}</b> ({sym})" if name and name.upper()!=sym else f"{mark} <b>{sym}</b>"
            lines.append(f"{base}  🕓 {tloc}" if tloc else base)
    return "\n".join(lines)

def no_relevant_msg(start: str, end: str) -> str:
    return ("🗓️ <b>EARNINGS WEEKLY PREVIEW | InvestX</b>\n"
            f"Semana 📆 {start} → {end}\n\n"
            "⚠️ No hay resultados en el calendario para esta semana.")

# ===== MAIN =====
def main():
    if not (BOT_TOKEN and CHAT_ID and FMP_API_KEY): return
    now_local = datetime.now(LOCAL_TZ)

    if not FORCE:
        if not is_run_window(now_local): return
        if read_lock() == current_iso_week_tag(now_local): return

    start, end = monday_to_friday_range(now_local)
    data = fetch_base(start, end)
    if not data:
        _post(no_relevant_msg(start, end))
        if not FORCE and now_local.weekday()==0:
            write_lock(current_iso_week_tag(now_local))
        return

    # === selección de símbolos a enriquecer ===
    all_syms = { (r["symbol"] or "").strip().upper() for r in data }
    wl_syms  = all_syms & WL_SET
    symbols  = wl_syms if ENRICH_WATCHLIST_ONLY else all_syms
    if not symbols:
        # fallback: si la intersección quedó vacía, enriquece todos
        symbols = all_syms

    # NOMBRES
    n_fixed = 0
    for sym in sorted(symbols):
        nm = enrich_symbol_name(sym)
        if nm:
            for r in data:
                if (r["symbol"] or "").upper() == sym:
                    cur = (r.get("company") or "")
                    if (not cur) or (cur.upper()==sym) or (len(cur)<=3):
                        r["company"] = nm
                        n_fixed += 1
        time.sleep(ENRICH_SLOWDOWN_MS/1000.0)

    # HORAS (por semana, 1 llamada/símbolo)
    t_fixed = 0
    for sym in sorted(symbols):
        times_map = enrich_symbol_times_for_week(sym, start, end)
        if times_map:
            for r in data:
                if (r["symbol"] or "").upper()==sym and r["date"] in times_map and not r.get("time_raw"):
                    r["time_raw"] = times_map[r["date"]]
                    t_fixed += 1
        time.sleep(ENRICH_SLOWDOWN_MS/1000.0)

    grouped = group_by_day(data)
    _post(build_message(grouped, start, end))

    if DEBUG:
        _post(f"[earnings] symbols={len(symbols)} | names={n_fixed} times={t_fixed} | wl_only={ENRICH_WATCHLIST_ONLY}")

    if not FORCE and now_local.weekday()==0:
        write_lock(current_iso_week_tag(now_local))

if __name__ == "__main__":
    main()
