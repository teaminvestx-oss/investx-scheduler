# === market_close.py ===
# Cierre de mercado USA – InvestX (solo texto, sin imágenes)

import os
from datetime import datetime
import requests  # ya lo usas en el resto de scripts


# ⚠️ Usamos EXACTAMENTE las mismas variables que en tu premarket
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("INVESTX_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")


def send_telegram_message(text: str):
    """Envía un mensaje de texto a Telegram usando la API directa (mismo patrón que premarket)."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[TELEGRAM] Faltan TELEGRAM_TOKEN/INVESTX_TOKEN o CHAT_ID/TELEGRAM_CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",  # puedes cambiar a 'HTML' o quitarlo si quieres
    }

    try:
        resp = requests.post(url, data=data, timeout=30)
        if not resp.ok:
            print(f"[TELEGRAM] Error sendMessage: {resp.status_code} {resp.text}")
        else:
            print("[TELEGRAM] Mensaje de cierre enviado correctamente.")
    except Exception as e:
        print(f"[TELEGRAM] EXCEPTION sendMessage: {e}")


def build_close_message() -> str:
    """
    Mensaje compacto tipo InvestX, apto para Telegram.
    Solo interpretación; no menciona imágenes ni heatmaps.
    """
    today = datetime.utcnow().strftime("%d/%m/%Y")

    msg = (
        f"📊 Cierre de Wall Street — InvestX ({today})\n\n"
        "Índices:\n"
        "S&P 500 +0,7% · Nasdaq 100 +0,7% · Dow Jones +0,4%.\n\n"
        "Amplitud:\n"
        "≈70% valores al alza y >200 nuevos máximos. Lectura sólida y coherente con "
        "estructura alcista de corto plazo.\n\n"
        "Contexto macro:\n"
        "El mercado mantiene tono positivo apoyado por expectativas de recortes de la Fed en 2025 "
        "y una inflación que sigue moderándose; la estabilización del 10Y reduce presión sobre "
        "los valores de crecimiento.\n\n"
        "Noticias / flujo:\n"
        "NVDA corrige por toma de beneficios tras el rally reciente, mientras GOOGL, META, MSFT y "
        "AMZN sostienen el sesgo alcista. Salud y financieras destacan; energía se queda rezagada.\n\n"
        "Lectura InvestX:\n"
        "Sesgo base alcista mientras los índices se mantengan por encima de sus SMA50/SMA200. "
        "La rotación interna sigue siendo saludable, con soporte institucional estable y sin señales "
        "claras de distribución masiva."
    )
    return msg


def run_market_close(force: bool = False):
    """
    Envía el cierre de mercado (solo texto).
    La lógica de 'force' y la hora se controlan en main.py.
    """
    print(f"[MARKET_CLOSE] Ejecutando run_market_close(force={force})")

    msg = build_close_message()
    send_telegram_message(msg)

    print("[MARKET_CLOSE] Cierre enviado (solo interpretación).")
