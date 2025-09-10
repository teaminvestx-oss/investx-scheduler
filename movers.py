# movers.py — Finviz Top Movers por índices USA + Heatmap robusto
import os, io, re, math, warnings
import requests
import pandas as pd
import matplotlib.pyplot as plt
import squarify
from PIL import Image

# ---------------- Config ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")
assert BOT_TOKEN and CHAT_ID, "Faltan BOT_TOKEN o CHAT_ID"

ONLY_GAINERS = False              # True => solo ganadores
LIMIT_PER_INDEX = 10              # Nº filas por índice
MAX_CAPTION = 1000                # < 1024 (límite Telegram)

# Índices Finviz
INDICES = {
    "S&P 500":      "idx_sp500",
    "Nasdaq-100":   "idx_nasdaq100",
    "Dow Jones":    "idx_dji",
    "Russell 2000": "idx_russell2000",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finviz.com/",
}

warnings.filterwarnings(
    "ignore",
    message="Passing literal html to 'read_html' is deprecated",
    category=FutureWarning
)

# ---------------- Utilidades ----------------
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,5}$")  # patrón razonable de ticker

def pick_finviz_table(html_text: str) -> pd.DataFrame:
    """
    Lee todas las tablas y devuelve SOLO la tabla de resultados del screener.
    Criterios:
      - Debe tener columna 'Ticker'
      - Al menos 5 filas con Ticker que cumpla el patrón regex
      - Debe contener 'Change' o 'Price'
    """
    tables = pd.read_html(html_text)
    best = None
    best_score = -1
    for df in tables:
        cols = [str(c) for c in df.columns]
        if "Ticker" not in cols:
            continue
        colset = set(cols)
        if not ({"Change","Price"} & colset):
            continue

        # cuenta tickers válidos
        valid = 0
        tickers = df["Ticker"].astype(str).tolist()
        for t in tickers:
            t = t.strip()
            if _TICKER_RE.match(t):
                valid += 1
        score = valid

        if score > best_score:
            best_score = score
            best = df

    if best is None:
        # Fallback a la última, pero luego filtraremos y si queda vacía enviaremos solo texto
        best = tables[-1]

    return best.copy()

def finviz_fetch(index_code: str, top: bool=True) -> pd.DataFrame:
    s = "ta_topgainers" if top else "ta_toplosers"
    url = f"https://finviz.com/screener.ashx?v=111&s={s}&f={index_code}"
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    df = pick_finviz_table(r.text)

    # Normaliza columnas esperadas
    pref_cols = ["Ticker","Company","Sector","Industry","Country","Market Cap","Price","Change","Volume"]
    cols = [c for c in pref_cols if c in df.columns]
    df = df[cols].copy()

    # Limpieza: quita filas basura (Reset Filters, cabeceras, etc.)
    def is_valid_ticker(x):
        return isinstance(x, str) and bool(_TICKER_RE.match(x.strip()))
    df = df[df["Ticker"].apply(is_valid_ticker)]

    # Asegura columnas
    for c in pref_cols:
        if c not in df.columns:
            df[c] = ""

    # Parseos
    def parse_change(x):
        if isinstance(x, str) and "%" in x:
            s = x.replace("%","").replace("+","").replace(",","").strip()
            try:
                val = float(s)
                return -val if x.strip().startswith("-") else val
            except:
                return float("nan")
        return float("nan")

    def parse_mcap(x):
        if not isinstance(x, str):
            return float("nan")
        s = x.strip().upper().replace(",","")
        m = re.match(r"^([0-9]*\.?[0-9]+)\s*([KMBT])$", s)
        if not m:
            return float("nan")
        val, suf = float(m.group(1)), m.group(2)
        mult = {"K":1e3,"M":1e6,"B":1e9,"T":1e12}[suf]
        return val*mult

    df["chg_pct"] = df["Change"].apply(parse_change) if "Change" in df.columns else float("nan")
    df["mcap"]    = df["Market Cap"].apply(parse_mcap) if "Market Cap" in df.columns else float("nan")

    # Orden por cambio o por precio si no hay change
    if df["chg_pct"].notna().any():
        df = df.sort_values("chg_pct", ascending=not top)
    elif "Price" in df.columns:
        # si no hay change (raro), ordena por Price desc (placeholder)
        df = df.sort_values("Price", ascending=False)

    return df.head(LIMIT_PER_INDEX).reset_index(drop=True)

def fmt_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "—"
    out = []
    for i, r in df.iterrows():
        change = r["Change"] if isinstance(r["Change"], str) and r["Change"] else f"{r['chg_pct']:+.2f}%"
        price  = r["Price"] if str(r["Price"]) != "" else "—"
        comp   = r["Company"] if str(r["Company"]) != "" else ""
        out.append(f"{i+1}) {r['Ticker']} {change} – {comp} (Price {price})")
    return "\n".join(out)

def top_usa(df: pd.DataFrame, n=10, reverse=False) -> pd.DataFrame:
    if df.empty:
        return df
    if df["chg_pct"].notna().any():
        return df.sort_values("chg_pct", ascending=reverse).head(n)
    return df.head(n)

# ---------------- Descarga por índices ----------------
frames_g, frames_l = [], []
for idx_name, idx_code in INDICES.items():
    try:
        g = finviz_fetch(idx_code, top=True);  g["Index"] = idx_name; frames_g.append(g)
        if not ONLY_GAINERS:
            l = finviz_fetch(idx_code, top=False); l["Index"] = idx_name; frames_l.append(l)
    except Exception as e:
        print(f"[WARN] {idx_name}: {e}")

gainers_all = pd.concat(frames_g, ignore_index=True) if frames_g else pd.DataFrame()
losers_all  = pd.concat(frames_l, ignore_index=True) if (frames_l and not ONLY_GAINERS) else pd.DataFrame()

# ---------------- Texto ----------------
caption_parts = []
caption_parts.append("📈 <b>InvestX – Top Movers USA</b>\nSPX + NDX + DJI + R2K (cierre)")

g_usa = top_usa(gainers_all, 10, reverse=False)
caption_parts.append("\n🏆 <b>Top 10 Subidas (USA)</b>")
caption_parts.append(fmt_table(g_usa))

if not ONLY_GAINERS and not losers_all.empty:
    l_usa = top_usa(losers_all, 10, reverse=True)
    caption_parts.append("\n📉 <b>Top 10 Caídas (USA)</b>")
    caption_parts.append(fmt_table(l_usa))

caption_parts.append("\n🗺️ Heatmap por movers en la imagen.")
caption = "\n".join(caption_parts)

# ---------------- Heatmap ----------------
heat_df = pd.concat([gainers_all, losers_all], ignore_index=True) if not ONLY_GAINERS else gainers_all.copy()

if heat_df.empty:
    # Si no hay datos válidos, envía solo el texto
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                  data={"chat_id": CHAT_ID, "text": caption[:4096], "parse_mode": "HTML"}, timeout=60)
    raise SystemExit("Sin datos válidos para graficar")

# Tamaño por market cap (fallback tamaño 1)
sizes = [r["mcap"] if (isinstance(r["mcap"], (int,float)) and not math.isnan(r["mcap"])) else 1.0
         for _, r in heat_df.iterrows()]
# Color por % cambio
chg = heat_df["chg_pct"].fillna(0.0)
mn, mx = float(chg.min()), float(chg.max())
if mx - mn < 1e-6:
    mn, mx = -1.0, 1.0
norm = plt.Normalize(mn, mx)
cmap = plt.cm.RdYlGn
colors = [cmap(norm(v)) for v in chg.tolist()]
labels = [f"{t}\n{c:+.2f}%" for t, c in zip(heat_df["Ticker"].tolist(), chg.tolist())]

plt.figure(figsize=(16, 9))
squarify.plot(sizes=sizes, label=labels, color=colors, alpha=0.9, text_kwargs={"fontsize": 10})
plt.axis("off")
plt.title("InvestX – Movers (SPX + NDX + DJI + R2K)")

# ---------- Export a JPEG 1280x720 y enviar ----------
buf_png = io.BytesIO()
plt.savefig(buf_png, format="png", dpi=140, bbox_inches="tight")
plt.close()
buf_png.seek(0)

img = Image.open(buf_png).convert("RGB")
TARGET_W, TARGET_H = 1280, 720
canvas = Image.new("RGB", (TARGET_W, TARGET_H), (255, 255, 255))
w, h = img.size
ratio = min(TARGET_W / w, TARGET_H / h)
new_w, new_h = max(1, int(w * ratio)), max(1, int(h * ratio))
img = img.resize((new_w, new_h), Image.LANCZOS)
offset = ((TARGET_W - new_w) // 2, (TARGET_H - new_h) // 2)
canvas.paste(img, offset)

buf_jpg = io.BytesIO()
canvas.save(buf_jpg, format="JPEG", quality=90, optimize=True, progressive=True)
buf_jpg.seek(0)

caption_safe = caption if len(caption) <= MAX_CAPTION else caption[:MAX_CAPTION] + "…"

# Envío con fallback a documento
files = {"photo": ("movers_heatmap.jpg", buf_jpg.getvalue(), "image/jpeg")}
payload = {"chat_id": CHAT_ID, "caption": caption_safe, "parse_mode": "HTML"}

resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                     data=payload, files=files, timeout=60)
print("Telegram response (sendPhoto):", resp.text)
if not resp.ok:
    files_doc = {"document": ("movers_heatmap.jpg", buf_jpg.getvalue(), "image/jpeg")}
    payload_doc = {"chat_id": CHAT_ID, "caption": caption_safe, "parse_mode": "HTML"}
    resp2 = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                          data=payload_doc, files=files_doc, timeout=60)
    print("Telegram response (sendDocument):", resp2.text)
    resp2.raise_for_status()
else:
    resp.raise_for_status()

print("✔️ Enviado a Telegram")

