# === market_close.py ===
# Cierre de mercado USA – InvestX
# Incluye: gráfico PNG (índices + sectores + VIX/F&G),
#          texto completo con sectores, movers, crypto,
#          resultados macro del día y titulares → interpretación IA Bloomberg-style

import os
import datetime as dt
from io import BytesIO
from typing import Optional, Dict, List

import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches

import requests
import yfinance as yf

from utils import call_gpt_mini

# ================================
# ENV VARS
# ================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("INVESTX_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

_FG_API_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"

_FG_RATING_ES = {
    "Extreme Fear": "Miedo extremo",
    "Fear":         "Miedo",
    "Neutral":      "Neutral",
    "Greed":        "Codicia",
    "Extreme Greed":"Codicia extrema",
}

# Abreviaciones para el gráfico
_SECTOR_SHORT = {
    "Tecnología / Comunicación": "Tecnología",
    "Semiconductores":           "Semiconductores",
    "Salud":                     "Salud",
    "Financieras":               "Financieras",
    "Energía":                   "Energía",
    "Consumo discrecional":      "Cons. discrecional",
    "Consumo básico":            "Cons. básico",
    "Industriales":              "Industriales",
}

# Paleta dark theme
_BG      = "#0d1117"
_PANEL   = "#161b22"
_TEXT    = "#e6edf3"
_MUTED   = "#8b949e"
_GREEN   = "#3fb950"
_RED     = "#f85149"
_BLUE    = "#58a6ff"
_YELLOW  = "#e3b341"
_BORDER  = "#30363d"


# ================================
# TELEGRAM: texto
# ================================
def send_telegram(text: str) -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[ERROR] Faltan TELEGRAM_TOKEN / CHAT_ID (market close texto).")
        return

    max_len = 3900
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [""]
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    for idx, chunk in enumerate(chunks, start=1):
        payload = {
            "chat_id":                  CHAT_ID,
            "text":                     chunk,
            "parse_mode":               "HTML",
            "disable_web_page_preview": True,
        }
        try:
            r = requests.post(url, data=payload, timeout=20)
            if r.status_code >= 400:
                print(f"[WARN] Telegram HTTP {r.status_code} (chunk {idx}): {r.text[:200]}")
        except Exception as e:
            print(f"[ERROR] send_telegram chunk {idx}: {e}")


# ================================
# TELEGRAM: foto
# ================================
def send_telegram_photo(img_bytes: bytes, caption: str = "") -> None:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("[ERROR] Faltan TELEGRAM_TOKEN / CHAT_ID (market close foto).")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {"photo": ("close_chart.png", img_bytes, "image/png")}
    data  = {"chat_id": CHAT_ID, "parse_mode": "HTML"}
    if caption:
        data["caption"] = caption[:1024]

    try:
        r = requests.post(url, data=data, files=files, timeout=30)
        if r.status_code >= 400:
            print(f"[WARN] Telegram sendPhoto HTTP {r.status_code}: {r.text[:200]}")
        else:
            print("[market_close] Gráfico enviado a Telegram.")
    except Exception as e:
        print(f"[ERROR] send_telegram_photo: {e}")


# ================================
# UTILIDADES YFINANCE
# ================================
def get_pct_change(symbol: str) -> Optional[float]:
    try:
        data = yf.Ticker(symbol).history(period="2d")
        if data is None or data.empty or len(data) < 2:
            return None
        prev_close = float(data["Close"].iloc[-2])
        last_close = float(data["Close"].iloc[-1])
        if prev_close == 0:
            return None
        return (last_close - prev_close) / prev_close * 100.0
    except Exception as e:
        print(f"[YF] Error {symbol}: {e}")
        return None


def avg_change(values: List[Optional[float]]) -> Optional[float]:
    nums = [v for v in values if v is not None]
    return sum(nums) / len(nums) if nums else None


def style_change(change_pct: float) -> str:
    if change_pct > 0.3:
        return "🟢"
    elif change_pct < -0.3:
        return "🔴"
    return "⚪️"


# ================================
# SENTIMIENTO: VIX + FEAR & GREED
# ================================
def _fetch_vix() -> Optional[Dict]:
    try:
        closes = yf.Ticker("^VIX").history(period="5d", interval="1d")["Close"].dropna()
        if len(closes) < 1:
            return None
        current = float(closes.iloc[-1])
        change  = (current - float(closes.iloc[-2])) if len(closes) >= 2 else 0.0
        change_pct = (change / float(closes.iloc[-2]) * 100.0) if len(closes) >= 2 else 0.0
        return {"value": round(current, 2), "change": round(change, 2), "change_pct": round(change_pct, 2)}
    except Exception as e:
        print(f"[WARN] VIX: {e}")
        return None


def _fetch_fear_and_greed() -> Optional[Dict]:
    try:
        resp = requests.get(
            _FG_API_URL,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://edition.cnn.com/"},
            timeout=10,
        )
        resp.raise_for_status()
        fg = resp.json().get("fear_and_greed") or {}
        score = fg.get("score")
        if score is None:
            return None
        return {"score": round(float(score)), "rating": fg.get("rating") or ""}
    except Exception as e:
        print(f"[WARN] F&G: {e}")
        return None


def _fg_emoji(score: int) -> str:
    if score <= 25: return "😱"
    if score <= 45: return "😨"
    if score <= 55: return "😐"
    if score <= 75: return "😊"
    return "🤑"


def _vix_label(value: float) -> str:
    if value < 15: return "calma"
    if value < 20: return "moderado"
    if value < 30: return "elevado"
    return "alto"


# ================================
# CRYPTO AL CIERRE
# ================================
def _fetch_crypto_close() -> List[Dict]:
    results = []
    for name, ticker in [("BTC", "BTC-USD"), ("ETH", "ETH-USD")]:
        pct = get_pct_change(ticker)
        if pct is not None:
            results.append({"name": name, "change_pct": round(pct, 2)})
    return results


# ================================
# DATOS MACRO DEL DÍA
# ================================
def _fetch_todays_macro_results(target_date: dt.date) -> str:
    try:
        from econ_calendar import fetch_ff_events
        events = fetch_ff_events(target_date)
        if not events:
            return ""
        lines = []
        for e in events:
            line = f"- {e['time_str']} {e['event']}"
            if e.get("actual"):   line += f" | real: {e['actual']}"
            if e.get("forecast"): line += f" | est: {e['forecast']}"
            if e.get("previous"): line += f" | ant: {e['previous']}"
            lines.append(line)
        return "\n".join(lines)
    except Exception as ex:
        print(f"[WARN] macro results: {ex}")
        return ""


# ================================
# TITULARES DEL DÍA
# ================================
def _fetch_todays_headlines() -> str:
    try:
        from news_es import fetch_items, select_items
        selected = select_items(fetch_items())
        return "\n".join(f"- {x[2]}" for x in selected[:5]) if selected else ""
    except Exception as ex:
        print(f"[WARN] headlines: {ex}")
        return ""


# ================================
# DATOS DEL CIERRE (índices + sectores)
# ================================
def get_close_market_data():
    indices_map = {
        "S&P 500":     "^GSPC",
        "Nasdaq 100":  "^NDX",
        "Dow Jones":   "^DJI",
        "Russell 2000":"^RUT",
    }
    indices = []
    for name, symbol in indices_map.items():
        pct = get_pct_change(symbol)
        if pct is not None:
            indices.append({"name": name, "symbol": symbol, "change_pct": round(pct, 2)})

    sector_tickers: Dict[str, List[str]] = {
        "Tecnología / Comunicación": ["AAPL", "MSFT", "GOOGL", "META", "AMZN", "NFLX"],
        "Semiconductores":           ["NVDA", "AMD", "INTC", "AVGO", "QCOM"],
        "Salud":                     ["JNJ", "LLY", "ABBV", "UNH", "PFE"],
        "Financieras":               ["JPM", "BAC", "C", "GS", "MS", "V", "MA"],
        "Energía":                   ["XOM", "CVX", "SLB", "COP"],
        "Consumo discrecional":      ["TSLA", "HD", "MCD", "NKE"],
        "Consumo básico":            ["PG", "KO", "PEP", "WMT", "COST"],
        "Industriales":              ["CAT", "DE", "GE", "HON"],
    }

    all_tickers = sorted({t for lst in sector_tickers.values() for t in lst})
    ticker_changes: Dict[str, Optional[float]] = {t: get_pct_change(t) for t in all_tickers}

    sectors: Dict[str, List[Dict]] = {}
    for sector, tks in sector_tickers.items():
        sector_list = [
            {"ticker": t, "change_pct": round(ticker_changes[t], 2)}
            for t in tks if ticker_changes.get(t) is not None
        ]
        if sector_list:
            sectors[sector] = sector_list

    return indices, sectors


# ================================
# GRÁFICO PNG
# ================================
def _tile_color(pct: float) -> str:
    """Color de fondo para un tile de sector según su variación diaria."""
    if pct >=  2.5: return "#0a5c3a"
    if pct >=  1.5: return "#136f45"
    if pct >=  0.5: return "#1a8a54"
    if pct >= -0.5: return "#2a3140"
    if pct >= -1.5: return "#7a1f1f"
    if pct >= -2.5: return "#9b2020"
    return "#5a0808"


def _generate_close_chart(
    indices: List[Dict],
    sectors: Dict[str, List[Dict]],
    vix: Optional[Dict],
    fg: Optional[Dict],
    crypto: List[Dict],
) -> Optional[bytes]:
    """Genera imagen PNG dark-theme: índices a la izquierda, heatmap de sectores a la derecha."""

    # Calcular promedios sectoriales y ordenar mejor → peor
    sec_data: List[tuple] = []
    for s_name, s_stocks in sectors.items():
        vals = [x["change_pct"] for x in s_stocks if x.get("change_pct") is not None]
        if vals:
            label = _SECTOR_SHORT.get(s_name, s_name[:18])
            sec_data.append((label, round(sum(vals) / len(vals), 2)))
    sec_data.sort(key=lambda x: x[1], reverse=True)

    # ── Figura ───────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(12, 6.5), facecolor=_BG)
    gs = gridspec.GridSpec(
        2, 2,
        figure=fig,
        height_ratios=[5.5, 1],
        width_ratios=[2.6, 4.4],
        hspace=0.06, wspace=0.08,
        left=0.03, right=0.97,
        top=0.87, bottom=0.03,
    )
    ax_left  = fig.add_subplot(gs[0, 0])
    ax_right = fig.add_subplot(gs[0, 1])
    ax_foot  = fig.add_subplot(gs[1, :])
    for ax in (ax_left, ax_right, ax_foot):
        ax.set_facecolor(_BG)
        ax.axis("off")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)

    # ── Título ───────────────────────────────────────────────────────────
    today_str = dt.date.today().strftime("%d/%m/%Y")
    fig.text(0.03, 0.960, "CIERRE DE WALL STREET",
             fontsize=15, fontweight="bold", color=_TEXT,
             va="top", fontfamily="monospace")
    fig.text(0.97, 0.960, f"InvestX  ·  {today_str}",
             fontsize=9, color=_MUTED, va="top", ha="right")
    fig.add_artist(plt.Line2D(
        [0.03, 0.97], [0.878, 0.878],
        transform=fig.transFigure, color=_BORDER, linewidth=0.8,
    ))

    # ── Panel izquierdo: índices + VIX ───────────────────────────────────
    ax_left.text(0.06, 0.97, "ÍNDICES",
                 color=_BLUE, fontsize=9, fontweight="bold",
                 va="top", fontfamily="monospace")

    n_idx  = len(indices)
    card_h = min(0.19, 0.78 / max(n_idx, 1))
    gap    = (0.78 - n_idx * card_h) / max(n_idx, 1)

    for i, idx in enumerate(indices):
        pct  = idx["change_pct"]
        col  = _GREEN if pct > 0 else _RED if pct < 0 else _MUTED
        sign = "+" if pct > 0 else ""
        arrow = "▲" if pct > 0 else "▼" if pct < 0 else "—"
        y0 = 0.90 - (i + 1) * card_h - i * gap

        rect = mpatches.FancyBboxPatch(
            (0.04, y0), 0.92, card_h * 0.88,
            boxstyle="round,pad=0.01",
            facecolor=col + "28", edgecolor=col + "60",
            linewidth=0.7, transform=ax_left.transAxes, clip_on=False,
        )
        ax_left.add_patch(rect)
        mid_y = y0 + card_h * 0.42
        ax_left.text(0.10, mid_y, idx["name"],
                     color=_TEXT, fontsize=9.2, va="center")
        ax_left.text(0.94, mid_y, f"{arrow} {sign}{pct:.2f}%",
                     color=col, fontsize=10, fontweight="bold",
                     va="center", ha="right")

    # VIX debajo de los índices
    if vix:
        vy   = 0.90 - (n_idx * (card_h + gap)) - 0.06
        vcol = _RED if vix["change"] > 0 else _GREEN
        vsign = "+" if vix["change"] >= 0 else ""
        vlbl  = _vix_label(vix["value"])
        ax_left.text(0.06, vy,        "VIX",
                     color=_MUTED, fontsize=8, fontweight="bold",
                     va="top", fontfamily="monospace")
        ax_left.text(0.06, vy - 0.10, f"{vix['value']:.1f}",
                     color=vcol, fontsize=18, fontweight="bold", va="top")
        ax_left.text(0.06, vy - 0.24, f"{vsign}{vix['change']:.2f} pts  ·  {vlbl}",
                     color=_MUTED, fontsize=7.5, va="top")

    # ── Panel derecho: tiles de sectores (heatmap) ────────────────────────
    ax_right.text(0.02, 0.97, "SECTORES",
                  color=_BLUE, fontsize=9, fontweight="bold",
                  va="top", fontfamily="monospace")

    if sec_data:
        n_cols  = 3
        n_rows  = math.ceil(len(sec_data) / n_cols)
        tile_w  = 0.305
        tile_h  = min(0.22, 0.84 / n_rows)
        x_gap   = (1.0 - n_cols * tile_w) / (n_cols + 1)
        y_start = 0.90
        y_gap   = (0.88 - n_rows * tile_h) / max(n_rows, 1)

        for i, (s_name, s_pct) in enumerate(sec_data):
            col = i % n_cols
            row = i // n_cols
            x0  = x_gap + col * (tile_w + x_gap)
            y0  = y_start - (row + 1) * tile_h - row * y_gap

            bg = _tile_color(s_pct)
            tile = mpatches.FancyBboxPatch(
                (x0, y0), tile_w, tile_h * 0.92,
                boxstyle="round,pad=0.015",
                facecolor=bg, edgecolor="#00000040",
                linewidth=0, transform=ax_right.transAxes, clip_on=False,
            )
            ax_right.add_patch(tile)

            sign = "+" if s_pct > 0 else ""
            cx   = x0 + tile_w / 2
            cy   = y0 + tile_h * 0.46
            ax_right.text(cx, cy + tile_h * 0.18, s_name,
                          color="#ffffffcc", fontsize=8.5,
                          ha="center", va="center", fontweight="bold")
            ax_right.text(cx, cy - tile_h * 0.14,
                          f"{sign}{s_pct:.2f}%",
                          color="white", fontsize=12,
                          ha="center", va="center", fontweight="bold")

    # ── Footer: F&G + Crypto ──────────────────────────────────────────────
    badges: List[str] = []
    if fg:
        rating_es = _FG_RATING_ES.get(fg["rating"], fg["rating"])
        emoji     = _fg_emoji(fg["score"])
        badges.append(f"Fear & Greed  {fg['score']}/100  —  {rating_es} {emoji}")
    for c in crypto:
        sign = "+" if c["change_pct"] > 0 else ""
        badges.append(f"{c['name']}  {sign}{c['change_pct']:.2f}%")

    if badges:
        ax_foot.text(0.5, 0.52, "     ·     ".join(badges),
                     ha="center", va="center", color=_MUTED, fontsize=9,
                     transform=ax_foot.transAxes)

    # ── Export ───────────────────────────────────────────────────────────
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150,
                facecolor=_BG, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ================================
# IMAGEN: Finviz heatmap (primario) → matplotlib (fallback)
# ================================
_FINVIZ_HEATMAP_URL = "https://finviz.com/map.ashx?t=sec&st=d1"
_FINVIZ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://finviz.com/",
    "DNT":             "1",
}


def _fetch_finviz_heatmap() -> Optional[bytes]:
    """
    Descarga el heatmap de sectores del S&P 500 de Finviz.
    Devuelve bytes PNG o None si Finviz bloquea la IP del datacenter.
    """
    try:
        resp = requests.get(_FINVIZ_HEATMAP_URL, headers=_FINVIZ_HEADERS, timeout=15)
        ct = resp.headers.get("content-type", "")
        if resp.ok and ct.startswith("image/"):
            print(f"[market_close] Finviz heatmap OK ({len(resp.content):,} bytes).")
            return resp.content
        print(f"[market_close] Finviz heatmap bloqueado (HTTP {resp.status_code}, ct={ct}).")
    except Exception as e:
        print(f"[market_close] Finviz heatmap error: {e}")
    return None



def format_market_close(indices, sectors, vix, fg, crypto):
    today = dt.date.today().strftime("%d/%m/%Y")
    display_lines: List[str] = []
    plain_lines:   List[str] = []

    display_lines.append(f"📊 <b>Cierre de Wall Street — InvestX</b> ({today})\n")

    if fg:
        emoji     = _fg_emoji(fg["score"])
        rating_es = _FG_RATING_ES.get(fg["rating"], fg["rating"])
        display_lines.append(
            f"🧭 Fear &amp; Greed <b>{fg['score']}/100</b> — {rating_es} {emoji}"
        )
        plain_lines.append(f"Fear & Greed: {fg['score']} ({fg['rating']})")
    if vix:
        sign      = "+" if vix["change"] >= 0 else ""
        direction = "↑" if vix["change"] >= 0 else "↓"
        label     = _vix_label(vix["value"])
        display_lines.append(
            f"📉 VIX <b>{vix['value']:.1f}</b> "
            f"({direction}{sign}{vix['change']:.2f} pts — {label})"
        )
        plain_lines.append(f"VIX: {vix['value']:.1f} ({sign}{vix['change']:.2f} pts, {label})")
    if fg or vix:
        display_lines.append("")

    # Índices
    if indices:
        display_lines.append("📈 <b>Índices</b>\n")
        for idx in indices:
            icon = style_change(idx["change_pct"])
            sign = "+" if idx["change_pct"] > 0 else ""
            pct  = f"{sign}{idx['change_pct']:.2f}%"
            display_lines.append(f"{icon} {idx['name']}: <b>{pct}</b>")
            plain_lines.append(f"{idx['name']}: {pct}")
        display_lines.append("")

    # Sectores top/bottom
    sector_avgs = {
        s: avg_change([x["change_pct"] for x in lst])
        for s, lst in sectors.items()
    }
    ranked = sorted(
        [(s, v) for s, v in sector_avgs.items() if v is not None],
        key=lambda x: x[1], reverse=True,
    )
    if ranked:
        top    = ranked[:2]
        bottom = ranked[-2:]
        display_lines.append("🟢 <b>Sectores fuertes</b>")
        for sec, val in top:
            sign = "+" if val > 0 else ""
            display_lines.append(f"  {_SECTOR_SHORT.get(sec, sec)}: <b>{sign}{val:.2f}%</b>")
            plain_lines.append(f"Sector líder {sec}: {sign}{val:.2f}%")
        display_lines.append("")
        display_lines.append("🔻 <b>Sectores débiles</b>")
        for sec, val in bottom:
            sign = "+" if val > 0 else ""
            display_lines.append(f"  {_SECTOR_SHORT.get(sec, sec)}: <b>{sign}{val:.2f}%</b>")
            plain_lines.append(f"Sector rezagado {sec}: {sign}{val:.2f}%")
        display_lines.append("")

    # Top movers
    all_stocks = [
        {"ticker": x["ticker"], "change_pct": x["change_pct"]}
        for lst in sectors.values() for x in lst
    ]
    top_movers = sorted(all_stocks, key=lambda x: abs(x["change_pct"]), reverse=True)[:4]
    if top_movers:
        display_lines.append("🏁 <b>Acciones destacadas</b>\n")
        parts_html = []
        for x in top_movers:
            sign = "+" if x["change_pct"] > 0 else ""
            parts_html.append(f"{x['ticker']} <b>{sign}{x['change_pct']:.2f}%</b>")
            plain_lines.append(f"{x['ticker']}: {sign}{x['change_pct']:.2f}%")
        display_lines.append("  " + "  ·  ".join(parts_html))
        display_lines.append("")

    # Crypto
    if crypto:
        display_lines.append("💰 <b>Crypto</b>\n")
        for c in crypto:
            icon = style_change(c["change_pct"])
            sign = "+" if c["change_pct"] > 0 else ""
            display_lines.append(f"{icon} {c['name']}: <b>{sign}{c['change_pct']:.2f}%</b>")
            plain_lines.append(f"{c['name']}: {sign}{c['change_pct']:.2f}%")
        display_lines.append("")

    return "\n".join(display_lines).strip(), "\n".join(plain_lines).strip()


# ================================
# INTERPRETACIÓN IA (Bloomberg-style)
# ================================
def interpret_market_close(
    plain_text: str,
    macro_context: str = "",
    news_context: str = "",
) -> str:
    if not plain_text:
        return ""

    system_prompt = (
        "Eres un analista institucional de mercados, estilo Bloomberg Terminal. "
        "Escribes en español neutro, directo y accionable para traders profesionales. "
        "No menciones IA ni modelos.\n\n"
        "Estructura exacta (un único bloque de texto, sin listas):\n"
        "1) Tono general de la sesión (risk-on / risk-off / mixto) e índices principales.\n"
        "2) Sectores líderes y rezagados; qué dice eso del flujo de capital.\n"
        "3) Si hubo datos macro hoy, si sorprendieron y cómo movieron el mercado.\n"
        "4) Si hay titulares relevantes que expliquen algún movimiento, incorpóralos.\n"
        "5) Frase final: 'Sesgo InvestX:' con lectura táctica para la próxima sesión.\n"
        "Total: 4–6 frases. Específico, sin frases genéricas."
    )

    macro_section = (
        f"\nDatos macro publicados hoy:\n{macro_context}"
        if macro_context else "\nDatos macro hoy: sin publicaciones de alto impacto."
    )
    news_section = f"\nTitulares del día:\n{news_context}" if news_context else ""

    user_prompt = (
        f"Datos del cierre de Wall Street:\n{plain_text}"
        f"{macro_section}{news_section}\n\n"
        "Redacta el análisis siguiendo la estructura indicada."
    )

    try:
        return (call_gpt_mini(system_prompt, user_prompt, max_tokens=400) or "").strip()
    except Exception as e:
        print(f"[ERROR] interpret_market_close: {e}")
        return ""


# ================================
# FUNCIÓN PRINCIPAL: MARKET CLOSE
# ================================
def run_market_close(force: bool = False) -> None:
    today = dt.date.today()

    if today.weekday() >= 5 and not force:
        print("[INFO] Fin de semana, no se envía Market Close.")
        return

    print("[market_close] Recogiendo datos de mercado...")
    indices, sectors = get_close_market_data()

    if not indices and not sectors:
        send_telegram("📊 <b>Cierre de Wall Street — InvestX</b>\n\nNo se han podido obtener datos de mercado hoy.")
        return

    vix    = _fetch_vix()
    fg     = _fetch_fear_and_greed()
    crypto = _fetch_crypto_close()

    # ── Imagen: Finviz heatmap (primario) → matplotlib (fallback) ────────
    print("[market_close] Intentando Finviz heatmap...")
    chart_bytes = _fetch_finviz_heatmap()
    if not chart_bytes:
        print("[market_close] Finviz no disponible, generando gráfico propio...")
        chart_bytes = _generate_close_chart(indices, sectors, vix, fg, crypto)
    if chart_bytes:
        send_telegram_photo(chart_bytes)
    else:
        print("[WARN] Sin imagen disponible, se envía solo el texto.")

    # ── Texto + IA ───────────────────────────────────────────────────────
    macro_context = _fetch_todays_macro_results(today)
    news_context  = _fetch_todays_headlines()

    display_text, plain_text = format_market_close(indices, sectors, vix, fg, crypto)
    interpretation = interpret_market_close(plain_text, macro_context, news_context)

    parts = [display_text]
    if interpretation:
        parts.append("\n🧠 <b>Análisis InvestX</b>\n")
        parts.append(interpretation)

    send_telegram("\n".join(parts).strip())
    print(f"[market_close] OK enviado (force={force}).")
