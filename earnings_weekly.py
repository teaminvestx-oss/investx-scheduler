# earnings_weekly.py  — v2.1 (semana cerrada + nombre + estrella)
import os, json, html
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
import requests

# ===== Entorno =====
BOT_TOKEN = os.getenv("INVESTX_TOKEN")
CHAT_ID   = os.getenv("CHAT_ID")
FMP_KEY   = os.getenv("FMP_API_KEY")
LOCAL_TZ  = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))
EARNINGS_FORCE = os.getenv("EARNINGS_FORCE", "0").lower() in {"1","true","yes"}
WATCHLIST = [t.strip().upper() for t in os.getenv("WATCHLIST","").split(",") if t.strip()]

# Icono de watchlist embebido en código (no variable)
WL_ICON = "⭐"

# ===== Sesión HTTP =====
S = requests.Session()
S.headers.update({"User-Agent":"InvestX-EarningsBot/2.1"})

def fmp_get(url, params):
    q = dict(params or {})
    q["apikey"] = FMP_KEY
    r = S.get(url, params=q, timeout=20)
    if r.status_code == 429:
        raise RuntimeError("FMP rate-limit 429")
    r.raise_for_status()
    return r.json()

# ===== Utilidades =====
def esc(s): return html.escape(s or "", quote=False)

CACHE_PATH = "/tmp/investx_company_cache.json"
LOCK_PATH  = "/tmp/investx_earnings_week.lock"

def load_cache():
    try:
        with open(CACHE_PATH,"r") as f: return json.load(f)
    except: return {}

def save_cache(d):
    try:
        with open(CACHE_PATH,"w") as f: json.dump(d,f)
    except: pass

def week_window_local(today: date):
    """Lunes 00:00 a Domingo 23:59 de la semana del 'today' en TZ local."""
    monday = (datetime.combine(today, datetime.min.time(), LOCAL_TZ)
              - timedelta(days=today.weekday()))
    sunday = monday + timedelta(days=6)
    return monday.date(), sunday.date()

def current_week_key():
    d = datetime.now(LOCAL_TZ).date()
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"

def already_sent_this_week():
    try:
        with open(LOCK_PATH,"r") as f:
            return f.read().strip() == current_week_key()
    except: return False

def mark_sent_this_week():
    try:
        with open(LOCK_PATH,"w") as f: f.write(current_week_key())
    except: pass

def send(text: str):
    if not (BOT_TOKEN and CHAT_ID): return
    S.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
           data={"chat_id":CHAT_ID,"text":text,"parse_mode":"HTML","disable_web_page_preview":True},
           timeout=20)

# ===== Mapa local de nombres (extracto, añade los tuyos si quieres) =====
LOCAL_NAME_MAP = {
    "AAPL":"Apple Inc.", "MSFT":"Microsoft Corporation", "GOOGL":"Alphabet Inc.",
    "GOOG":"Alphabet Inc.", "AMZN":"Amazon.com, Inc.", "META":"Meta Platforms, Inc.",
    "NVDA":"NVIDIA Corporation", "TSLA":"Tesla, Inc.", "NFLX":"Netflix, Inc.",
    "ADBE":"Adobe Inc.", "INTC":"Intel Corporation", "AMD":"Advanced Micro Devices, Inc.",
    "ORCL":"Oracle Corporation", "CRM":"Salesforce, Inc.", "IBM":"International Business Machines Corporation",
    "SAP":"SAP SE", "SNOW":"Snowflake Inc.", "PANW":"Palo Alto Networks, Inc.",
    "CRWD":"CrowdStrike Holdings, Inc.", "ZS":"Zscaler, Inc.", "NOW":"ServiceNow, Inc.",
    "PLTR":"Palantir Technologies Inc.", "ABNB":"Airbnb, Inc.", "MELI":"MercadoLibre, Inc.",
    "RDDT":"Reddit, Inc.", "DUOL":"Duolingo, Inc.", "COIN":"Coinbase Global, Inc.",
    "PYPL":"PayPal Holdings, Inc.", "RBLX":"Roblox Corporation", "ROKU":"Roku, Inc.",
    "SPOT":"Spotify Technology S.A.", "ETSY":"Etsy, Inc.", "MSFT":"Microsoft Corporation",
    "GE":"General Electric Company", "GM":"General Motors Company", "F":"Ford Motor Company",
    "LMT":"Lockheed Martin Corporation", "BA":"The Boeing Company", "T":"AT&T Inc.",
    "VZ":"Verizon Communications Inc.", "SBUX":"Starbucks Corporation", "KO":"Coca-Cola Company",
    "PEP":"PepsiCo, Inc.", "WMT":"Walmart Inc.", "HD":"The Home Depot, Inc.",
    "NKE":"NIKE, Inc.", "ABBV":"AbbVie Inc.", "CVX":"Chevron Corporation", "XOM":"Exxon Mobil Corporation",
    "MSFT":"Microsoft Corporation", "AAL":"American Airlines Group Inc.", "MGM":"MGM Resorts International",
    # añade más si quieres...
}

# ===== FMP: calendario semanal =====
def fetch_week_calendar():
    today_local = datetime.now(LOCAL_TZ).date()
    start, end = week_window_local(today_local)
    js = fmp_get("https://financialmodelingprep.com/stable/earnings-calendar",
                 {"from": str(start), "to": str(end)})
    # Filtro duro: solo fechas dentro [start, end]
    rows = []
    for it in js:
        sym = (it.get("symbol") or "").upper().strip()
        ds  = (it.get("date") or "").strip()
        if not sym or not ds: continue
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (start <= d <= end):  # evita que se cuele el lunes siguiente
            continue
        rows.append({"symbol": sym, "date": d})
    # dedup
    seen=set(); out=[]
    for r in rows:
        k=(r["symbol"], r["date"])
        if k in seen: continue
        seen.add(k); out.append(r)
    return out, start, end

# ===== Resolver nombres con caché y fallback =====
def get_company_name(sym: str):
    cache = load_cache()
    if sym in cache: return cache[sym]
    name = LOCAL_NAME_MAP.get(sym)  # primero mapa local (rápido y gratis)
    if not name:
        try:
            # 1) /stable/search intenta por símbolo exacto
            js = fmp_get("https://financialmodelingprep.com/stable/search",
                         {"query": sym, "limit": 5})
            for row in js or []:
                if (row.get("symbol") or "").upper() == sym:
                    name = row.get("name"); break
            if not name and js:
                name = js[0].get("name")
            # 2) /stable/search-symbol como segundo intento
            if not name:
                js2 = fmp_get("https://financialmodelingprep.com/stable/search-symbol",
                              {"query": sym, "limit": 5})
                for row in js2 or []:
                    if (row.get("symbol") or "").upper() == sym:
                        name = row.get("name"); break
                if not name and js2:
                    name = js2[0].get("name")
        except Exception:
            pass
    if name:
        cache[sym] = name
        save_cache(cache)
    return name

def enrich_names(rows):
    uniq = sorted({r["symbol"] for r in rows})
    names={}
    for s in uniq:
        nm = get_company_name(s)
        if nm: names[s]=nm
    return names

# ===== Mensaje =====
DIAS_ES = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
def day_es(d: date):  # d es date (local ya)
    wd = DIAS_ES[d.weekday()]
    return f"{wd} {d:%Y-%m-%d}"

def build_message(rows, names, start, end):
    by_day={}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r["symbol"])
    parts = []
    header = (
        "🗓️ <b>EARNINGS WEEKLY PREVIEW | InvestX</b>\n"
        f"Semana: <b>{start}</b> → <b>{end}</b>\n\n"
        "<b>Agenda por día:</b>\n"
    )
    parts.append(header)
    for d in sorted(by_day.keys()):
        parts.append(f"\n<u>{day_es(d)}</u>")
        for sym in sorted(by_day[d]):
            star = WL_ICON if sym in WATCHLIST else "•"
            nm = names.get(sym, "")
            tail = f" — {esc(nm)}" if nm else ""
            parts.append(f"{star} <b>{esc(sym)}</b>{tail}")
    return "\n".join(parts).strip()

# ===== Main =====
def main():
    if not FMP_KEY: raise SystemExit("Falta FMP_API_KEY")
    if not (BOT_TOKEN and CHAT_ID): raise SystemExit("Faltan INVESTX_TOKEN/CHAT_ID")

    now = datetime.now(LOCAL_TZ)
    if not EARNINGS_FORCE:
        if now.weekday() != 0:  # solo lunes
            print("[earnings] skip: no es lunes"); return
        if already_sent_this_week():
            print("[earnings] ya enviado esta semana"); return

    rows, start, end = fetch_week_calendar()
    if not rows:
        send("🗓️ <b>EARNINGS WEEKLY PREVIEW</b>\nNo hay publicaciones esta semana.")
        if not EARNINGS_FORCE: mark_sent_this_week()
        return

    names = enrich_names(rows)
    msg = build_message(rows, names, start, end)
    send(msg)
    if not EARNINGS_FORCE: mark_sent_this_week()
    print(f"[earnings] enviado | rows={len(rows)} | names={len(names)}")

if __name__ == "__main__":
    main()
