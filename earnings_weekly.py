# Earnings Weekly Preview – usa FMP y publica en Telegram.
# Ejecuta SOLO los lunes en la franja local (por defecto 12–14h ≈ 13h).
# Si no hay empresas relevantes (WATCHLIST_*), publica aviso.

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Tuple
import requests

# ========= Config =========
CHAT_ID        = os.getenv("CHAT_ID")
BOT_TOKEN      = os.getenv("INVESTX_TOKEN")
FMP_API_KEY    = (os.getenv("FMP_API_KEY") or "").strip()
LOCAL_TZ       = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))

WATCHLIST_PRIORITY  = [s.strip().upper() for s in (os.getenv("WATCHLIST_PRIORITY") or "").replace(";", ",").split(",") if s.strip()]
WATCHLIST_SECONDARY = [s.strip().upper() for s in (os.getenv("WATCHLIST_SECONDARY") or "").replace(";", ",").split(",") if s.strip()]

# Solo lunes y ventana ~13h local (cámbialo con EARNINGS_MORNING_FROM_H/TO_H)
MORNING_WINDOW_LOCAL_H1 = int(os.getenv("EARNINGS_MORNING_FROM_H", "12"))
MORNING_WINDOW_LOCAL_H2 = int(os.getenv("EARNINGS_MORNING_TO_H",   "14"))  # inclusivo
FMP_URL = "https://financialmodelingprep.com/api/v3/earning_calendar"

def is_run_window(now_local: datetime) -> bool:
    # 0 = lunes
    return now_local.weekday() == 0 and (MORNING_WINDOW_LOCAL_H1 <= now_local.hour <= MORNING_WINDOW_LOCAL_H2)

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

def classify_time(t: str | None) -> Tuple[str, str]:
    if not t: return ("TBD", "🕒")
    t = t.lower()
    if "bmo" in t or "before" in t: return ("Pre-Market", "☀️")
    if "amc" in t or "after" in t:  return ("After-Close", "🌙")
    return ("TBD", "🕒")

def group_by_day(rows: List[Dict]) -> Dict[str, List[Dict]]:
    g: Dict[str, List[Dict]] = {}
    for r in rows:
        d = r.get("date") or r.get("dateCalendar") or ""
        if not d: continue
        g.setdefault(d, []).append(r)
    for d, lst in g.items():
        lst.sort(key=lambda x: (str(x.get("time") or "tbd"), x.get("symbol") or ""))
    return dict(sorted(g.items(), key=lambda kv: kv[0]))

def post_telegram_html(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }, timeout=30)
    r.raise_for_status()

def badge(sym: str, pri: List[str], sec: List[str]) -> str:
    u = (sym or "").upper()
    if u in pri: return "🟨"
    if u in sec: return "🟦"
    return "▫️"

def shorten(name: str, n: int = 24) -> str:
    return "" if not name else (name if len(name) <= n else name[:n-1] + "…")

def build_message(grouped: Dict[str, List[Dict]], pri: List[str], sec: List[str], start: str, end: str) -> str:
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
            exp = f" | expEPS: {e:.2f}" if isinstance(e, (int,float)) else ""
            return f"{badge(sym, pri, sec)} {emj} <b>{sym}</b> — {name} ({sess}{exp})"

        for it in pr_items:     lines.append("• " + render(it))
        for it in other[:8]:    lines.append("• " + render(it))
        if len(other) > 8:      lines.append(f"… (+{len(other)-8} más)")

    lines.append("\n🧠 Seguimiento centrado en 🟨/🟦 (planes activos).")
    return "\n".join(lines)

def build_no_relevant_message(start: str, end: str) -> str:
    return (
        "📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>\n"
        f"Semana: {start} → {end}\n\n"
        "⚠️ <b>No hay earnings relevantes</b> de tu watchlist (🟨/🟦) esta semana.\n"
        "Actualizaremos si surge algún cambio."
    )

def main():
    if not (BOT_TOKEN and CHAT_ID and FMP_API_KEY):
        raise SystemExit("Faltan vars: INVESTX_TOKEN, CHAT_ID, FMP_API_KEY")

    now_local = datetime.now(LOCAL_TZ)
    if not is_run_window(now_local):
        return  # silencioso fuera de ventana

    start, end = monday_to_friday_range(now_local)
    data = fetch_earnings(start, end)
    grouped = group_by_day(data)

    pri = set(WATCHLIST_PRIORITY); sec = set(WATCHLIST_SECONDARY)
    relevant = any(
        (it.get("symbol","").upper() in pri or it.get("symbol","").upper() in sec)
        for items in grouped.values() for it in items
    )

    if not grouped or not relevant:
        post_telegram_html(build_no_relevant_message(start, end))
    else:
        post_telegram_html(build_message(grouped, WATCHLIST_PRIORITY, WATCHLIST_SECONDARY, start, end))

if __name__ == "__main__":
    main()
