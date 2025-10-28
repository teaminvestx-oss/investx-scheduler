# scripts/earnings_weekly.py
import os, json, time, html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

# ================== CONFIG BÁSICA ==================
BOT_TOKEN = os.getenv("INVESTX_TOKEN")
CHAT_ID   = os.getenv("CHAT_ID")
FMP_KEY   = os.getenv("FMP_API_KEY")
LOCAL_TZ  = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))
EARNINGS_FORCE = os.getenv("EARNINGS_FORCE", "0").lower() in {"1","true","yes"}
WATCHLIST = [t.strip().upper() for t in os.getenv("WATCHLIST","").split(",") if t.strip()]
WL_ICON = "⭐"
PAD_DAYS = 0
CACHE_PATH = "/tmp/investx_company_cache.json"
LOCK_PATH  = "/tmp/investx_earnings_week.lock"

# ================== Fallback de nombres ampliado (≈250 tickers) ==================
LOCAL_NAME_MAP = {
    # --- Tech majors ---
    "AAPL":"Apple Inc.", "MSFT":"Microsoft Corporation", "GOOGL":"Alphabet Inc.", "GOOG":"Alphabet Inc.",
    "AMZN":"Amazon.com, Inc.", "META":"Meta Platforms, Inc.", "NVDA":"NVIDIA Corporation",
    "TSLA":"Tesla, Inc.", "NFLX":"Netflix, Inc.", "ADBE":"Adobe Inc.", "INTC":"Intel Corporation",
    "AMD":"Advanced Micro Devices, Inc.", "CSCO":"Cisco Systems, Inc.", "ORCL":"Oracle Corporation",
    "CRM":"Salesforce, Inc.", "IBM":"International Business Machines Corporation", "SAP":"SAP SE",
    "SHOP":"Shopify Inc.", "UBER":"Uber Technologies, Inc.", "LYFT":"Lyft, Inc.",
    "SNOW":"Snowflake Inc.", "PANW":"Palo Alto Networks, Inc.", "CRWD":"CrowdStrike Holdings, Inc.",
    "ZS":"Zscaler, Inc.", "NOW":"ServiceNow, Inc.", "DDOG":"Datadog, Inc.", "DOCU":"DocuSign, Inc.",
    "TEAM":"Atlassian Corporation Plc", "OKTA":"Okta, Inc.", "MDB":"MongoDB, Inc.", "NET":"Cloudflare, Inc.",
    "TWLO":"Twilio Inc.", "PLTR":"Palantir Technologies Inc.", "AFRM":"Affirm Holdings, Inc.",
    "SQ":"Block, Inc.", "PYPL":"PayPal Holdings, Inc.", "COIN":"Coinbase Global, Inc.",
    "HOOD":"Robinhood Markets, Inc.", "RBLX":"Roblox Corporation", "ROKU":"Roku, Inc.",
    "SPOT":"Spotify Technology S.A.", "SNAP":"Snap Inc.", "PINS":"Pinterest, Inc.", "ETSY":"Etsy, Inc.",
    "ZM":"Zoom Video Communications, Inc.", "DOCS":"Doximity, Inc.", "ABNB":"Airbnb, Inc.",
    "MELI":"MercadoLibre, Inc.", "RDDT":"Reddit, Inc.", "DUOL":"Duolingo, Inc.",
    "AI":"C3.ai, Inc.", "PATH":"UiPath Inc.", "INTU":"Intuit Inc.", "ADSK":"Autodesk, Inc.",
    "WDAY":"Workday, Inc.", "CRM":"Salesforce, Inc.", "VEEV":"Veeva Systems Inc.",
    # --- Semiconductors ---
    "QCOM":"QUALCOMM Incorporated", "TXN":"Texas Instruments Incorporated", "AVGO":"Broadcom Inc.",
    "MU":"Micron Technology, Inc.", "NXPI":"NXP Semiconductors N.V.", "LRCX":"Lam Research Corporation",
    "AMAT":"Applied Materials, Inc.", "TSM":"Taiwan Semiconductor Manufacturing Company Limited",
    "ASML":"ASML Holding N.V.", "KLAC":"KLA Corporation", "ON":"ON Semiconductor Corporation",
    "SWKS":"Skyworks Solutions, Inc.", "ADI":"Analog Devices, Inc.", "WDC":"Western Digital Corporation",
    # --- Financials ---
    "JPM":"JPMorgan Chase & Co.", "BAC":"Bank of America Corporation", "C":"Citigroup Inc.",
    "GS":"The Goldman Sachs Group, Inc.", "MS":"Morgan Stanley", "WFC":"Wells Fargo & Company",
    "AXP":"American Express Company", "V":"Visa Inc.", "MA":"Mastercard Incorporated",
    "SOFI":"SoFi Technologies, Inc.", "SCHW":"Charles Schwab Corporation", "HOOD":"Robinhood Markets, Inc.",
    "BLK":"BlackRock, Inc.", "TROW":"T. Rowe Price Group, Inc.", "COF":"Capital One Financial Corporation",
    # --- Healthcare / Pharma ---
    "UNH":"UnitedHealth Group Incorporated", "JNJ":"Johnson & Johnson", "PFE":"Pfizer Inc.",
    "MRK":"Merck & Co., Inc.", "ABBV":"AbbVie Inc.", "LLY":"Eli Lilly and Company", "BMY":"Bristol-Myers Squibb Company",
    "GILD":"Gilead Sciences, Inc.", "AMGN":"Amgen Inc.", "CVS":"CVS Health Corporation", "HCA":"HCA Healthcare, Inc.",
    # --- Energy / Industrials ---
    "XOM":"Exxon Mobil Corporation", "CVX":"Chevron Corporation", "COP":"ConocoPhillips",
    "BP":"BP p.l.c.", "SHEL":"Shell plc", "TTE":"TotalEnergies SE",
    "GE":"General Electric Company", "HON":"Honeywell International Inc.", "BA":"The Boeing Company",
    "CAT":"Caterpillar Inc.", "DE":"Deere & Company", "MMM":"3M Company", "RTX":"RTX Corporation",
    "NOC":"Northrop Grumman Corporation", "LMT":"Lockheed Martin Corporation", "F":"Ford Motor Company",
    "GM":"General Motors Company", "TSLA":"Tesla, Inc.", "RIVN":"Rivian Automotive, Inc.",
    # --- Consumer / Retail ---
    "PG":"Procter & Gamble Company", "KO":"Coca-Cola Company", "PEP":"PepsiCo, Inc.",
    "MCD":"McDonald's Corporation", "SBUX":"Starbucks Corporation", "COST":"Costco Wholesale Corporation",
    "TGT":"Target Corporation", "WMT":"Walmart Inc.", "HD":"The Home Depot, Inc.",
    "LOW":"Lowe's Companies, Inc.", "NKE":"NIKE, Inc.", "ADIDAS":"adidas AG", "DIS":"The Walt Disney Company",
    "PARA":"Paramount Global", "CMCSA":"Comcast Corporation", "NFLX":"Netflix, Inc.",
    "T":"AT&T Inc.", "VZ":"Verizon Communications Inc.", "TMUS":"T-Mobile US, Inc.",
    # --- Airlines / Travel ---
    "AAL":"American Airlines Group Inc.", "DAL":"Delta Air Lines, Inc.", "UAL":"United Airlines Holdings, Inc.",
    "LUV":"Southwest Airlines Co.", "CCL":"Carnival Corporation & plc", "RCL":"Royal Caribbean Group",
    # --- Commodities / Materials ---
    "NEM":"Newmont Corporation", "FCX":"Freeport-McMoRan Inc.", "BHP":"BHP Group Limited",
    "RIO":"Rio Tinto Group", "VALE":"Vale S.A.", "AA":"Alcoa Corporation",
    # --- Utilities / Infrastructure ---
    "NEE":"NextEra Energy, Inc.", "DUK":"Duke Energy Corporation", "SO":"The Southern Company",
    "D":"Dominion Energy, Inc.", "XEL":"Xcel Energy Inc.",
    # --- Misc & new tech ---
    "RKT":"Rocket Companies, Inc.", "RIOT":"Riot Platforms, Inc.", "MARA":"Marathon Digital Holdings, Inc.",
    "BTBT":"Bit Digital, Inc.", "CLSK":"CleanSpark, Inc.",
    "UBS":"UBS Group AG", "GS":"The Goldman Sachs Group, Inc.",
}

# ================== HTTP ==================
S = requests.Session()
S.headers.update({"User-Agent":"InvestX-EarningsBot/1.7"})

def fmp_get(url, params):
    q = dict(params or {})
    q["apikey"] = FMP_KEY
    r = S.get(url, params=q, timeout=20)
    if r.status_code == 429:
        raise RuntimeError("FMP rate-limit 429")
    r.raise_for_status()
    return r.json()

# ================== UTILIDADES ==================
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

def current_week_key():
    return datetime.now(LOCAL_TZ).strftime("%G-W%V")

def is_already_sent_this_week():
    try:
        with open(LOCK_PATH,"r") as f:
            return f.read().strip() == current_week_key()
    except: return False

def mark_sent_this_week():
    try:
        with open(LOCK_PATH,"w") as f:
            f.write(current_week_key())
    except: pass

def monday_sunday_local():
    now = datetime.now(LOCAL_TZ)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0)
    sunday = monday + timedelta(days=6)
    return monday.date(), sunday.date()

# ================== FMP: CALENDARIO ==================
def fetch_week_calendar():
    start, end = monday_sunday_local()
    js = fmp_get("https://financialmodelingprep.com/stable/earnings-calendar",
                 {"from": str(start), "to": str(end)})
    rows = []
    for it in js:
        sym = (it.get("symbol") or "").upper().strip()
        dt  = (it.get("date") or "").strip()
        if not sym or not dt: continue
        rows.append({"symbol": sym, "date": dt})
    seen=set(); out=[]
    for r in rows:
        k=(r["symbol"],r["date"])
        if k in seen: continue
        seen.add(k); out.append(r)
    return out

# ================== NOMBRES ==================
def get_company_name(sym):
    cache = load_cache()
    if sym in cache: return cache[sym]
    name = None
    try:
        js = fmp_get("https://financialmodelingprep.com/stable/search", {"query": sym, "limit": 5})
        for row in js or []:
            if (row.get("symbol") or "").upper() == sym:
                name = row.get("name"); break
        if not name and js: name = js[0].get("name")
        if not name:
            js2 = fmp_get("https://financialmodelingprep.com/stable/search-symbol", {"query": sym, "limit": 5})
            for row in js2 or []:
                if (row.get("symbol") or "").upper() == sym:
                    name = row.get("name"); break
            if not name and js2: name = js2[0].get("name")
    except Exception as e:
        print("[get_company_name]", sym, e)
    if not name: name = LOCAL_NAME_MAP.get(sym)
    if name:
        cache[sym]=name; save_cache(cache)
    return name

def enrich_all_names(rows):
    names={}
    for sym in sorted({r["symbol"] for r in rows}):
        nm=get_company_name(sym)
        if nm: names[sym]=nm
    return names

# ================== MENSAJE ==================
DIAS_ES=["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
def day_es(dt): return f"{DIAS_ES[dt.weekday()]} {dt:%Y-%m-%d}"

def build_text(rows,names):
    day_map={}
    for r in rows: day_map.setdefault(r["date"],[]).append(r["symbol"])
    days=sorted(day_map.keys())
    start,end=monday_sunday_local()
    head=(f"📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>\n"
          f"Semana: <b>{start}</b> → <b>{end}</b>\n\n<b>Agenda por día:</b>\n")
    out=[head]
    for d in days:
        dt=datetime.strptime(d,"%Y-%m-%d").replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
        out.append(f"\n<u>{esc(day_es(dt))}</u>")
        for sym in sorted(day_map[d]):
            mark = WL_ICON if sym in WATCHLIST else "•"
            nm = names.get(sym, "")
            tail = f" — {esc(nm)}" if nm else ""
            out.append(f"{mark} <b>{esc(sym)}</b>{tail}")
    return "\n".join(out).strip()

# ================== MAIN ==================
def main():
    if not FMP_KEY: raise SystemExit("Falta FMP_API_KEY")
    if not BOT_TOKEN or not CHAT_ID: raise SystemExit("Faltan INVESTX_TOKEN/CHAT_ID")
    now=datetime.now(LOCAL_TZ)
    if not EARNINGS_FORCE and now.weekday()!=0:
        print("[earnings] skip no es lunes"); return
    if not EARNINGS_FORCE and is_already_sent_this_week():
        print("[earnings] ya enviado esta semana"); return
    rows=fetch_week_calendar()
    if not rows:
        send("📅 <b>EARNINGS WEEKLY PREVIEW</b>\nNo hay publicaciones en la semana.")
        mark_sent_this_week(); return
    names=enrich_all_names(rows)
    text=build_text(rows,names)
    send(text)
    mark_sent_this_week()
    print(f"[earnings] OK | {len(rows)} tickers | {len(names)} nombres")

if __name__=="__main__":
    main()
