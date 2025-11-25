# === market_close.py ===
# Cierre de mercado USA – InvestX

import os
from datetime import datetime
from telegram import Bot

# TOKEN y CHAT_ID desde variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

bot = Bot(token=TELEGRAM_TOKEN)


def build_close_message() -> str:
    """Mensaje compacto tipo InvestX, apto para Telegram."""
    today = datetime.utcnow().strftime("%d/%m/%Y")

    msg = (
        f"📊 Cierre de Wall Street — InvestX ({today})\n\n"
        "Índices:\n"
        "S&P 500 +0,7% · Nasdaq 100 +0,7% · Dow Jones +0,4%.\n\n"
        "Amplitud:\n"
        "≈70% valores al alza y >200 nuevos máximos. Lectura sólida.\n\n"
        "Contexto macro:\n"
        "El mercado mantiene tono positivo apoyado por expectativas de recortes de la Fed en 2025 "
        "y una inflación que sigue moderándose; el 10Y estable reduce presión sobre growth.\n\n"
        "Noticias / flujo:\n"
        "NVDA corrige por toma de beneficios, mientras GOOGL, META, MSFT y AMZN sostienen "
        "el sesgo alcista. Salud y financieras destacan; energía rezagada.\n\n"
        "Lectura InvestX:\n"
        "Sesgo alcista vigente mientras los índices sigan sobre SMA50/SMA200. Rotación interna "
        "saludable y soporte institucional estable.\n\n"
        "Heatmap Finviz adjunto."
    )
    return msg


def download_finviz_heatmap(path: str = "/tmp/finviz_heatmap.png") -> str:
    """Descarga la imagen oficial del heatmap de Finviz."""
    import requests

    url = "https://finviz.com/publish/map/map.png"
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    return path


def run_market_close(force: bool = False):
    """
    Envía el mensaje de cierre + heatmap.
    El parámetro `force` se usa solo para trazas, la lógica de forzado está en main.py.
    """
    print(f"[MARKET_CLOSE] Iniciando run_market_close(force={force})")

    text = build_close_message()
    heatmap_path = download_finviz_heatmap()

    # Primero la imagen, luego el texto
    with open(heatmap_path, "rb") as img:
        bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=img)

    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
    print("[MARKET_CLOSE] Cierre enviado correctamente.")
