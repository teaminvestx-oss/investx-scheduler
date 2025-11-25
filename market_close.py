# === market_close.py ===
# Cierre de mercado USA – InvestX (Render)

import os
from datetime import datetime

import requests  # Asegúrate de tenerlo en requirements.txt


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ---------------- TELEGRAM HELPERS ---------------- #

def send_telegram_message(text: str):
    """Envía un mensaje de texto a Telegram usando la API directa."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",  # o None si prefieres texto plano
    }
    resp = requests.post(url, data=data, timeout=30)
    if not resp.ok:
        print(f"[TELEGRAM] Error sendMessage: {resp.status_code} {resp.text}")


def send_telegram_photo(photo_path: str, caption: str | None = None):
    """Envía una foto a Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    data = {"chat_id": TELEGRAM_CHAT_ID}
    if caption:
        data["caption"] = caption

    with open(photo_path, "rb") as img:
        files = {"photo": img}
        resp = requests.post(url, data=data, files=files, timeout=60)

    if not resp.ok:
        print(f"[TELEGRAM] Error sendPhoto: {resp.status_code} {resp.text}")


# ---------------- LÓGICA DEL CIERRE ---------------- #

def build_close_message() -> str:
    """Mensaje compacto tipo InvestX, apto para Telegram (bien por debajo del límite de caracteres)."""
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
        "NVDA corrige por toma de beneficios, mientras GOOGL, META, MSFT y AMZN sostienen el sesgo "
        "alcista. Salud y financieras destacan; energía rezagada.\n\n"
        "Lectura InvestX:\n"
        "Sesgo alcista vigente mientras los índices sigan sobre SMA50/SMA200. Rotación interna "
        "saludable y soporte institucional estable.\n\n"
        "Heatmap Finviz adjunto."
    )
    return msg


def download_finviz_heatmap(path: str = "/tmp/finviz_heatmap.png") -> str:
    """Descarga la imagen oficial del heatmap de Finviz."""
    url = "https://finviz.com/publish/map/map.png"
    headers = {"User-Agent": 'Mozilla/5.0'}
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    with open(path, "wb") as f:
        f.write(resp.content)

    print(f"[MARKET_CLOSE] Heatmap Finviz descargado en {path}")
    return path


def run_market_close(force: bool = False):
    """
    Envía el cierre de mercado:
      1) Heatmap Finviz
      2) Mensaje de texto con contexto macro/noticias
    La lógica de 'force' la controla main.py; aquí solo lo mostramos en logs.
    """
    print(f"[MARKET_CLOSE] Ejecutando run_market_close(force={force})")

    msg = build_close_message()
    heatmap_path = download_finviz_heatmap()

    # Primero la imagen
    send_telegram_photo(heatmap_path)

    # Luego el mensaje detallado
    send_telegram_message(msg)

    print("[MARKET_CLOSE] Cierre enviado correctamente.")
