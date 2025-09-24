#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, io, math, textwrap
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

# ==== Config ====
TELEGRAM_TOKEN = os.getenv("INVESTX_TOKEN", "").strip()
CHAT_ID        = os.getenv("CHAT_ID", "").strip()
LOCAL_TZ       = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))

# Tickers: futuros (precio "pre-mercado") y cash (cierre oficial)
PAIRS = [
    # name, future_ticker, cash_ticker
    ("S&P 500",  "ES=F",  "^GSPC"),
    ("Nasdaq 100","NQ=F",  "^NDX"),
    ("Dow Jones","YM=F",  "^DJI"),
    ("Russell 2000","RTY=F","^RUT"),
]

# Paleta simple
BG_COLOR     = (12, 12, 12)    # fondo
CARD_BG      = (22, 22, 26)
TEXT_PRIMARY = (240, 240, 240)
TEXT_MUTED   = (180, 180, 180)
GREEN        = (34, 166, 94)
RED          = (214, 69, 65)
NEUTRAL      = (120, 120, 120)

# Fuentes (fallback a default si no están)
def load_font(size, weight="regular"):
    # Puedes añadir tus .ttf al repo y cambiarlas aquí (ej. Inter, Roboto)
    try:
        if weight == "bold":
            return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except:
        return ImageFont.load_default()

def pct_color(p):
    if p > 0.10:   return GREEN
    if p < -0.10:  return RED
    return NEUTRAL

def fetch_prices():
    rows = []
    for name, fut, cash in PAIRS:
        f = yf.Ticker(fut)
        c = yf.Ticker(cash)

        # Último precio de futuro (casi en tiempo real para premarket)
        f_info = f.fast_info if hasattr(f, "fast_info") else {}
        fut_px  = None
        try:
            fut_px = float(f_info.get("last_price")) if f_info.get("last_price") else None
        except:
            fut_px = None

        # Cierre oficial del cash (día hábil previo)
        cash_hist = c.history(period="5d", interval="1d", prepost=False, auto_adjust=False)
        prev_close = None
        if not cash_hist.empty:
            prev_close = float(cash_hist["Close"].dropna().iloc[-1])

        pct = None
        if fut_px and prev_close and prev_close != 0:
            pct = (fut_px/prev_close - 1.0) * 100.0

        rows.append({
            "name": name,
            "future": fut,
            "cash": cash,
            "fut_px": fut_px,
            "prev_close": prev_close,
            "pct": pct
        })
    return pd.DataFrame(rows)

def build_interpretation(df):
    # Clasificación simple según media y dispersión
    valid = df.dropna(subset=["pct"]).copy()
    if valid.empty:
        return "Sin datos fiables de premarket ahora mismo."

    mean = valid["pct"].mean()
    worst = valid.loc[valid["pct"].idxmin()]
    best  = valid.loc[valid["pct"].idxmax()]

    tone = "🔴 Sesgo bajista" if mean < -0.2 else ("🟢 Sesgo alcista" if mean > 0.2 else "⚪ Sesgo neutral")
    bullets = []

    # Líneas clave
    bullets.append(f"{tone} en futuros: media {mean:+.2f}%.")
    bullets.append(f"Mejor: {best['name']} {best['pct']:+.2f}% | Peor: {worst['name']} {worst['pct']:+.2f}%.")

    # Señal cualitativa
    spread = best["pct"] - worst["pct"]
    if abs(spread) >= 0.6:
        bullets.append("Rotación marcada entre índices (dispersión > 0,6 pp).")
    else:
        bullets.append("Movimiento relativamente homogéneo entre índices.")

    # Matiz táctico
    if mean > 0.6:
        bullets.append("Clima positivo antes de la apertura; ojo a tomas de beneficio si no hay catalizadores.")
    elif mean < -0.6:
        bullets.append("Apertura en presión; vigila soportes iniciales y posibles rebotes técnicos.")
    else:
        bullets.append("Apertura mixta; niveles iniciales y flujo de noticia dictarán el sesgo intradía.")

    return " ".join(bullets)

def draw_image(df, timestamp_local):
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), BG_COLOR)
    d = ImageDraw.Draw(img)

    title_font = load_font(44, "bold")
    label_font = load_font(28, "bold")
    pct_font   = load_font(48, "bold")
    small_font = load_font(22, "regular")

    # Título
    title = "Premarket US — Futuros vs cierre cash"
    d.text((40, 30), title, font=title_font, fill=TEXT_PRIMARY)

    # Timestamp
    ts = timestamp_local.strftime("%d %b %Y, %H:%M %Z")
    d.text((40, 90), f"Actualizado: {ts}", font=small_font, fill=TEXT_MUTED)

    # Grid 2x2
    pad = 40
    card_w = (W - pad*3) // 2
    card_h = (H - 170 - pad*3) // 2
    x0 = 40
    y0 = 150

    positions = [
        (x0, y0),
        (x0 + card_w + pad, y0),
        (x0, y0 + card_h + pad),
        (x0 + card_w + pad, y0 + card_h + pad),
    ]

    for (idx, row) in df.reset_index(drop=True).iterrows():
        if idx > 3: break
        cx, cy = positions[idx]
        # Caja
        d.rounded_rectangle([cx, cy, cx+card_w, cy+card_h], radius=24, fill=CARD_BG)

        # Título índice
        d.text((cx+30, cy+28), row["name"], font=label_font, fill=TEXT_PRIMARY)

        # % cambio
        pct = row["pct"]
        if pct is None or math.isnan(pct):
            pct_str = "—"
            color = NEUTRAL
        else:
            pct_str = f"{pct:+.2f}%"
            color = pct_color(pct)

        d.text((cx+30, cy+90), pct_str, font=pct_font, fill=color)

        # Precio futuro y ref cierre
        fut = row["fut_px"]
        prev = row["prev_close"]
        line2 = f"Futuro: {fut:.2f}" if fut else "Futuro: —"
        line3 = f"Cierre cash: {prev:.2f}" if prev else "Cierre cash: —"
        d.text((cx+30, cy+170), line2, font=small_font, fill=TEXT_MUTED)
        d.text((cx+30, cy+200), line3, font=small_font, fill=TEXT_MUTED)

    return img

def send_telegram_photo(img_pil, caption):
    assert TELEGRAM_TOKEN and CHAT_ID, "Faltan INVESTX_TOKEN o CHAT_ID"
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"

    bio = io.BytesIO()
    img_pil.save(bio, format="PNG", optimize=True)
    bio.seek(0)

    files = {"photo": ("premarket.png", bio, "image/png")}
    data = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}

    r = requests.post(url, data=data, files=files, timeout=30)
    r.raise_for_status()
    return r.json()

def main():
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(LOCAL_TZ)

    df = fetch_prices()
    interpretation = build_interpretation(df)
    img = draw_image(df, now_local)

    # Caption: breve + tabla en texto
    lines = ["<b>Premarket USA</b>"]
    for _, r in df.iterrows():
        p = "—" if (r["pct"] is None or math.isnan(r["pct"])) else f"{r['pct']:+.2f}%"
        lines.append(f"• <b>{r['name']}</b>: {p}")
    lines.append("")
    lines.append(interpretation)

    caption = "\n".join(lines)
    send_telegram_photo(img, caption)

if __name__ == "__main__":
    main()
