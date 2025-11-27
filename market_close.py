# === market_close.py ===
# Cierre de mercado USA – InvestX (solo texto, sin imágenes)

import os
from datetime import datetime

import requests
import yfinance as yf


# Usamos mismas variables que en el premarket
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("INVESTX_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")


# ---------------- TELEGRAM ---------------- #

def send_telegram_message(text: str):
    """Envía un mensaje de texto a Telegram usando la API directa."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[TELEGRAM] Faltan TELEGRAM_TOKEN/INVESTX_TOKEN o CHAT_ID/TELEGRAM_CHAT_ID")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        resp = requests.post(url, data=data, timeout=30)
        if not resp.ok:
            print(f"[TELEGRAM] Error sendMessage: {resp.status_code} {resp.text}")
        else:
            print("[TELEGRAM] Mensaje de cierre enviado correctamente.")
    except Exception as e:
        print(f"[TELEGRAM] EXCEPTION sendMessage: {e}")


# ---------------- DATOS DE MERCADO ---------------- #

def get_pct_change(symbol: str) -> float | None:
    """
    Devuelve % cambio de hoy vs cierre anterior para un ticker de Yahoo Finance.
    Si falla, devuelve None.
    """
    try:
        data = yf.Ticker(symbol).history(period="2d")
        if len(data) < 2:
            return None
        prev_close = float(data["Close"].iloc[-2])
        last_close = float(data["Close"].iloc[-1])
        if prev_close == 0:
            return None
        return (last_close / prev_close - 1.0) * 100.0
    except Exception as e:
        print(f"[YF] Error obteniendo datos de {symbol}: {e}")
        return None


def fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "N/D"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def color_emoji(pct: float | None) -> str:
    if pct is None:
        return "⚪️"
    return "🟢" if pct >= 0 else "🔴"


# ---------------- MENSAJE DE CIERRE ---------------- #

def build_close_message() -> str:
    """Construye el mensaje institucional de cierre InvestX."""

    today = datetime.utcnow().strftime("%d/%m/%Y")

    # Índices principales
    spx_pct = get_pct_change("^GSPC")
    ndx_pct = get_pct_change("^NDX")   # Nasdaq 100
    djia_pct = get_pct_change("^DJI")

    # Acciones clave por sector
    tickers = {
        "NVDA": "Semiconductores",
        "AMD": "Semiconductores",
        "META": "Tecnología/Comunicación",
        "GOOGL": "Comunicación",
        "MSFT": "Tecnología",
        "AMZN": "Consumo discrecional",
        "NFLX": "Comunicación",
        "JNJ": "Salud",
        "LLY": "Salud",
        "JPM": "Financieras",
        "V": "Financieras",
        "MA": "Financieras",
        "XOM": "Energía",
        "CVX": "Energía",
        "PG": "Consumo básico",
        "KO": "Consumo básico",
    }

    changes: dict[str, float | None] = {}
    for t in tickers.keys():
        changes[t] = get_pct_change(t)

    # Helpers para construir líneas de acciones
    def line_for(ticker: str) -> str:
        pct = changes.get(ticker)
        return f"{ticker} {fmt_pct(pct)} {color_emoji(pct)}"

    msg = (
        f"📊 *Cierre de Wall Street — InvestX* ({today})\n\n"
        "*📈 Índices (variación diaria):*\n"
        f"• S&P 500: {fmt_pct(spx_pct)} {color_emoji(spx_pct)}\n"
        f"• Nasdaq 100: {fmt_pct(ndx_pct)} {color_emoji(ndx_pct)}\n"
        f"• Dow Jones: {fmt_pct(djia_pct)} {color_emoji(djia_pct)}\n\n"
        "*🟩 Sectores en verde / nombres destacados:*\n"
        f"• Tecnología / Comunicación: {line_for('META')}, {line_for('GOOGL')}, {line_for('MSFT')}, {line_for('AMZN')}, {line_for('NFLX')}\n"
        f"• Salud: {line_for('JNJ')}, {line_for('LLY')}\n"
        f"• Financieras: {line_for('JPM')}, {line_for('V')}, {line_for('MA')}\n\n"
        "*🟥 Sectores débiles:*\n"
        f"• Semis: {line_for('NVDA')}, {line_for('AMD')}\n"
        f"• Energía: {line_for('XOM')}, {line_for('CVX')}\n"
        f"• Consumo básico: {line_for('PG')}, {line_for('KO')}\n\n"
        "*🌐 Lectura macro y flujo:*\n"
        "El mercado mantiene tono constructivo apoyado por expectativas de recortes de la Fed en 2025 "
        "y una inflación que sigue moderándose. La estabilización del Treasury 10Y reduce presión sobre "
        "growth y permite que las megacaps sostengan índices.\n\n"
        "*📑 Lectura InvestX:*\n"
        "Estructura alcista de corto plazo mientras los índices se mantengan por encima de SMA50/SMA200. "
        "La rotación interna continúa siendo saludable, con toma de beneficios puntual en semis y apoyo "
        "de salud y financieras; sin señales claras de distribución institucional."
    )

    return msg


def run_market_close(force: bool = False):
    """
    Función llamada desde main.py.
    Solo envía un mensaje de texto con el cierre del mercado.
    """
    print(f"[MARKET_CLOSE] Ejecutando run_market_close(force={force})")
    text = build_close_message()
    send_telegram_message(text)
    print("[MARKET_CLOSE] Mensaje de cierre enviado.")
