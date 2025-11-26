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

# Fichero local para controlar "solo 1 vez al día"
PREMARKET_STATE_FILE = "premarket_state.json"


# ================================
# ESTADO DIARIO (NO DUPLICAR)
# ================================
def _load_state():
    if not os.path.exists(PREMARKET_STATE_FILE):
        return {}
    try:
        with open(PREMARKET_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    try:
        with open(PREMARKET_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception:
        pass


def _already_sent_today(today_str: str) -> bool:
    state = _load_state()
    return state.get("last_sent_date") == today_str


def _mark_sent_today(today_str: str) -> None:
    state = _load_state()
    state["last_sent_date"] = today_str
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
# CÁLCULO PREMARKET
# ================================
def _get_premarket_data(ticker_map: dict):
    """
    ticker_map: { nombre_mostrar: ticker_yfinance }

    Devuelve lista de dicts:
      {
        'name': str,
        'last_price': float,          # precio actual (premarket si hay)
        'last_close': float,          # último cierre regular
        'change_pct': float           # (last_price vs last_close)
      }
    """
    results = []

    for name, yf_ticker in ticker_map.items():
        try:
            t = yf.Ticker(yf_ticker)

            # Último cierre regular (mercado abierto)
            daily = t.history(period="2d", interval="1d", prepost=False)
            if daily is None or daily.empty:
                continue
            last_close = float(daily["Close"].iloc[-1])

            # Precio actual (incluyendo pre/post)
            intraday = t.history(period="1d", interval="1m", prepost=True)
            if intraday is not None and not intraday.empty:
                last_price = float(intraday["Close"].iloc[-1])
            else:
                last_price = last_close

            if last_close == 0:
                continue

            change_pct = (last_price - last_close) / last_close * 100.0

            results.append(
                {
                    "name": name,
                    "last_price": round(last_price, 2),
                    "last_close": round(last_close, 2),
                    "change_pct": round(change_pct, 2),
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
    return _get_premarket_data(cryptos)


# ================================
# FORMATEO CON COLORES Y FLECHAS
# ================================
def style_change(change_pct: float):
    """
    Devuelve (icono, flecha) según si sube, baja o está plano.
    (ÚNICO CAMBIO: 🟡 → ⚪️)
    """
    if change_pct > 0.3:
        icon = "🟢"
        arrow = "↑"
    elif change_pct < -0.3:
        icon = "🔴"
        arrow = "↓"
    else:
        icon = "⚪️"   # << NUEVO COLOR NEUTRO (gris) >>
        arrow = "→"
    return icon, arrow


def format_premarket_lines(indices, megacaps, sectors, cryptos):
    """
    Devuelve:
    - texto formateado para Telegram (con iconos y precio)
    - texto plano para interpretación del modelo
    """
    display_lines = []
    plain_lines = []

    def add_block(title, items):
        if not items:
            return
        if display_lines:
            display_lines.append("")
        display_lines.append(title + "\n")
        for item in items:
            icon, arrow = style_change(item["change_pct"])
            price_txt = f"{item['last_price']:.2f}"
            sign = "+" if item["change_pct"] > 0 else ""
            pct_txt = f"{sign}{item['change_pct']:.2f}%"
            display_lines.append(
                f"{icon} {item['name']} {arrow} {price_txt} ({pct_txt})"
            )
            plain_lines.append(
                f"{item['name']}: precio {price_txt}, cambio {pct_txt} vs último cierre"
            )

    add_block("📈 <b>Índices / Futuros</b>", indices)
    add_block("📊 <b>Mega-caps USA</b>", megacaps)
    add_block("🏦 <b>Otros sectores clave</b>", sectors)
    add_block("💰 <b>Criptomonedas</b>", cryptos)

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
        "Estos son los movimientos aproximados de hoy en índices, acciones y criptomonedas "
        "(precio actual del premarket y cambio vs cierre previo):\n\n"
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
    today_str = today.isoformat()

    # Fines de semana fuera, salvo force
    if today.weekday() >= 5 and not force:
        print("[INFO] Es fin de semana, no se envía 'Buenos días'.")
        return

    # Control "solo una vez al día" si no es forzado
    if not force and _already_sent_today(today_str):
        print("[INFO] Premarket ya enviado hoy, no se repite (force=False).")
        return

    # Índices
    indices_map = {
        "Nasdaq 100": "^NDX",
        "S&P 500": "^GSPC",
    }
    indices = _get_premarket_data(indices_map)

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
    megacaps = _get_premarket_data(mega_map)

    # Otros sectores
    sectors_map = {
        "JPM": "JPM",   # financiero
        "XOM": "XOM",   # energía
        "MCD": "MCD",   # consumo defensivo
        "UNH": "UNH",   # salud
    }
    sectors = _get_premarket_data(sectors_map)

    cryptos = get_crypto_changes()

    if not (indices or megacaps or sectors or cryptos):
        send_telegram("🌅 <b>Buenos días</b>\n\nNo se ha podido obtener el premarket hoy.")
        return

    display_text, plain_text = format_premarket_lines(indices, megacaps, sectors, cryptos)
    interpretation = interpret_premarket(plain_text)

    today_str_nice = today.strftime("%d/%m/%Y")

    parts = [
        "🌅 <b>Buenos días, equipo</b>\n",
        f"Así viene el mercado hoy — {today_str_nice}:\n",
        display_text,
    ]

    if interpretation:
        parts.append("\n")
        parts.append(interpretation)

    final_msg = "\n".join(parts).strip()
    send_telegram(final_msg)

    if not force:
        _mark_sent_today(today_str)
        print("[INFO] Premarket marcado como enviado para hoy.")
