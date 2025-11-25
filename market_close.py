# === market_close.py ===
# Cierre de mercado USA – InvestX (Render)

import os
from datetime import datetime

import requests  # asegúrate de tenerlo en requirements.txt


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
        "parse_mode": "Markdown",
    }
    try:
        resp = requests.post(url, data=data, timeout=30)
        if not resp.ok:
            print(f"[TELEGRAM] Error sendMessage: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[TELEGRAM] EXCEPTION sendMessage: {e}")


def send_telegram_photo(photo_path: str, caption: str | None = None):
    """Envía una foto a Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    data = {"chat_id": TELEGRAM_CHAT_ID}
    if caption:
        data["caption"] = caption

    try:
        with open(photo_path, "rb") as img:
            files = {"photo": img}
            resp = requests.post(url, data=data, files=files, timeout=60)

        if not resp.ok:
            print(f"[TELEGRAM] Error sendPhoto: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[TELEGRAM] EXCEPTION sendPhoto: {e}")


# ---------------- LÓGICA DEL CIERRE ---------------- #

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
        "NVDA corrige por toma de beneficios, mientras GOOGL, META, MSFT y AMZN sostienen el sesgo "
        "alcista. Salud y financieras destacan; energía rezagada.\n\n"
        "Lectura InvestX:\n"
        "Sesgo alcista vigente mientras los índices sigan sobre SMA50/SMA200. Rotación interna "
        "saludable y soporte institucional estable."
    )
    return msg


def download_finviz_heatmap(path: str = "/tmp/finviz_heatmap.png") -> str | None:
    """
    Intenta descargar la imagen oficial del heatmap de Finviz.
    Si hay error (500, timeout, etc.) devuelve None para no tumbar el cron.
    """
    url = "https://finviz.com/publish/map/map.png"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"[MARKET_CLOSE] Finviz devolvió {resp.status_code}, no hay heatmap.")
            return None

        with open(path, "wb") as f:
            f.write(resp.content)

        print(f"[MARKET_CLOSE] Heatmap Finviz descargado en {path}")
        return path

    except Exception as e:
        print(f"[MARKET_CLOSE] EXCEPTION descargando heatmap Finviz: {e}")
        return None


def run_market_close(force: bool = False):
    """
    Envía el cierre de mercado:
      - Si el heatmap se descarga bien: foto + texto
      - Si falla Finviz: solo texto
    """
    print(f"[MARKET_CLOSE] Ejecutando run_market_close(force={force})")

    msg = build_close_message()
    heatmap_path = download_finviz_heatmap()

    if heatmap_path:
        # Primero la imagen (sin caption para no repetir el texto)
        send_telegram_photo(heatmap_path)
    else:
        print("[MARKET_CLOSE] No se ha podido obtener heatmap, se envía solo texto.")

    # En cualquier caso, enviamos el mensaje
    send_telegram_message(msg)

    print("[MARKET_CLOSE] Cierre enviado (con o sin heatmap).")
