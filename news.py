# scripts/news_es.py
import os, calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import feedparser, requests
from urllib.parse import urlparse

# === Config ===
CHAT_ID        = os.getenv("CHAT_ID")
BOT_TOKEN      = os.getenv("INVESTX_TOKEN")
DEEPL_API_KEY  = os.getenv("DEEPL_API_KEY", "").strip()  # usar "API Free" o "API Pro"
LOCAL_TZ       = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "10"))  # ventana
MAX_ITEMS      = min(10, int(os.getenv("MAX_ITEMS", "10")))  # tope duro en 10

KEYWORDS = [s.strip().lower() for s in os.getenv("KEYWORDS",
    "fed,ecb,boe,ipc,cpi,pmi,ism,nonfarm,empleo,inflación,inflation,tipos,rates,hike,cut,earnings,resultados,forecast,guidance,merger,acquisition,m&a,opa,downgrade,upgrade,oil,gas,war,china,tariffs"
).split(",")]

WATCHLIST = [s.strip().upper() for s in os.getenv("WATCHLIST",
    "AAPL,MSFT,AMZN,NVDA,GOOGL,META,TSLA,SAP,ASML,ADIDAS,CRM,SPOT,BTC,ETH"
).split(",")]

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

def fecha_es(dt):
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
        return d.replace("www.", "").split(":")[0]
    except Exception:
        return "Fuente"

def deepl_translate(text: str) -> str:
    # Si no hay clave, devolvemos el texto original
    if not DEEPL_API_KEY or not text:
        return text
    try:
        # Para cuenta Free usa api-free; la Pro usa api.deepl.com
        endpoint = "https://api-free.deepl.com/v2/translate"
        r = requests.post(
            endpoint,
            data={"auth_key": DEEPL_API_KEY, "text": text, "target_lang": "ES"},
            timeout=15,
        )
        r.raise_for_status()
        js = r.json()
        return js["translations"][0]["text"]
    except Exception:
        return text  # fallback sin romper

def score_item(title: str, link: str, published_utc: datetime) -> float:
    t = title.lower()
    score = 0.0
    for k in KEYWORDS:
        if k and k in t:
            score += 2.0
    for tk in WATCHLIST:
        if tk and tk.lower() in t:
            score += 3.0
    for k in ["breaking", "urgent", "profit warning"]:
        if k in t:
            score += 4.0
    if "cnbc.com" in (link or "").lower():
        score += 2.5  # prioriza CNBC
    age_minutes = (datetime.now(timezone.utc) - published_utc).total_seconds() / 60.0
    if age_minutes <= 180:
        score += 2.0 * (1.0 - (age_minutes / 180.0))  # recencia
    return score

def _to_dt_utc(entry):
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime.fromtimestamp(calendar.timegm(entry.published_parsed), tz=timezone.utc)
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return datetime.fromtimestamp(calendar.timegm(entry.updated_parsed), tz=timezone.utc)
    return None

def fetch_items():
    items = []
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=LOOKBACK_HOURS)

    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:60]:
                dt_utc = _to_dt_utc(e)
                if not dt_utc or dt_utc < cutoff:
                    continue
                title = (getattr(e, "title", "") or "").strip()
                link  = (getattr(e, "link", "")  or "").strip()
                if not title or not link:
                    continue
                s = score_item(title, link, dt_utc)
                items.append((s, dt_utc, title, link))
        except Exception:
            continue

    items.sort(key=lambda x: (x[0], x[1]), reverse=True)

    seen = set()
    uniq = []
    for s, dt_utc, title, link in items:
        dom = urlparse(link).netloc.lower().replace("www.", "")
        key = (title.lower(), dom)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((s, dt_utc, title, link))
        if len(uniq) >= MAX_ITEMS:
            break
    return uniq

def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={
        "chat_id": CHAT_ID,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "text": text
    }, timeout=30)
    r.raise_for_status()

def main():
    items = fetch_items()
    now_local = datetime.now(LOCAL_TZ)
    header = f"🗞️ <b>Noticias clave — {fecha_es(now_local)}</b>\n"
    header += "Desde <b>InvestX</b> os recalcamos las noticias más importantes recientes:\n\n"

    if not items:
        send_message(header + "• No hay titulares destacados en la ventana seleccionada.")
        return

    lines = []
    for s, dt_utc, title, link in items:
        title_es = deepl_translate(title)  # traducir al castellano
        ts_local  = dt_utc.astimezone(LOCAL_TZ)
        fuente    = source_label(link)
        # hipervínculo con el nombre de la fuente
        lines.append(f"• <b>{title_es}</b> — {fecha_es(ts_local)} · <a href=\"{link}\">{fuente}</a>")

    text = header + "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n…"
    send_message(text)

if __name__ == "__main__":
    main()
