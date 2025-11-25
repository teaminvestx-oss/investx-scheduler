# === premarket.py ===
import os
import datetime as dt
import requests
import yfinance as yf
from openai import OpenAI
import json

# ================================
# STATE CONTROL (solo 1 envío/día)
# ================================
STATE_FILE = "premarket_state.json"

def _load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def _save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except:
        pass

def _already_sent_today():
    today = dt.date.today().isoformat()
    state = _load_state()
    return state.get("sent_day") == today

def _mark_sent_today():
    today = dt.date.today().isoformat()
    state = _load_state()
    state["sent_day"] = today
    _save_state(state)

# ================================
# ENV VARS
# ================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("INVESTX_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# ================================
# TELEGRAM (troceo automático)
# ================================
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[ERROR] Faltan TELEGRAM_TOKEN / CHAT_ID para enviar mensaje.")
        return

    max_len = 3900
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    for chunk in chunks:
        payload = {
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
        }
        try:
            requests.post(url, data=payload, timeout=20)
        except:
            pass


# ================================
# DATOS DE MERCADO (precio premarket)
# ================================
def get_price_and_change(yf_ticker: str):
    """
    Devuelve:
    - last_price (precio actual/pre-market)
    - pct_change (vs último cierre real)
    """
    try:
        t = yf.Ticker(yf_ticker)

        info = t.history(period="2d", prepost=True)
        if info is None or info.empty:
            return None, None

        last_price = info["Close"].iloc[-1]  # precio más reciente (pre/post incluído)
        prev_close = info["Close"].iloc[-2]  # cierre anterior real

        if prev_close == 0:
            return last_price, None

        pct = (last_price - prev_close) / prev_close * 100
        return float(last_price), round(pct, 2)

    except Exception as e:
        print(f"[WARN] Error obteniendo precio para {yf_ticker}: {e}")
        return None, None


# ================================
# COLORES / ICONOS
# ================================
def style_change(change_pct: float):
    if change_pct is None:
        return "⚪️", "→"

    if change_pct > 0.3:
        return "🟢", "↑"
    elif change_pct < -0.3:
        return "🔴", "↓"
    else:
        return "⚪️", "→"


# ================================
# FORMATO LÍNEAS
# ================================
def format_block(title: str, mapping: dict):
    lines = [f"<b>{title}</b>"]

    for name, ticker in mapping.items():
        price, pct = get_price_and_change(ticker)
        if price is None:
            continue

        icon, arrow = style_change(pct)
        pct_txt = f"{arrow} {pct:.2f}%" if pct is not None else "—"
        price_txt = f"{price:.2f}"

        lines.append(f"{icon} {name}: {price_txt} ({pct_txt})")

    return "\n".join(lines)


# ================================
# INTERPRETACIÓN IA
# ================================
def interpret_premarket(plain: str):
    if not client:
        return ""

    prompt = f"""
Analiza brevemente el tono de mercado según estos movimientos (índices, megacaps y criptos).
No menciones IA ni modelos. Máx 3 frases.

Datos:
{plain}
"""

    try:
        r = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system",
                 "content": "Eres un analista de mercados experimentado. Sé conciso, profesional y claro."},
                {"role": "user", "content": prompt}
            ]
        )
        return r.choices[0].message.content.strip()
    except:
        return ""


# ================================
# FUNCIÓN PRINCIPAL
# ================================
def run_premarket_morning(force=False):
    today = dt.date.today()
    if today.weekday() >= 5:
        print("[INFO] Fin de semana -> no se envía premarket.")
        return

    if not force:
        if _already_sent_today():
            print("[INFO] Premarket ya enviado hoy.")
            return

    # Grupos
    indices = {
        "Nasdaq 100": "^NDX",
        "S&P 500": "^GSPC",
    }

    megacaps = {
        "AAPL": "AAPL",
        "MSFT": "MSFT",
        "NVDA": "NVDA",
        "META": "META",
        "AMZN": "AMZN",
        "TSLA": "TSLA",
        "GOOGL": "GOOGL",
    }

    sectors = {
        "JPM": "JPM",
        "XOM": "XOM",
        "MCD": "MCD",
        "UNH": "UNH",
    }

    cryptos = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
    }

    # Construcción del mensaje
    parts = ["🌅 <b>Buenos días, equipo</b>\n"]
    parts.append(format_block("📈 Índices / Futuros", indices))
    parts.append("")
    parts.append(format_block("📊 Mega-caps USA", megacaps))
    parts.append("")
    parts.append(format_block("🏦 Sectores clave", sectors))
    parts.append("")
    parts.append(format_block("💰 Criptomonedas", cryptos))

    # Interpretación IA
    plain_summary = " | ".join(
        f"{name}: {get_price_and_change(t)[1]}%"
        for name, t in {**indices, **megacaps, **sectors, **cryptos}.items()
    )

    interpretation = interpret_premarket(plain_summary)
    if interpretation:
        parts.append("\n" + interpretation)

    final_msg = "\n".join(parts).strip()

    send_telegram(final_msg)

    if not force:
        _mark_sent_today()
