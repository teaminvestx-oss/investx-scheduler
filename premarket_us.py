#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, io, math, traceback
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import requests, yfinance as yf, pandas as pd
from PIL import Image, ImageDraw, ImageFont

TELEGRAM_TOKEN = os.getenv("INVESTX_TOKEN","").strip()
CHAT_ID        = os.getenv("CHAT_ID","").strip()
LOCAL_TZ       = ZoneInfo(os.getenv("LOCAL_TZ","Europe/Madrid"))
EVENT_NAME     = os.getenv("GITHUB_EVENT_NAME","").strip()  # "schedule" o "workflow_dispatch"
FORCE_SEND     = os.getenv("FORCE_SEND","0") == "1"         # override manual opcional

PAIRS = [
    ("S&P 500",     "ES=F",  "^GSPC", None),
    ("Nasdaq 100",  "NQ=F",  "^NDX",  "QQQ"),
    ("Dow Jones",   "YM=F",  "^DJI",  None),
    ("Russell 2000","RTY=F", "^RUT",  "IWM"),
]

BG=(12,12,12); CARD=(22,22,26); TXT=(240,240,240); MUT=(180,180,180)
GREEN=(34,166,94); RED=(214,69,65); NEU=(160,160,160)

def font(size,bold=False):
    try: return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
    except: return ImageFont.load_default()

def col(p):
    if p is None or math.isnan(p): return NEU
    if p>=0.10: return GREEN
    if p<=-0.10: return RED
    return NEU

def last_px(tk):
    t=yf.Ticker(tk)
    for iv in ("1m","5m","15m"):
        try:
            h=t.history(period="2d", interval=iv, prepost=True, auto_adjust=False)
            s=h["Close"].dropna()
            if not s.empty and float(s.iloc[-1])>0: return float(s.iloc[-1])
        except: pass
    try:
        fi=getattr(t,"fast_info",{}); v=fi.get("last_price")
        if v is not None: return float(v)
    except: pass
    return None

def prev_close(tk, proxy=None):
    try:
        h=yf.Ticker(tk).history(period="10d", interval="1d", prepost=False, auto_adjust=False)
        s=h["Close"].dropna()
        if not s.empty: return float(s.iloc[-1])
    except: pass
    if proxy:
        try:
            h=yf.Ticker(proxy).history(period="10d", interval="1d", prepost=False, auto_adjust=False)
            s=h["Close"].dropna()
            if not s.empty: return float(s.iloc[-1])
        except: pass
    return None

def snapshot():
    rows=[]
    for name,fut,cash,proxy in PAIRS:
        f=last_px(fut); c=prev_close(cash,proxy)
        pct=(f/c-1)*100 if (f and c and c!=0) else None
        rows.append({"name":name,"fut_px":f,"prev_close":c,"pct":pct})
    return pd.DataFrame(rows)

def interp(df):
    v=df.dropna(subset=["pct"])
    if v.empty: return "Sin datos fiables ahora mismo (las fuentes no han actualizado)."
    mean=v["pct"].mean(); b=v.loc[v["pct"].idxmax()]; w=v.loc[v["pct"].idxmin()]
    tone="🟢 Sesgo alcista" if mean>0.2 else ("🔴 Sesgo bajista" if mean<-0.2 else "⚪ Sesgo neutral")
    spread=b["pct"]-w["pct"]
    msg=[f"{tone} en futuros: media {mean:+.2f}%.",
         f"Mejor: {b['name']} {b['pct']:+.2f}% | Peor: {w['name']} {w['pct']:+.2f}%.",
         "Rotación marcada." if abs(spread)>=0.6 else "Movimiento homogéneo."]
    if   mean>0.6:  msg.append("Clima positivo; vigilar tomas de beneficio.")
    elif mean<-0.6: msg.append("Presión vendedora; ojo a soportes.")
    else:           msg.append("Apertura mixta; mandará el flujo de noticias.")
    return " ".join(msg)

def render(df, ts):
    W,H=1200,630; im=Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(im)
    d.text((40,30),"Premarket US — Futuros vs cierre cash",font=font(44,True),fill=TXT)
    d.text((40,90),f"Actualizado: {ts.strftime('%d %b %Y, %H:%M %Z')}",font=font(22),fill=MUT)
    pad=40; cw=(W-pad*3)//2; ch=(H-170-pad*3)//2
    pos=[(40,150),(40+cw+pad,150),(40,150+ch+pad),(40+cw+pad,150+ch+pad)]
    for i,r in df.reset_index(drop=True).iterrows():
        if i>3: break
        x,y=pos[i]
        d.rounded_rectangle([x,y,x+cw,y+ch],radius=24,fill=CARD)
        d.text((x+30,y+28),r["name"],font=font(28,True),fill=TXT)
        p=r["pct"]; ptxt="—" if (p is None or math.isnan(p)) else f"{p:+.2f}%"
        d.text((x+30,y+90),ptxt,font=font(48,True),fill=col(p))
        f=r["fut_px"]; c=r["prev_close"]
        d.text((x+30,y+170), f"Futuro: {f:.2f}" if f is not None else "Futuro: —", font=font(22), fill=MUT)
        d.text((x+30,y+200), f"Cierre cash: {c:.2f}" if c is not None else "Cierre cash: —", font=font(22), fill=MUT)
    return im

def send(img, caption):
    assert TELEGRAM_TOKEN and CHAT_ID, "Faltan INVESTX_TOKEN o CHAT_ID"
    url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    bio=io.BytesIO(); img.save(bio,"PNG",optimize=True); bio.seek(0)
    r=requests.post(url, data={"chat_id":CHAT_ID,"caption":caption,"parse_mode":"HTML"},
                    files={"photo":("premarket.png",bio,"image/png")}, timeout=60)
    r.raise_for_status()

def main():
    now_utc=datetime.now(timezone.utc)
    now_local=now_utc.astimezone(LOCAL_TZ)

    # Ventana 11:55–12:15 local (o fuerza manual)
    in_window = (now_local.time() >= (now_local.replace(hour=11,minute=55,second=0,microsecond=0).time())
                 and now_local.time() <= (now_local.replace(hour=12,minute=15,second=0,microsecond=0).time()))
    if not (in_window or EVENT_NAME=="workflow_dispatch" or FORCE_SEND):
        print(f"[guard] {now_local.strftime('%H:%M %Z')} fuera de ventana 12:00 → no envío.")
        return

    try:
        df=snapshot()
        cap=["<b>Premarket USA</b>"]
        for _,r in df.iterrows():
            p="—" if (r['pct'] is None or math.isnan(r['pct'])) else f"{r['pct']:+.2f}%"
            cap.append(f"• <b>{r['name']}</b>: {p}")
        cap.append(""); cap.append(interp(df))
        img=render(df, now_local)
        send(img, "\n".join(cap))
        print("[ok] Enviado.")
    except Exception as e:
        print("[error]", e); traceback.print_exc()

if __name__=="__main__":
    main()

