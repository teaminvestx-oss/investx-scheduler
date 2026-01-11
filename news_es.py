# scripts/news_es.py
import os
import re
import sys
import json
import calendar
import html
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import requests

from utils import call_gpt_mini  # fallback traducción + briefs

# ========= Config =========
CHAT_ID        = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
BOT_TOKEN      = os.getenv("INVESTX_TOKEN") or os.getenv("TELEGRAM_TOKEN")
DEEPL_API_KEY  = (os.getenv("DEEPL_API_KEY") or "").strip()
DEEPL_PLAN     = (os.getenv("DEEPL_PLAN") or "").strip().lower()  # "free" | "pro" (auto si vacío)

LOCAL_TZ       = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "10"))

# Objetivo 5–6 (cap duro 6)
MAX_ITEMS_ENV  = int(os.getenv("MAX_ITEMS", "6"))
MAX_ITEMS      = min(6, max(3, MAX_ITEMS_ENV))  # duro 6, mínimo razonable 3

INCLUDE_DESC   = (os.getenv("INCLUDE_DESC", "0").strip().lower() in {"1", "true", "yes", "y"})

KEYWORDS = [s.strip().lower() for s in os.getenv("KEYWORDS",
    "fed,ecb,boe,ipc,cpi,pmi,ism,nonfarm,empleo,inflación,inflation,tipos,rates,hike,cut,"
    "earnings,resultados,forecast,guidance,merger,acquisition,m&a,opa,downgrade,upgrade,"
    "oil,gas,war,china,tariffs,trump,biden,white house,election,elecciones,aranceles"
).split(",") if s.strip()]

WATCHLIST = [s.strip().upper() for s in os.getenv("WATCHLIST",
    "AAPL,MSFT,AMZN,NVDA,GOOGL,META,TSLA,SAP,ASML,ADIDAS,CRM,SPOT,BTC,ETH"
).split(",") if s.strip()]

IMPORTANT_ENTITIES = [s.strip().lower() for s in os.getenv("IMPORTANT_ENTITIES",
    "trump,donald trump,biden,white house,congress,senate,house,gop,democrats,election,elecciones,tariffs,aranceles"
).split(",") if s.strip()]

# Nombres de empresas para captar titulares sin ticker (ajustable)
COMPANY_NAMES = [s.strip().lower() for s in os.getenv("COMPANY_NAMES",
    "apple,microsoft,amazon,nvidia,alphabet,google,meta,tesla,asml,sap,adidas,salesforce,spotify"
).split(",") if s.strip()]

FEEDS = [
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/10001147/device/rss/rss.html",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.reuters.com/reuters/marketsNews",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://www.ft.com/companies?format=rss",
]

DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

TRANSLATION_CACHE_FILE = "news_translation_cache.json"

# ========= Session =========
def build_requests_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "InvestX-NewsBot/1.2",
        "Accept": "application/json, text/plain, */*",
    })
    return s

SESSION = build_requests_session()

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
        blacklist = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "utm_id", "utm_name", "utm_creative", "cmpid", "seg", "mbid", "ocid", "sref"
        }
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in blacklist]
        p = p._replace(query=urlencode(q), fragment="")
        scheme = "https" if p.scheme in ("http", "https") else p.scheme
        netloc = p.netloc.lower().replace("www.", "")
        return urlunparse((scheme, netloc, p.path, p.params, p.query, ""))
    except Exception:
        return u

def _load_cache():
    try:
        if os.path.exists(TRANSLATION_CACHE_FILE):
            with open(TRANSLATION_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}

def _save_cache(d):
    try:
        with open(TRANSLATION_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass

_TRANSLATION_CACHE = _load_cache()

def deepl_translate(text: str) -> str:
    if not DEEPL_API_KEY or not text:
        return text
    try:
        if DEEPL_PLAN:
            base = "https://api-free.deepl.com" if DEEPL_PLAN == "free" else "https://api.deepl.com"
        else:
            # auto: pro -> free
            for base in ("https://api.deepl.com", "https://api-free.deepl.com"):
                try:
                    r = SESSION.post(
                        f"{base}/v2/translate",
                        data={"auth_key": DEEPL_API_KEY, "text": text, "target_lang": "ES"},
                        timeout=15,
                    )
                    r.raise_for_status()
                    js = r.json()
                    return js["translations"][0]["text"]
                except Exception:
                    continue
            return text

        r = SESSION.post(
            f"{base}/v2/translate",
            data={"auth_key": DEEPL_API_KEY, "text": text, "target_lang": "ES"},
            timeout=15,
        )
        r.raise_for_status()
        js = r.json()
        return js["translations"][0]["text"]
    except Exception:
        return text

def translate_to_es(text: str) -> str:
    """
    Traducción garantizada:
    - Cache -> DeepL -> GPT-mini fallback
    """
    if not text:
        return ""
    key = text.strip()
    if key in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[key]

    out = deepl_translate(key)
    if out and out != key:
        _TRANSLATION_CACHE[key] = out
        _save_cache(_TRANSLATION_CACHE)
        return out

    # fallback GPT-mini (si no hay key, utils devuelve "")
    system = "Eres traductor financiero profesional. Traduce al español neutro y natural, sin añadir información."
    user = f"Traduce al español (máx 1 frase), sin inventar nada:\n\n{key}"
    g = (call_gpt_mini(system, user, max_tokens=120) or "").strip()
    if g:
        _TRANSLATION_CACHE[key] = g
        _save_cache(_TRANSLATION_CACHE)
        return g

    return key  # último recurso


_TICKER_PATTERNS = [
    re.compile(rf"(?<![A-Z0-9]){re.escape(t)}(?![A-Z0-9])")
    for t in WATCHLIST if t
]
_IMPORTANT_PATTERNS = [
    re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
    for term in IMPORTANT_ENTITIES
]

# ========= Clasificación temática =========
EARNINGS_TERMS = (
    "earnings","results","guidance","outlook","forecast","profit","revenue","margin",
    "beats","misses","raises forecast","cuts forecast","profit warning","buyback"
)
DEAL_TERMS = (
    "partnership","alliance","agreement","contract","deal","collaboration","joint venture","jv",
    "acquisition","acquire","merger","m&a","takeover","stake","opa"
)
MACRO_TERMS = (
    "fed","ecb","boe","cpi","ipc","pce","inflation","inflación","rates","tipos","hike","cut",
    "nonfarm","nfp","jobless","employment","empleo","pmi","ism","treasury","auction","yield","yields"
)
POLITICS_TERMS = (
    "trump","white house","congress","senate","election","elecciones","tariff","tariffs","arancel","aranceles","biden"
)

LAST_HOUR_TERMS = (
    "breaking","urgent","exclusive","profit warning","bankruptcy","insolvency",
    "downgrade","upgrade","halts","fda","sec","probe","lawsuit",
    # macro top-tier:
    "cpi","ipc","nonfarm","nfp","fed decision","rate decision"
)

def _has_watchlist_ticker(title: str) -> bool:
    up = (title or "").upper()
    return any(pat.search(up) for pat in _TICKER_PATTERNS)

def _has_company_name(title: str) -> bool:
    t = (title or "").lower()
    return any(n in t for n in COMPANY_NAMES)

def classify_item(title: str) -> str:
    t = (title or "").lower()

    if any(k in t for k in EARNINGS_TERMS):
        return "earnings"
    if any(k in t for k in DEAL_TERMS):
        return "deals"
    if _has_watchlist_ticker(title) or _has_company_name(title):
        return "company"
    if any(k in t for k in MACRO_TERMS):
        return "macro"
    if any(k in t for k in POLITICS_TERMS):
        return "politics"
    return "other"

def is_last_hour(title: str) -> bool:
    t = (title or "").lower()
    if any(k in t for k in LAST_HOUR_TERMS):
        return True
    # combo: watchlist + (earnings/deal) => última hora
    if _has_watchlist_ticker(title) and (any(k in t for k in EARNINGS_TERMS) or any(k in t for k in DEAL_TERMS)):
        return True
    return False

# ========= Scoring =========
def score_item(title: str, link: str, published_utc: datetime) -> float:
    t = (title or "").lower()
    score = 0.0

    # keywords generales
    for k in KEYWORDS:
        if k and k in t:
            score += 2.0

    # watchlist tickers
    if _has_watchlist_ticker(title):
        score += 3.0

    # nombres empresas
    if _has_company_name(title):
        score += 2.0

    # política importante (bajamos peso para que no monopolice)
    for pat in _IMPORTANT_PATTERNS:
        if pat.search(title or ""):
            score += 2.0
            break

    # bonus corporativo (earnings/deals)
    if any(k in t for k in EARNINGS_TERMS):
        score += 4.0
    if any(k in t for k in DEAL_TERMS):
        score += 3.5

    # macro “hard”
    if any(k in t for k in ("cpi","ipc","pce","nonfarm","nfp","jobless","fed","ecb","boe","treasury","auction","yield")):
        score += 2.0

    # breaking cues
    if any(k in t for k in ("breaking","urgent","profit warning")):
        score += 4.0

    # fuente
    if "cnbc.com" in (link or "").lower():
        score += 2.0

    # recencia (hasta 3h)
    age_minutes = (datetime.now(timezone.utc) - published_utc).total_seconds() / 60.0
    if age_minutes <= 180:
        score += 2.0 * (1.0 - (age_minutes / 180.0))

    return score

def _to_dt_utc(entry):
    for fld in ("published_parsed", "updated_parsed"):
        if hasattr(entry, fld) and getattr(entry, fld):
            return datetime.fromtimestamp(calendar.timegm(getattr(entry, fld)), tz=timezone.utc)
    return None

# ========= Fetch =========
def fetch_items():
    items = []
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=LOOKBACK_HOURS)

    feedparser.USER_AGENT = "InvestX-NewsBot/1.2"

    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:120]:
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

    # score + recencia
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)

    # dedupe
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

    return uniq

# ========= Selección: última hora + mix =========
def select_items(uniq):
    if not uniq:
        return []

    last_hour = [x for x in uniq if is_last_hour(x[2])]
    rest = [x for x in uniq if x not in last_hour]

    selected = []

    # 1) Última hora (máx 2)
    selected += last_hour[:2]

    # 2) Clasificación para mix (sin forzar)
    buckets = {"macro": [], "earnings": [], "deals": [], "company": [], "politics": [], "other": []}
    for x in rest:
        buckets[classify_item(x[2])].append(x)

    # Cuotas objetivo (no rígidas)
    # Queremos típicamente: macro 1–2, empresas/earnings 2–3, deals 0–1, política 0–1
    target_total = MAX_ITEMS
    need = max(0, target_total - len(selected))

    def take(bucket_name, n):
        nonlocal selected
        out = []
        for it in buckets[bucket_name]:
            if it in selected:
                continue
            out.append(it)
            if len(out) >= n:
                break
        selected += out

    # Prioridad de relleno
    # Primero aseguramos algo corporativo y macro si existe
    if need > 0:
        take("macro", 2)
    if len(selected) < target_total:
        take("earnings", 2)
    if len(selected) < target_total:
        take("company", 2)
    if len(selected) < target_total:
        take("deals", 1)
    if len(selected) < target_total:
        take("politics", 1)

    # Completa con lo mejor restante (sin forzar)
    if len(selected) < target_total:
        pool = []
        for k in ("macro", "earnings", "company", "deals", "politics", "other"):
            pool += [x for x in buckets[k] if x not in selected]
        selected += pool[: max(0, target_total - len(selected))]

    # Si aun así queda corto, es que no hay material -> aceptamos menos (no rellenamos)
    return selected[:target_total]

# ========= Telegram =========
def send_message(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = SESSION.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "text": text,
        },
        timeout=30,
    )
    r.raise_for_status()

def rating_stars(index: int, total: int) -> str:
    if total <= 2:
        return "⭐⭐⭐" if index == 0 else "⭐⭐"
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
    line = (
        f"{stars} <b>{title_es}</b>\n"
        f"   {fecha_es(ts_local)} · <a href=\"{link}\">{fuente}</a>"
    )
    if INCLUDE_DESC and desc_es:
        d = desc_es.strip().replace("\n", " ")
        if len(d) > 160:
            d = d[:160].rstrip() + "…"
        line += f"\n   {html_escape(d)}"
    return line

def macro_brief_from_titles(macro_titles_es):
    if not macro_titles_es:
        return ""
    system = (
        "Eres analista macro en un desk institucional. Escribes en español, conciso y con criterio.\n"
        "No inventes cifras ni detalles; usa solo el contenido implícito en los titulares."
    )
    user = (
        "Redacta un 'Macro Brief' en 2 a 4 frases, estilo Bloomberg/Reuters, basado SOLO en estos titulares:\n"
        + "\n".join(f"- {t}" for t in macro_titles_es[:5])
        + "\n\nConecta con: expectativas de la Fed/tipos, yields, USD y sentimiento de renta variable (risk-on/off)."
    )
    return (call_gpt_mini(system, user, max_tokens=180) or "").strip()

# ========= Lógica principal =========
def run_news_once(force: bool = False):
    """
    - force=False -> horario lo gobierna main.py
    - force=True  -> ignora restricciones y envía siempre
    """
    now_local = datetime.now(LOCAL_TZ)
    if not force:
        print(f"{now_local} | NEWS | Ejecutado por main.py (sin validación de ventana en news_es.py).")
    else:
        print(f"{now_local} | NEWS | Envío forzado (force=True).")

    uniq = fetch_items()
    selected = select_items(uniq)

    header = f"🗞️ <b>Noticias clave — {fecha_es(now_local)}</b>\n\n"

    if not selected:
        send_message(header + "• No hay titulares destacados en la ventana seleccionada.")
        return

    # Traducción + preparación
    prepared = []
    for s, dt_utc, title, link, desc in selected:
        title_es = translate_to_es(title) or title
        desc_es  = translate_to_es(desc) if desc else ""
        ts_local = dt_utc.astimezone(LOCAL_TZ)
        fuente   = source_label(link)
        cat      = classify_item(title)
        lh       = is_last_hour(title)
        prepared.append((s, dt_utc, title_es, link, desc_es, ts_local, fuente, cat, lh))

    # Secciones
    last_hour_items = [x for x in prepared if x[8] is True]
    normal_items    = [x for x in prepared if x[8] is False]

    # Macro brief (solo si hay macro en lo seleccionado)
    macro_titles_es = [x[2] for x in prepared if x[7] == "macro"]
    brief = macro_brief_from_titles(macro_titles_es) if macro_titles_es else ""

    blocks = [header]

    if last_hour_items:
        blocks.append("⏰ <b>ÚLTIMA HORA</b>\n")
        total_lh = len(last_hour_items)
        for i, x in enumerate(last_hour_items):
            stars = rating_stars(i, total_lh)
            blocks.append(build_bullet(stars, x[2], x[5], x[3], x[6], x[4]))
        blocks.append("")  # línea en blanco

    if brief:
        blocks.append("🧠 <b>Macro Brief</b>\n" + html_escape(brief) + "\n")

    # Orden por categorías (solo si hay contenido)
    def add_section(title, key):
        items = [x for x in normal_items if x[7] == key]
        if not items:
            return
        blocks.append(f"{title}\n")
        total = len(items)
        for i, x in enumerate(items):
            stars = rating_stars(i, total)
            blocks.append(build_bullet(stars, x[2], x[5], x[3], x[6], x[4]))
        blocks.append("")

    add_section("🏦 <b>Macro / Mercados</b>", "macro")
    add_section("🏢 <b>Empresas</b>", "company")
    add_section("📊 <b>Earnings / Guidance</b>", "earnings")
    add_section("🤝 <b>Alianzas / M&amp;A</b>", "deals")
    add_section("🏛️ <b>Política</b>", "politics")

    # Si quedara algo “other”
    add_section("🧩 <b>Otros</b>", "other")

    text = "\n".join([b for b in blocks if b is not None]).strip()

    # recorte si hace falta
    MAX_TELEGRAM = 3900
    if len(text) > MAX_TELEGRAM:
        # recorta por secciones, manteniendo el header
        lines = text.split("\n")
        out = []
        acc = 0
        for ln in lines:
            if acc + len(ln) + 1 > MAX_TELEGRAM - 10:
                out.append("…")
                break
            out.append(ln)
            acc += len(ln) + 1
        text = "\n".join(out).strip()

    send_message(text)
    print(f"{now_local} | NEWS | Mensaje de noticias enviado correctamente.")

def main(force: bool = False):
    run_news_once(force=force)

if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        raise SystemExit("Faltan variables de entorno: INVESTX_TOKEN y/o CHAT_ID")

    FORCE_ENV = (os.getenv("NEWS_FORCE", "0").strip().lower() in {"1", "true", "yes", "y"})
    FORCE_ARG = ("--force" in sys.argv)

    main(force=(FORCE_ENV or FORCE_ARG))
