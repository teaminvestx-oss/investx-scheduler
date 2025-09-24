#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, io, math, traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

TELEGRAM_TOKEN = os.getenv("INVESTX_TOKEN", "").strip()
CHAT_ID        = os.getenv("CHAT_ID", "").strip()
LOCAL_TZ       = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))

PAIRS = [
    ("S&P 500",     "ES=F",  "^GSPC", None),
    ("Nasdaq 100",  "NQ=F",  "^NDX",  "QQQ"),
    ("Dow Jones",   "YM=F",  "^DJI",  None),
    ("Russell 2000","RTY=F", "^RUT",  "IWM"),
]

BG_COLOR=(12,12,12); CARD_BG=(22,22,26)
TEXT_PRIMARY=(240,240,240); TEXT_MUTED=(180,180,180)
GREEN=(34,166,94); RED=(214,69,65); NEUTRAL=(160,160,160)

def load_font(size, weight="regular"):
    try:
        if weight=="bold": return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except: return ImageFont.load_default()

def pct_color(p):
    if p is None or math.isnan(p): return NEUTRAL
    if p >= 0.10: return GREEN
    if p <= -0.10: return RED
    return NEUTRAL

def last_trade_price(ticker:str):
    t = yf.Ticker(ticker)
    for interval in ("1m","5m","15m"):
        try:
            h = t.history(period="2d", interval=interval, prepost=True, auto_adjust=False)
            if not h.empty:
                ser = h["Close"].dropna()
                if not ser.empty and float(ser.iloc[-1])>0:
                    return float(ser.iloc[-1])
        except Exception:
            continue
    try:
        fi = getattr(t,"fast_info",{})
        val = fi.get("last_price")
        if val is not None: return float(val)
    except Exception:
        pass
    return None

def previous_cash_close(ticker:str, proxy:str|None):
    for tk in (ticker,):
        try:
            h = yf.Ticker(tk).history(period="10d", interval="1d", prepost=False, auto_adjust=False)
            s = h["Close"].dropna()
            if not s.empty: return float(s.iloc[-1])
        except Exception:
            pass
    if proxy:
        try:
            h = yf.Ticker(proxy).history(period="10d", interval="1d", prepost=False, auto_adjust=False)
            s = h["Close"].dropna()
            if not s.empty: return float(s.iloc[-1])
        except Exception:
            pass
    return None

def fetch_snapshot():
    rows=[]
    for name,fut,cash,proxy in PAIRS:
        fut_px  = last_trade_price(fut)
        prev    = previous_cash_close(cash, proxy)
        pct = (fut_px/prev - 1.0)*100.0 if (fut_px and prev and prev!=0) else None
        rows.append({"name":name,"fut_px":fut_px,"prev_close":prev,"pct":pct})
    return pd.DataFrame(rows)

def build_interpretation(df:pd.DataFrame)->str:
    valid = df.dropna(subset=["pct"]).copy()
    if valid.empty:
        return "Sin datos fiables ahora mismo (fuentes sin actualización reciente)."
    mean = valid["pct"].mean()
    best = valid.loc[valid["pct"].idxmax()]
    worst= valid.loc[valid["pct"].idxmin()]
    tone = "🟢 Sesgo alcista" if mean>0.2 else ("🔴 Sesgo bajista" if mean<-0.2 else "⚪ Sesgo neutral")
    spread = best["pct"]-worst["pct"]
    msg = [
        f"{tone} en futuros: media {mean:+.2f}%.",
        f"Mejor: {best['name']} {best['pct']:+.2f}% | Peor: {worst['name']} {worst['pct']:+.2f}%.",
        ("Rotación marcada." if abs(spread)>=0.6 else "Movimiento homogéneo."),
    ]
    if mean>0.6:   msg.append("Clima positivo; vigilar tomas de beneficio.")
    elif mean<-0.6:msg.append("Presión vendedora; ojo a soportes iniciales.")
    else:          msg.append("Apertura mixta; el flujo de noticias mandará.")
    return " ".join(msg)

def draw_image(df:pd.DataFrame, ts_local:datetime):
    from PIL import Image
    W,H=1200,630; img=Image.new("RGB",(W,H),BG_COLOR); d=ImageDraw.Draw(img)
    title_font=load_font(44,"bold"); label_font=load_font(28,"bold")
    pct_font=load_font(48,"bold"); small_font=load_font(22,"regular")
    d.text((40,30),"Premarket US — Futuros vs cierre cash",font=title_font,fill=TEXT_PRIMARY)
    d.text((40,90),f"Actualizado: {ts_local.strftime('%d %b %Y, %H:%M %Z')}",font=small_font,fill=TEXT_MUTED)
    pad=40; card_w=(W-pad*3)//2; card_h=(H-170-pad*3)//2
    positions=[(40,150),(40+card_w+pad,150),(40,150+card_h+pad),(40+card_w+pad,150+card_h+pad)]
    for idx,row in df.reset_index(drop=True).iterrows():
        if idx>3: break
        cx,cy=positions[idx]
        d.rounded_rectangle([cx,cy,cx+card_w,cy+card_h],radius=24,fill=CARD_BG)
        d.text((cx+30,cy+28),row["name"],font=label_font,fill=TEXT_PRIMARY)
        pct=row["pct"]; pct_str="—" if (pct is None or math.isnan(pct)) else f"{pct:+.2f}%"
        d.text((cx+30,cy+90),pct_str,font=pct_font,fill=pct_color(pct))
        fut=row["fut_px"]; prev=row["prev_close"]
        l2 = f"Futuro: {fut:.2f}" if (fut is not None) else "Futuro: —"
        l3 = f"Cierre cash: {prev:.2f}" if (prev is not None) else "Cierre cash: —"
        d.text((cx+30,cy+170),l2,font=small_font,fill=TEXT_MUTED)
        d.text((cx+30,cy+200),l3,font=small_font,fill=TEXT_MUTED)
    return img

def send_telegram_photo(img, caption:str):
    assert TELEGRAM_TOKEN and CHAT_ID, "Faltan INVESTX_TOKEN o CHAT_ID"
    url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    bio=io.BytesIO(); img.save(bio, format="PNG", optimize=True); bio.seek(0)
    files={"photo":("premarket.png",bio,"image/png")}
    data={"chat_id":CHAT_ID,"caption":caption,"parse_mode":"HTML"}
    r=requests.post(url,data=data,files=files,timeout=45)
    r.raise_for_status()
    return r.json()

def main():
    now_utc=datetime.now(timezone.utc)
    now_loc=now_utc.astimezone(LOCAL_TZ)

    # Guardia: solo 12:00 hora local (evita doble envío por cambio horario)
    if not (now_loc.hour==12 and now_loc.minute==0):
        print(f"[guard] Son las {now_loc.strftime('%H:%M %Z')} (local). No es 12:00, salgo sin enviar.")
        return

    try:
        df=fetch_snapshot()
        interpretation=build_interpretation(df)
        img=draw_image(df, now_loc)

        # Caption
        lines=["<b>Premarket USA</b>"]
        for _,r in df.iterrows():
            p="—" if (r["pct"] is None or math.isnan(r["pct"])) else f"{r['pct']:+.2f}%"
            lines.append(f"• <b>{r['name']}</b>: {p}")
        lines.append(""); lines.append(interpretation)
        caption="\n".join(lines)

        send_telegram_photo(img, caption)
        print("[ok] Mensaje enviado.")
    except Exception as e:
        # Log detallado para Actions
        print("[error] Fallo en ejecución:", str(e))
        traceback.print_exc()

if __name__=="__main__":
    main()
