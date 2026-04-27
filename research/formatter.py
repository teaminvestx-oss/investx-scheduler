import os
import logging

import yfinance as yf
from openai import OpenAI

logger = logging.getLogger(__name__)


def _get_openai_client() -> OpenAI:
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])

SYSTEM_PROMPT = (
    "Eres el analista InvestX. Formatea este análisis para Telegram en español. "
    "Tono: profesional, directo, sin adornos. Máximo 400 palabras."
)


def get_current_price(ticker: str) -> str | None:
    try:
        info = yf.Ticker(ticker).fast_info
        price = getattr(info, "last_price", None)
        if price:
            return f"{float(price):.2f}"
    except Exception as e:
        logger.warning(f"yfinance failed for {ticker}: {e}")
    return None


def _pct(val) -> str:
    try:
        return f"{float(val) * 100:.0f}"
    except (TypeError, ValueError):
        return str(val) if val is not None else ""


def _v(data: dict, key: str) -> str:
    val = data.get(key, "")
    if val is None or (isinstance(val, float) and val != val):  # nan check
        return ""
    return str(val)


def format_analysis(data: dict, queries_remaining: int, queries_limit: int) -> str:
    ticker = _v(data, "ticker").upper()
    price = get_current_price(ticker)
    price_str = f"${price}" if price else "N/D (error yfinance)"

    user_prompt = (
        f"📊 ANÁLISIS INVESTX — ${ticker} | {_v(data, 'asset_name')}\n"
        f"🏭 Sector: {_v(data, 'sector')}\n"
        f"📅 Actualizado: {_v(data, 'last_updated')} · Precio actual: {price_str}\n\n"
        f"📌 SESGO 1-3M\n{_v(data, 'bias_1_3m')}\n\n"
        f"🔍 LECTURA ACTUAL\n{_v(data, 'current_read')}\n\n"
        f"🎯 ESCENARIOS (Jerarquía: {_v(data, 'scenario_hierarchy')})\n\n"
        f"1️⃣ Zona {_v(data, 'long1_entry')} · Stop {_v(data, 'long1_sl')} · "
        f"TP1 {_v(data, 'long1_tp1')} · TP2 {_v(data, 'long1_tp2')}\n"
        f"Prob: {_pct(data.get('long1_probability'))}% — {_v(data, 'long1_light_why')}\n\n"
        f"2️⃣ Zona {_v(data, 'long2_entry')} · Stop {_v(data, 'long2_sl')} · "
        f"TP1 {_v(data, 'long2_tp1')} · TP2 {_v(data, 'long2_tp2')}\n"
        f"Prob: {_pct(data.get('long2_probability'))}% — {_v(data, 'long2_light_why')}\n\n"
        f"3️⃣ Zona {_v(data, 'long3_entry')} · Stop {_v(data, 'long3_sl')} · "
        f"TP1 {_v(data, 'long3_tp1')} · TP2 {_v(data, 'long3_tp2')}\n"
        f"Prob: {_pct(data.get('long3_probability'))}% — {_v(data, 'long3_light_why')}\n\n"
        f"✅ CONCLUSIÓN\n{_v(data, 'final_conclusion')}\n\n"
        f"📊 Consultas restantes este mes: {queries_remaining}/{queries_limit}\n\n"
        f"⚠️ No es asesoramiento financiero · @investx_trading"
    )

    response = _get_openai_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=800,
    )
    return response.choices[0].message.content.strip()
