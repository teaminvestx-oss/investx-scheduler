# movers.py — Finviz Top Gainers/Losers por índice + heatmap
import os, io, math, re
import requests
import pandas as pd
import matplotlib.pyplot as plt
import squarify

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID   = os.environ.get("CHAT_ID")
assert BOT_TOKEN and CHAT_ID, "Faltan BOT_TOKEN o CHAT_ID"

# ---- Config ----
ONLY_GAINERS = False          # Si quieres SOLO ganadores, pon True
LIMIT_PER_INDEX = 10          # nº filas por índice
INDICES = {
    "S&P 500":     "idx_sp500",
    "Nasdaq-100":  "idx_nasdaq100",
    "Dow Jones":   "idx_dji",
    "Russell 2000":"idx_russell2000",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finviz.com/",
}

def finviz_table(url: str) -> pd.DataFrame:
    """Descarga y devuelve la tabla principal del screener de Finviz."""
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    dfs = pd.read_html(r.text)
    # Busca la tabla que tenga columnas típicas del screener
    for df in dfs:
        cols = [c for c in df.columns]
        if "Ticker" in cols and "Company" in cols and "Sector" in cols and "Change" in cols:
            return df.copy()
    # Fallback: última tabla
    return dfs[-1].copy()

def parse_change(x: str) -> float:
    # "+3.45%" -> 3.45 ; "-1.2%" -> -1.2
    if isinstance(x, str) and "%" in x:
        try:
            return float(x.replace("%","").replace("+","").strip()) * ( -1 if x.strip().startswith("-") else 1 )
        except Exception:
            return float("nan")
    return float("nan")

def parse_mcap(x: str) -> float:
    # "1.23T" -> 1.23e12, "45.6B" -> 4.56e10, "320M" -> 3.2e8, "—" -> nan
    if not isinstance(x, str): return float("nan")
    s = x.strip().upper().replace(",","")
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*([KMBT])$", s)
    if not m: 
        return float("nan")
    val, suf = float(m.group(1)), m.group(2)
    mult = {"K":1e3, "M":1e6, "B":1e9, "T":1e12}[suf]
    return val * mult

def get_movers(index_code: str, top: bool=True, limit:int=10) -> pd.DataFrame:
    # s = ta_topgainers / ta_toplosers
    s = "ta_topgainers" if top else "ta_toplosers"
    url = f"https://finviz.com/screener.ashx?v=111&s={s}&f={index_code}"
    df = finviz_table(url)
    # Normaliza columnas que nos interesan
    keep = [c for c in ["Ticker","Company","Sector","Industry","Country","Market Cap","Price","Change","Volume"] if c in df.columns]
    df = df[keep].head(limit).copy()
    df["chg_pct"] = df["Change"].apply(parse_change)
    df["mcap"]    = df.get("Market Cap", pd.Series([float("nan")]*len(df))).apply(parse_mcap)
    return df

# ---- Descarga por índice ----
frames_g, frames_l = [], []
for idx_name, idx_code in INDICES.items():
    try:
        g = get_movers(idx_code, top=True,  limit=LIMIT_PER_INDEX);  g["Index"]=idx_name; frames_g.append(g)
        if not ONLY_GAINERS:
            l = get_movers(idx_code, top=False, limit=LIMIT_PER_INDEX); l["Index"]=idx_name; frames_l.append(l)
    except Exception as e:
        print(f"[WARN] No se pudo leer {idx_name}: {e}")

gainers_all = pd.concat(frames_g, ignore_index=True) if frames_g else pd.DataFrame()
losers_all  = pd.concat(frames_l, ignore_index=True) if (frames_l and not ONLY_GAINERS) else pd.DataFrame()

# ---- Texto Telegram ----
def fmt_table(df: pd.DataFrame) -> str:
    if df.empty: return "—"
    lines = []
    for i, r in df.reset_index(drop=True).iterrows():
        # "1) NVDA +3.21% (SPX) – NVIDIA Corp. (Price 124.30)"
        lines.append(f"{i+1}) {r['Ticker']} {r['Change']} ({r['Index']}) – {r['Company']} (Price {r['Price']})")
    return "\n".join(lines)

# Top 10 USA combinado por % (sobre el conjunto de índices)
def top_usa(df: pd.DataFrame, n=10) -> pd.DataFrame:
    if df.empty: return df
    return df.sort_values("chg_pct", ascending=False).head(n)

caption_parts = []
caption_parts.append("📈 <b>InvestX – Top Movers USA</b>\n🕥 Resumen del cierre (SPX + NDX + DJI + R2K)")

# Top Gainers (USA)
g_usa = top_usa(gainers_all, 10)
caption_parts.append("🏆 <b>Top 10 Subidas (USA)</b>")
caption_parts.append(fmt_table(g_usa))

# Por índice (ganadores)
for idx in INDICES.keys():
    sub = gainers_all[gainers_all["Index"]==idx].copy()
    if not sub.empty:
        caption_parts.append(f"➕ <b>Top Subidas – {idx}</b>")
        caption_parts.append(fmt_table(sub))

# (Opcional) Top Losers
if not ONLY_GAINERS and not losers_all.empty:
    l_usa = losers_all.sort_values("chg_pct", ascending=True).head(10)
    caption_parts.append("\n📉 <b>Top 10 Caídas (USA)</b>")
    caption_parts.append(fmt_table(l_usa))

caption_parts.append("\n🗺️ Heatmap por movers en la imagen.")
caption = "\n".join(caption_parts)

# ---- Heatmap (treemap) de los movers combinados (ganadores + perdedores) ----
heat_df = pd.concat([gainers_all, losers_all], ignore_index=True) if not ONLY_GAINERS else gainers_all.copy()
if heat_df.empty:
    # si no hay datos, manda texto y termina
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                  data={"chat_id": CHAT_ID, "text": caption, "parse_mode":"HTML"}, timeout=60)
    raise SystemExit("Sin datos que graficar")

# tamaño: market cap (fallback = igual tamaño)
sizes = []
for _, r in heat_df.iterrows():
    sizes.append(r["mcap"] if not math.isnan(r["mcap"]) else 1.0)

# color: % cambio normalizado
chg = heat_df["chg_pct"].fillna(0.0)
mn, mx = float(chg.min()), float(chg.max())
if mx - mn < 1e-6:
    mn, mx = -1.0, 1.0
norm = plt.Normalize(mn, mx)
cmap = plt.cm.RdYlGn  # rojo (caídas) ↔ verde (subidas)
colors = [cmap(norm(v)) for v in chg.tolist()]

labels = [f"{t}\n{c:+.2f}%" for t, c in zip(heat_df["Ticker"].tolist(), chg.tolist())]

plt.figure(figsize=(16, 9))
squarify.plot(sizes=sizes, label=labels, color=colors, alpha=0.9, text_kwargs={"fontsize":10})
plt.axis("off")
plt.title("InvestX – Heatmap movers (SPX + NDX + DJI + R2K)")

buf = io.BytesIO()
plt.savefig(buf, format="png", dpi=140, bbox_inches="tight")
plt.close()
buf.seek(0)

# ---- Envío a Telegram (foto + caption) ----
files = {"photo": ("movers_heatmap.png", buf.getvalue(), "image/png")}
payload = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=payload, files=files, timeout=60)
r.raise_for_status()
print("✔️ Enviado a Telegram")
