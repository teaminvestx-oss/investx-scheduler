# === premarket.py — InvestX (Premarket + interpretación) ===
import os
import json
import datetime as dt
import requests
import yfinance as yf

from utils import call_gpt_mini  # unificamos OpenAI


# ================================
# ENV VARS
# ================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("INVESTX_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

# Timezone Madrid (igual que main.py por offset)
TZ_OFFSET = int(os.getenv("TZ_OFFSET", "1"))

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
    if not TELELEGRAM_TOKEN_OK():
        return

    max_len = 3900  # margen bajo 4096
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [""]

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    for idx, chunk in enumerate(chunks, start=1):
        payload = {
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            r = requests.post(url, data=payload, timeout=20)
            if r.status_code >= 400:
                print(f"[WARN] Error Telegram HTTP {r.status_code} (chunk {idx}/{len(chunks)}): {r.text}")
        except Exception as e:
            print(f"[ERROR] Excepción enviando Telegram (chunk {idx}/{len(chunks)}): {e}")


def TELELEGRAM_TOKEN_OK() -> bool:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[ERROR] Faltan TELEGRAM_TOKEN / CHAT_ID para enviar mensaje.")
        return False
    return True


# ================================
# UTILIDADES YFINANCE
# ================================
def _get_last_price(t: yf.Ticker) -> float:
    """
    Último precio intradía (incluye pre/post si existe).
    Fallback: 5m si 1m está vacío.
    """
    intraday = t.history(period="1d", interval="1m", prepost=True)
    if intraday is None or intraday.empty:
        intraday = t.history(period="1d", interval="5m", prepost=True)

    if intraday is not None and not intraday.empty:
        return float(intraday["Close"].iloc[-1])

    return float("nan")


def _compute_close(daily, is_crypto: bool) -> float:
    closes = daily["Close"].dropna()
    if closes.empty:
        return float("nan")

    if is_crypto:
        # Cierre fijo del día anterior
        return float(closes.iloc[-2]) if len(closes) >= 2 else float(closes.iloc[-1])

    # Último cierre disponible para acciones/índices/ETFs/futuros
    return float(closes.iloc[-1])


# ================================
# CÁLCULO PREMARKET (con fallback de tickers)
# ================================
def _get_premarket_data(ticker_map: dict, is_crypto: bool = False):
    """
    ticker_map: { nombre_mostrar: ticker OR [ticker1, ticker2, ticker3...] }

    Devuelve lista de dicts:
      {
        'name': str,
        'last_price': float,
        'last_close': float,
        'change_pct': float
      }

    Fallback de tickers: usa el primer ticker que devuelva datos válidos.
    """
    results = []

    for name, yf_tickers in ticker_map.items():
        if isinstance(yf_tickers, str):
            yf_tickers = [yf_tickers]

        best = None

        for yf_ticker in yf_tickers:
            try:
                t = yf.Ticker(yf_ticker)

                daily = t.history(period="10d", interval="1d", prepost=False)
                if daily is None or daily.empty:
                    continue

                last_close = _compute_close(daily, is_crypto=is_crypto)
                if last_close != last_close or last_close == 0:  # NaN o 0
                    continue

                last_price = _get_last_price(t)
                if last_price != last_price:  # NaN
                    last_price = last_close

                change_pct = (last_price - last_close) / last_close * 100.0

                best = {
                    "name": name,
                    "last_price": round(float(last_price), 2),
                    "last_close": round(float(last_close), 2),
                    "change_pct": round(float(change_pct), 2),
                }
                break  # ✅ si funciona, paramos

            except Exception as e:
                print(f"[WARN] Error {name} ({yf_ticker}): {e}")
                continue

        if best:
            results.append(best)

    return results


def get_crypto_changes():
    cryptos = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
    }
    return _get_premarket_data(cryptos, is_crypto=True)


# ================================
# FORMATEO CON COLORES Y FLECHAS
# ================================
def style_change(change_pct: float):
    if change_pct > 0.3:
        return "🟢", "↑"
    elif change_pct < -0.3:
        return "🔴", "↓"
    else:
        return "⚪️", "→"


def format_premarket_lines(indices, megacaps, sectors, cryptos):
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
            display_lines.append(f"{icon} {item['name']} {arrow} {price_txt} ({pct_txt})")
            plain_lines.append(f"{item['name']}: precio {price_txt}, cambio {pct_txt} vs cierre previo")

    add_block("📈 <b>Índices / Futuros</b>", indices)
    add_block("📊 <b>Mega-caps USA</b>", megacaps)
    add_block("🏦 <b>Otros sectores clave</b>", sectors)
    add_block("💰 <b>Criptomonedas</b>", cryptos)

    return "\n".join(display_lines).strip(), "\n".join(plain_lines).strip()


# ================================
# INTERPRETACIÓN DEL DÍA (IA unificada)
# ================================
def interpret_premarket(plain_text: str) -> str:
    if not plain_text:
        return ""

    system_prompt = (
        "Eres un analista de mercados. Escribes en español claro, neutral e institucional para un canal de trading.\n"
        "No menciones IA ni modelos.\n"
        "Requisitos:\n"
        "- 4–6 frases cortas.\n"
        "- Explica el tono general (alcista/bajista/mixto) y por qué.\n"
        "- Menciona si tecnología lidera o no.\n"
        "- Indica si BTC/ETH acompañan o divergen.\n"
        "- Si casi todo se mueve <0.3%, di que el arranque es plano/mixto.\n"
        "- Termina con 'En resumen, ...'."
    )

    user_prompt = (
        "Movimientos del premarket (precio actual y cambio vs cierre previo):\n\n"
        f"{plain_text}\n\n"
        "Redacta el comentario siguiendo estrictamente los requisitos."
    )

    try:
        return (call_gpt_mini(system_prompt, user_prompt, max_tokens=260) or "").strip()
    except Exception:
        return ""


# ================================
# FUNCIÓN PRINCIPAL: BUENOS DÍAS
# ================================
def run_premarket_morning(force: bool = False):
    now_local = dt.datetime.utcnow() + dt.timedelta(hours=TZ_OFFSET)
    today = now_local.date()
    today_str = today.isoformat()

    # Fines de semana fuera, salvo force
    if today.weekday() >= 5 and not force:
        print("[INFO] Es fin de semana, no se envía 'Buenos días'.")
        return

    # Solo 1 vez al día si no es force
    if not force and _already_sent_today(today_str):
        print("[INFO] Premarket ya enviado hoy, no se repite (force=False).")
        return

    # ====================================================
    # ÍNDICES / FUTUROS con fallback (garantiza SP500)
    # ====================================================
    indices_map = {
        "S&P 500": ["ES=F", "^GSPC", "SPY"],
        "Nasdaq 100": ["NQ=F", "^NDX", "QQQ"],
        "Russell 2000": ["RTY=F", "^RUT", "IWM"],
        # opcional:
        # "Dow": ["YM=F", "^DJI", "DIA"],
    }
    indices = _get_premarket_data(indices_map, is_crypto=False)

    # Mega-caps USA
    mega_map = {
        "AAPL": "AAPL",
        "MSFT": "MSFT",
        "NVDA": "NVDA",
        "META": "META",
        "AMZN": "AMZN",
        "TSLA": "TSLA",
        "GOOGL": "GOOGL",
    }
    megacaps = _get_premarket_data(mega_map, is_crypto=False)

    # Otros sectores clave
    sectors_map = {
        "JPM": "JPM",
        "XOM": "XOM",
        "MCD": "MCD",
        "UNH": "UNH",
    }
    sectors = _get_premarket_data(sectors_map, is_crypto=False)

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
