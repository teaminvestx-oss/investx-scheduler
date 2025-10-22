import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Tuple
import requests

# === CONFIG ===
CHAT_ID    = os.getenv("CHAT_ID", "").strip()
BOT_TOKEN  = os.getenv("INVESTX_TOKEN", "").strip()
FMP_API_KEY = (os.getenv("FMP_API_KEY") or "").strip()
LOCAL_TZ   = ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Madrid"))

WATCHLIST_PRIORITY  = [s.strip().upper() for s in (os.getenv("WATCHLIST_PRIORITY") or "").replace(";", ",").split(",") if s.strip()]
WATCHLIST_SECONDARY = [s.strip().upper() for s in (os.getenv("WATCHLIST_SECONDARY") or "").replace(";", ",").split(",") if s.strip()]

H1 = int(os.getenv("EARNINGS_MORNING_FROM_H", "12"))
H2 = int(os.getenv("EARNINGS_MORNING_TO_H",   "14"))
HARD_FILTER = os.getenv("EARNINGS_HARD_FILTER", "0").lower() in {"1","true","yes","y"}
FORCE       = os.getenv("EARNINGS_FORCE",        "0").lower() in {"1","true","yes","y"}
DEBUG       = os.getenv("EARNINGS_DEBUG",        "0").lower() in {"1","true","yes","y"}

# probamos ambos paths (algunas API keys nuevas solo soportan /v3)
FMP_URLS = ["https://financialmodelingprep.com/stable/earnings-calendar"]

# === FUNCIONES AUXILIARES ===
def _post(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("[earnings] Telegram desactivado", flush=True)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=30).raise_for_status()


def _log(msg: str):
    print(f"[earnings] {msg}", flush=True)
    if DEBUG and BOT_TOKEN and CHAT_ID:
        try:
            _post(f"[earnings] {msg}")
        except Exception:
            pass


def is_run_window(now_local: datetime) -> bool:
    return (now_local.weekday() == 0) and (H1 <= now_local.hour <= H2)


def monday_to_friday_range(today_local: datetime) -> Tuple[str, str]:
    d = today_local.date()
    monday = d - timedelta(days=d.weekday())
    friday = monday + timedelta(days=4)
    return monday.isoformat(), friday.isoformat()


# === FETCH MEJORADO ===
def http_get_with_debug(url: str, params: dict) -> list:
    """GET con diagnóstico detallado (status + cuerpo). Devuelve [] si no es lista."""
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        js = r.json()
        return js if isinstance(js, list) else []
    except requests.HTTPError as e:
        try:
            body = r.text[:600]
        except Exception:
            body = "<sin cuerpo>"
        msg = f"ERROR FMP HTTP {getattr(r,'status_code','?')}: {body}"
        _log(msg)
        raise
    except Exception as e:
        msg = f"ERROR FMP GEN {type(e).__name__}"
        _log(msg)
        raise


def fetch_earnings(start: str, end: str) -> list:
    """Intenta v3 y luego v4; si ambos fallan, relanza para que el caller avise."""
    params = {"from": start, "to": end, "apikey": FMP_API_KEY}
    last_err = None
    for url in FMP_URLS:
        try:
            _log(f"GET {url} {params}")
            return http_get_with_debug(url, params)
        except Exception as e:
            last_err = e
            continue
    raise last_err


def classify_time(t: str | None):
    if not t: return ("TBD","🕒")
    t=t.lower()
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
    u=(sym or "").upper()
    if u in pri: return "🟨"
    if u in sec: return "🟦"
    return "▫️"


def shorten(name: str, n: int=24) -> str:
    return "" if not name else (name if len(name)<=n else name[:n-1]+"…")


def build_message(grouped: Dict[str, List[Dict]], pri: list, sec: list, start: str, end: str) -> str:
    lines=[]
    lines.append("📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>")
    lines.append(f"Semana: {start} → {end}\n")
    if pri: lines.append("🟨 <b>Prioridad alta:</b> " + ", ".join(pri))
    if sec: lines.append("🟦 <b>Secundaria:</b> "   + ", ".join(sec))
    if pri or sec: lines.append("")
    lines.append("<b>Agenda por día</b>:")
    DIAS_ES=["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
    for d, items in grouped.items():
        dt = datetime.fromisoformat(d); lines.append(f"\n<u>{DIAS_ES[dt.weekday()]} {d}</u>")
        pr_items=[it for it in items if (it.get("symbol","").upper() in pri or it.get("symbol","").upper() in sec)]
        other=[it for it in items if it not in pr_items]
        def render(it):
            sym=(it.get("symbol") or "").upper()
            name=shorten(it.get("company") or it.get("name") or sym)
            sess,emj=classify_time(it.get("time"))
            e=it.get("epsEstimated") or it.get("epsEstimate")
            exp=f" | expEPS: {e:.2f}" if isinstance(e,(int,float)) else ""
            return f"{badge(sym,pri,sec)} {emj} <b>{sym}</b> — {name} ({sess}{exp})"
        for it in pr_items: lines.append("• " + render(it))
        if not HARD_FILTER:
            for it in other[:8]: lines.append("• " + render(it))
            if len(other)>8: lines.append(f"… (+{len(other)-8} más)")
    lines.append("\n🧠 Seguimiento centrado en 🟨/🟦 (planes activos).")
    return "\n".join(lines)


def no_relevant_msg(start: str, end: str) -> str:
    return ("📅 <b>EARNINGS WEEKLY PREVIEW | InvestX</b>\n"
            f"Semana: {start} → {end}\n\n"
            "⚠️ <b>No hay earnings relevantes</b> de tu watchlist (🟨/🟦) esta semana.\n"
            "Actualizaremos si surge algún cambio.")


# === MAIN ===
def main():
    _log(f"start | FORCE={FORCE} DEBUG={DEBUG} HARD={HARD_FILTER}")

    if not FMP_API_KEY:
        _log("FALTA FMP_API_KEY"); return
    if not (BOT_TOKEN and CHAT_ID):
        _log("FALTA INVESTX_TOKEN o CHAT_ID"); return

    now_local = datetime.now(LOCAL_TZ)
    _log(f"now={now_local.isoformat()} weekday={now_local.weekday()} hour={now_local.hour}")

    if not (FORCE or is_run_window(now_local)):
        _log("fuera de ventana (no lunes o fuera de horas)"); return

    start, end = monday_to_friday_range(now_local)
    _log(f"range {start}->{end}")

    try:
        data = fetch_earnings(start, end)
        _log(f"fmp rows={len(data)}")
    except Exception as e:
        _log(f"ERROR FMP: {type(e).__name__}")
        return

    if HARD_FILTER:
        wl = set(WATCHLIST_PRIORITY + WATCHLIST_SECONDARY)
        before = len(data)
        data = [d for d in data if (d.get('symbol','').upper() in wl)]
        _log(f"hard filter {before}->{len(data)}")

    grouped = group_by_day(data)
    _log(f"días con eventos={len(grouped)}")

    pri, sec = set(WATCHLIST_PRIORITY), set(WATCHLIST_SECONDARY)
    relevant = any((it.get("symbol","").upper() in pri or it.get("symbol","").upper() in sec)
                   for items in grouped.values() for it in items)

    if not grouped or not relevant:
        _log("no relevantes -> enviar aviso")
        try:
            _post(no_relevant_msg(start, end)); _log("aviso OK")
        except Exception as e:
            _log(f"ERROR Telegram aviso: {type(e).__name__}")
        return

    msg = build_message(grouped, WATCHLIST_PRIORITY, WATCHLIST_SECONDARY, start, end)
    try:
        _post(msg); _log("preview OK")
    except Exception as e:
        _log(f"ERROR Telegram preview: {type(e).__name__}")


if __name__ == "__main__":
    main()
