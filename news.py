import os, time
from datetime import datetime, timedelta, timezone
import feedparser
import requests

# ---- Config ----
CHAT_ID   = os.getenv("CHAT_ID")
BOT_TOKEN = os.getenv("INVESTX_TOKEN")
TZ_OFFSET = int(os.getenv("TZ_OFFSET_MINUTES", "120"))  # Madrid verano=120, invierno=60
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "12"))
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "5"))

KEYWORDS = [s.lower() for s in os.getenv("KEYWORDS",
    "fed,ecb,boe,ipc,cpi,pmi,ism,nonfarm,empleo,inflación,inflation,tipos,rates,hike,cut,earnings,resultados,forecast,guidance,merger,acquisition,m&a,opa,downgrade,upgrade,oil,gas,war,china,tariffs"
).split(",")]

WATCHLIST = [s.strip().upper() for s in os.getenv("WATCHLIST",
    "AAPL,MSFT,AMZN,NVDA,GOOGL,META,TSLA,SAP,ASML,ADIDAS,CRM,SPOT,BTC,ETH"
).split(",")]

FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.reuters.com/reuters/marketsNews",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",
]

# ---- Fechas en español ----
DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
            "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

def fecha_es(dt):
    d = DIAS_ES[dt.weekday()]
    m = MESES_ES[dt.month - 1]
    return f"{d} {dt.day} {m} {dt:%H:%M}"

# ---- Lógica ----
def score_item(title: str):
    t = title.lower()
    score = 0
    for k in KEYWORDS:
        if k and k in t:
            score += 2
    for tk in WATCHLIST:
        if tk and tk.lower() in t:
            score += 3
    for k in ["breaking", "urgent", "profit warning"]:
        if k in t:
            score += 4
    return score

def fetch_items():
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    for url in FEEDS:
        feed = feedparser.parse(url)
        for e in feed.entries[:50]:
            published = None
            if hasattr(e, "published_parsed") and e.published_parsed:
                published = datetime.fromtimestamp(time.mktime(e.published_parsed), tz=timezone.utc)
            elif hasattr(e, "updated_parsed") and e.updated_parsed:
                published = datetime.fromtimestamp(time.mktime(e.updated_parsed), tz=timezone.utc)
            else:
                continue
            if published < cutoff:
                continue
            title = e.title.strip()
            link = getattr(e, "link", "")
            s = score_item(title)
            items.append((s, published, title, link))
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    seen = set(); uniq = []
    for it in items:
        key = it[2].lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq[:MAX_ITEMS]

def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={
        "chat_id": CHAT_ID,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "text": text
    }, timeout=30)
    r.raise_for_status()
    return r.json()

def main():
    items = fetch_items()
    local = datetime.utcnow() + timedelta(minutes=TZ_OFFSET)
    header = f"🗞️ <b>Noticias clave — {fecha_es(local)}</b>\n"
    header += "Desde <b>InvestX</b> os recalcamos las noticias más importantes:\n\n"
    if not items:
        text = header + "• No hay titulares destacados en la ventana seleccionada."
        send_message(text); return
    lines = []
    for s, dt, title, link in items:
        ts_local = (dt + timedelta(minutes=TZ_OFFSET))
        lines.append(f"• <b>{title}</b> — {fecha_es(ts_local)}\n{link}")
    text = header + "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n…"
    send_message(text)

if __name__ == "__main__":
    main()
