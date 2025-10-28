# scripts/earnings_weekly.py
import os, json, time, html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

# ========= ENV (mínimas) =========
BOT_TOKEN   = os.getenv("INVESTX_TOKEN")
CHAT_ID     = os.getenv("CHAT_ID")
FMP_KEY     = os.getenv("FMP_API_KEY")
LOCAL_TZ    = ZoneInfo(os.getenv("LOCAL_TZ","Europe/Madrid"))

# Ejecución
EARNINGS_FORCE = os.getenv("EARNINGS_FORCE","0").lower() in {"1","true","yes"}
PAD_DAYS       = int(os.getenv("EARNINGS_PAD_DAYS","2"))     # acolchado ±2 días alrededor de la semana
SHOW_HEADER    = os.getenv("SHOW_HEADER","1").lower() in {"1","true","yes"}

# Enriquecidos
ENRICH_NAMES     = os.getenv("ENRICH_NAMES","1").lower() in {"1","true","yes"}
ENRICH_ONLY_WL   = os.getenv("ENRICH_WATCHLIST_ONLY","0").lower() in {"1","true","yes"}
SLOW_MS          = int(os.getenv("ENRICH_SLOWDOWN_MS","100"))

# Watchlist
WATCHLIST = [t.strip().upper() for t in os.getenv("WATCHLIST","").split(",") if t.strip()]
WL_ICON = "⭐"   # <— icono fijo (no por variable)

# Ficheros auxiliares
LOCK_PATH  = "/tmp/investx_earnings_week.lock"   # marca “ya publicado esta semana”
CACHE_PATH = "/tmp/investx_company_cache.json"   # caché de nombres

# ========= HTTP =========
S = requests.Session()
S.headers.update({"User-Agent":"InvestX-EarningsBot/1.4"})

def fmp_get(url, params):
    q = dict(params or {})
    q["apikey"] = FMP_KEY
    r = S.get(url, params=q, timeout=20)
    if r.status_code == 429:
        raise RuntimeError("FMP rate-limit 429")
    r.raise_for_status()
    return r.json()

# ========= UTIL =========
def esc(s): return html.escape(s or "", quote=False)

def send(text):
    if not BOT_TOKEN or not CHAT_ID: return
    S.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id":CHAT_ID, "parse_mode":"HTML", "disable_web_page_preview":True, "text":text},
        timeout=20
    )

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

# ========= RANGO DE SEMANA (con acolchado) =========
def week_range_local_with_pad():
    now = datetime.now(LOCAL_TZ)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=6, hours=23, minutes=59)
    return (monday - timedelta(days=PAD_DAYS)).date(), (sunday + timedelta(days=PAD_DAYS)).date()

# ========= FMP: calendario =========
def fetch_week_calendar():
    start, end = week_range_local_with_pad()
    js = fmp_get("https://financialmodelingprep.com/stable/earnings-calendar",
                 {"from": str(start), "to": str(end)})
    rows = []
    for it in js:
        sym = (it.get("symbol") or "").upper().strip()
        dt  = (it.get("date") or "").strip()
        if not sym or not dt: continue
        rows.append({"symbol": sym, "date": dt})
    # dedupe
    seen = set(); out = []
    for r in rows:
        k = (r["symbol"], r["date"])
        if k in seen: continue
        seen.add(k); out.append(r)
    return out

# ========= Enriquecido de nombres (doble fallback + caché) =========
def get_company_name(sym: str):
    cache = load_cache()
    if sym in cache:
        return cache[sym]
    try:
        # A) /stable/search
        js = fmp_get("https://financialmodelingprep.com/stable/search",
                     {"query": sym, "limit": 5})
        name = None
        for row in js or []:
            if (row.get("symbol") or "").upper() == sym:
                name = row.get("name"); break
        if not name and js:
            name = js[0].get("name")
        # B) /stable/search-symbol (fallback)
        if not name:
            js2 = fmp_get("https://financialmodelingprep.com/stable/search-symbol",
                          {"query": sym, "limit": 5})
            for row in js2 or []:
                if (row.get("symbol") or "").upper() == sym:
                    name = row.get("name"); break
            if not name and js2:
                name = js2[0].get("name")
        if name:
            cache[sym] = name
            save_cache(cache)
            time.sleep(SLOW_MS/1000.0)
            return name
    except Exception as e:
        print("[get_company_name] error", sym, e)
    return None

def enrich_names(rows):
    if not ENRICH_NAMES:
        return {}
    names = {}
    for sym in sorted({r["symbol"] for r in rows}):
        if ENRICH_ONLY_WL and WATCHLIST and sym not in WATCHLIST:
            continue
        nm = get_company_name(sym)
        if nm:
            names[sym] = nm
    return names

# ========= Render de mensaje =========
DIAS_ES = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
def day_es(d: datetime): return f"{DIAS_ES[d.weekday()]} {d:%Y-%m-%d}"

def build_text(rows, names):
    # Agrupar por fecha exacta
    day_map = {}
    for r in rows:
        day_map.setdefault(r["date"], []).append(r["symbol"])
    days = sorted(day_map.keys())

    # Cabecera
    head = ""
    if SHOW_HEADER:
        s_pad, e_pad = week_range_local_with_pad()
        s_real = (datetime.strptime(str(s_pad), "%Y-%m-%d") + timedelta(days=PAD_DAYS)).date()
        e_real = (datetime.strptime(str(e_pad), "%Y-%m-%d") - timedelta(days=PAD_DAYS)).date()
        head = ("📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>\n"
                f"Semana: <b>{s_real}</b> → <b>{e_real}</b>\n\n"
                "<b>Agenda por día:</b>\n")

    lines = [head] if head else []
    for d in days:
        dtloc = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
        lines.append(f"\n<u>{esc(day_es(dtloc))}</u>")
        for sym in sorted(day_map[d]):
            star = WL_ICON if (WATCHLIST and sym in WATCHLIST) else "•"
            nm = names.get(sym, "")
            tail = f" — {esc(nm)}" if nm else ""
            lines.append(f"{star} <b>{esc(sym)}</b>{tail}")
    return "\n".join(lines).strip()

# ========= MAIN =========
def main():
    if not FMP_KEY: raise SystemExit("Falta FMP_API_KEY")
    if not BOT_TOKEN or not CHAT_ID: raise SystemExit("Faltan INVESTX_TOKEN/CHAT_ID")

    now = datetime.now(LOCAL_TZ)
    if not EARNINGS_FORCE and now.weekday() != 0:   # solo lunes
        print("[earnings] skip (no es lunes y FORCE=0)")
        return
    if not EARNINGS_FORCE and is_same_week_mark():
        print("[earnings] skip (ya publicado esta semana)")
        return

    rows = fetch_week_calendar()
    if not rows:
        send("📅 <b>EARNINGS WEEKLY PREVIEW</b>\nNo hay publicaciones en la semana seleccionada.")
        write_week_mark()
        return

    names = enrich_names(rows)
    text  = build_text(rows, names)
    send(text)
    write_week_mark()
    print(f"[earnings] sent OK | rows={len(rows)} | names={len(names)}")

if __name__ == "__main__":
    main()
