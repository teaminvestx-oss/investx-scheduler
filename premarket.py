# === premarket.py ===
import os
import json
import datetime as dt
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
# ESTADO: SOLO 1 ENVÍO AL DÍA
# ================================
STATE_FILE = "premarket_state.json"


def _load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def _already_sent_today(day_key: str) -> bool:
    state = _load_state()
    return state.get("sent_day") == day_key


def _mark_sent_today(day_key: str):
    state = _load_state()
    state["sent_day"] = day_key
    _save_state(state)


# ================================
# TELEGRAM (con troceo)
# ================================
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[ERROR] Faltan TELEGRAM_TOKEN / CHAT_ID para enviar mensaje.")
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
# CÁLCULO DE PRECIO + CAMBIOS
# ================================
def _get_price_and_change(yf_ticker: str):
    """
    Devuelve (precio_actual, change_pct) para un ticker:

    - precio_actual: último precio (incluyendo pre/after market si existe)
    - change_pct: variación % vs CIERRE REGULAR del día anterior
    """
    t = yf.Ticker(yf_ticker)

    # 1) Cierre del día anterior (regular market)
    prev_close = None
    try:
        daily = t.history(period="3d", interval="1d", prepost=False)
        daily = daily.dropna(subset=["Close"])
        if len(daily) >= 2:
            prev_close = float(daily["Close"].iloc[-2])
        elif len(daily) == 1:
            prev_close = float(daily["Close"].iloc[-1])
    except Exception as e:
        print(f"[WARN] Error obteniendo daily history de {yf_ticker}: {e}")

    # 2) Precio actual (idealmente premarket/último intradía)
    last_price = None
    try:
        intraday = t.history(period="1d", interval="1m", prepost=True)
        intraday = intraday.dropna(subset=["Close"])
        if not intraday.empty:
            last_price = float(intraday["Close"].iloc[-1])
    except Exception as e:
        print(f"[WARN] Error obteniendo intraday de {yf_ticker}: {e}")

    # Fallback: si no hay intradía, usamos último close disponible
    if last_price is None and prev_close is not None:
        last_price = prev_close
    elif last_price is None:
        # último recurso: intento rápido
        try:
            fast = t.fast_info
            last_price = float(fast.get("last_price"))
            if prev_close is None:
                prev_close = float(fast.get("previous_close"))
        except Exception:
            pass

    if last_price is None or prev_close is None or prev_close == 0:
        return None, None

    change_pct = (last_price - prev_close) / prev_close * 100.0
    return last_price, round(change_pct, 2)


def get_price_change_map(ticker_map: dict):
    """
    ticker_map: { nombre_mostrar: ticker_yfinance }
    Devuelve lista de dicts con:
      {name, price, change_pct}
    """
    results = []
    for name, yf_ticker in ticker_map.items():
        try:
            price, change_pct = _get_price_and_change(yf_ticker)
            if price is None or change_pct is None:
                continue
            results.append(
                {
                    "name": name,
                    "price": round(price, 2),
                    "change_pct": change_pct,
                }
            )
        except Exception as e:
            print(f"[WARN] Error obteniendo datos de {name} ({yf_ticker}): {e}")
            continue
    return results


def get_crypto_changes():
    cryptos = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
    }
    return get_price_change_map(cryptos)


# ================================
# FORMATEO CON COLORES Y FLECHAS
# ================================
def style_change(change_pct: float):
    """
    Devuelve (icono, flecha, texto_pct) según si sube, baja o está plano.
    texto_pct es solo el porcentaje formateado, sin paréntesis.
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
    - texto formateado para Telegram (con iconos, precios y %)
    - texto plano para interpretación del modelo
    """
    display_lines = []
    plain_lines = []

    if indices:
        display_lines.append("📈 <b>Índices / Futuros</b>\n")
        for item in indices:
            icon, arrow, pct_txt = style_change(item["change_pct"])
            line = f"{icon} {item['name']} {item['price']:.2f} ({arrow} {pct_txt})"
            display_lines.append(line)
            plain_lines.append(f"{item['name']}: {item['price']:.2f} ({pct_txt})")

    if megacaps:
        display_lines.append("")
        display_lines.append("📊 <b>Mega-caps USA</b>\n")
        for item in megacaps:
            icon, arrow, pct_txt = style_change(item["change_pct"])
            line = f"{icon} {item['name']} {item['price']:.2f} ({arrow} {pct_txt})"
            display_lines.append(line)
            plain_lines.append(f"{item['name']}: {item['price']:.2f} ({pct_txt})")

    if sectors:
        display_lines.append("")
        display_lines.append("🏦 <b>Otros sectores clave</b>\n")
        for item in sectors:
            icon, arrow, pct_txt = style_change(item["change_pct"])
            line = f"{icon} {item['name']} {item['price']:.2f} ({arrow} {pct_txt})"
            display_lines.append(line)
            plain_lines.append(f"{item['name']}: {item['price']:.2f} ({pct_txt})")

    if cryptos:
        display_lines.append("")
        display_lines.append("💰 <b>Criptomonedas</b>\n")
        for item in cryptos:
            icon, arrow, pct_txt = style_change(item["change_pct"])
            line = f"{icon} {item['name']} {item['price']:.2f} ({arrow} {pct_txt})"
            display_lines.append(line)
            plain_lines.append(f"{item['name']}: {item['price']:.2f} ({pct_txt})")

    display_text = "\n".join(display_lines).strip()
    plain_text = "\n".join(plain_lines).strip()
    return display_text, plain_text


# ================================
# INTERPRETACIÓN DEL DÍA
# ================================
def interpret_premarket(plain_text: str) -> str:
    """
    Devuelve unas frases explicando de forma natural
    cómo pinta el día según índices, acciones y cripto.
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
def run_premarket_morning(force: bool = False):
    today = dt.date.today()
    # Fin de semana -> no se envía (salvo que fuerces si quieres cambiar esto)
    if today.weekday() >= 5 and not force:
        print("[INFO] Es fin de semana, no se envía 'Buenos días'.")
        return

    day_key = today.isoformat()
    if not force and _already_sent_today(day_key):
        print("[INFO] Premarket ya enviado hoy, no se vuelve a enviar.")
        return

    # Índices
    indices_map = {
        "Nasdaq 100": "^NDX",
        "S&P 500": "^GSPC",
    }
    indices = get_price_change_map(indices_map)

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
    megacaps = get_price_change_map(mega_map)

    # Otros sectores
    sectors_map = {
        "JPM": "JPM",   # financiero
        "XOM": "XOM",   # energía
        "MCD": "MCD",   # consumo defensivo
        "UNH": "UNH",   # salud
    }
    sectors = get_price_change_map(sectors_map)

    cryptos = get_crypto_changes()

    if not (indices or megacaps or sectors or cryptos):
        send_telegram("🌅 <b>Buenos días</b>\n\nNo se ha podido obtener el premarket hoy.")
        if not force:
            _mark_sent_today(day_key)
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

    if not force:
        _mark_sent_today(day_key)
