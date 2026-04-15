# === premarket.py — InvestX (Premarket + interpretación) ===
import os
import json
import datetime as dt
import requests
import yfinance as yf

from utils import call_gpt_mini  # unificamos OpenAI

# ================================
# CONSTANTES SENTIMIENTO
# ================================
_FG_API_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"

_FG_RATING_ES = {
    "Extreme Fear": "Miedo extremo",
    "Fear": "Miedo",
    "Neutral": "Neutral",
    "Greed": "Codicia",
    "Extreme Greed": "Codicia extrema",
}


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
def TELELEGRAM_TOKEN_OK() -> bool:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[ERROR] Faltan TELEGRAM_TOKEN / CHAT_ID para enviar mensaje.")
        return False
    return True


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
    """
    Cierre de referencia:
    - Cripto: cierre del día anterior (para variabilidad real)
    - No cripto: si el último daily es "hoy" (parcial), usar el de "ayer"
    """
    closes = daily["Close"].dropna()
    if closes.empty:
        return float("nan")

    if is_crypto:
        return float(closes.iloc[-2]) if len(closes) >= 2 else float(closes.iloc[-1])

    # Evitar que el "close" sea el de HOY (parcial) y te deje en 0.00%
    try:
        last_day = closes.index[-1].date()
        today_utc = dt.datetime.utcnow().date()
        if last_day == today_utc and len(closes) >= 2:
            return float(closes.iloc[-2])
    except Exception:
        pass

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
# SENTIMIENTO: VIX + FEAR & GREED
# ================================
def _fetch_vix():
    """Último cierre del VIX y variación vs día anterior."""
    try:
        t = yf.Ticker("^VIX")
        daily = t.history(period="5d", interval="1d")
        if daily is None or daily.empty:
            return None
        closes = daily["Close"].dropna()
        if len(closes) < 1:
            return None
        current = float(closes.iloc[-1])
        if len(closes) >= 2:
            prev = float(closes.iloc[-2])
            change = current - prev
            change_pct = (change / prev) * 100.0
        else:
            change = 0.0
            change_pct = 0.0
        return {
            "value": round(current, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
        }
    except Exception as e:
        print(f"[WARN] Error fetching VIX: {e}")
        return None


def _fetch_fear_and_greed():
    """Fear & Greed Index actual desde CNN Data Viz."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; InvestX-Bot/1.0)",
        "Accept": "application/json",
        "Referer": "https://edition.cnn.com/",
    }
    try:
        resp = requests.get(_FG_API_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        fg = data.get("fear_and_greed") or {}
        score = fg.get("score")
        rating = fg.get("rating") or ""
        if score is None:
            return None
        return {
            "score": round(float(score)),
            "rating": rating,
        }
    except Exception as e:
        print(f"[WARN] Error fetching Fear & Greed: {e}")
        return None


def _fg_emoji(score: int) -> str:
    if score <= 25:
        return "😱"
    elif score <= 45:
        return "😨"
    elif score <= 55:
        return "😐"
    elif score <= 75:
        return "😊"
    return "🤑"


def _vix_label(value: float) -> str:
    if value < 15:
        return "calma"
    elif value < 20:
        return "moderado"
    elif value < 30:
        return "elevado"
    return "alto"


def _format_sentiment_block(vix, fg):
    """
    Devuelve (display_html, plain_text) con el bloque de sentimiento.
    Si ambos son None devuelve ('', '').
    """
    display_lines = []
    plain_lines = []

    if fg:
        emoji = _fg_emoji(fg["score"])
        rating_es = _FG_RATING_ES.get(fg["rating"], fg["rating"])
        display_lines.append(
            f"  Fear &amp; Greed: <b>{fg['score']}</b> — {rating_es} {emoji}"
        )
        plain_lines.append(
            f"Fear & Greed Index: {fg['score']} ({fg['rating']})"
        )

    if vix:
        sign = "+" if vix["change"] >= 0 else ""
        direction = "↑" if vix["change"] >= 0 else "↓"
        label = _vix_label(vix["value"])
        display_lines.append(
            f"  VIX: <b>{vix['value']:.1f}</b>"
            f" ({direction}{sign}{vix['change']:.2f} pts — {label})"
        )
        plain_lines.append(
            f"VIX: {vix['value']:.1f}"
            f" (variación {sign}{vix['change']:.2f} pts, nivel {label})"
        )

    if not display_lines:
        return "", ""

    header = "🧭 <b>Sentimiento</b>"
    display = header + "\n" + "\n".join(display_lines)
    return display, "\n".join(plain_lines)


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
# CONTEXTO MACRO Y NOTICIAS PARA IA
# ================================
def _fetch_todays_macro_context(target_date) -> str:
    """Eventos macro de hoy (ForexFactory) para dar contexto a la IA."""
    try:
        from econ_calendar import fetch_ff_events
        events = fetch_ff_events(target_date)
        if not events:
            return ""
        lines = []
        for e in events:
            line = f"- {e['time_str']} {e['event']}"
            if e.get("previous"):
                line += f" | ant: {e['previous']}"
            if e.get("forecast"):
                line += f" | est: {e['forecast']}"
            lines.append(line)
        return "\n".join(lines)
    except Exception as ex:
        print(f"[WARN] Error fetching macro context: {ex}")
        return ""


def _fetch_recent_headlines() -> str:
    """Top titulares recientes (RSS) para dar contexto a la IA. Sin traducción."""
    try:
        from news_es import fetch_items, select_items
        uniq = fetch_items()
        selected = select_items(uniq)
        if not selected:
            return ""
        return "\n".join(f"- {x[2]}" for x in selected[:5])
    except Exception as ex:
        print(f"[WARN] Error fetching headlines: {ex}")
        return ""


# ================================
# INTERPRETACIÓN DEL DÍA (IA unificada)
# ================================
def interpret_premarket(plain_text: str, macro_context: str = "", news_context: str = "") -> str:
    if not plain_text:
        return ""

    system_prompt = (
        "Eres un analista de mercados senior. Escribes en español claro, neutral e institucional "
        "para un canal de trading profesional. No menciones IA ni modelos.\n\n"
        "Requisitos:\n"
        "- 4–6 frases cortas y directas.\n"
        "- Usa Fear & Greed y VIX para contextualizar el sentimiento.\n"
        "- Si hay eventos macro programados hoy, menciona el más relevante y a qué hora.\n"
        "- Si hay titulares que estén moviendo el mercado, incorpóralos brevemente.\n"
        "- Explica el tono general (alcista/bajista/mixto) y los motivos.\n"
        "- Menciona si el sector tecnológico lidera o diverge.\n"
        "- Indica si BTC/ETH acompañan o van a contracorriente.\n"
        "- Si casi todo se mueve <0.3%, señala que el arranque es plano/mixto.\n"
        "- Termina con 'En resumen, ...'."
    )

    macro_section = (
        f"\nAgenda macro de hoy (hora Madrid):\n{macro_context}"
        if macro_context else "\nAgenda macro de hoy: sin eventos de alto impacto."
    )
    news_section = (
        f"\nTitulares recientes:\n{news_context}"
        if news_context else ""
    )

    user_prompt = (
        "Datos del premarket:\n"
        f"{plain_text}"
        f"{macro_section}"
        f"{news_section}\n\n"
        "Redacta el comentario siguiendo la estructura indicada."
    )

    try:
        return (call_gpt_mini(system_prompt, user_prompt, max_tokens=350) or "").strip()
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

    # Marcar al inicio para evitar doble ejecución si el cron solapa
    if not force:
        _mark_sent_today(today_str)

    # ====================================================
    # ÍNDICES / FUTUROS (FUTUROS -> ETF -> ÍNDICE CASH)
    # Esto evita 0.00% por caer en ^GSPC/^NDX/^RUT (sin premarket)
    # ====================================================
    indices_map = {
        "S&P 500": ["ES=F", "SPY", "^GSPC"],
        "Nasdaq 100": ["NQ=F", "QQQ", "^NDX"],
        "Russell 2000": ["RTY=F", "IWM", "^RUT"],
        # opcional:
        # "Dow": ["YM=F", "DIA", "^DJI"],
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

    # Sentimiento: VIX + Fear & Greed
    vix = _fetch_vix()
    fg = _fetch_fear_and_greed()
    sentiment_display, sentiment_plain = _format_sentiment_block(vix, fg)

    # Enriquecemos el plain_text con sentimiento para la IA
    if sentiment_plain:
        plain_text = sentiment_plain + "\n" + plain_text

    # Contexto macro y noticias para la IA
    macro_context = _fetch_todays_macro_context(today)
    news_context = _fetch_recent_headlines()

    interpretation = interpret_premarket(plain_text, macro_context, news_context)

    today_str_nice = today.strftime("%d/%m/%Y")

    parts = [
        "🌅 <b>Buenos días, equipo</b>\n",
        f"Así viene el mercado hoy — {today_str_nice}:\n",
    ]

    if sentiment_display:
        parts.append(sentiment_display + "\n")

    parts.append(display_text)

    if interpretation:
        parts.append("\n")
        parts.append(interpretation)

    final_msg = "\n".join(parts).strip()
    send_telegram(final_msg)

    if not force:
        _mark_sent_today(today_str)
        print("[INFO] Premarket marcado como enviado para hoy.")
