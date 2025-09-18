# scripts/news_es.py
import os, re, calendar, html, math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import feedparser, requests
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# ========= Config =========
CHAT_ID        = os.getenv("CHAT_ID")
BOT_TOKEN      = os.getenv("INVESTX_TOKEN")
DEEPL_API_KEY  = (os.getenv("DEEPL_API_KEY") or "").strip()
DEEPL_PLAN     = (os.getenv("DEEPL_PLAN") or "").strip().lower()  # "free" | "pro" (auto si vacío)
LOCAL_TZ       = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "10"))
# Limite duro: 6
MAX_ITEMS_ENV  = int(os.getenv("MAX_ITEMS", "6"))
MAX_ITEMS      = min(6, MAX_ITEMS_ENV)  # no superar 6 por requisito

INCLUDE_DESC   = (os.getenv("INCLUDE_DESC", "0").strip() in {"1","true","yes","y"})

KEYWORDS = [s.strip().lower() for s in os.getenv("KEYWORDS",
    "fed,ecb,boe,ipc,cpi,pmi,ism,nonfarm,empleo,inflación,inflation,tipos,rates,hike,cut,earnings,resultados,forecast,guidance,merger,acquisition,m&a,opa,downgrade,upgrade,oil,gas,war,china,tariffs"
).split(",") if s.strip()]

WATCHLIST = [s.strip().upper() for s in os.getenv("WATCHLIST",
    "AAPL,MSFT,AMZN,NVDA,GOOGL,META,TSLA,SAP,ASML,ADIDAS,CRM,SPOT,BTC,ETH"
).split(",") if s.strip()]

FEEDS = [
    # CNBC (prioridad)
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",  # Top News
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",   # World Markets
    # Otros
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.reuters.com/reuters/marketsNews",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",          # WSJ Markets
    "https://www.ft.com/companies?format=rss",                # FT Companies
]

DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

# ========= Utilidades =========
def fecha_es(dt: datetime) -> str:
    d = DIAS_ES[dt.weekday()]
    m = MESES_ES[dt.month - 1]
    return f"{d} {dt.day} {m} {dt:%H:%M}"

def source_label(url: str) -> str:
    try:
        d = urlparse(url).netloc.lower()
        if "cnbc.com" in d: return "CNBC"
        if "reuters" in d: return "Reuters"
        if "wsj" in d or "dowjones" in d: return "WSJ"
        if "ft.com" in d: return "Financial Times"
        return d.replace("www.", "").split(":")[0].capitalize()
    except Exception:
        return "Fuente"

def html_escape(s: str) -> str:
    return html.escape(s or "", quote=False)

def normalize_url(u: str) -> str:
    try:
        p = urlparse(u)
        blacklist = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content",
                     "utm_id","utm_name","utm_creative","cmpid","seg","mbid","ocid","sref"}
        q = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in blacklist]
        p = p._replace(query=urlencode(q), fragment="")
        scheme = "https" if p.scheme in ("http","https") else p.scheme
        netloc = p.netloc.lower().replace("www.", "")
        return urlunparse((scheme, netloc, p.path, p.params, p.query, ""))
    except Exception:
        return u

def build_requests_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "InvestX-NewsBot/1.1 (+https://investx.local)",
        "Accept": "application/json, text/plain, */*",
        "Connection": "keep-alive",
    })
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET","POST"])
        )
        s.mount("https://", HTTPAdapter(max_retries=retry))
        s.mount("http://",  HTTPAdapter(max_retries=retry))
    except Exception:
        pass
    return s

SESSION = build_requests_session()

def deepl_translate(text: str) -> str:
    if not DEEPL_API_KEY or not text:
        return text
    try:
        if DEEPL_PLAN:
            base = "https://api-free.deepl.com" if DEEPL_PLAN == "free" else "https://api.deepl.com"
        else:
            for base in ("https://api.deepl.com","https://api-free.deepl.com"):
                try:
                    r = SESSION.post(f"{base}/v2/translate",
                                     data={"auth_key": DEEPL_API_KEY, "text": text, "target_lang": "ES"},
                                     timeout=15)
                    r.raise_for_status()
                    js = r.json()
                    return js["translations"][0]["text"]
                except Exception:
                    continue
            return text
        r = SESSION.post(f"{base}/v2/translate",
                         data={"auth_key": DEEPL_API_KEY, "text": text, "target_lang": "ES"},
                         timeout=15)
        r.raise_for_status()
        js = r.json()
        return js["translations"][0]["text"]
    except Exception:
        return text

_TICKER_PATTERNS = [re.compile(rf"(?<![A-Z0-9]){re.escape(t)}(?![A-Z0-9])") for t in WATCHLIST if t]

def score_item(title: str, link: str, published_utc: datetime) -> float:
    t = (title or "").lower()
    score = 0.0
    for k in KEYWORDS:
        if k and k in t:
            score += 2.0
    up = (title or "").upper()
    for pat in _TICKER_PATTERNS:
        if pat.search(up):
            score += 3.0
    for k in ("breaking", "urgent", "profit warning"):
        if k in t:
            score += 4.0
    if "cnbc.com" in (link or "").lower():
        score += 2.5
    age_minutes = (datetime.now(timezone.utc) - published_utc).total_seconds() / 60.0
    if age_minutes <= 180:
        score += 2.0 * (1.0 - (age_minutes / 180.0))
    return score

def _to_dt_utc(entry):
    for fld in ("published_parsed", "updated_parsed"):
        if hasattr(entry, fld) and getattr(entry, fld):
            return datetime.fromtimestamp(calendar.timegm(getattr(entry, fld)), tz=timezone.utc)
    return None

def fetch_items():
    items = []
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=LOOKBACK_HOURS)

    feedparser.USER_AGENT = "InvestX-NewsBot/1.1 (+https://investx.local)"
    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:80]:
                dt_utc = _to_dt_utc(e)
                if not dt_utc or dt_utc < cutoff:
                    continue
                title = (getattr(e, "title", "") or "").strip()
                link  = (getattr(e, "link", "")  or "").strip()
                if not title or not link:
                    continue
                link_norm = normalize_url(link)
                s = score_item(title, link_norm, dt_utc)
                desc = (getattr(e, "summary", "") or "").strip()
                items.append((s, dt_utc, title, link_norm, desc))
        except Exception:
            continue

    items.sort(key=lambda x: (x[0], x[1]), reverse=True)

    # Dedupe por (título+dominio) y por URL normalizada
    seen_title_dom = set()
    seen_url = set()
    uniq = []
    for s, dt_utc, title, link, desc in items:
        dom = urlparse(link).netloc.lower().replace("www.", "")
        key = (title.lower(), dom)
        if key in seen_title_dom or link in seen_url:
            continue
        seen_title_dom.add(key)
        seen_url.add(link)
        uniq.append((s, dt_utc, title, link, desc))
        if len(uniq) >= MAX_ITEMS:
            break
    return uniq

def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = SESSION.post(url, data={
        "chat_id": CHAT_ID,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "text": text
    }, timeout=30)
    r.raise_for_status()

def rating_stars(index: int, total: int) -> str:
    """
    Reparte por orden:
      - Top ~1/3 → ⭐⭐⭐
      - Medio ~1/3 → ⭐⭐
      - Último ~1/3 → ⭐
    Con 6 elementos: 2/2/2.
    """
    if total <= 2:
        # 1 → ⭐⭐⭐, 2 → ⭐⭐
        return "⭐⭐⭐" if index == 0 else "⭐⭐"
    # cortes proporcionales
    top_cut = max(1, math.ceil(total / 3))
    mid_cut = max(1, math.ceil(2 * total / 3))
    if index < top_cut:
        return "⭐⭐⭐"
    elif index < mid_cut:
        return "⭐⭐"
    else:
        return "⭐"

def build_bullet(stars: str, title_es: str, ts_local: datetime, link: str, fuente: str, desc_es: str = "") -> str:
    title_es = html_escape(title_es)
    fuente = html_escape(fuente)
    line = f"{stars} <b>{title_es}</b>\n   {fecha_es(ts_local)} · <a href=\"{link}\">{fuente}</a>"
    if INCLUDE_DESC and desc_es:
        d = desc_es.strip().replace("\n", " ")
        if len(d) > 160:
            d = d[:160].rstrip() + "…"
        line += f"\n   {html_escape(d)}"
    return line

def main():
    items = fetch_items()
    now_local = datetime.now(LOCAL_TZ)
    header = f"🗞️ <b>Noticias clave — {fecha_es(now_local)}</b>\n"
   

    if not items:
        send_message(header + "• No hay titulares destacados en la ventana seleccionada.")
        return

    lines = []
    total = len(items)
    for i, (s, dt_utc, title, link, desc) in enumerate(items):
        title_es = deepl_translate(title) or title
        desc_es  = deepl_translate(desc) if desc else ""
        ts_local = dt_utc.astimezone(LOCAL_TZ)
        fuente   = source_label(link)
        stars    = rating_stars(i, total)
        lines.append(build_bullet(stars, title_es, ts_local, link, fuente, desc_es))

   

    # Control de tamaño
    text = header + "\n".join(lines) + footer
    MAX_TELEGRAM = 3900
    if len(text) > MAX_TELEGRAM:
        acc = header
        for ln in lines:
            if len(acc) + len(ln) + 1 > MAX_TELEGRAM - len(footer) - 10:
                acc += "\n…"
                break
            acc += ("\n" + ln)
        text = acc + footer

    send_message(text)

if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("Faltan variables de entorno: INVESTX_TOKEN y/o CHAT_ID")
    main()
