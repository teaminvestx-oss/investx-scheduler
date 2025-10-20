# Earnings Weekly Preview – InvestX (raíz del repo)
# - FMP v4 (/earning_calendar)
# - Publica SOLO los lunes en la ventana local [EARNINGS_MORNING_FROM_H..TO_H]
# - Hard Filter opcional: solo WATCHLIST_* (🟨/🟦)
# - FORCE/DEBUG para pruebas y diagnóstico
# - Si no hay tickers relevantes, envía aviso

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Tuple
import requests

# ========= ENV =========
CHAT_ID    = os.getenv("CHAT_ID", "").strip()
BOT_TOKEN  = os.getenv("INVESTX_TOKEN", "").strip()
FMP_API_KEY = (os.getenv("FMP_API_KEY") or "").strip()
LOCAL_TZ   = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))

WATCHLIST_PRIORITY  = [s.strip().upper() for s in (os.getenv("WATCHLIST_PRIORITY") or "").replace(";", ",").split(",") if s.strip()]
WATCHLIST_SECONDARY = [s.strip().upper() for s in (os.getenv("WATCHLIST_SECONDARY") or "").replace(";", ",").split(",") if s.strip()]

# Ventana local (por defecto 12–14; para EXACTO 13h: 13 y 13)
H1 = int(os.getenv("EARNINGS_MORNING_FROM_H", "12"))
H2 = int(os.getenv("EARNINGS_MORNING_TO_H",   "14"))  # inclusivo

# Modos
HARD_FILTER = os.getenv("EARNINGS_HARD_FILTER", "0").lower() in {"1","true","yes","y"}
FORCE       = os.getenv("EARNINGS_FORCE",        "0").lower() in {"1","true","yes","y"}
DEBUG       = os.getenv("EARNINGS_DEBUG",        "0").lower() in {"1","true","yes","y"}

# Endpoint FMP (v4)
FMP_URL = "https://financialmodelingprep.com/api/v4/earning_calendar"

# ========= Helpers =========
def post_telegram_html(text: str):
    if not (BOT_TOKEN and CHAT_ID):
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=30).raise_for_status()
    except Exception:
        pass

def is_run_window(now_local: datetime) -> bool:
    # 0 = lunes
    return (now_local.weekday() == 0) and (H1 <= now_local.hour <= H2)

def monday_to_friday_range(today_local: datetime) -> Tuple[str, str]:
    d = today_local.date()
    monday = d - timedelta(days=d.weekday())
    friday = monday + timedelta(days=4)
    return monday.isoformat(), friday.isoformat()

def fetch_earnings(start: str, end: str) -> List[Dict]:
    r = requests.get(FMP_URL, params={"from": start, "to": end, "apikey": FMP_API_KEY}, timeout=30)
    r.raise_for_status()
    js = r.json()
    return js if isinstance(js, list) else []

def classify_time(t: str | None):
    if not t: return ("TBD","🕒")
    t = t.lower()
    if "bmo" in t or "before" in t: return ("Pre-Market","☀️")
    if "amc" in t or "after" in t:  return ("After-Close","🌙")
    return ("TBD","🕒")

def group_by_day(rows: List[Dict]) -> Dict[str, List[Dict]]:
    g: Dict[str, List[Dict]] = {}
    for r in rows:
        d = r.get("date") or r.get("dateCalendar") or ""
        if not d: continue
        g.setdefault(d, []).append(r)
    for d, lst in g.items():
        lst.sort(key=lambda x: (str(x.get("time") or "tbd"), (x.get("symbol") or "").upper()))
    return dict(sorted(g.items(), key=lambda kv: kv[0]))

def badge(sym: str, pri: list, sec: list) -> str:
    u = (sym or "").upper()
    if u in pri: return "🟨"
    if u in sec: return "🟦"
    return "▫️"

def shorten(name: str, n: int = 24) -> str:
    if not name: return ""
    return name if len(name) <= n else name[:n-1] + "…"

def build_message(grouped: Dict[str, List[Dict]], pri: list, sec: list, start: str, end: str) -> str:
    lines = []
    lines.append("📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>")
    lines.append(f"Semana: {start} → {end}\n")
    if pri: lines.append("🟨 <b>Prioridad alta:</b> " + ", ".join(pri))
    if sec: lines.append("🟦 <b>Secundaria:</b> "   + ", ".join(sec))
    if pri or sec: lines.append("")
    lines.append("<b>Agenda por día</b>:")

    DIAS_ES = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
    for d, items in grouped.items():
        dt = datetime.fromisoformat(d)
        lines.append(f"\n<u>{DIAS_ES[dt.weekday()]} {d}</u>")

        pr_items = [it for it in items if (it.get("symbol","").upper() in pri or it.get("symbol","").upper() in sec)]
        other    = [it for it in items if it not in pr_items]

        def render(it):
            sym = (it.get("symbol") or "").upper()
            name = shorten(it.get("company") or it.get("name") or sym)
            sess, emj = classify_time(it.get("time"))
            e = it.get("epsEstimated") or it.get("epsEstimate")
            exp = f" | expEPS: {e:.2f}" if isinstance(e,(int,float)) else ""
            return f"{badge(sym,pri,sec)} {emj} <b>{sym}</b> — {name} ({sess}{exp})"

        for it in pr_items:
            lines.append("• " + render(it))
        if not HARD_FILTER:
            for it in other[:8]:
                lines.append("• " + render(it))
            if len(other) > 8:
                lines.append(f"… (+{len(other)-8} más)")

    lines.append("\n🧠 Seguimiento centrado en 🟨/🟦 (planes activos).")
    return "\n".join(lines)

def no_relevant_msg(start: str, end: str) -> str:
    return ("📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>\n"
            f"Semana: {start} → {end}\n\n"
            "⚠️ <b>No hay earnings relevantes</b> de tu watchlist (🟨/🟦) esta semana.\n"
            "Actualizaremos si surge algún cambio.")

# ========= Main =========
def main():
    try:
        if not FMP_API_KEY:
            if DEBUG: post_telegram_html("⚠️ Earnings: falta FMP_API_KEY")
            return

        now_local = datetime.now(LOCAL_TZ)
        if not (FORCE or is_run_window(now_local)):
            if DEBUG: post_telegram_html(f"ℹ️ Earnings: fuera de ventana (weekday={now_local.weekday()}, hora={now_local.hour})")
            return

        start, end = monday_to_friday_range(now_local)

        try:
            data = fetch_earnings(start, end)
        except Exception as e:
            if DEBUG: post_telegram_html(f"❌ Earnings: error API FMP ({type(e).__name__})")
            return

        if HARD_FILTER:
            wl = set(WATCHLIST_PRIORITY + WATCHLIST_SECONDARY)
            data = [d for d in data if (d.get('symbol','').upper() in wl)]

        grouped = group_by_day(data)
        pri, sec = set(WATCHLIST_PRIORITY), set(WATCHLIST_SECONDARY)
        relevant = any((it.get("symbol","").upper() in pri or it.get("symbol","").upper() in sec)
                       for items in grouped.values() for it in items)

        if not grouped or not relevant:
            post_telegram_html(no_relevant_msg(start, end))
            return

        post_telegram_html(build_message(grouped, WATCHLIST_PRIORITY, WATCHLIST_SECONDARY, start, end))

    except Exception as e:
        if DEBUG: post_telegram_html(f"❌ Earnings: fallo inesperado ({type(e).__name__})")

if __name__ == "__main__":
    main()
