# === premarket.py ===
# "Buenos días" / premarket InvestX

import os
import json
import datetime as dt
from typing import Dict, List

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

# Fichero de estado para NO repetir mensaje varias veces al día
PREMARKET_STATE_FILE = "premarket_state.json"


# ================================
# Estado diario (para 1 envío / día)
# ================================
def _load_state() -> Dict:
    if not os.path.exists(PREMARKET_STATE_FILE):
        return {}
    try:
        with open(PREMARKET_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("[WARN] premarket: no se pudo leer estado:", e)
        return {}


def _save_state(state: Dict) -> None:
    try:
        with open(PREMARKET_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        print("[WARN] premarket: no se pudo guardar estado:", e)


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
# CÁLCULO DE CAMBIOS (precio + %)
# ================================
def _get_last_and_prev(yf_ticker: str):
    """
    Intenta obtener:
    - last_price (precio actual / premarket)
    - previous_close (cierre anterior)
    usando fast_info. Si falla, hace fallback a history(2d).
    """
    try:
        t = yf.Ticker(yf_ticker)
        fi = t.fast_info

        last_price = getattr(fi, "last_price", None)
        prev_close = getattr(fi, "previous_close", None)

        # Si fi es dict-like
        if last_price is None and isinstance(fi, dict):
            last_price = fi.get("last_price")
        if prev_close is None and isinstance(fi, dict):
            prev_close = fi.get("previous_close")

        # Fallback a history si falta algo
        if last_price is None or prev_close is None:
            hist = t.history(period="2d")
            if hist is None or hist.empty or len(hist) < 1:
                return None, None
            last_price = float(hist["Close"].iloc[-1])
            if len(hist) >= 2:
                prev_close = float(hist["Close"].iloc[-2])
            else:
                prev_close = last_price

        if prev_close == 0:
            return last_price, None

        return float(last_price), float(prev_close)

    except Exception as e:
        print(f"[WARN] Error obteniendo datos de {yf_ticker}: {e}")
        return None, None


def get_changes_map(ticker_map: dict) -> List[Dict]:
    """
    ticker_map: { nombre_mostrar: ticker_yfinance }
    Devuelve lista con:
      { "name", "price", "change_pct" }
    donde price = precio actual (premarket si aplica)
    y change_pct = % vs previous_close.
    """
    results = []
    for name, yf_ticker in ticker_map.items():
        last_price, prev_close = _get_last_and_prev(yf_ticker)
        if last_price is None or prev_close is None:
            continue
        change_pct = (last_price - prev_close) / prev_close * 100.0
        results.append(
            {
                "name": name,
                "price": round(last_price, 2),
                "change_pct": round(change_pct, 2),
            }
        )
    return results


def get_crypto_changes() -> List[Dict]:
    cryptos = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
    }
    return get_changes_map(cryptos)


# ================================
# FORMATEO CON COLORES Y FLECHAS
# ================================
def style_change(change_pct: float):
    """
    Devuelve (icono, flecha, texto_porcentaje) según si sube, baja o está plano.
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
    pct_text = f"{sign}{change_pct:.2f}%"
    return icon, arrow, pct_text


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
            icon, arrow, pct = style_change(item["change_pct"])
            price = f"{item['price']:.2f}"
            display_lines.append(f"{icon} {item['name']} {price} {arrow} ({pct})")
            plain_lines.append(f"{item['name']}: {price} ({pct})")

    if megacaps:
        display_lines.append("")
        display_lines.append("📊 <b>Mega-caps USA</b>\n")
        for item in megacaps:
            icon, arrow, pct = style_change(item["change_pct"])
            price = f"{item['price']:.2f}"
            display_lines.append(f"{icon} {item['name']} {price} {arrow} ({pct})")
            plain_lines.append(f"{item['name']}: {price} ({pct})")

    if sectors:
        display_lines.append("")
        display_lines.append("🏦 <b>Otros sectores clave</b>\n")
        for item in sectors:
            icon, arrow, pct = style_change(item["change_pct"])
            price = f"{item['price']:.2f}"
            display_lines.append(f"{icon} {item['name']} {price} {arrow} ({pct})")
            plain_lines.append(f"{item['name']}: {price} ({pct})")

    if cryptos:
        display_lines.append("")
        display_lines.append("💰 <b>Criptomonedas</b>\n")
        for item in cryptos:
            icon, arrow, pct = style_change(item["change_pct"])
            price = f"{item['price']:.2f}"
            display_lines.append(f"{icon} {item['name']} {price} {arrow} ({pct})")
            plain_lines.append(f"{item['name']}: {price} ({pct})")

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
    """
    Envía el mensaje de 'Buenos días / premarket' UNA sola vez al día,
    salvo que force=True (por ejemplo, si lo ejecutas manualmente).
    """
    today = dt.date.today()
    today_str = today.isoformat()

    if today.weekday() >= 5 and not force:
        # sábado (5) o domingo (6)
        print("[INFO] Es fin de semana, no se envía 'Buenos días'.")
        return

    if not force and _already_sent_today(today_str):
        print("[INFO] premarket: ya se envió 'Buenos días' hoy. No se repite.")
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

    today_str_human = today.strftime("%d/%m/%Y")

    parts = [
        "🌅 <b>Buenos días, equipo</b>\n",
        f"Así viene el mercado hoy — {today_str_human}:\n",
        display_text,
    ]

    if interpretation:
        parts.append("\n")
        parts.append(interpretation)

    final_msg = "\n".join(parts).strip()
    send_telegram(final_msg)

    if not force:
        _mark_sent_today(today_str)
        print("[INFO] premarket: marcado como enviado para hoy.")
