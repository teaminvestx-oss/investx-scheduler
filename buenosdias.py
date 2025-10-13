# buenosdias.py – InvestX (saludo + snapshot pre-market + macro + sesgo)
# Corrige % de acciones en premarket (sin usar regular_market_change_percent)
# Requiere: requests, python-dateutil, yfinance, pandas, numpy, lxml

import os, argparse, datetime, logging, math
import requests
import pandas as pd
import yfinance as yf
from requests.adapters import HTTPAdapter, Retry
from dateutil import tz

VERSION = "InvestX buenosdias v1.4"

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
    retries = Retry(total=3, backoff_factor=0.8,
                    status_forcelist=(429,500,502,503,504),
                    allowed_methods=frozenset(["POST"]))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.request_timeout = timeout
    return s

def telegram_send(text: str, *, token: str, chat_id: str, session: requests.Session, disable_preview=True) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": disable_preview}
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
    return (dt.toordinal()*17 + dt.isocalendar().week*7 + bucket) % n

def build_greeting(dt_local: datetime.datetime, *, override_text: str | None = None) -> str:
    if override_text: return override_text
    d = DIAS[dt_local.weekday()]
    return MENSAJES[stable_index(dt_local, len(MENSAJES))].format(dia=d)

# ------------------------- Helpers precios -------------------------
def pct(a, b):
    if a is None or b is None or b == 0 or (isinstance(a,float) and math.isnan(a)) or (isinstance(b,float) and math.isnan(b)):
        return None
    return (a - b) / b * 100.0

def fmt_pct(x):
    if x is None: return "—"
    val = f"{abs(x):.2f}%"
    if x > 0:  return f"🟢▲{val}"
    if x < 0:  return f"🔴▼{val}"
    return "⚪0.00%"

def fmt_price(x, suffix: str = ""):
    if x is None or (isinstance(x,float) and math.isnan(x)): return "—"
    s = f"{x:.2f}" if abs(x) < 1000 else f"{x:,.0f}".replace(",", ".")
    return s + suffix

def fetch_quote(ticker: str, prefer_premarket: bool) -> tuple[float|None, float|None, float|None, str]:
    """
    Devuelve (price, previous_close, change_percent, source).
    - prefer_premarket=True (acciones/ETFs antes de la apertura): usa pre_market_change_percent si existe; si no, calcula (precio_pre vs prev_close). No usa regular_market_change_percent.
    - prefer_premarket=False (futuros, macro, cripto): calcula con (price vs prev).
    """
    try:
        tk = yf.Ticker(ticker)
        src = []

        fi = getattr(tk, "fast_info", {}) or {}
        pre   = fi.get("pre_market_price")
        post  = fi.get("post_market_price")
        last  = fi.get("last_price")
        prev  = fi.get("previous_close")
        pre_cp = fi.get("pre_market_change_percent")  # 0.0123 => 1.23%

        # precio
        price = pre if pre not in (None, 0) else (last if last not in (None, 0) else post)
        if price is not None: src.append("fast_info")

        # fallbacks de precio
        if price is None:
            h1m = tk.history(period="1d", interval="1m", prepost=True)
            if isinstance(h1m, pd.DataFrame) and not h1m.empty:
                price = float(h1m["Close"].dropna().iloc[-1]); src.append("1m")
        if prev is None:
            h1d = tk.history(period="5d", interval="1d", prepost=True)
            if isinstance(h1d, pd.DataFrame) and len(h1d.dropna()) >= 2:
                prev = float(h1d["Close"].dropna().iloc[-2]); src.append("1d_prev")
        if price is None and ticker.endswith("-USD"):
            h1m2 = tk.history(period="1d", interval="1m")
            if isinstance(h1m2, pd.DataFrame) and not h1m2.empty:
                price = float(h1m2["Close"].dropna().iloc[-1]); src.append("1m_crypto")

        # % cambio
        change_pct = None
        if ticker.endswith("-USD"):
            change_pct = pct(price, prev) if (price is not None and prev is not None) else None
        elif prefer_premarket:
            if pre_cp is not None:
                change_pct = float(pre_cp) * 100.0; src.append("pre_cp")
            else:
                change_pct = pct(price, prev) if (price is not None and prev is not None) else None; src.append("manual_pre")
        else:
            change_pct = pct(price, prev) if (price is not None and prev is not None) else None; src.append("manual")

        return price, prev, change_pct, "+".join(src) or "none"
    except Exception as e:
        logging.info(f"[yfinance] {ticker} error: {e}")
        return None, None, None, "error"

def fetch_many(tickers: list[str], *, prefer_premarket: bool) -> dict[str, tuple[float|None, float|None, float|None, str]]:
    return {t: fetch_quote(t, prefer_premarket=prefer_premarket) for t in tickers}

# ------------------------- Bloques -------------------------
DEFAULT_FUTURES   = ["ES=F","NQ=F","YM=F"]                              # E-mini S&P / Nasdaq / Dow
DEFAULT_WATCHLIST = ["SPY","QQQ","AAPL","MSFT","NVDA","AMZN","META","TSLA","BTC-USD","ETH-USD"]
DEFAULT_MACRO     = ["^VIX","^TNX","DX-Y.NYB","BZ=F"]                   # VIX / US10Y / DXY / Brent

def macro_suffix(t: str) -> str:
    return "%" if t == "^TNX" else ""

def build_premarket_block() -> tuple[str, dict]:
    futs = [s.strip() for s in os.environ.get("FUTURES","").split(",") if s.strip()] or DEFAULT_FUTURES
    wl   = [s.strip() for s in os.environ.get("WATCHLIST","").split(",") if s.strip()] or DEFAULT_WATCHLIST

    snap_fut = fetch_many(futs, prefer_premarket=False)   # futuros: manual vs prev
    snap_wl  = fetch_many(wl,   prefer_premarket=True)    # acciones/ETF: prioriza premarket

    lines = []
    lines.append("<b>📈 Pre-Market (UTC) / Futuros</b>")
    for t in futs:
        price, _prev, chg, _ = snap_fut.get(t, (None, None, None, ""))
        lines.append(f"<code>{t:<6}</code>  {fmt_price(price):>8}  {fmt_pct(chg)}")

    lines.append("")
    lines.append("<b>🏷️ Acciones & Cripto</b>")
    for t in wl:
        label = "Spot" if t.endswith("-USD") else "Pre"
        price, _prev, chg, _ = snap_wl.get(t, (None, None, None, ""))
        lines.append(f"<code>{t:<8}</code> {fmt_price(price):>10}  {fmt_pct(chg)}  <i>{label}</i>")

    return "\n".join(lines), {"futures": snap_fut, "watch": snap_wl}

def build_macro_block() -> tuple[str, dict]:
    macro = [s.strip() for s in os.environ.get("MACRO","").split(",") if s.strip()] or DEFAULT_MACRO
    snap  = fetch_many(macro, prefer_premarket=False)
    lines = ["", "<b>📊 Macro</b>"]
    for t in macro:
        price, _prev, chg, _ = snap.get(t, (None, None, None, ""))
        lines.append(f"<code>{t:<8}</code> {fmt_price(price, macro_suffix(t)):>10}  {fmt_pct(chg)}")
    return "\n".join(lines), snap

# ------------------------- Sesgo macro (heurístico ligero) -------------------------
def macro_bias(snap_fut: dict, snap_macro: dict) -> str:
    # coge % de ES=F, y de macro: ^VIX, ^TNX, DX-Y.NYB
    def get_chg(d: dict, k: str):
        tup = d.get(k)
        return tup[2] if tup else None

    es = get_chg(snap_fut, "ES=F")
    vix = get_chg(snap_macro, "^VIX")
    tnx = get_chg(snap_macro, "^TNX")
    dxy = get_chg(snap_macro, "DX-Y.NYB")

    score = 0.0
    if es is not None:
        score += 1.0 if es > 0.3 else (-1.0 if es < -0.3 else 0.0)
    if vix is not None:
        score += 1.0 if vix < -3 else (-1.0 if vix > 3 else 0.0)
    if tnx is not None:
        score += 0.5 if tnx < 0 else (-0.5 if tnx > 0 else 0.0)
    if dxy is not None:
        score += 0.5 if dxy < 0 else (-0.5 if dxy > 0 else 0.0)

    if score >= 1.0: return "📊 Sesgo macro: <b>Alcista</b> 🟢"
    if score <= -1.0: return "📊 Sesgo macro: <b>Defensivo</b> 🔴"
    return "📊 Sesgo macro: <b>Neutral</b> ⚪"

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

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s: %(message)s")

    now_utc = datetime.datetime.utcnow().replace(tzinfo=tz.UTC)
    local_tz = tz.gettz(args.tz)
    now_local = now_utc.astimezone(local_tz)
    wd = now_local.weekday()  # 0=lunes … 6=domingo

    if not args.force and not args.allow_weekend and wd > 4:
        logging.info("Fin de semana: no se envía (use --allow-weekend o --force).")
        return

    greeting = build_greeting(now_local, override_text=args.message)
    pre_block, pre_data = build_premarket_block()
    macro_block, macro_data = build_macro_block()
    bias_line = macro_bias(pre_data["futures"], macro_data)

    hora_local = now_local.strftime("%H:%M")
    msg = (
        f"{greeting}\n\n{pre_block}\n{macro_block}\n\n"
        f"{bias_line}\n\n"
        f"🕒 Datos actualizados a las {hora_local} (Europe/Madrid)"
    )

    logging.info("✅ Pre-market snapshot generado correctamente.")

    if args.dry_run:
        print(f"{VERSION}\n{msg}")
        return

    token  = get_env("INVESTX_TOKEN", "TELEGRAM_BOT_TOKEN")
    chat_id = get_env("CHAT_ID", "TELEGRAM_CHAT_ID")

    session = make_session()
    telegram_send(f"{VERSION}\n{msg}", token=token, chat_id=chat_id, session=session)
    logging.info("Mensaje enviado correctamente.")

if __name__ == "__main__":
    main()

