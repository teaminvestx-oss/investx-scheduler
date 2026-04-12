# === market_close.py ===
# Cierre de mercado USA – InvestX
# Incluye: índices, sectores, top movers, crypto, VIX, Fear & Greed,
#          resultados macro del día y titulares → interpretación IA Bloomberg-style

import os
import datetime as dt
from typing import Optional, Dict, List

import requests
import yfinance as yf

from utils import call_gpt_mini

# ================================
# ENV VARS
# ================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("INVESTX_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

_FG_API_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"

_FG_RATING_ES = {
    "Extreme Fear": "Miedo extremo",
    "Fear": "Miedo",
    "Neutral": "Neutral",
    "Greed": "Codicia",
    "Extreme Greed": "Codicia extrema",
}


# ================================
# TELEGRAM
# ================================
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[ERROR] Faltan TELEGRAM_TOKEN / CHAT_ID para enviar mensaje (market close).")
        return

    max_len = 3900
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
def get_pct_change(symbol: str) -> Optional[float]:
    """% cambio del día vs cierre anterior."""
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
    return sum(nums) / len(nums) if nums else None


def style_change(change_pct: float) -> str:
    if change_pct > 0.3:
        return "🟢"
    elif change_pct < -0.3:
        return "🔴"
    return "⚪️"


# ================================
# SENTIMIENTO: VIX + FEAR & GREED
# ================================
def _fetch_vix() -> Optional[Dict]:
    try:
        t = yf.Ticker("^VIX")
        daily = t.history(period="5d", interval="1d")
        if daily is None or daily.empty:
            return None
        closes = daily["Close"].dropna()
        if len(closes) < 1:
            return None
        current = float(closes.iloc[-1])
        change = (current - float(closes.iloc[-2])) if len(closes) >= 2 else 0.0
        change_pct = (change / float(closes.iloc[-2]) * 100.0) if len(closes) >= 2 else 0.0
        return {
            "value": round(current, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
        }
    except Exception as e:
        print(f"[WARN] Error fetching VIX: {e}")
        return None


def _fetch_fear_and_greed() -> Optional[Dict]:
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
        if score is None:
            return None
        return {"score": round(float(score)), "rating": fg.get("rating") or ""}
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


# ================================
# CRYPTO AL CIERRE
# ================================
def _fetch_crypto_close() -> List[Dict]:
    cryptos = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
    results = []
    for name, ticker in cryptos.items():
        pct = get_pct_change(ticker)
        if pct is not None:
            results.append({"name": name, "change_pct": round(pct, 2)})
    return results


# ================================
# DATOS MACRO DEL DÍA (con actual)
# ================================
def _fetch_todays_macro_results(target_date: dt.date) -> str:
    """
    Eventos macro de hoy con valor real (actual) si ya se publicaron.
    Devuelve texto plano para la IA.
    """
    try:
        from econ_calendar import fetch_ff_events
        events = fetch_ff_events(target_date)
        if not events:
            return ""
        lines = []
        for e in events:
            line = f"- {e['time_str']} {e['event']}"
            if e.get("actual"):
                line += f" | real: {e['actual']}"
            if e.get("forecast"):
                line += f" | est: {e['forecast']}"
            if e.get("previous"):
                line += f" | ant: {e['previous']}"
            lines.append(line)
        return "\n".join(lines)
    except Exception as ex:
        print(f"[WARN] Error fetching macro results: {ex}")
        return ""


# ================================
# TITULARES DEL DÍA
# ================================
def _fetch_todays_headlines() -> str:
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
# DATOS DEL CIERRE (índices + sectores)
# ================================
def get_close_market_data():
    indices_map = {
        "S&P 500":    "^GSPC",
        "Nasdaq 100": "^NDX",
        "Dow Jones":  "^DJI",
    }
    indices = []
    for name, symbol in indices_map.items():
        pct = get_pct_change(symbol)
        if pct is not None:
            indices.append({"name": name, "symbol": symbol, "change_pct": round(pct, 2)})

    sector_tickers: Dict[str, List[str]] = {
        "Tecnología / Comunicación": ["AAPL", "MSFT", "GOOGL", "META", "AMZN", "NFLX"],
        "Semiconductores":           ["NVDA", "AMD", "INTC", "AVGO", "QCOM"],
        "Salud":                     ["JNJ", "LLY", "ABBV", "UNH", "PFE"],
        "Financieras":               ["JPM", "BAC", "C", "GS", "MS", "V", "MA"],
        "Energía":                   ["XOM", "CVX", "SLB", "COP"],
        "Consumo discrecional":      ["TSLA", "HD", "MCD", "NKE"],
        "Consumo básico":            ["PG", "KO", "PEP", "WMT", "COST"],
        "Industriales":              ["CAT", "DE", "GE", "HON"],
    }

    all_tickers = sorted({t for lst in sector_tickers.values() for t in lst})
    ticker_changes: Dict[str, Optional[float]] = {t: get_pct_change(t) for t in all_tickers}

    sectors: Dict[str, List[Dict]] = {}
    for sector, tks in sector_tickers.items():
        sector_list = [
            {"ticker": t, "change_pct": round(ticker_changes[t], 2)}
            for t in tks if ticker_changes.get(t) is not None
        ]
        if sector_list:
            sectors[sector] = sector_list

    return indices, sectors


# ================================
# FORMATEO PARA TELEGRAM + IA
# ================================
def format_market_close(indices, sectors, vix, fg, crypto):
    today = dt.date.today().strftime("%d/%m/%Y")
    display_lines: List[str] = []
    plain_lines: List[str] = []

    display_lines.append(f"📊 <b>Cierre de Wall Street — InvestX</b> ({today})\n")

    # --- SENTIMIENTO ---
    sentiment_parts = []
    if fg:
        emoji = _fg_emoji(fg["score"])
        rating_es = _FG_RATING_ES.get(fg["rating"], fg["rating"])
        sentiment_parts.append(f"  Fear &amp; Greed: <b>{fg['score']}</b> — {rating_es} {emoji}")
        plain_lines.append(f"Fear & Greed Index: {fg['score']} ({fg['rating']})")
    if vix:
        sign = "+" if vix["change"] >= 0 else ""
        direction = "↑" if vix["change"] >= 0 else "↓"
        label = _vix_label(vix["value"])
        sentiment_parts.append(
            f"  VIX cierre: <b>{vix['value']:.1f}</b> ({direction}{sign}{vix['change']:.2f} pts — {label})"
        )
        plain_lines.append(f"VIX al cierre: {vix['value']:.1f} (variación {sign}{vix['change']:.2f} pts, nivel {label})")
    if sentiment_parts:
        display_lines.append("🧭 <b>Sentimiento</b>\n")
        display_lines.extend(sentiment_parts)
        display_lines.append("")

    # --- ÍNDICES ---
    if indices:
        display_lines.append("📈 <b>Índices</b>\n")
        for idx in indices:
            icon = style_change(idx["change_pct"])
            sign = "+" if idx["change_pct"] > 0 else ""
            pct_txt = f"{sign}{idx['change_pct']:.2f}%"
            display_lines.append(f"{icon} {idx['name']}: <b>{pct_txt}</b>")
            plain_lines.append(f"{idx['name']}: {pct_txt} vs cierre previo")
        display_lines.append("")

    # --- SECTORES: TOP 2 y BOTTOM 2 ---
    sector_avgs = {
        s: avg_change([x["change_pct"] for x in lst])
        for s, lst in sectors.items()
    }
    ranked = sorted(
        [(s, v) for s, v in sector_avgs.items() if v is not None],
        key=lambda x: x[1], reverse=True,
    )
    if ranked:
        top = ranked[:2]
        bottom = ranked[-2:]
        display_lines.append("🟢 <b>Sectores fuertes</b>\n")
        for sec, val in top:
            sign = "+" if val > 0 else ""
            display_lines.append(f"  {sec}: <b>{sign}{val:.2f}%</b>")
            plain_lines.append(f"Sector {sec}: {sign}{val:.2f}% (líder)")
        display_lines.append("")
        display_lines.append("🔻 <b>Sectores débiles</b>\n")
        for sec, val in bottom:
            sign = "+" if val > 0 else ""
            display_lines.append(f"  {sec}: <b>{sign}{val:.2f}%</b>")
            plain_lines.append(f"Sector {sec}: {sign}{val:.2f}% (rezagado)")
        display_lines.append("")

    # --- TOP MOVERS ---
    all_stocks = [
        {"ticker": x["ticker"], "change_pct": x["change_pct"]}
        for lst in sectors.values() for x in lst
    ]
    top_movers = sorted(all_stocks, key=lambda x: abs(x["change_pct"]), reverse=True)[:3]
    if top_movers:
        display_lines.append("🏁 <b>Acciones destacadas</b>\n")
        parts_html = []
        for x in top_movers:
            sign = "+" if x["change_pct"] > 0 else ""
            parts_html.append(f"{x['ticker']} <b>{sign}{x['change_pct']:.2f}%</b>")
            plain_lines.append(f"{x['ticker']}: {sign}{x['change_pct']:.2f}%")
        display_lines.append("  " + "  ·  ".join(parts_html))
        display_lines.append("")

    # --- CRYPTO ---
    if crypto:
        display_lines.append("💰 <b>Crypto</b>\n")
        for c in crypto:
            icon = style_change(c["change_pct"])
            sign = "+" if c["change_pct"] > 0 else ""
            display_lines.append(f"{icon} {c['name']}: <b>{sign}{c['change_pct']:.2f}%</b>")
            plain_lines.append(f"{c['name']}: {sign}{c['change_pct']:.2f}%")
        display_lines.append("")

    display_text = "\n".join(display_lines).strip()
    plain_text = "\n".join(plain_lines).strip()
    return display_text, plain_text


# ================================
# INTERPRETACIÓN IA (Bloomberg-style)
# ================================
def interpret_market_close(
    plain_text: str,
    macro_context: str = "",
    news_context: str = "",
) -> str:
    if not plain_text:
        return ""

    system_prompt = (
        "Eres un analista institucional de mercados, estilo Bloomberg Terminal. "
        "Escribes en español neutro, directo y accionable para traders profesionales. "
        "No menciones IA ni modelos.\n\n"
        "Estructura exacta (un único bloque de texto, sin listas):\n"
        "1) Frase sobre el tono general de la sesión (risk-on / risk-off / mixto) "
        "y el comportamiento de los índices principales.\n"
        "2) Qué sectores lideraron y cuáles lastraron; qué dice eso del flujo de capital.\n"
        "3) Si se publicaron datos macro hoy, menciona si sorprendieron al alza o a la baja "
        "y cómo afectaron al mercado.\n"
        "4) Si hay titulares relevantes que expliquen algún movimiento, incorpóralos brevemente.\n"
        "5) Frase final: 'Sesgo InvestX:' con la lectura táctica para la próxima sesión "
        "(continuación, consolidación, rebote técnico, cautela, etc.).\n"
        "Total: 4–6 frases. Sé específico, sin frases genéricas."
    )

    macro_section = (
        f"\nDatos macro publicados hoy:\n{macro_context}"
        if macro_context else "\nDatos macro hoy: sin publicaciones de alto impacto."
    )
    news_section = (
        f"\nTitulares del día:\n{news_context}"
        if news_context else ""
    )

    user_prompt = (
        "Datos del cierre de Wall Street:\n"
        f"{plain_text}"
        f"{macro_section}"
        f"{news_section}\n\n"
        "Redacta el análisis siguiendo la estructura indicada."
    )

    try:
        return (call_gpt_mini(system_prompt, user_prompt, max_tokens=400) or "").strip()
    except Exception as e:
        print(f"[ERROR] interpret_market_close: {e}")
        return ""


# ================================
# FUNCIÓN PRINCIPAL: MARKET CLOSE
# ================================
def run_market_close(force: bool = False):
    today = dt.date.today()

    if today.weekday() >= 5 and not force:
        print("[INFO] Es fin de semana, no se envía 'Market Close'.")
        return

    indices, sectors = get_close_market_data()

    if not indices and not sectors:
        send_telegram("📊 <b>Cierre de Wall Street — InvestX</b>\n\nNo se han podido obtener datos de mercado hoy.")
        return

    # Sentimiento y crypto
    vix = _fetch_vix()
    fg = _fetch_fear_and_greed()
    crypto = _fetch_crypto_close()

    # Contexto para la IA
    macro_context = _fetch_todays_macro_results(today)
    news_context = _fetch_todays_headlines()

    display_text, plain_text = format_market_close(indices, sectors, vix, fg, crypto)
    interpretation = interpret_market_close(plain_text, macro_context, news_context)

    parts = [display_text]
    if interpretation:
        parts.append("\n🧠 <b>Análisis InvestX</b>\n")
        parts.append(interpretation)

    send_telegram("\n".join(parts).strip())
    print(f"[INFO] Market Close enviado correctamente (force={force}).")
