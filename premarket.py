# === premarket.py ===
import os
import datetime as dt
import requests
import yfinance as yf
from openai import OpenAI

# ================================
# ENV VARS
# ================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("INVESTX_TOKEN")
CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def env_ok() -> bool:
    missing = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN/INVESTX_TOKEN")
    if not CHAT_ID:
        missing.append("CHAT_ID/TELEGRAM_CHAT_ID")
    if missing:
        print("Faltan env vars:", ", ".join(missing))
        return False
    return True


# ================================
# TELEGRAM
# ================================
def send_telegram(msg: str):
    if not env_ok():
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, data=payload, timeout=15)
        if resp.status_code >= 400:
            print(f"[WARN] Error Telegram HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        print("Error enviando Telegram (premarket):", e)


# ================================
# CÁLCULO DE CAMBIOS
# ================================
def get_changes_map(ticker_map: dict, period: str = "2d"):
    """
    ticker_map: { nombre_mostrar: ticker_yfinance }
    Calcula % de cambio entre los dos últimos cierres disponibles.
    """
    results = []
    for name, yf_ticker in ticker_map.items():
        try:
            data = yf.Ticker(yf_ticker).history(period=period)
            if data is None or data.empty or len(data) < 2:
                continue
            last = data["Close"].iloc[-1]
            prev = data["Close"].iloc[-2]
            if prev == 0:
                continue
            change_pct = (last - prev) / prev * 100
            results.append({"name": name, "change_pct": round(change_pct, 2)})
        except Exception as e:
            print(f"[WARN] Error obteniendo datos de {name} ({yf_ticker}): {e}")
            continue
    return results


def get_crypto_changes():
    """
    Variación aproximada usando histórico reciente de BTC y ETH.
    El texto no menciona periodos, solo muestra el %.
    """
    cryptos = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
    }
    results = []
    for name, yf_ticker in cryptos.items():
        try:
            data = yf.Ticker(yf_ticker).history(period="2d")
            if data is None or data.empty or len(data) < 2:
                continue
            last = data["Close"].iloc[-1]
            prev = data["Close"].iloc[-2]
            if prev == 0:
                continue
            change_pct = (last - prev) / prev * 100
            results.append({"name": name, "change_pct": round(change_pct, 2)})
        except Exception as e:
            print(f"[WARN] Error obteniendo datos de cripto {name}: {e}")
            continue
    return results


# ================================
# FORMATEO CON COLORES Y FLECHAS
# ================================
def style_change(change_pct: float):
    """
    Devuelve (icono, flecha, texto_formateado) según si sube, baja o está plano.
    No menciona periodos, solo el porcentaje.
    """
    if change_pct > 0.3:
        icon = "🟢"
        arrow = "↑"
    elif change_pct < -0.3:
        icon = "🔴"
        arrow = "↓"
    else:
        icon = "🟡"
        arrow = "→"

    sign = "+" if change_pct > 0 else ""
    text = f"{sign}{change_pct:.2f}%"
    return icon, arrow, text


def format_premarket_lines(indices, megacaps, sectors, cryptos):
    """
    Devuelve:
    - texto formateado para Telegram (con iconos)
    - texto plano para interpretación del modelo
    """
    display_lines = []
    plain_lines = []

    if indices:
        display_lines.append("📈 <b>Índices / Futuros</b>\n")
        for item in indices:
            icon, arrow, txt = style_change(item["change_pct"])
            display_lines.append(f"{icon} {item['name']} {arrow} {txt}")
            plain_lines.append(f"{item['name']}: {item['change_pct']:.2f}%")

    if megacaps:
        display_lines.append("")
        display_lines.append("📊 <b>Mega-caps USA</b>\n")
        for item in megacaps:
            icon, arrow, txt = style_change(item["change_pct"])
            display_lines.append(f"{icon} {item['name']} {arrow} {txt}")
            plain_lines.append(f"{item['name']}: {item['change_pct']:.2f}%")

    if sectors:
        display_lines.append("")
        display_lines.append("🏦 <b>Otros sectores clave</b>\n")
        for item in sectors:
            icon, arrow, txt = style_change(item["change_pct"])
            display_lines.append(f"{icon} {item['name']} {arrow} {txt}")
            plain_lines.append(f"{item['name']}: {item['change_pct']:.2f}%")

    if cryptos:
        display_lines.append("")
        display_lines.append("💰 <b>Criptomonedas</b>\n")
        for item in cryptos:
            icon, arrow, txt = style_change(item["change_pct"])
            display_lines.append(f"{icon} {item['name']} {arrow} {txt}")
            plain_lines.append(f"{item['name']}: {item['change_pct']:.2f}%")

    display_text = "\n".join(display_lines).strip()
    plain_text = "\n".join(plain_lines).strip()
    return display_text, plain_text


# ================================
# INTERPRETACIÓN DEL DÍA (TONO NATURAL)
# ================================
def interpret_premarket(plain_text: str) -> str:
    """
    Devuelve unas pocas frases explicando de forma natural
    cómo pinta el día según el movimiento de acciones, índices y cripto.
    No menciona IA ni periodos.
    """
    if not client or not plain_text:
        return ""

    system_prompt = (
        "Eres un analista de mercados que explica en español, de forma sencilla y neutra, "
        "cómo pinta la sesión de hoy a partir de los movimientos de índices USA, "
        "grandes compañías y BTC/ETH. No menciones que eres un modelo ni hables de IA. "
        "Tu respuesta debe tener:\n"
        "- 2–4 frases cortas explicando el tono general (más alcista, bajista o mixto).\n"
        "- Comenta si la tecnología está tirando del mercado o no.\n"
        "- Comenta si las criptos acompañan el movimiento o van por su cuenta.\n"
        "- Termina con una frase tipo 'En resumen, ...' que sintetice el sesgo del día."
    )

    user_prompt = (
        "Estos son los movimientos aproximados de hoy en índices, acciones y criptomonedas:\n\n"
        f"{plain_text}\n\n"
        "Haz un comentario breve en español siguiendo las instrucciones."
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
        print("Error interpretando premarket:", e)
        return ""


# ================================
# FUNCIÓN PRINCIPAL: BUENOS DÍAS
# ================================
def run_premarket_morning():
    if not env_ok():
        return

    today = dt.date.today()
    if today.weekday() >= 5:
        print("[INFO] Es fin de semana, no se envía 'Buenos días'.")
        return

    # Índices
    indices_map = {
        "Nasdaq 100": "^NDX",
        "S&P 500": "^GSPC",
    }
    indices = get_changes_map(indices_map)

    # Mega-caps tech
    mega_map = {
        "AAPL": "AAPL",
        "MSFT": "MSFT",
        "NVDA": "NVDA",
        "META": "META",
        "AMZN": "AMZN",
        "TSLA": "TSLA",
        "GOOGL": "GOOGL",
    }
    megacaps = get_changes_map(mega_map)

    # Otros sectores
    sectors_map = {
        "JPM": "JPM",   # financiero
        "XOM": "XOM",   # energía
        "MCD": "MCD",   # consumo defensivo
        "UNH": "UNH",   # salud
    }
    sectors = get_changes_map(sectors_map)

    cryptos = get_crypto_changes()

    if not (indices or megacaps or sectors or cryptos):
        send_telegram("🌅 <b>Buenos días</b>\n\nNo se ha podido obtener el premarket hoy.")
        return

    display_text, plain_text = format_premarket_lines(indices, megacaps, sectors, cryptos)
    interpretation = interpret_premarket(plain_text)

    today_str = today.strftime("%d/%m/%Y")

    parts = [
        "🌅 <b>Buenos días, equipo</b>\n",
        f"Así viene el mercado hoy — {today_str}:\n",
        display_text,
    ]

    if interpretation:
        parts.append("\n")
        parts.append(interpretation)

    final_msg = "\n".join(parts).strip()
    send_telegram(final_msg)
