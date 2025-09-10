# movers.py — Finviz Top Gainers/Losers por índices USA + Heatmap (JPEG seguro para Telegram)
import os, io, re, math, warnings
import requests
import pandas as pd
import matplotlib.pyplot as plt
import squarify
from PIL import Image

# ---------- Config ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")
assert BOT_TOKEN and CHAT_ID, "Faltan BOT_TOKEN o CHAT_ID"

ONLY_GAINERS = False              # True => solo ganadores (sin perdedores)
LIMIT_PER_INDEX = 10              # Nº de filas por índice
MAX_CAPTION = 1000                # Seguridad bajo límite telegram (1024)

# Índices principales de USA en Finviz
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
    "Cache-Control": "no-cache",
}

warnings.filterwarnings(
    "ignore",
    message="Passing literal html to 'read_html' is deprecated",
    category=FutureWarning
)

# ---------- Utilidades ----------
def finviz_table(url: str) -> pd.DataFrame:
    """Descarga la página del screener y devuelve la tabla principal."""
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    dfs = pd.read_html(r.text)
    # Busca una tabla con columnas típicas
    for df in dfs:
        cols = set(map(str, df.columns))
        if {"Ticker", "Company", "Sector", "Change"} & cols:
            return df.copy()
    # Fallback
    return dfs[-1].copy()

def parse_change(x: str) -> float:
    # "+3.45%" -> 3.45 ; "-1.2%" -> -1.2
    if isinstance(x, str) and "%" in x:
        s = x.strip().replace("%", "")
        try:
            val = float(s.replace("+", "").replace(",", ""))
            return -val if x.strip().startswith("-") else val
        except Exception:
            return float("nan")
    return float("nan")

def parse_mcap(x: str) -> float:
    # "1.23T" -> 1.23e12, "45.6B" -> 4.56e10, "320M" -> 3.2e8
    if not isinstance(x, str):
        return float("nan")
    s = x.strip().upper().replace(",", "")
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*([KMBT])$", s)
    if not m:
        return float("nan")
    val, suf = float(m.group(1)), m.group(2)
    mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[suf]
    return val * mult

def get_movers(index_code: str, top: bool = True, limit: int = 10) -> pd.DataFrame:
    """Obtiene top gainers/losers del índice en Finviz."""
    s = "ta_topgainers" if top else "ta_toplosers"
    url = f"https://finviz.com/screener.ashx?v=111&s={s}&f={index_code}"
    df = finviz_table(url)

    # columnas que solemos ver en Finviz (algunas pueden faltar)
    pref_cols = ["Ticker","Company","Sector","Industry","Country","Market Cap","Price","Change","Volume"]
    cols = [c for c in pref_cols if c in df.columns]
    df = df[cols].head(limit).copy()

    # normalizamos
    if "Change" in df.columns:
        df["chg_pct"] = df["Change"].apply(parse_change)
    else:
        df["chg_pct"] = float("nan")

    if "Market Cap" in df.columns:
        df["mcap"] = df["Market Cap"].apply(parse_mcap)
    else:
        df["mcap"] = float("nan")

    # Asegura columnas clave para formatear
    for c in ["Company","Sector","Price","Volume"]:
        if c not in df.columns:
            df[c] = ""
    return df

def fmt_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "—"
    lines = []
    for i, r in df.reset_index(drop=True).iterrows():
        price = r["Price"] if str(r["Price"]) != "" else "—"
        comp  = r["Company"] if str(r["Company"]) != "" else ""
        lines.append(f"{i+1}) {r['Ticker']} {r['Change']} – {comp} (Price {price})")
    return "\n".join(lines)

def top_usa(df: pd.DataFrame, n=10, reverse=False) -> pd.DataFrame:
    if df.empty:
        return df
    return df.sort_values("chg_pct", ascending=reverse).head(n)

# ---------- Descarga movers por índice ----------
frames_g, frames_l = [], []
for idx_name, idx_code in INDICES.items():
    try:
        g = get_movers(idx_code, top=True,  limit=LIMIT_PER_INDEX);  g["Index"] = idx_name; frames_g.append(g)
        if not ONLY_GAINERS:
            l = get_movers(idx_code, top=False, limit=LIMIT_PER_INDEX); l["Index"] = idx_name; frames_l.append(l)
    except Exception as e:
        print(f"[WARN] No se pudo leer {idx_name}: {e}")

gainers_all = pd.concat(frames_g, ignore_index=True) if frames_g else pd.DataFrame()
losers_all  = pd.concat(frames_l, ignore_index=True) if (frames_l and not ONLY_GAINERS) else pd.DataFrame()

# ---------- Construcción del texto ----------
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

# ---------- Heatmap (treemap) de ganadores + perdedores ----------
heat_df = pd.concat([gainers_all, losers_all], ignore_index=True) if not ONLY_GAINERS else gainers_all.copy()
if heat_df.empty:
    # sin datos, manda solo texto
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                  data={"chat_id": CHAT_ID, "text": caption[:4096], "parse_mode": "HTML"}, timeout=60)
    raise SystemExit("Sin datos que graficar")

# tamaño: market cap (si falta => 1.0)
sizes = [r["mcap"] if not math.isnan(r["mcap"]) else 1.0 for _, r in heat_df.iterrows()]
# color: cambio %
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

# ---------- Guardar PNG, convertir a JPEG seguro, y enviar ----------
# Guardamos el plot en PNG en memoria
buf_png = io.BytesIO()
plt.savefig(buf_png, format="png", dpi=140, bbox_inches="tight")
plt.close()
buf_png.seek(0)

# Convertimos a JPEG (RGB sin alpha) y normalizamos tamaño
img = Image.open(buf_png).convert("RGB")
max_w, max_h = 1600, 900
w, h = img.size
ratio = min(max_w / w, max_h / h, 1.0)
if ratio < 1.0:
    img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
if img.size[0] < 10 or img.size[1] < 10:
    img = img.resize((800, 450), Image.LANCZOS)

buf_jpg = io.BytesIO()
img.save(buf_jpg, format="JPEG", quality=90, optimize=True)
buf_jpg.seek(0)

caption_safe = caption if len(caption) <= MAX_CAPTION else caption[:MAX_CAPTION] + "…"

files = {"photo": ("movers_heatmap.jpg", buf_jpg.getvalue(), "image/jpeg")}
payload = {"chat_id": CHAT_ID, "caption": caption_safe, "parse_mode": "HTML"}

r = requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
    data=payload,
    files=files,
    timeout=60
)
print("Telegram response:", r.text)
r.raise_for_status()
print("✔️ Enviado a Telegram")

