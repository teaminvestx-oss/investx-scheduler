#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, io, math, traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import requests
from PIL import Image, ImageDraw, ImageFont

# === Config ===
BOT  = os.getenv("INVESTX_TOKEN", "").strip()
CHAT = os.getenv("CHAT_ID", "").strip()
TZ   = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))
EVENT_NAME = os.getenv("GITHUB_EVENT_NAME", "").strip()   # schedule | workflow_dispatch
FORCE_SEND = os.getenv("FORCE_SEND", "0") == "1"

# (nombre, futuro, cash, proxy cash si hiciera falta)
PAIRS = [
    ("S&P 500",     "ES=F",  "^GSPC", None),
    ("Nasdaq 100",  "NQ=F",  "^NDX",  "QQQ"),
    ("Dow Jones",   "YM=F",  "^DJI",  None),
    ("Russell 2000","RTY=F", "^RUT",  "IWM"),
]

YH_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={intv}&includePrePost=true"

# ---------- Data fetch ----------
def yahoo_last_price(sym: str, rng="1d", intv="1m"):
    """Devuelve el último close no nulo de las velas del símbolo."""
    url = YH_CHART.format(sym=sym, rng=rng, intv=intv)
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    data = r.json()
    res = (data.get("chart") or {}).get("result") or []
    if not res:
        return None
    quote = (((res[0].get("indicators") or {}).get("quote") or []) or [{}])[0]
    closes = quote.get("close") or []
    closes = [c for c in closes if isinstance(c, (int, float))]
    return float(closes[-1]) if closes else None

def prev_cash_close(sym: str, proxy: str | None):
    """Cierre del día hábil previo del índice cash o, si falla, del ETF proxy."""
    symbols = [sym] if not proxy else [sym, proxy]
    for s in symbols:
        try:
            px = yahoo_last_price(s, rng="5d", intv="1d")
            if px and px > 0:
                return px
        except Exception:
            pass
    return None

# ---------- Render ----------
BG = (12, 12, 12)
CARD = (22, 22, 26)
TXT = (240, 240, 240)
MUT = (180, 180, 180)
GREEN = (34, 166, 94)
RED = (214, 69, 65)
NEU = (160, 160, 160)

def font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()

def color_pct(p):
    if p is None or math.isnan(p): return NEU
    if p >= 0.10: return GREEN
    if p <= -0.10: return RED
    return NEU

def draw_image(rows, ts_local):
    from PIL import Image
    W, H = 1200, 630
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    d.text((40, 30), "Premarket US — Futuros vs cierre cash", font=font(44, True), fill=TXT)
    d.text((40, 90), f"Actualizado: {ts_local.strftime('%d %b %Y, %H:%M %Z')}", font=font(22), fill=MUT)

    pad = 40
    cw = (W - pad * 3) // 2
    ch = (H - 170 - pad * 3) // 2
    pos = [
        (40, 150),
        (40 + cw + pad, 150),
        (40, 150 + ch + pad),
        (40 + cw + pad, 150 + ch + pad),
    ]

    for i, r in enumerate(rows):
        if i > 3: break
        x, y = pos[i]
        d.rounded_rectangle([x, y, x + cw, y + ch], radius=24, fill=CARD)
        d.text((x + 30, y + 28), r["name"], font=font(28, True), fill=TXT)

        pct = r["pct"]
        pct_s = "—" if pct is None or math.isnan(pct) else f"{pct:+.2f}%"
        d.text((x + 30, y + 90), pct_s, font=font(48, True), fill=color_pct(pct))

        fut = r["fut"]; cash = r["cash"]
        d.text((x + 30, y + 170), f"Futuro: {fut:.2f}" if isinstance(fut, (int, float)) else "Futuro: —", font=font(22), fill=MUT)
        d.text((x + 30, y + 200), f"Cierre cash: {cash:.2f}" if isinstance(cash, (int, float)) else "Cierre cash: —", font=font(22), fill=MUT)
    return im

def interpretation(rows):
    vals = [r["pct"] for r in rows if r["pct"] is not None and not math.isnan(r["pct"])]
    if not vals:
        return "Sin datos fiables ahora mismo (fuentes sin actualización reciente)."
    mean = sum(vals) / len(vals)
    best = max(rows, key=lambda r: -1e9 if r["pct"] is None else r["pct"])
    worst = min(rows, key=lambda r: +1e9 if r["pct"] is None else r["pct"])
    tone = "🟢 Sesgo alcista" if mean > 0.2 else ("🔴 Sesgo bajista" if mean < -0.2 else "⚪ Sesgo neutral")
    spread = (best["pct"] - worst["pct"]) if (best["pct"] is not None and worst["pct"] is not None) else None
    out = [
        f"{tone} en futuros: media {mean:+.2f}%.",
        f"Mejor: {best['name']} {best['pct']:+.2f}% | Peor: {worst['name']} {worst['pct']:+.2f}%.",
    ]
    out.append("Rotación marcada entre índices." if (spread is not None and abs(spread) >= 0.6) else "Movimiento relativamente homogéneo.")
    out.append("Apertura sujeta a titulares macro/empresa; vigilar niveles iniciales.")
    return " ".join(out)

def send_photo(img, caption):
    assert BOT and CHAT, "Faltan INVESTX_TOKEN o CHAT_ID"
    url = f"https://api.telegram.org/bot{BOT}/sendPhoto"
    bio = io.BytesIO()
    img.save(bio, "PNG", optimize=True)
    bio.seek(0)
    r = requests.post(
        url,
        data={"chat_id": CHAT, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("premarket.png", bio, "image/png")},
        timeout=60,
    )
    r.raise_for_status()

# ---------- Main ----------
def main():
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(TZ)

    # Ventana 11:55–12:15 local (o envío si se lanzó manualmente / forzado)
    mins = now_local.hour * 60 + now_local.minute
    in_window = (mins >= 11 * 60 + 55 and mins <= 12 * 60 + 15)
    if not (in_window or EVENT_NAME == "workflow_dispatch" or FORCE_SEND):
        print(f"[guard] {now_local.strftime('%H:%M %Z')} fuera de ventana 12:00 → no envío.")
        return

    rows = []
    for name, fut, cash, proxy in PAIRS:
        try:
            fut_px = yahoo_last_price(fut, rng="1d", intv="1m")
        except Exception:
            fut_px = None
        try:
            prev = prev_cash_close(cash, proxy)
        except Exception:
            prev = None
        pct = (fut_px / prev - 1) * 100 if (isinstance(fut_px, (int, float)) and isinstance(prev, (int, float)) and prev != 0) else None
        rows.append({"name": name, "fut": fut_px, "cash": prev, "pct": pct})

    img = draw_image(rows, now_local)

    cap = ["<b>Premarket USA</b>"]
    for r in rows:
        pct_s = "—" if r["pct"] is None else f"{r['pct']:+.2f}%"
        cap.append(f"• <b>{r['name']}</b>: {pct_s}")
    cap.append("")
    cap.append(interpretation(rows))

    send_photo(img, "\n".join(cap))
    print("[ok] enviado.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[error]", e)
        traceback.print_exc()

