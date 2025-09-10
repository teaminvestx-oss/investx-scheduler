import os, io, requests
import pandas as pd
import matplotlib.pyplot as plt
import squarify

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Finviz URLs
url_gainers = "https://finviz.com/screener.ashx?v=111&s=ta_topgainers"
url_losers  = "https://finviz.com/screener.ashx?v=111&s=ta_toplosers"

headers = {"User-Agent": "Mozilla/5.0"}

def get_table(url):
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    dfs = pd.read_html(r.text)
    # Finviz: tabla 8 columnas (No., Ticker, Company, Sector, Industry, Country, Market Cap, Price, Change, Volume)
    df = dfs[-2]  # la penúltima suele ser la tabla
    return df

gainers = get_table(url_gainers).head(10)
losers  = get_table(url_losers).head(10)

# Texto Telegram
def fmt(df):
    lines = []
    for i, r in df.iterrows():
        lines.append(f"{i+1}) {r['Ticker']} {r['Change']} (cierre {r['Price']})")
    return "\n".join(lines)

txt_gainers = fmt(gainers)
txt_losers  = fmt(losers)

caption = (
    "📈 <b>InvestX – Top Movers</b>\n"
    "🕥 Resumen del cierre USA (Top 10 del día)\n\n"
    "🏆 <b>Top Subidas</b>\n"
    f"{txt_gainers}\n\n"
    "📉 <b>Top Caídas</b>\n"
    f"{txt_losers}\n\n"
    "🗺️ Heatmap por sectores: pendiente añadir."
)

# (por ahora, solo enviamos texto sin heatmap)
requests.post(
    f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
    data={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML"}
)

