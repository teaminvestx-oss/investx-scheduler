# earnings_weekly.py — InvestX (mensaje limpio)
# Fuente: FMP /stable/earnings-calendar
# Publica SOLO los lunes dentro de [EARNINGS_MORNING_FROM_H..TO_H] (hora LOCAL_TZ)
# EARNINGS_FORCE=1 -> ignora la ventana (para pruebas)
# EARNINGS_HARD_FILTER=1 -> limita a WATCHLIST_*; si no, muestra toda la agenda

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Tuple
import requests

# ========= ENV =========
CHAT_ID    = os.getenv("CHAT_ID", "").strip()
BOT_TOKEN  = os.getenv("INVESTX_TOKEN", "").strip()

# Sanitizar posible whitespace / caracteres raros en la API key
_raw_key = (os.getenv("FMP_API_KEY") or "").strip()
FMP_API_KEY = "".join(ch for ch in _raw_key if ch.isalnum())
FMP_URL     = "https://financialmodelingprep.com/stable/earnings-calendar"

LOCAL_TZ  = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))

WATCHLIST_PRIORITY  = [s.strip().upper() for s in (os.getenv("WATCHLIST_PRIORITY") or "").replace(";", ",").split(",") if s.strip()]
WATCHLIST_SECONDARY = [s.strip().upper() for s in (os.getenv("WATCHLIST_SECONDARY") or "").replace(";", ",").split(",") if s.strip()]

H1 = int(os.getenv("EARNINGS_MORNING_FROM_H", "12"))
H2 = int(os.getenv("EARNINGS_MORNING_TO_H",   "14"))  # inclusivo

HARD_FILTER = os.getenv("EARNINGS_HARD_FILTER", "0").lower() in {"1","true","yes","y"}
FORCE       = os.getenv("EARNINGS_FORCE",        "0").lower() in {"1","true","yes","y"}

# ========= TELEGRAM =========
def _post(text: str):
    if not (BOT_TOKEN and CHAT_ID):
        return
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30
    ).raise_for_status()

# ========= TIEMPO / RANGO =========
def is_run_window(now_local: datetime) -> bool:
    # 0 = Lunes
    return (now_local.weekday() == 0) and (H1 <= now_local.hour <= H2)

def monday_to_friday_range(today_local: datetime) -> Tuple[str, str]:
    d = today_local.date()
    monday = d - timedelta(days=d.weekday())
    friday = monday + timedelta(days=4)
    return monday.isoformat(), friday.isoformat()

# ========= FETCH =========
def fetch_earnings(start: str, end: str) -> List[Dict]:
    if not FMP_API_KEY:
        return []
    params = {"from": start, "to": end, "apikey": FMP_API_KEY}
    r = requests.get(FMP_URL, params=params, timeout=30)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        return []  # si 401/403/etc., no publicamos nada
    data = r.json()
    if not isinstance(data, list):
        return []
    # Normalizar campos
    out = []
    for d in data:
        sym = (d.get("symbol") or "").upper()
        # intentar distintas claves de nombre según endpoint
        name = (
            d.get("company")
            or d.get("companyName")
            or d.get("name")
            or sym
        )
        out.append({
            "symbol": sym,
            "company": name,
            "date": d.get("date") or d.get("dateCalendar") or "",
            "time": (d.get("time") or d.get("hour") or "").lower(),  # bmo/amc/tbd
        })
    return out

# ========= FORMATO =========
def classify_session(t: str | None) -> str:
    """Convierte el 'time' en etiqueta legible con emoji."""
    if not t:
        return "⏰ TBD"
    t = t.strip().lower()
    # variantes conocidas
    if t in {"bmo", "pre", "premarket", "pre-market", "before"}:
        return "🕖 Pre-Market"
    if t in {"amc", "post", "postmarket", "after-market", "after"}:
        return "🌙 After-Market"
    return "⏰ TBD"

def group_by_day(rows: List[Dict]) -> Dict[str, List[Dict]]:
    g: Dict[str, List[Dict]] = {}
    for r in rows:
        d = r.get("date") or ""
        if not d:
            continue
        g.setdefault(d, []).append(r)
    # ordenar cada día por símbolo; luego por fecha ascendente
    for d, lst in g.items():
        lst.sort(key=lambda x: (x.get("symbol") or ""))
    return dict(sorted(g.items(), key=lambda kv: kv[0]))

def build_message(grouped: Dict[str, List[Dict]], start: str, end: str) -> str:
    lines = []
    # Encabezado (solo título + semana)
    lines.append("📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>")
    lines.append(f"Semana 📆 {start} → {end}\n")
    lines.append("<b>Agenda por día</b>:")

    DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    for d, items in grouped.items():
        dt = datetime.fromisoformat(d)
        lines.append(f"\n<u>{DIAS_ES[dt.weekday()]} {d}</u>")
        for it in items:
            sym  = (it.get("symbol") or "").upper()
            name = it.get("company") or sym
            sess = classify_session(it.get("time"))
            lines.append(f"• <b>{sym}</b> — <i>{name}</i>  {sess}")
    return "\n".join(lines)

def no_relevant_msg(start: str, end: str) -> str:
    return ("📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>\n"
            f"Semana 📆 {start} → {end}\n\n"
            "⚠️ No hay resultados relevantes esta semana.")

# ========= MAIN =========
def main():
    if not (BOT_TOKEN and CHAT_ID):
        return

    now_local = datetime.now(LOCAL_TZ)
    if not (FORCE or is_run_window(now_local)):
        return

    start, end = monday_to_friday_range(now_local)
    data = fetch_earnings(start, end)

    # Filtrado por watchlist si se desea
    if HARD_FILTER:
        wl = set(WATCHLIST_PRIORITY + WATCHLIST_SECONDARY)
        data = [d for d in data if (d.get("symbol", "").upper() in wl)]

    grouped = group_by_day(data)
    if not grouped:
        _post(no_relevant_msg(start, end))
        return

    _post(build_message(grouped, start, end))

if __name__ == "__main__":
    main()
