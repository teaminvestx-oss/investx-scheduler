import os, io
import pandas as pd
import yfinance as yf
import requests
import matplotlib.pyplot as plt
import squarify

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
assert BOT_TOKEN and CHAT_ID, "Faltan BOT_TOKEN o CHAT_ID"

# 1) Universo y sectores: S&P 500 desde Wikipedia
spx = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
spx = spx.rename(columns={"Symbol": "ticker", "GICS Sector": "sector"})
spx["ticker"] = spx["ticker"].str.replace(".", "-", regex=False)  # YF usa '-' en tickers con punto

tickers = spx["ticker"].tolist()

# 2) Descarga de 3 días para conseguir dos cierres válidos
data = yf.download(
    tickers=tickers,
    period="3d",
    interval="1d",
    auto_adjust=False,
    group_by="ticker",
    threads=True,
    progress=False,
)

rows = []
for t in tickers:
    try:
        df = data[t]
        closes = df["Close"].dropna()
        if len(closes) < 2:
            continue
        prev_close, last_close = closes.iloc[-2], closes.iloc[-1]
        chg_pct = (last_close/prev_close - 1.0) * 100.0
        rows.append((t, float(last_close), float(chg_pct)))
    except Exception:
        continue

px = pd.DataFrame(rows, columns=["ticker", "last", "chg_pct"])
px = px.merge(spx[["ticker", "sector"]], on="ticker", how="left").dropna(subset=["chg_pct"])

# 3) Top 10 subidas y caídas
gainers = px.sort_values("chg_pct", ascending=False).head(10).reset_index(drop=True)
losers  = px.sort_values("chg_pct", ascending=True).head(10).reset_index(drop=True)

def fmt(df):
    lines = []
    for i, r in df.iterrows():
        sign = "+" if r["chg_pct"] >= 0 else ""
        lines.append(f"{i+1}) {r['ticker']} {sign}{r['chg_pct']:.2f}% (cierre {r['last']:.2f})")
    return "\n".join(lines)

txt_gainers = fmt(gainers)
txt_losers  = fmt(losers)

# 4) Heatmap por sectores (tamaño = nº componentes, color = variación media del día)
sector_stats = px.groupby("sector", as_index=False).agg(
    mean_chg_pct=("chg_pct", "mean"),
    count=("ticker", "count")
).sort_values("mean_chg_pct", ascending=False)

labels = [f"{row['sector']}\n{row['mean_chg_pct']:.2f}%"
          for _, row in sector_stats.iterrows()]
sizes  = sector_stats["count"].tolist()
colors = sector_stats["mean_chg_pct"].tolist()

plt.figure(figsize=(14, 8))
norm = plt.Normalize(min(colors), max(colors) if max(colors) != min(colors) else min(colors) + 1e-9)
cmap = plt.cm.get_cmap()
squarify.plot(
    sizes=sizes,
    label=labels,
    color=[cmap(norm(c)) for c in colors],
    alpha=0.9,
    text_kwargs={"fontsize": 10},
)
plt.axis("off")
plt.title("S&P 500 – Variación media por sector (hoy)")

buf = io.BytesIO()
plt.savefig(buf, format="png", dpi=140, bbox_inches="tight")
plt.close()
buf.seek(0)

# 5) Envío a Telegram: foto + caption (un único mensaje)
caption = (
    "📈 <b>InvestX – Top Movers (S&P 500)</b>\n"
    "🕥 Resumen del cierre USA (Top 10 del día)\n\n"
    "🏆 <b>Top Subidas</b>\n"
    f"{txt_gainers}\n\n"
    "📉 <b>Top Caídas</b>\n"
    f"{txt_losers}\n\n"
    "🗺️ Heatmap por sectores en la imagen."
)

files = {"photo": ("heatmap.png", buf.getvalue(), "image/png")}
payload = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}

r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=payload, files=files, timeout=60)
r.raise_for_status()
print("✔️ Enviado a Telegram")
