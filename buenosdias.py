# buenosdias.py (greeting + premarket snapshot)
import os, argparse, datetime, logging
import math
import requests
from requests.adapters import HTTPAdapter, Retry
from dateutil import tz

# ---- NUEVO: yfinance para precios ----
import yfinance as yf

# ------------------------- Utilidades -------------------------
def get_env(name, *aliases) -> str:
    for k in (name, *aliases):
        v = os.environ.get(k)
        if v:
            return v
    alias_txt = (", alias: " + ", ".join(aliases)) if aliases else ""
    raise RuntimeError(f"Falta la variable de entorno: {name}{alias_txt}")

def make_session(timeout=20) -> requests.Session:
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
    bucket = dt.hour // 3
    base = (dt.toordinal() * 17 + dt.isocalendar().week * 7 + bucket) % n
    return base

def build_greeting(dt_local: datetime.datetime, *, override_text: str | None = None) -> str:
    if override_text:
        return override_text
    d = DIAS[dt_local.weekday()]
    idx = stable_index(dt_local, len(MENSAJES))
    return MENSAJES[idx].format(dia=d)

# ------------------------- Premarket -------------------------
DEFAULT_FUTURES = ["ES=F","NQ=F","YM=F"]  # S&P, Nasdaq 100, Dow
DEFAULT_WATCHLIST = ["SPY","QQQ","AAPL","MSFT","NVDA","AMZN","META","TSLA","BTC-USD","ETH-USD"]

def pct(a, b):
    if b in (None, 0) or a is None or math.isnan(b) or math.isnan(a):
        return None
    try:
        return (a - b) / b * 100.0
    except Exception:
        return None

def fmt_pct(x):
    if x is None:
        return "—"
    sign = "▲" if x >= 0 else "▼"
    return f"{sign}{abs(x):.2f}%"

def fmt_price(x):
    if x is None or math.isnan(x):
        return "—"
    # precios enteros si son grandes, 2 decimales si no
    return f"{x:.2f}" if x < 1000 else f"{x:,.0f}".replace(",", ".")

def fetch_snapshot(tickers: list[str]) -> dict:
    """Devuelve dict {ticker: (price, prev_close)} usando yfinance."""
    data = {}
    if not tickers:
        return data
    yfs = yf.Tickers(" ".join(tickers))
    for t, tk in yfs.tickers.items():
        try:
            fi = getattr(tk, "fast_info", {})
            # Algunos campos pueden faltar -> defensivo
            pre = fi.get("pre_market_price", None)
            post = fi.get("post_market_price", None)
            last = fi.get("last_price", None)
            prev = fi.get("previous_close", None)

            # prioriza premarket, si no hay toma last o post
            price = pre if pre not in (None, 0) else (last if last not in (None, 0) else post)
            data[t] = (price, prev)
        except Exception:
            data[t] = (None, None)
    return data

def build_premarket_block() -> str:
    # Lee envs o usa defaults
    futs = os.environ.get("FUTURES", "")
    wl = os.environ.get("WATCHLIST", "")

    fut_list = [s.strip() for s in futs.split(",") if s.strip()] or DEFAULT_FUTURES
    wl_list  = [s.strip() for s in wl.split(",") if s.strip()] or DEFAULT_WATCHLIST

    snap_fut = fetch_snapshot(fut_list)
    snap_wl  = fetch_snapshot(wl_list)

    # Cabeceras
    lines = []
    lines.append("<b>📈 Pre-Market (UTC) / Futuros</b>")
    for t in fut_list:
        price, prev = snap_fut.get(t, (None, None))
        lines.append(f"<code>{t:<6}</code>  {fmt_price(price):>8}  {fmt_pct(pct(price, prev))}")

    lines.append("")
    lines.append("<b>🏷️ Acciones & Cripto</b>")
    for t in wl_list:
        label = "Spot" if t.endswith("-USD") else "Pre"
        price, prev = snap_wl.get(t, (None, None))
        lines.append(f"<code>{t:<8}</code> {fmt_price(price):>10}  {fmt_pct(pct(price, prev))}  <i>{label}</i>")

    return "\n".join(lines)

# ------------------------- Main -------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tz", default="Europe/Madrid", help="Zona horaria local (p.ej. Europe/Madrid)")
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
        logging.info("Fin de semana: no se envía (use --allow-weekend o --force para enviar).")
        return

    greeting = build_greeting(now_local, override_text=args.message)
    pre_block = build_premarket_block()
    msg = f"{greeting}\n\n{pre_block}"

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
