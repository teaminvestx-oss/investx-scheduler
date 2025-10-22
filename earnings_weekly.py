# earnings_weekly.py — InvestX (formato limpio)
# Publica un único mensaje con la agenda de earnings por día.
# - Fuente: FMP /stable/earnings-calendar
# - Lunes en ventana [EARNINGS_MORNING_FROM_H..TO_H] (hora LOCAL_TZ)
# - EARNINGS_FORCE=1 => ignora ventana (para pruebas)
# - EARNINGS_HARD_FILTER=1 => limita a WATCHLIST_* (si no, publica todo)

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Tuple
import requests

# ========= ENV =========
CHAT_ID    = os.getenv("CHAT_ID", "").strip()
BOT_TOKEN  = os.getenv("INVESTX_TOKEN", "").strip()

# Sanitizamos la key por si hubiera espacios o saltos ocultos
_raw_key = (os.getenv("FMP_API_KEY") or "").strip()
FMP_API_KEY = "".join(ch for ch in _raw_key if ch.isalnum())

FMP_URL   = "https://financialmodelingprep.com/stable/earnings-calendar"

LOCAL_TZ  = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))

WATCHLIST_PRIORITY  = [s.strip().upper() for s in (os.getenv("WATCHLIST_PRIORITY") or "").replace(";", ",").split(",") if s.strip()]
WATCHLIST_SECONDARY = [s.strip().upper() for s in (os.getenv("WATCHLIST_SECONDARY") or "").replace(";", ",").split(",") if s.strip()]

H1 = int(os.getenv("EARNINGS_MORNING_FROM_H", "12"))
H2 = int(os.getenv("EARNINGS_MORNING_TO_H",   "14"))  # inclusivo

HARD_FILTER = os.getenv("EARNINGS_HARD_FILTER", "0").lower() in {"1","true","yes","y"}
FORCE       = os.getenv("EARNINGS_FORCE",        "0").lower() in {"1","true","yes","y"}

# ========= UTIL =========
def _post(text: str):
    if not (BOT_TOKEN and CHAT_ID): return
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30
    ).raise_for_status()

def is_run_window(now_local: datetime) -> bool:
    return (now_local.weekday() == 0) and (H1 <= now_local.hour <= H2)  # 0 = lunes

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
        # si la key fuese inválida o sin permisos, no publicamos nada
        return []
    data = r.json()
    if not isinstance(data, list):
        return []
    # normalizar campos mínimos
    out = []
    for d in data:
        out.append({
            "symbol": (d.get("symbol") or "").upper(),
            "company": d.get("company") or d.get("name") or "",
            "date": d.get("date") or d.get("dateCalendar") or "",
            "time": (d.get("time") or d.get("hour") or "").lower(),  # bmo/amc/tbd
            "epsEstimated": d.get("epsEstimated") or d.get("epsEstimate"),
        })
    return out

# ========= RENDER =========
def group_by_day(rows: List[Dict]) -> Dict[str, List[Dict]]:
    g: Dict[str, List[Dict]] = {}
    for r in rows:
        d = r.get("date") or ""
        if not d:
            continue
        g.setdefault(d, []).append(r)
    for d, lst in g.items():
        lst.sort(key=lambda x: (str(x.get("time") or "tbd"), (x.get("symbol") or "").upper()))
    # ordenar por fecha asc
    return dict(sorted(g.items(), key=lambda kv: kv[0]))

def build_message_clean(grouped: Dict[str, List[Dict]], start: str, end: str) -> str:
    lines = []
    lines.append("📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>")
    lines.append(f"Semana: {start} → {end}\n")
    lines.append("<b>Agenda por día</b>:")

    DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

    for d, items in grouped.items():
        dt = datetime.fromisoformat(d)
        lines.append(f"\n<u>{DIAS_ES[dt.weekday()]} {d}</u>")
        # ordenar por ticker para consistencia
        items.sort(key=lambda it: (it.get("symbol") or ""))
        for it in items:
            sym  = (it.get("symbol") or "").upper()
            name = it.get("company") or it.get("name") or sym
            e    = it.get("epsEstimated")
            exp  = f" | expEPS: {e:.2f}" if isinstance(e, (int, float)) else ""
            lines.append(f"• <b>{sym}</b> — {name}{exp}")
    return "\n".join(lines)

def no_relevant_msg(start: str, end: str) -> str:
    return ("📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>\n"
            f"Semana: {start} → {end}\n\n"
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

    _post(build_message_clean(grouped, start, end))

if __name__ == "__main__":
    main()
