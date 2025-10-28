# scripts/earnings_weekly.py
import os, json, time, html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

# ========= ENV =========
BOT_TOKEN   = os.getenv("INVESTX_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")
FMP_KEY     = os.getenv("FMP_API_KEY")  # obligatorio
LOCAL_TZ    = ZoneInfo(os.getenv("LOCAL_TZ","Europe/Madrid"))

# Publicar solo lunes salvo fuerza
EARNINGS_FORCE = (os.getenv("EARNINGS_FORCE","0").lower() in {"1","true","yes"})
WL_ICON        = os.getenv("WL_MARK_ICON","⭐")
ENRICH_NAMES   = (os.getenv("ENRICH_NAMES","1").lower() in {"1","true","yes"})
ENRICH_ONLY_WL = (os.getenv("ENRICH_WATCHLIST_ONLY","0").lower() in {"1","true","yes"})
SLOW_MS        = int(os.getenv("ENRICH_SLOWDOWN_MS","150"))

WATCHLIST = [s.strip().upper() for s in os.getenv("WATCHLIST","").split(",") if s.strip()]

LOCK_PATH = "/tmp/investx_earnings_week.lock"
CACHE_PATH = "/tmp/investx_company_cache.json"

# ========= HTTP =========
S = requests.Session()
S.headers.update({"User-Agent":"InvestX-EarningsBot/1.2"})

def fmp_get(url, params):
    """GET a FMP with apikey appended; fail soft."""
    q = dict(params or {})
    q["apikey"] = FMP_KEY
    r = S.get(url, params=q, timeout=20)
    if r.status_code == 429:
        raise RuntimeError("FMP rate-limit 429")
    r.raise_for_status()
    return r.json()

# ========= UTIL =========
def send(text):
    if not BOT_TOKEN or not CHAT_ID: return
    S.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
           data={"chat_id":CHAT_ID,"parse_mode":"HTML","disable_web_page_preview":True,"text":text},
           timeout=20)

def esc(s): return html.escape(s or "", quote=False)

def load_cache():
    try:
        with open(CACHE_PATH,"r") as f: return json.load(f)
    except: return {}

def save_cache(d):
    try:
        with open(CACHE_PATH,"w") as f: json.dump(d,f)
    except: pass

def is_same_week_mark():
    try:
        with open(LOCK_PATH,"r") as f:
            return f.read().strip() == datetime.now(LOCAL_TZ).strftime("%G-W%V")
    except: return False

def write_week_mark():
    try:
        with open(LOCK_PATH,"w") as f:
            f.write(datetime.now(LOCAL_TZ).strftime("%G-W%V"))
    except: pass

# ========= DATA =========
def week_range_local():
    now = datetime.now(LOCAL_TZ)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0,minute=0,second=0,microsecond=0)
    sunday = monday + timedelta(days=6, hours=23, minutes=59)
    # acolchado ±1 día para no perder nada por husos
    return (monday - timedelta(days=1)).date(), (sunday + timedelta(days=1)).date()

def fetch_week_calendar():
    start, end = week_range_local()
    # endpoint estable (free): NO nombre ni hora, pero fiable
    url = "https://financialmodelingprep.com/stable/earnings-calendar"
    js = fmp_get(url, {"from": str(start), "to": str(end)})
    # normaliza
    out = []
    for it in js:
        sym = (it.get("symbol") or "").upper()
        dt  = it.get("date")
        if not sym or not dt: continue
        out.append({"symbol": sym, "date": dt})
    return out

def enrich_names(rows):
    if not ENRICH_NAMES: return {}
    cache = load_cache()
    names = {}
    for r in rows:
        sym = r["symbol"]
        if ENRICH_ONLY_WL and WATCHLIST and sym not in WATCHLIST:
            continue
        if sym in cache:
            names[sym] = cache[sym]; continue
        # FMP estable search por símbolo exacto
        try:
            js = fmp_get("https://financialmodelingprep.com/stable/search", {"query": sym, "limit": 5})
            # escoger coincidencia exacta de símbolo si existe
            name = None
            for cand in js:
                if (cand.get("symbol") or "").upper() == sym:
                    name = cand.get("name"); break
            if not name and js: name = js[0].get("name")
            if name:
                cache[sym] = name
                names[sym] = name
                time.sleep(SLOW_MS/1000.0)
        except Exception:
            pass
    save_cache(cache)
    return names

def enrich_times(rows, start, end):
    """Intenta obtener hora (BMO/AMC/TAS) si el plan lo permite (v3). Silencioso si 401."""
    times = {}
    for r in rows:
        sym = r["symbol"]
        if ENRICH_ONLY_WL and WATCHLIST and sym not in WATCHLIST:
            continue
        try:
            js = fmp_get("https://financialmodelingprep.com/api/v3/earning_calendar",
                         {"symbol": sym, "from": str(start), "to": str(end)})
        except requests.HTTPError as e:
            # si tu plan no lo permite, nos vamos sin hora
            if e.response is not None and e.response.status_code in (401,403,404): return {}
            else: continue
        except Exception:
            continue
        # buscar el item con la fecha exacta
        want = r["date"]
        for it in js or []:
            if it.get("date") == want:
                tm = (it.get("time") or "").strip().upper()
                # mapeo a etiquetas claras en ES si viene
                if tm in {"BMO","AM","B","PRE-MARKET"}: txt = "🕒 pre-market"
                elif tm in {"AMC","PM","A","AFTER-MARKET","POST-MARKET"}: txt = "🕒 after-hours"
                else: txt = None
                if txt: times[(sym, want)] = txt
                break
        time.sleep(SLOW_MS/1000.0)
    return times

# ========= RENDER =========
DIAS_ES = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
def day_es(d: datetime): return f"{DIAS_ES[d.weekday()]} {d:%Y-%m-%d}"

def build_text(rows, names, times):
    # agrupar por día
    by_day = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r["symbol"])
    # ordenar días
    days = sorted(by_day.keys())
    # encabezado
    s_local, e_local = week_range_local()
    head = (
        "📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>\n"
        f"Semana: <b>{s_local + timedelta(days=1)}</b> → <b>{e_local - timedelta(days=1)}</b>\n\n"
        "<b>Agenda por día:</b>\n"
    )
    lines = [head]
    for d in days:
        dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
        lines.append(f"\n<u>{esc(day_es(dt))}</u>")
        for sym in sorted(by_day[d]):
            star = WL_ICON if (WATCHLIST and sym in WATCHLIST) else "•"
            name = names.get(sym, "")
            time_txt = times.get((sym, d), "")
            tail = f" — {esc(name)}" if name else ""
            when = f"  {time_txt}" if time_txt else ""
            lines.append(f"{star} <b>{esc(sym)}</b>{tail}{when}")
    return "\n".join(lines)

# ========= MAIN =========
def main():
    now = datetime.now(LOCAL_TZ)
    if not EARNINGS_FORCE and now.weekday()!=0:
        return  # solo lunes

    # lock semanal: no repetir
    if not EARNINGS_FORCE and is_same_week_mark():
        return

    base = fetch_week_calendar()
    if not base:
        send("📅 <b>EARNINGS WEEKLY PREVIEW</b>\nNo hay publicaciones relevantes en la semana seleccionada.")
        write_week_mark()
        return

    start, end = week_range_local()
    names = enrich_names(base)
    times = enrich_times(base, start, end)  # si tu plan no lo permite, simplemente no pondrá hora

    text = build_text(base, names, times)
    send(text)
    write_week_mark()

if __name__ == "__main__":
    if not FMP_KEY: raise SystemExit("Falta FMP_API_KEY")
    if not BOT_TOKEN or not CHAT_ID: raise SystemExit("Faltan INVESTX_TOKEN/CHAT_ID")
    main()
