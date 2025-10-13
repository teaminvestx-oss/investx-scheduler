# buenosdias.py – versión completa (saludo + snapshot pre-market con fallback robusto)

import os, argparse, datetime, logging, math
import requests
import pandas as pd
import yfinance as yf
from requests.adapters import HTTPAdapter, Retry
from dateutil import tz

# ------------------------- Utilidades -------------------------
def get_env(name, *aliases) -> str:
    """Lee variable de entorno con posibles alias."""
    for k in (name, *aliases):
        v = os.environ.get(k)
        if v:
            return v
    alias_txt = (", alias: " + ", ".join(aliases)) if aliases else ""
    raise RuntimeError(f"Falta la variable de entorno: {name}{alias_txt}")

def make_session(timeout=20) -> requests.Session:
    """Sesión con reintentos automáticos."""
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["POST"])
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.request_timeout = timeout
    return s

def telegram_send(text: str, *, token: str, chat_id: str, session: requests.Session, disable_preview=True) -> None:
    """Envía el mensaje a Telegram."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    r = session.post(url, data=payload, timeout=session.request_timeout)
    r.raise_for_status()

# ------------------------- Mensajes -------------------------
MENSAJES = [
    "🌞 Buenos días equipo, arrancamos este {dia} con InvestX. Mercados listos, foco y disciplina.",
    "📊 ¡Buenos días traders! Hoy es {dia}. En InvestX seguimos marcando niveles clave.",
    "☕ Café en mano y gráficos en pantalla: así empieza el {dia} en InvestX.",
    "🚀 Buenos días 👋. Recuerda: menos teoría, más acción. Filosofía InvestX.",
    "📈 Arrancamos este {dia} con setups claros. La oportunidad está ahí, InvestX te la acerca.",
    "🔔 Buenos días desde InvestX. Mercado abierto, cabeza fría y estrategia por delante.",
    "⚡ El trading nunca fue tan simple: buenos días y feliz {dia} con InvestX.",
    "💡 Buenos días. Hoy en InvestX toca constancia y paciencia, claves para ganar.",
]
DIAS = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]

def stable_index(dt: datetime.datetime, n: int) -> int:
    """Índice determinista (evita repeticiones)."""
    bucket = dt.hour // 3
    base = (dt.toordinal() * 17 + dt.isocalendar().week * 7 + bucket) % n
    return base

def build_greeting(dt_local: datetime.datetime, *, override_text: str | None = None) -> str:
    if override_text:
        return override_text
    d = DIAS[dt_local.weekday()]
    idx = stable_index(dt_local, len(MENSAJES))
    return MENSAJES[idx].format(dia=d)

# ------------------------- Premarket robusto -------------------------
def pct(a, b):
    if a is None or b is None or b == 0 or (isinstance(a,float) and math.isnan(a)) or (isinstance(b,float) and math.isnan(b)):
        return None
    return (a - b) / b * 100.0

def fmt_pct(x):
    if x is None:
        return "—"
    return ("▲" if x >= 0 else "▼") + f"{abs(x):.2f}%"

def fmt_price(x):
    if x is None or (isinstance(x,float) and math.isnan(x)):
        return "—"
    return f"{x:.2f}" if x < 1000 else f"{x:,.0f}".replace(",", ".")

def fetch_price_prev(t: str) -> tuple[float|None, float|None, str]:
    """Obtiene (precio, cierre previo, fuente) con varios fallbacks."""
    try:
        tk = yf.Ticker(t)
        src = []
        fi = getattr(tk, "fast_info", {}) or {}
        pre = fi.get("pre_market_price"); post = fi.get("post_market_price")
        last = fi.get("last_price"); prev = fi.get("previous_close")
        price = pre or last or post
        if price: src.append("fast_info")

        if price is None:
            h1m = tk.history(period="1d", interval="1m", prepost=True)
            if isinstance(h1m, pd.DataFrame) and not h1m.empty:
                price = float(h1m["Close"].dropna().iloc[-1])
                src.append("1m")

        if prev is None:
            h1d = tk.history(period="5d", interval="1d", prepost=True)
            if isinstance(h1d, pd.DataFrame) and len(h1d) >= 2:
                prev = float(h1d["Close"].dropna().iloc[-2])
                src.append("1d_prev")

        if price is None and t.endswith("-USD"):
            h1m2 = tk.history(period="1d", interval="1m")
            if isinstance(h1m2, pd.DataFrame) and not h1m2.empty:
                price = float(h1m2["Close"].dropna().iloc[-1])
                src.append("1m_crypto")

        return price, prev, "+".join(src) or "none"
    except Exception as e:
        import logging; logging.info(f"[yfinance] {t} error: {e}")
        return None, None, "error"

def fetch_many(tickers: list[str]) -> dict:
    out = {}
    for t in tickers:
        p, prev, source = fetch_price_prev(t)
        out[t] = (p, prev, source)
    return out

def build_premarket_block() -> str:
    futs = os.environ.get("FUTURES", "")
    wl   = os.environ.get("WATCHLIST", "")
    fut_list = [s.strip() for s in futs.split(",") if s.strip()] or ["ES=F","NQ=F","YM=F"]
    wl_list  = [s.strip() for s in wl.split(",") if s.strip()]  or ["SPY","QQQ","AAPL","MSFT","NVDA","AMZN","META","TSLA","BTC-USD","ETH-USD"]

    snap_fut = fetch_many(fut_list)
    snap_wl  = fetch_many(wl_list)

    lines = []
    lines.append("<b>📈 Pre-Market (UTC) / Futuros</b>")
    for t in fut_list:
        price, prev, _src = snap_fut.get(t, (None, None, ""))
        lines.append(f"<code>{t:<6}</code>  {fmt_price(price):>8}  {fmt_pct(pct(price, prev))}")

    lines.append("")
    lines.append("<b>🏷️ Acciones & Cripto</b>")
    for t in wl_list:
        label = "Spot" if t.endswith("-USD") else "Pre"
        price, prev, _src = snap_wl.get(t, (None, None, ""))
        lines.append(f"<code>{t:<8}</code> {fmt_price(price):>10}  {fmt_pct(pct(price, prev))}  <i>{label}</i>")

    return "\n".join(lines)

# ------------------------- Main -------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tz", default="Europe/Madrid", help="Zona horaria local")
    ap.add_argument("--allow-weekend", action="store_true", help="Permite enviar también Sábado y Domingo")
    ap.add_argument("--force", action="store_true", help="Fuerza envío aunque no sea L–V")
    ap.add_argument("--dry-run", action="store_true", help="No envía, solo imprime el mensaje")
    ap.add_argument("--message", help="Mensaje custom (HTML permitido)")
    ap.add_argument("--verbose", action="store_true", help="Log INFO")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s")

    now_utc = datetime.datetime.utcnow().replace(tzinfo=tz.UTC)
    local_tz = tz.gettz(args.tz)
    now_local = now_utc.astimezone(local_tz)
    wd = now_local.weekday()  # 0=lunes … 6=domingo

    if not args.force and not args.allow_weekend and wd > 4:
        logging.info("Fin de semana: no se envía (use --allow-weekend o --force).")
        return

    greeting = build_greeting(now_local, override_text=args.message)
    pre_block = build_premarket_block()
    msg = f"{greeting}\n\n{pre_block}"

    logging.info("✅ Pre-market snapshot generado correctamente.")

    if args.dry_run:
        print(msg)
        return

    token  = get_env("INVESTX_TOKEN", "TELEGRAM_BOT_TOKEN")
    chat_id = get_env("CHAT_ID", "TELEGRAM_CHAT_ID")

    session = make_session()
    telegram_send(msg, token=token, chat_id=chat_id, session=session)
    logging.info("Mensaje enviado correctamente.")

if __name__ == "__main__":
    main()

