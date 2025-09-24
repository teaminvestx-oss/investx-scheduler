#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, io, math, time, traceback
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
DEBUG = True  # deja True para ver en logs lo que devuelve cada fetch

# (nombre, futuro, cash, proxy cash, proxy ETF para fallback de precio intradía)
PAIRS = [
    ("S&P 500",     "ES=F",  "^GSPC", None, "SPY"),
    ("Nasdaq 100",  "NQ=F",  "^NDX",  "QQQ", "QQQ"),
    ("Dow Jones",   "YM=F",  "^DJI",  None, "DIA"),
    ("Russell 2000","RTY=F", "^RUT",  "IWM", "IWM"),
]

YH = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={iv}&includePrePost=true"
HDRS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept": "*/*",
}

def log(*a): 
    if DEBUG: print(*a, flush=True)

# ---------- Yahoo helpers (con retry + diagnóstico) ----------
def yahoo_last_price(sym: str, rng="1d", iv="1m", retries=3, sleep_s=1.2):
    url = YH.format(sym=sym, rng=rng, iv=iv)
    last = None; ncl = 0
    for k in range(retries):
        try:
            r = requests.get(url, headers=HDRS, timeout=25)
            r.raise_for_status()
            data = r.json()
            res = (data.get("chart") or {}).get("result") or []
            if not res:
                log(f"[{sym}] sin result en intento {k+1}")
                time.sleep(sleep_s); continue
            q = (((res[0].get("indicators") or {}).get("quote") or []) or [{}])[0]
            closes = q.get("close") or []
            vals = [c for c in closes if isinstance(c,(int,float))]
            ncl = len(vals)
            last = float(vals[-1]) if vals else None
            log(f"[{sym}] rng={rng} iv={iv} -> ncloses={ncl} last={last}")
            break
        except Exception as e:
            log(f"[{sym}] error intento {k+1}: {e}")
            time.sleep(sleep_s)
    return last, ncl

def prev_cash_close(sym: str, proxy: str | None):
    # cierre previo del índice cash (o ETF proxy si falla)
    for s in ([sym] + ([proxy] if proxy else [])):
        px, n = yahoo_last_price(s, rng="5d", iv="1d")
        if px and px > 0:
            log(f"[prev_close] {s} -> {px} (n={n})")
            return px
    log(f"[prev_close] {sym} sin dato (ni proxy)")
    return None

# ---------- Render ----------
BG = (12,12,12); CARD = (22,22,26)
TXT = (240,240,240); MUT = (180,180,180)
GREEN = (34,166,94); RED = (214,69,65); NEU = (160,160,160)

def font(size, bold=False):
    try: return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
    except: return ImageFont.load_default()

def color_pct(p):
    if p is None or math.isnan(p): return NEU
    if p >= 0.10: return GREEN
    if p <= -0.10: return RED
    return NEU

def draw_image(rows, ts_local):
    from PIL import Image
    W,H=1200,630
    im=Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(im)
    d.text((40,30),"Premarket US — Futuros vs cierre cash",font=font(44,True),fill=TXT)
    d.text((40,90),f"Actualizado: {ts_local.strftime('%d %b %Y, %H:%M %Z')}",font=font(22),fill=MUT)
    pad=40; cw=(W-pad*3)//2; ch=(H-170-pad*3)//2
    pos=[(40,150),(40+cw+pad,150),(40,150+ch+pad),(40+cw+pad,150+ch+pad)]
    for i,r in enumerate(rows[:4]):
        x,y=pos[i]
        d.rounded_rectangle([x,y,x+cw,y+ch],radius=24,fill=CARD)
        d.text((x+30,y+28),r["name"],font=font(28,True),fill=TXT)
        pct=r["pct"]; pct_s="—" if pct is None or math.isnan(pct) else f"{pct:+.2f}%"
        d.text((x+30,y+90),pct_s,font=font(48,True),fill=color_pct(pct))
        fut=r["fut"]; cash=r["cash"]
        d.text((x+30,y+170), f"Futuro/Proxy: {fut:.2f}" if isinstance(fut,(int,float)) else "Futuro/Proxy: —", font=font(22), fill=MUT)
        d.text((x+30,y+200), f"Cierre cash: {cash:.2f}" if isinstance(cash,(int,float)) else "Cierre cash: —", font=font(22), fill=MUT)
    return im

def interpretation(rows):
    vals=[r["pct"] for r in rows if r["pct"] is not None and not math.isnan(r["pct"])]
    if not vals:
        return "Sin datos fiables ahora mismo (la fuente no devolvió velas)."
    mean=sum(vals)/len(vals)
    best=max(rows, key=lambda r: -1e9 if r["pct"] is None else r["pct"])
    worst=min(rows, key=lambda r: +1e9 if r["pct"] is None else r["pct"])
    tone="🟢 Sesgo alcista" if mean>0.2 else ("🔴 Sesgo bajista" if mean<-0.2 else "⚪ Sesgo neutral")
    spread=(best["pct"]-worst["pct"]) if (best["pct"] is not None and worst["pct"] is not None) else None
    out=[f"{tone} en futuros/ETF: media {mean:+.2f}%.",
         f"Mejor: {best['name']} {best['pct']:+.2f}% | Peor: {worst['name']} {worst['pct']:+.2f}%."]
    out.append("Rotación marcada." if (spread is not None and abs(spread)>=0.6) else "Movimiento homogéneo.")
    out.append("Apertura sujeta a titulares; vigilar niveles.")
    return " ".join(out)

def send_photo(img, caption):
    assert BOT and CHAT, "Faltan INVESTX_TOKEN o CHAT_ID"
    url=f"https://api.telegram.org/bot{BOT}/sendPhoto"
    bio=io.BytesIO(); img.save(bio,"PNG",optimize=True); bio.seek(0)
    r=requests.post(url,data={"chat_id":CHAT,"caption":caption,"parse_mode":"HTML"},
                    files={"photo":("premarket.png",bio,"image/png")},timeout=60)
    r.raise_for_status()

# ---------- Main ----------
def main():
    now_utc=datetime.now(timezone.utc); now_local=now_utc.astimezone(TZ)
    # Ventana 11:55–12:15 local (o manual/forzado)
    mins=now_local.hour*60+now_local.minute
    in_window=(mins>=11*60+55 and mins<=12*60+15)
    if not (in_window or EVENT_NAME=="workflow_dispatch" or FORCE_SEND):
        print(f"[guard] {now_local.strftime('%H:%M %Z')} fuera de ventana 12:00 → no envío."); return

    rows=[]
    all_missing=True
    for name, fut, cash, proxy_cash, proxy_etf in PAIRS:
        # 1) Precio futuro 1d/1m
        fut_px, n_fut = yahoo_last_price(fut, rng="1d", iv="1m")
        # 2) Cierre cash previo
        prev = prev_cash_close(cash, proxy_cash)
        # 3) Si no hay futuro, intenta proxy ETF en 1d/1m
        if fut_px is None:
            etf_px, n_etf = yahoo_last_price(proxy_etf, rng="1d", iv="1m") if proxy_etf else (None,0)
            if etf_px is not None:
                log(f"[{name}] usando PROXY ETF {proxy_etf}: last={etf_px} (n={n_etf})")
                fut_px = etf_px
        pct = (fut_px/prev - 1)*100 if (isinstance(fut_px,(int,float)) and isinstance(prev,(int,float)) and prev!=0) else None
        if pct is not None: all_missing=False
        rows.append({"name":name,"fut":fut_px,"cash":prev,"pct":pct})

    # Log resumen crudo
    for r in rows:
        log(f"[row] {r['name']}: fut={r['fut']} cash={r['cash']} pct={r['pct']}")

    img=draw_image(rows, now_local)

    cap=["<b>Premarket USA</b>"]
    for r in rows:
        pct_s="—" if r["pct"] is None else f"{r['pct']:+.2f}%"
        cap.append(f"• <b>{r['name']}</b>: {pct_s}")
    cap.append("")
    cap.append(interpretation(rows) if not all_missing else
               "Sin datos fiables (Yahoo no devolvió velas para futuros ni proxies).")

    send_photo(img, "\n".join(cap))
    print("[ok] enviado.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[error]", e); traceback.print_exc()

