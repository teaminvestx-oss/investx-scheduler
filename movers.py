# movers.py — Top movers USA (SPX+NDX+DJI+R2K)
# Heatmap jerárquico estilo Finviz: Sector → Industria → Tickers
import os, io, re, math, warnings
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import squarify
from PIL import Image
from math import ceil

# ---------------- Config ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")
assert BOT_TOKEN and CHAT_ID, "Faltan BOT_TOKEN o CHAT_ID"

ONLY_GAINERS = False           # True: solo subidas
LIMIT_PER_INDEX = 60           # traemos más filas para cubrir sectores/industrias
TOP_N = 10
MAX_CAPTION = 980              # < 1024

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
warnings.filterwarnings("ignore", category=FutureWarning)
_TK = re.compile(r"^[A-Z][A-Z0-9.\-]{0,5}$")

BASE_COLS = ["Ticker","Company","Sector","Industry","Country","Market Cap","Price","Change","Volume"]

# ---------------- Helpers ----------------
def pick_finviz_table(html_text: str) -> pd.DataFrame:
    """Elige la tabla de resultados del screener (no la de filtros)."""
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

def ensure_columns(df: pd.DataFrame, cols=BASE_COLS) -> pd.DataFrame:
    """Garantiza que existan todas las columnas necesarias."""
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    return df

def finviz_fetch(index_code: str, top=True, limit=LIMIT_PER_INDEX) -> pd.DataFrame:
    """Devuelve un DataFrame estandarizado (puede ser vacío, pero con columnas)."""
    s = "ta_topgainers" if top else "ta_toplosers"
    url = f"https://finviz.com/screener.ashx?v=111&s={s}&f={index_code}"
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    df = pick_finviz_table(r.text)

    # si falta Ticker, devolvemos vacío estandarizado
    if "Ticker" not in df.columns:
        out = pd.DataFrame(columns=BASE_COLS + ["chg_pct","mcap"])
        return out

    df = df[[c for c in BASE_COLS if c in df.columns]].copy()
    df = ensure_columns(df)

    # limpia filas basura
    df = df[df["Ticker"].astype(str).str.strip().apply(lambda x: bool(_TK.match(x)))]
    # si tras limpiar quedó vacío, devolvemos vacío estandarizado
    if df.empty:
        out = pd.DataFrame(columns=BASE_COLS + ["chg_pct","mcap"])
        return out

    def pchg(x):
        if isinstance(x,str) and "%" in x:
            s = x.replace("%","").replace("+","").replace(",","").strip()
            try:
                v = float(s)
                return -v if x.strip().startswith("-") else v
            except:
                return float("nan")
        return float("nan")

    def pmcap(x):
        if not isinstance(x,str): return float("nan")
        s = x.strip().upper().replace(",","")
        m = re.match(r"^([0-9]*\.?[0-9]+)\s*([KMBT])$", s)
        if not m: return float("nan")
        val, suf = float(m.group(1)), m.group(2)
        mult = {"K":1e3,"M":1e6,"B":1e9,"T":1e12}[suf]
        return val*mult

    df["chg_pct"] = df["Change"].apply(pchg) if "Change" in df.columns else float("nan")
    df["mcap"]    = df["Market Cap"].apply(pmcap) if "Market Cap" in df.columns else float("nan")

    # orden consistente
    if df["chg_pct"].notna().any():
        df = df.sort_values("chg_pct", ascending=not top)

    return df.head(limit).reset_index(drop=True)

def fmt_top(df: pd.DataFrame, n=TOP_N, losers=False) -> str:
    if df.empty: return "—"
    if "chg_pct" in df.columns and df["chg_pct"].notna().any():
        df = df.sort_values("chg_pct", ascending=losers).head(n)
    else:
        df = df.head(n)
    lines=[]
    for i,r in df.reset_index(drop=True).iterrows():
        ch = r["Change"] if isinstance(r["Change"],str) and r["Change"] else (f"{r['chg_pct']:+.2f}%" if "chg_pct" in r else "—")
        price = r["Price"] if str(r["Price"])!="" else "—"
        comp  = r["Company"] if str(r["Company"])!="" else ""
        lines.append(f"{i+1}) {r['Ticker']} {ch} – {comp} (Price {price})")
    return "\n".join(lines)

# ---------------- Fetch movers ----------------
frames_g, frames_l = [], []
for name, code in INDICES.items():
    try:
        g = finviz_fetch(code, top=True);  g["Index"]=name; frames_g.append(g)
        if not ONLY_GAINERS:
            l = finviz_fetch(code, top=False); l["Index"]=name; frames_l.append(l)
    except Exception as e:
        print(f"[WARN] {name}: {e}")

gainers_all = pd.concat(frames_g, ignore_index=True) if frames_g else pd.DataFrame(columns=BASE_COLS + ["chg_pct","mcap"])
losers_all  = pd.concat(frames_l, ignore_index=True) if (frames_l and not ONLY_GAINERS) else pd.DataFrame(columns=BASE_COLS + ["chg_pct","mcap"])

# ---------------- Caption ----------------
caption_parts = [
    "📈 <b>InvestX – Top Movers USA</b>\nSPX + NDX + DJI + R2K (cierre)"
]
if not gainers_all.empty:
    caption_parts += ["\n🏆 <b>Top 10 Subidas (USA)</b>", fmt_top(gainers_all, TOP_N, losers=False)]
if not ONLY_GAINERS and not losers_all.empty:
    caption_parts += ["\n📉 <b>Top 10 Caídas (USA)</b>", fmt_top(losers_all, TOP_N, losers=True)]
caption_parts += ["\n🗺️ Heatmap estilo Finviz por sector/industria en la imagen."]
caption = "\n".join(caption_parts)
caption_safe = caption if len(caption)<=MAX_CAPTION else caption[:MAX_CAPTION]+"…"

# ---------------- Datos para heatmap jerárquico ----------------
movers = pd.concat([gainers_all, losers_all], ignore_index=True) if not ONLY_GAINERS else gainers_all.copy()
# aseguro columnas aunque algún índice haya venido vacío
movers = ensure_columns(movers)
# filtro seguro por sector/industria no vacíos
movers = movers[(movers["Sector"].astype(str)!="") & (movers["Industry"].astype(str)!="")]

if movers.empty:
    # sin datos válidos → enviamos solo texto
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                  data={"chat_id": CHAT_ID, "text": caption_safe, "parse_mode":"HTML"}, timeout=60)
    raise SystemExit("Sin datos para el heatmap")

# ----------- Dibujo: grid de sectores; dentro, industrias y tickers -----------
sectors = sorted(movers["Sector"].dropna().unique().tolist())
n_sec   = len(sectors)
cols    = min(4, n_sec) if n_sec>0 else 1
rows    = ceil(n_sec/cols) if n_sec>0 else 1

fig_w, fig_h = 18, max(10, 2.8*rows)
fig, axes = plt.subplots(rows, cols, figsize=(fig_w, fig_h))
if isinstance(axes, np.ndarray):
    axes = axes.flatten()
else:
    axes = np.array([axes])

for ax in axes: ax.axis("off")
plt.suptitle("InvestX – Sector → Industria → Tickers (tamaño=mcap, color=%)", y=0.995, fontsize=14)

cmap = plt.cm.RdYlGn  # rojo⇄verde

global_chg = movers["chg_pct"].fillna(0.0)
gmin, gmax = float(global_chg.min()), float(global_chg.max())
if gmax - gmin < 1e-6: gmin, gmax = -1.0, 1.0
gnorm = plt.Normalize(gmin, gmax)

for i, sec in enumerate(sectors):
    ax = axes[i]
    ax.set_title(sec, fontsize=11, pad=4)

    sub_sec = movers[movers["Sector"]==sec].copy()

    # 1) INDUSTRIAS dentro del sector
    ind_group = sub_sec.groupby("Industry", as_index=False).agg(
        ind_mcap=("mcap", lambda s: float(s[s.notna()].sum()) if s.notna().any() else float(len(s))),
        ind_chg=("chg_pct","mean")
    ).sort_values("ind_mcap", ascending=False)

    sizes_ind = ind_group["ind_mcap"].tolist()
    # rectángulos industria (en un lienzo 0..100)
    rects_ind = squarify.squarify(
        squarify.normalize_sizes(sizes_ind, 100, 100), 0, 0, 100, 100
    )

    # Dibuja cada industria y, dentro, los tickers
    for rect, (_, row) in zip(rects_ind, ind_group.iterrows()):
        x0, y0, w, h = rect["x"], rect["y"], rect["dx"], rect["dy"]
        # marco de la industria
        ax.add_patch(Rectangle((x0, y0), w, h, fill=False, lw=0.5, ec="#222222"))

        sub_ind = sub_sec[sub_sec["Industry"]==row["Industry"]].copy()

        # 2) TICKERS dentro de la industria
        sizes_tk = [v if (isinstance(v,(int,float)) and not math.isnan(v) and v>0) else 1.0 for v in sub_ind["mcap"].tolist()]
        rects_tk = squarify.squarify(
            squarify.normalize_sizes(sizes_tk, w, h), x0, y0, w, h
        )

        chg = sub_ind["chg_pct"].fillna(0.0).tolist()
        for (rx, ry, rw, rh), tk, cval in zip(
            [(r["x"], r["y"], r["dx"], r["dy"]) for r in rects_tk],
            sub_ind["Ticker"].tolist(),
            chg
        ):
            color = cmap(gnorm(cval))
            ax.add_patch(Rectangle((rx, ry), rw, rh, color=color, lw=0.3, ec="#111111"))
            # etiqueta
            fg = "#000" if cval>0 else "#fff"
            ax.text(rx+rw*0.02, ry+rh*0.05, f"{tk}\n{cval:+.2f}%", fontsize=7, color=fg, ha="left", va="top")

    ax.set_xlim(0,100); ax.set_ylim(0,100); ax.invert_yaxis()
    ax.axis("off")

plt.tight_layout(rect=[0,0,1,0.97])

# -------- Export a JPEG y enviar (con fallback) --------
buf_png = io.BytesIO()
plt.savefig(buf_png, format="png", dpi=140, bbox_inches="tight"); plt.close(); buf_png.seek(0)
img = Image.open(buf_png).convert("RGB")

# Normaliza tamaño
target_w = 1600
ratio = target_w / img.size[0]
new_h = int(img.size[1] * ratio)
if new_h > 1000:
    ratio = 1000 / img.size[1]; target_w = int(img.size[0]*ratio); new_h = 1000
img = img.resize((target_w, new_h), Image.LANCZOS)

buf_jpg = io.BytesIO()
img.save(buf_jpg, format="JPEG", quality=90, optimize=True, progressive=True)
buf_jpg.seek(0)

files = {"photo": ("movers_finviz_style.jpg", buf_jpg.getvalue(), "image/jpeg")}
payload = {"chat_id": CHAT_ID, "caption": caption_safe, "parse_mode": "HTML"}

resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=payload, files=files, timeout=60)
print("Telegram response (sendPhoto):", resp.text)
if not resp.ok:
    files_doc = {"document": ("movers_finviz_style.jpg", buf_jpg.getvalue(), "image/jpeg")}
    resp2 = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
        data={"chat_id": CHAT_ID, "caption": caption_safe, "parse_mode":"HTML"},
        files=files_doc, timeout=60
    )
    print("Telegram response (sendDocument):", resp2.text)
    resp2.raise_for_status()
else:
    resp.raise_for_status()
print("✔️ Enviado a Telegram")


