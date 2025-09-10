# movers_table.py — Finviz (USA) Top 10 Gainers & Top 10 Losers en dos mensajes (sin gráfico)
import os, time, re, warnings, requests
import pandas as pd
import numpy as np

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")
assert BOT_TOKEN and CHAT_ID, "Faltan BOT_TOKEN o CHAT_ID"

USE_PLAYWRIGHT = os.environ.get("USE_PLAYWRIGHT", "0") == "1"
RETRIES = 3
SLEEP_BASE = 2.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finviz.com/",
    "Connection": "keep-alive",
}
warnings.filterwarnings("ignore", category=FutureWarning)

_TK = re.compile(r"^[A-Z][A-Z0-9.\-]{0,5}$")
BASE_COLS = ["Ticker","Company","Sector","Industry","Country","Market Cap","Price","Change","Volume"]

# ---------- HTTP helpers (Playwright + fallback) ----------
session = requests.Session()
session.headers.update(HEADERS)

def get_html(url: str) -> str:
    """Intenta cargar con Playwright; si no, requests con backoff."""
    if USE_PLAYWRIGHT:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent=HEADERS["User-Agent"],
                    viewport={"width": 1366, "height": 900}
                )
                page = context.new_page()
                try:
                    page.goto("https://finviz.com/", timeout=30000)
                    page.wait_for_timeout(1200)
                except Exception:
                    pass
                page.goto(url, timeout=45000)
                try:
                    page.wait_for_selector("table", timeout=15000)
                except Exception:
                    pass
                html = page.content()
                browser.close()
                if html and len(html) > 3000:
                    return html
        except Exception as e:
            print(f"[WARN] Playwright falló: {e}")

    # fallback requests
    try:
        session.get("https://finviz.com/", timeout=15)
        session.get("https://finviz.com/maps.ashx", timeout=15)
    except Exception:
        pass

    last = None
    for i in range(1, RETRIES+1):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200 and len(r.text) > 3000:
                return r.text
            last = RuntimeError(f"HTTP {r.status_code} len={len(r.text)}")
        except Exception as e:
            last = e
        time.sleep(SLEEP_BASE * i)

    print(f"[WARN] No HTML para {url}: {last}")
    return ""

# ---------- Finviz parse ----------
def pick_finviz_table(html_text: str) -> pd.DataFrame:
    tables = pd.read_html(html_text)
    best, score = None, -1
    for df in tables:
        cols = [str(c) for c in df.columns]
        if "Ticker" not in cols or not (set(cols) & {"Change","Price"}):
            continue
        valid = sum(bool(_TK.match(str(t).strip())) for t in df["Ticker"])
        if valid > score:
            best, score = df, valid
    return (best or tables[-1]).copy()

def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    for c in BASE_COLS:
        if c not in df.columns:
            df[c] = ""
    return df

def finviz_fetch_usa(top=True, limit=10) -> pd.DataFrame:
    """
    USA only (geo_usa), vista 111 y orden por % explícito:
      - Gainers: o=-change
      - Losers:  o=change
    """
    s = "ta_topgainers" if top else "ta_toplosers"
    order = "o=-change" if top else "o=change"
    url = f"https://finviz.com/screener.ashx?v=111&{order}&s={s}&f=geo_usa&r=1"
    html = get_html(url)
    if not html:
        return pd.DataFrame(columns=BASE_COLS)

    df = pick_finviz_table(html)
    df = df[[c for c in BASE_COLS if c in df.columns]].copy()
    df = ensure_columns(df)
    # limpia filas basura
    df = df[df["Ticker"].astype(str).str.strip().apply(lambda x: bool(_TK.match(x)))]
    # nos quedamos con las columnas “típicas” del listado de Finviz
    keep = ["Ticker","Company","Sector","Price","Change","Volume"]
    df = df[keep]
    # top N (ya vienen ordenados por la query; re-ordenamos por si acaso)
    def pchg(x):
        try:
            return float(str(x).replace("%","").replace("+","").replace(",","").strip())
        except:
            return -9e9
    df["__chg"] = df["Change"].apply(pchg)
    df = df.sort_values("__chg", ascending=not top).drop(columns="__chg").head(limit).reset_index(drop=True)
    return df

# ---------- Formato tabla (tipo Finviz) ----------
def to_pretty_table(df: pd.DataFrame, title: str) -> str:
    # Anchos parecidos a Finviz (ajustados para móvil/Telegram)
    widths = {"Ticker":6, "Company":26, "Sector":18, "Price":8, "Change":8, "Volume":11}
    def cut(s, w):
        s = "" if s is None else str(s)
        return s if len(s) <= w else s[:max(0,w-1)] + "…"

    header = " ".join([f"{k:<{widths[k]}}" for k in ["Ticker","Company","Sector","Price","Change","Volume"]])
    lines = [f"📊 {title}", "<pre>", header, "-"*len(header)]
    for _, r in df.iterrows():
        row = " ".join([
            f"{cut(r['Ticker'],  widths['Ticker']):<{widths['Ticker']}}",
            f"{cut(r['Company'], widths['Company']):<{widths['Company']}}",
            f"{cut(r['Sector'],  widths['Sector']):<{widths['Sector']}}",
            f"{cut(r['Price'],   widths['Price']):>{widths['Price']}}",
            f"{cut(r['Change'],  widths['Change']):>{widths['Change']}}",
            f"{cut(r['Volume'],  widths['Volume']):>{widths['Volume']}}",
        ])
        lines.append(row)
    lines.append("</pre>")
    return "\n".join(lines)

# ---------- Send helpers ----------
def tg_send(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=60)
    print("Telegram response:", resp.text)
    resp.raise_for_status()

# ---------- Main ----------
def main():
    gainers = finviz_fetch_usa(top=True, limit=10)
    losers  = finviz_fetch_usa(top=False, limit=10)

    # Mensaje 1: Top Gainers
    if not gainers.empty:
        txt1 = "📈 <b>Top Gainers USA (Finviz)</b>\n" + to_pretty_table(gainers, "Top 10 Subidas (USA)")
        tg_send(txt1)
        time.sleep(1.2)
    else:
        tg_send("📈 <b>Top Gainers USA (Finviz)</b>\nNo se han podido obtener datos hoy.")

    # Mensaje 2: Top Losers
    if not losers.empty:
        txt2 = "📉 <b>Top Losers USA (Finviz)</b>\n" + to_prety_table(losers, "Top 10 Caídas (USA)")  # typo fixed below
        tg_send(txt2)
    else:
        tg_send("📉 <b>Top Losers USA (Finviz)</b>\nNo se han podido obtener datos hoy.")

# fix small typo function name
def to_prety_table(df, title):  # alias for safety if copy/paste
    return to_pretty_table(df, title)

if __name__ == "__main__":
    main()

