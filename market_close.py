# === market_close.py ===
# Cierre de mercado USA – InvestX (datos + interpretación vía OpenAI, sin imágenes)

import os
import datetime as dt
from typing import Optional, Dict, List

import requests
import yfinance as yf
from openai import OpenAI

# ================================
# ENV VARS
# ================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("INVESTX_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# ================================
# TELEGRAM (con troceo)
# ================================
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[ERROR] Faltan TELEGRAM_TOKEN / CHAT_ID para enviar mensaje (market close).")
        return

    max_len = 3900  # margen bajo los 4096 de Telegram
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [""]

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    for idx, chunk in enumerate(chunks, start=1):
        payload = {
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
        }
        try:
            r = requests.post(url, data=payload, timeout=20)
            if r.status_code >= 400:
                print(f"[WARN] Error Telegram HTTP {r.status_code} (chunk {idx}/{len(chunks)}): {r.text}")
        except Exception as e:
            print(f"[ERROR] Excepción enviando mensaje Telegram (chunk {idx}/{len(chunks)}): {e}")


# ================================
# UTILIDADES DE MERCADO
# ================================
def get_pct_change(symbol: str) -> Optional[float]:
    """
    Devuelve % cambio de hoy vs cierre anterior para un ticker de Yahoo Finance.
    Si falla, devuelve None.
    """
    try:
        data = yf.Ticker(symbol).history(period="2d")
        if data is None or data.empty or len(data) < 2:
            return None
        prev_close = float(data["Close"].iloc[-2])
        last_close = float(data["Close"].iloc[-1])
        if prev_close == 0:
            return None
        return (last_close - prev_close) / prev_close * 100.0
    except Exception as e:
        print(f"[YF] Error obteniendo datos de {symbol}: {e}")
        return None


def avg_change(values: List[Optional[float]]) -> Optional[float]:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def style_change(change_pct: float) -> str:
    """
    Devuelve icono según si sube, baja o está plano.
    Mismo criterio que en el premarket.
    """
    if change_pct > 0.3:
        return "🟢"
    elif change_pct < -0.3:
        return "🔴"
    else:
        return "⚪️"


# ================================
# CONSTRUCCIÓN DE DATOS DEL CIERRE
# ================================
def get_close_market_data():
    """
    Devuelve:
      - indices: lista de dicts {name, symbol, change_pct}
      - sectors: dict sector -> lista de {ticker, change_pct}
    """
    indices_map = {
        "S&P 500": "^GSPC",
        "Nasdaq 100": "^NDX",
        "Dow Jones": "^DJI",
    }

    indices = []
    for name, symbol in indices_map.items():
        pct = get_pct_change(symbol)
        if pct is not None:
            indices.append(
                {
                    "name": name,
                    "symbol": symbol,
                    "change_pct": round(pct, 2),
                }
            )

    # Universo de acciones por sector (contexto suficiente para la IA)
    sector_tickers: Dict[str, List[str]] = {
        "Tecnología / Comunicación": ["AAPL", "MSFT", "GOOGL", "META", "AMZN", "NFLX"],
        "Semiconductores": ["NVDA", "AMD", "INTC", "AVGO", "QCOM"],
        "Salud": ["JNJ", "LLY", "ABBV", "UNH", "PFE"],
        "Financieras": ["JPM", "BAC", "C", "GS", "MS", "V", "MA"],
        "Energía": ["XOM", "CVX", "SLB", "COP"],
        "Consumo discrecional": ["TSLA", "HD", "MCD", "NKE", "AMZN"],
        "Consumo básico": ["PG", "KO", "PEP", "WMT", "COST"],
        "Industriales": ["CAT", "DE", "GE", "HON"],
    }

    all_tickers = sorted({t for lst in sector_tickers.values() for t in lst})

    ticker_changes: Dict[str, Optional[float]] = {}
    for t in all_tickers:
        ticker_changes[t] = get_pct_change(t)

    sectors: Dict[str, List[Dict[str, float]]] = {}
    for sector, tks in sector_tickers.items():
        sector_list = []
        for t in tks:
            pct = ticker_changes.get(t)
            if pct is not None:
                sector_list.append({"ticker": t, "change_pct": round(pct, 2)})
        if sector_list:
            sectors[sector] = sector_list

    return indices, sectors


# ================================
# FORMATEO PARA TELEGRAM + IA
# ================================
def format_market_close(indices, sectors):
    """
    Devuelve:
    - display_text: HTML para Telegram (datos)
    - plain_text: resumen plano para el modelo
    """
    today = dt.date.today().strftime("%d/%m/%Y")

    display_lines: List[str] = []
    plain_lines: List[str] = []

    display_lines.append(f"📊 <b>Cierre de Wall Street — InvestX</b> ({today})\n")

    # ÍNDICES
    if indices:
        display_lines.append("📈 <b>Índices</b>\n")
        for idx in indices:
            icon = style_change(idx["change_pct"])
            sign = "+" if idx["change_pct"] > 0 else ""
            pct_txt = f"{sign}{idx['change_pct']:.2f}%"
            display_lines.append(f"{icon} {idx['name']}: <b>{pct_txt}</b>")
            plain_lines.append(f"{idx['name']}: cambio {pct_txt} vs cierre previo")
        display_lines.append("")

    # Medias por sector
    sector_avgs: Dict[str, Optional[float]] = {}
    for sector, lst in sectors.items():
        sector_avgs[sector] = avg_change([x["change_pct"] for x in lst])

    ranked = sorted(
        [s for s in sector_avgs.items() if s[1] is not None],
        key=lambda x: x[1],
        reverse=True,
    )

    # TOP 2 y BOTTOM 2 sectores
    top_sectors = ranked[:2] if ranked else []
    bottom_sectors = ranked[-2:] if ranked else []

    if top_sectors:
        display_lines.append("🟢 <b>Sectores fuertes</b>\n")
        for sec, val in top_sectors:
            sign = "+" if val > 0 else ""
            display_lines.append(f"{sec}: <b>{sign}{val:.2f}%</b>")
            plain_lines.append(f"Sector {sec}: cambio medio {sign}{val:.2f}% (entre los más fuertes)")
        display_lines.append("")

    if bottom_sectors:
        display_lines.append("🔻 <b>Sectores débiles</b>\n")
        for sec, val in bottom_sectors:
            sign = "+" if val > 0 else ""
            display_lines.append(f"{sec}: <b>{sign}{val:.2f}%</b>")
            plain_lines.append(f"Sector {sec}: cambio medio {sign}{val:.2f}% (entre los más débiles)")
        display_lines.append("")

    # ACCIONES CLAVE: cogemos 3 movimientos “extremos” globales
    all_stocks: List[Dict[str, float]] = []
    for sec_name, lst in sectors.items():
        for x in lst:
            all_stocks.append({"ticker": x["ticker"], "change_pct": x["change_pct"]})

    # Ordenamos por movimiento absoluto desc (para top movers)
    all_stocks_sorted = sorted(
        all_stocks,
        key=lambda x: abs(x["change_pct"]),
        reverse=True,
    )

    key_names = []
    for x in all_stocks_sorted[:3]:
        sign = "+" if x["change_pct"] > 0 else ""
        key_names.append(f"{x['ticker']} <b>{sign}{x['change_pct']:.2f}%</b>")

    if key_names:
        display_lines.append("🏁 <b>Acciones clave</b>\n")
        display_lines.append(", ".join(key_names))
        display_lines.append("")
        for x in all_stocks_sorted[:3]:
            sign = "+" if x["change_pct"] > 0 else ""
            plain_lines.append(
                f"{x['ticker']}: movimiento destacado {sign}{x['change_pct']:.2f}%"
            )

    display_text = "\n".join(display_lines).strip()
    plain_text = "\n".join(plain_lines).strip()
    return display_text, plain_text


# ================================
# INTERPRETACIÓN DINÁMICA (OpenAI)
# ================================
def interpret_market_close(plain_text: str) -> str:
    """
    Devuelve un comentario dinámico corto, profesional, estilo Bloomberg,
    con lectura final tipo 'Sesgo InvestX'.
    """
    if not client or not plain_text:
        return ""

    system_prompt = (
        "Eres un analista institucional de mercados (estilo Bloomberg) que escribe en español. "
        "Tu tarea es interpretar el cierre de Wall Street a partir de los datos que te paso "
        "(índices, sectores y acciones). No menciones que eres un modelo ni hables de IA.\n\n"
        "Tu respuesta debe cumplir esto:\n"
        "- 1 frase sobre el tono general del mercado (risk-on, risk-off o mixto) y el comportamiento de los índices.\n"
        "- 1–2 frases explicando qué sectores y tipos de compañías han liderado y cuáles han lastrado la sesión.\n"
        "- 1 frase opcional de contexto macro si se intuye por el patrón de sectores (rotación a defensivas, castigo a growth, fuerza de energía, etc.).\n"
        "- 1 frase final empezando por 'Sesgo InvestX:' que resuma cómo operaría un trader institucional tras esta sesión "
        "(corrección sana, distribución, continuación alcista, fase de consolidación, prudencia, etc.).\n"
        "Sé conciso (3–5 frases en total), profesional y específico; evita frases genéricas repetitivas."
    )

    user_prompt = (
        "Estos son los datos aproximados del cierre de mercado en índices, sectores y acciones USA:\n\n"
        f"{plain_text}\n\n"
        "Haz un comentario siguiendo las instrucciones, en un único bloque de texto, sin listas."
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print("Error interpretando market close:", e)
        return ""


# ================================
# FUNCIÓN PRINCIPAL: MARKET CLOSE
# ================================
def run_market_close(force: bool = False):
    """
    Llamada desde main.py.
    El control de horario (22:30, L-V o CLOSE_FORCE) se hace en main.py.
    Aquí solo montamos mensaje y lo enviamos.
    """
    today = dt.date.today()

    # Por si acaso, evitamos fines de semana si no es force, aunque main.py ya lo filtra.
    if today.weekday() >= 5 and not force:
        print("[INFO] Es fin de semana, no se envía 'Market Close'.")
        return

    indices, sectors = get_close_market_data()

    if not indices and not sectors:
        send_telegram("📊 <b>Cierre de Wall Street — InvestX</b>\n\nNo se han podido obtener datos de mercado hoy.")
        return

    display_text, plain_text = format_market_close(indices, sectors)
    interpretation = interpret_market_close(plain_text)

    parts = [display_text]
    if interpretation:
        parts.append("🧠 <b>Análisis InvestX</b>\n")
        parts.append(interpretation)

    final_msg = "\n".join(parts).strip()
    send_telegram(final_msg)
    print(f"[INFO] Market Close enviado correctamente (force={force}).")
