# === insider_trading.py ===
# InvestX — Insider Trading semanal (SEC EDGAR Form 4)
# - Fuente: data.sec.gov (API pública, sin auth, sin bloqueo datacenter)
# - CIKs resueltos dinámicamente desde company_tickers.json de la propia SEC
# - Solo transacciones open-market (código P=compra, S=venta)
# - Umbral mínimo configurable via INSIDER_MIN_VALUE (default $500K)
# - Envío semanal los lunes, anti-duplicado por semana ISO

from __future__ import annotations

import os
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from utils import call_gpt_mini, send_telegram_message

TZ = ZoneInfo("Europe/Madrid")
STATE_FILE = "insider_trading_state.json"

MIN_VALUE    = float(os.getenv("INSIDER_MIN_VALUE", "500000"))  # $500K por defecto
HTTP_TIMEOUT = int(os.getenv("INSIDER_HTTP_TIMEOUT", "15"))
_REQ_DELAY   = 0.12  # seg entre llamadas SEC (límite: 10 req/s)

_SEC_HEADERS = {
    "User-Agent": "InvestX-Bot/1.0 bot@investx.io",
    "Accept-Encoding": "gzip, deflate",
}

DIAS_ES  = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

# ---------------------------------------------------------------------------
# Lista de tickers a vigilar (~280 empresas, S&P 500 + growth relevantes)
# Los CIKs se resuelven en tiempo de ejecución desde la API de la SEC.
# ---------------------------------------------------------------------------
_TICKERS: List[str] = [
    # Tecnología / Software / Cloud
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "META", "AMZN", "TSLA",
    "ADBE", "CRM", "NOW", "ORCL", "IBM", "CSCO", "INTU", "SNPS", "CDNS",
    "AMD", "INTC", "AVGO", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC",
    "MRVL", "MCHP", "NXPI", "ON", "STX", "WDC", "NTAP", "HPQ", "HPE", "DELL",
    "ACN", "CTSH", "EPAM", "GLOB",
    "PANW", "CRWD", "ZS", "OKTA", "FTNT", "CYBR",
    "SNOW", "MDB", "DDOG", "NET", "PLTR", "APP", "TTD",
    "NFLX", "SPOT", "RBLX", "EA", "TTWO",
    "COIN", "UBER", "ABNB", "LYFT", "DASH",
    "PYPL", "SQ", "AFRM", "SOFI",
    # Finanzas
    "JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "TFC", "COF",
    "AXP", "V", "MA", "BLK", "BX", "APO", "KKR", "CG",
    "SCHW", "ICE", "CME", "CBOE", "NDAQ", "SPGI", "MCO",
    "PRU", "MET", "AFL", "ALL", "PGR", "TRV", "CB", "AIG",
    "FITB", "RF", "HBAN", "MTB", "CFG", "KEY", "ALLY", "SYF", "DFS",
    # Salud / Biotech / Farma
    "JNJ", "LLY", "ABBV", "UNH", "PFE", "MRK", "BMY",
    "AMGN", "GILD", "REGN", "VRTX", "BIIB", "MRNA", "BNTX",
    "ISRG", "MDT", "ABT", "BSX", "EW", "STE", "DXCM", "RMD",
    "TMO", "DHR", "A", "IDXX", "WAT", "MTD",
    "CVS", "CI", "HUM", "ELV", "CNC", "MOH",
    "INCY", "EXEL", "ALNY", "VRTX",
    # Energía
    "XOM", "CVX", "COP", "SLB", "OXY", "MPC", "VLO", "PSX",
    "HES", "DVN", "FANG", "EOG", "HAL", "BKR", "EQT", "AR",
    "APA", "MRO", "SM", "CTRA",
    # Consumo discrecional
    "HD", "LOW", "COST", "WMT", "TGT",
    "NKE", "LULU", "TJX", "ROST", "BURL", "AEO", "ANF", "GPS",
    "MCD", "YUM", "SBUX", "CMG", "DPZ", "QSR", "WING",
    "HLT", "MAR", "RCL", "CCL", "NCLH", "BKNG", "EXPE",
    "DAL", "UAL", "LUV", "AAL", "ALK",
    "DIS", "CMCSA", "CHTR", "PARA", "WBD", "NFLX",
    "EBAY", "ETSY", "W",
    # Consumo básico
    "PG", "KO", "PEP", "PM", "MO", "STZ", "MNST", "CELH",
    "GIS", "K", "HSY", "MDLZ", "KHC", "SJM", "MKC",
    "EL", "CL", "CHD", "CLX", "KMB",
    # Industrial / Aeroespacial / Transporte
    "CAT", "DE", "GE", "HON", "RTX", "LMT", "NOC", "GD", "BA",
    "UPS", "FDX", "CSX", "UNP", "NSC", "JBHT", "ODFL", "SAIA", "XPO",
    "ITW", "EMR", "ROK", "PH", "AME", "IEX", "ROP", "GNRC",
    "URI", "FAST", "GWW", "SWK", "SNA",
    # Materiales
    "LIN", "APD", "DOW", "DD", "SHW", "PPG", "ECL", "IFF",
    "NEM", "FCX", "AA", "NUE", "STLD",
    "MOS", "CF", "NTR",
    # Utilities
    "NEE", "DUK", "SO", "D", "AEP", "EXC", "PCG", "SRE", "XEL", "WEC",
    # Real Estate / REIT
    "AMT", "CCI", "EQIX", "DLR", "PLD", "SPG",
    # Comunicación / Telecom
    "T", "VZ",
]
# Eliminar duplicados manteniendo orden
_TICKERS = list(dict.fromkeys(_TICKERS))


# ---------------------------------------------------------------------------
# CIK dinámico desde la SEC
# ---------------------------------------------------------------------------
def _build_cik_map() -> Dict[str, str]:
    """
    Descarga company_tickers.json de la propia SEC y resuelve los CIKs
    de todos los tickers en _TICKERS. Sin CIKs hardcodeados que puedan
    quedar obsoletos.
    """
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        resp = requests.get(url, headers=_SEC_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[insider] Error descargando company_tickers.json: {e}")
        return {}

    sec_map: Dict[str, str] = {}
    for item in data.values():
        ticker = (item.get("ticker") or "").strip().upper()
        cik    = str(item.get("cik_str") or "").zfill(10)
        if ticker:
            sec_map[ticker] = cik

    wanted = {t: sec_map[t] for t in _TICKERS if t in sec_map}
    print(f"[insider] CIKs resueltos: {len(wanted)}/{len(_TICKERS)} tickers")
    return wanted


# ---------------------------------------------------------------------------
# Estado anti-duplicado semanal
# ---------------------------------------------------------------------------
def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(d: Dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def _week_key(d: date) -> str:
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _already_sent_this_week(d: date) -> bool:
    return _load_state().get("sent_week") == _week_key(d)


def _mark_sent(d: date) -> None:
    st = _load_state()
    st["sent_week"] = _week_key(d)
    st["sent_at"]   = datetime.now(TZ).isoformat()
    _save_state(st)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _format_value(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def _safe_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _strip_ns(xml_bytes: bytes) -> bytes:
    """Elimina namespaces XML para simplificar el parsing con ElementTree."""
    s = xml_bytes.decode("utf-8", errors="replace")
    s = re.sub(r'\s+xmlns[^=]*="[^"]*"', "", s)
    s = re.sub(r"<([a-zA-Z]+:)", "<", s)
    s = re.sub(r"</([a-zA-Z]+:)", "</", s)
    return s.encode("utf-8")


def _fetch_xml_content(cik_int: int, accession: str, primary_doc: str) -> Optional[bytes]:
    """
    Descarga el XML de un Form 4.

    EDGAR almacena los Form 4 XML en la raíz del directorio del filing.
    El campo primaryDocument a menudo incluye un prefijo de subdirectorio
    XSLT como "xslF345X06/form4.xml" — ese prefijo hay que eliminarlo.

    Estrategias:
      1. Nombre base del primaryDoc sin prefijo de subdirectorio (fix principal)
      2. Nombre completo del primaryDoc tal como viene
      3. Índice JSON del filing → buscar cualquier .xml en la raíz
      4. {accession}.xml con guiones (nombre estándar SEC)
    """
    import posixpath
    acc_clean = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}"

    # Nombre base sin prefijo de subdirectorio (e.g. "xslF345X06/form4.xml" → "form4.xml")
    doc_basename = posixpath.basename(primary_doc) if primary_doc else ""

    def _try(url: str) -> Optional[bytes]:
        try:
            r = requests.get(url, headers=_SEC_HEADERS, timeout=HTTP_TIMEOUT)
            time.sleep(_REQ_DELAY)
            if r.ok and b"ownershipDocument" in r.content:
                return r.content
        except Exception:
            pass
        return None

    # Estrategia 1: nombre base sin el prefijo xslF345X06/ (fix principal)
    if doc_basename and doc_basename != primary_doc:
        content = _try(f"{base}/{doc_basename}")
        if content:
            return content

    # Estrategia 2: ruta completa original
    if primary_doc:
        content = _try(f"{base}/{primary_doc}")
        if content:
            return content

    # Estrategia 3: índice JSON del filing → todos los .xml de la raíz
    try:
        idx_url = f"{base}/{accession}-index.json"
        r_idx = requests.get(idx_url, headers=_SEC_HEADERS, timeout=HTTP_TIMEOUT)
        time.sleep(_REQ_DELAY)
        if r_idx.ok:
            items = r_idx.json().get("directory", {}).get("item", [])
            for item in items:
                name = item.get("name", "")
                if name.lower().endswith(".xml") and "index" not in name.lower():
                    content = _try(f"{base}/{name}")
                    if content:
                        return content
    except Exception:
        pass

    # Estrategia 4: nombre estándar SEC con guiones
    content = _try(f"{base}/{accession}.xml")
    if content:
        return content

    return None


# ---------------------------------------------------------------------------
# Fetch SEC EDGAR
# ---------------------------------------------------------------------------
def _get_form4_filings(cik: str, week_start: date, week_end: date) -> List[Dict]:
    """
    Consulta submissions API y devuelve Form 4s cuya fecha de transacción
    (reportDate) cae en la semana objetivo.

    IMPORTANTE: filtramos por reportDate (fecha de la operación), NO por
    filingDate (fecha de presentación a la SEC), porque los ejecutivos tienen
    hasta 2 días hábiles para presentar, por lo que filings de operaciones
    del viernes pueden aparecer el lunes/martes de la semana siguiente.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = requests.get(url, headers=_SEC_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[insider] Error submissions CIK={cik}: {e}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms  = recent.get("form", [])
    fdates = recent.get("filingDate", [])
    rdates = recent.get("reportDate", [])   # fecha real de la transacción
    accs   = recent.get("accessionNumber", [])
    pdocs  = recent.get("primaryDocument", [])

    # Ventana de búsqueda amplia: filings presentados hasta 7 días después del
    # fin de semana objetivo, para no perder presentaciones tardías
    filing_cutoff = week_end + timedelta(days=7)

    filings = []
    for form, fd_str, rd_str, acc, pdoc in zip(forms, fdates, rdates, accs, pdocs):
        if form not in ("4", "4/A"):
            continue

        # Parsear fecha de presentación
        try:
            fd = date.fromisoformat(fd_str)
        except Exception:
            continue

        # Si el filing es demasiado antiguo o futuro, saltar
        if fd < week_start or fd > filing_cutoff:
            continue

        # Filtro principal: fecha de transacción (reportDate) dentro de la semana
        if rd_str:
            try:
                rd = date.fromisoformat(rd_str)
                if week_start <= rd <= week_end:
                    filings.append({"accession": acc, "primary_doc": pdoc or "", "filing_date": fd})
            except Exception:
                pass
        else:
            # Sin reportDate: usar filingDate como aproximación
            if week_start <= fd <= week_end:
                filings.append({"accession": acc, "primary_doc": pdoc or "", "filing_date": fd})

    return filings


def _parse_form4_xml(cik: str, filing: Dict) -> List[Dict]:
    """
    Descarga y parsea el XML de un Form 4.
    Devuelve transacciones open-market (P/S) de officers/directors > umbral.
    """
    cik_int   = int(cik)
    accession = filing["accession"]  # con guiones: "0001234567-24-000123"

    xml_content = _fetch_xml_content(cik_int, accession, filing["primary_doc"])
    if not xml_content:
        print(f"[insider]   ✗ XML no descargado: {accession} primaryDoc={filing['primary_doc']}")
        return []

    try:
        root = ET.fromstring(_strip_ns(xml_content))
    except Exception as e:
        print(f"[insider]   ✗ XML parse error {accession}: {e}")
        return []

    issuer_ticker = (root.findtext(".//issuerTradingSymbol") or "").strip().upper()
    issuer_name   = (root.findtext(".//issuerName") or "").strip()
    owner_name    = (root.findtext(".//rptOwnerName") or "").strip()
    is_officer    = root.findtext(".//isOfficer")  == "1"
    is_director   = root.findtext(".//isDirector") == "1"

    if not is_officer and not is_director:
        return []

    role = (root.findtext(".//officerTitle") or "").strip() or (
        "Director" if is_director else "Insider"
    )

    transactions = []
    for txn in root.findall(".//nonDerivativeTransaction"):
        code = (txn.findtext(".//transactionCode") or "").strip()
        if code not in ("P", "S"):
            continue

        shares = _safe_float(txn.findtext(".//transactionShares/value") or "")
        price  = _safe_float(txn.findtext(".//transactionPricePerShare/value") or "")
        if not shares or not price:
            continue

        value = shares * price
        transactions.append({
            "ticker":      issuer_ticker or "???",
            "issuer_name": issuer_name,
            "owner_name":  owner_name,
            "role":        role,
            "code":        code,
            "shares":      shares,
            "price":       price,
            "value":       value,
            "date":        filing["filing_date"],
        })

    return transactions


# ---------------------------------------------------------------------------
# Fetch semanal
# ---------------------------------------------------------------------------
def fetch_weekly_insider_trades(week_start: date, week_end: date) -> List[Dict]:
    """
    1. Resuelve CIKs desde la SEC en tiempo real
    2. Por cada empresa, obtiene Form 4s de la semana
    3. Parsea XMLs y filtra por umbral de valor
    """
    cik_map = _build_cik_map()
    if not cik_map:
        print("[insider] No se pudo construir el CIK map.")
        return []

    all_trades: List[Dict] = []
    total = len(cik_map)
    total_filings   = 0
    xml_ok          = 0
    xml_fail        = 0
    below_threshold = 0

    for idx, (ticker, cik) in enumerate(cik_map.items(), 1):
        print(f"[insider] {idx}/{total} {ticker} ...", end="\r", flush=True)
        filings = _get_form4_filings(cik, week_start, week_end)
        time.sleep(_REQ_DELAY)

        if filings:
            total_filings += len(filings)
            print(f"\n[insider] {ticker}: {len(filings)} Form 4(s) en la semana")

        for filing in filings:
            txns = _parse_form4_xml(cik, filing)
            if txns is None or txns == []:
                # Distinguir entre XML no descargado y XML sin transacciones P/S
                pass
            for t in txns:
                if t["value"] >= MIN_VALUE:
                    all_trades.append(t)
                    print(f"[insider]   ✓ {t['owner_name']} ({t['ticker']}) "
                          f"{'COMPRA' if t['code']=='P' else 'VENTA'} "
                          f"{_format_value(t['value'])}")
                else:
                    below_threshold += 1

    print()
    print(f"[insider] RESUMEN: {total_filings} filings encontrados | "
          f"{len(all_trades)} sobre umbral | "
          f"{below_threshold} por debajo de {_format_value(MIN_VALUE)}")

    # Compras primero, luego ventas; dentro de cada grupo por valor desc
    all_trades.sort(key=lambda x: (x["code"] != "P", -x["value"]))
    return all_trades


# ---------------------------------------------------------------------------
# Mensaje
# ---------------------------------------------------------------------------
def _fmt_name(raw: str) -> str:
    """Normaliza nombres: 'PRINCE MATTHEW' o 'Prince Matthew' → 'Matthew Prince'."""
    parts = raw.strip().split()
    if not parts:
        return raw
    # Si está todo en mayúsculas asumimos orden apellido-nombre → invertir
    if raw == raw.upper() and len(parts) >= 2:
        parts = parts[1:] + [parts[0]]
    return " ".join(p.capitalize() for p in parts)


def _short_company(name: str) -> str:
    """Acorta el nombre legal de la empresa para mostrar en el mensaje."""
    for suffix in [", Inc.", " Inc.", " Corp.", " Corporation", ", Ltd.", " Ltd.",
                   " LLC", " L.P.", " PLC", " N.V.", " S.A."]:
        name = name.replace(suffix, "")
    return name.strip()[:24]


def _aggregate_trades(trades: List[Dict]) -> List[Dict]:
    """
    Agrupa lotes del mismo insider+ticker+tipo en una sola entrada.
    Reduce el ruido de los planes 10b5-1 que ejecutan múltiples órdenes parciales.
    """
    groups: Dict[tuple, Dict] = {}
    for t in trades:
        key = (t["owner_name"], t["ticker"], t["code"])
        if key not in groups:
            groups[key] = dict(t)
            groups[key]["n_ops"] = 1
        else:
            groups[key]["shares"] += t["shares"]
            groups[key]["value"]  += t["value"]
            groups[key]["n_ops"]  += 1
            # Usar la fecha más temprana
            if t["date"] < groups[key]["date"]:
                groups[key]["date"] = t["date"]
    return list(groups.values())


def _build_message(trades: List[Dict], week_start: date, week_end: date) -> str:
    ws = f"{week_start.day} {MESES_ES[week_start.month - 1]}"
    we = f"{week_end.day} {MESES_ES[week_end.month - 1]}"

    header = (
        f"🕵️ *Lo que los directivos hicieron con su propio dinero*\n"
        f"_Insider Trading · Semana {ws}–{we}_"
    )

    if not trades:
        return (
            header +
            f"\n\nSin operaciones open-market significativas esta semana "
            f"(umbral: {_format_value(MIN_VALUE)})."
        )

    # Agregar lotes del mismo insider
    agg = _aggregate_trades(trades)
    buys  = sorted([t for t in agg if t["code"] == "P"], key=lambda x: -x["value"])
    sells = sorted([t for t in agg if t["code"] == "S"], key=lambda x: -x["value"])[:10]

    summary = f"\n_{len(buys)} compras · {len(sells)} ventas destacadas · umbral {_format_value(MIN_VALUE)}_\n"
    lines = [header, summary]

    def _trade_line(t: Dict) -> str:
        icon    = "🟢" if t["code"] == "P" else "🔴"
        action  = "Compra" if t["code"] == "P" else "Venta"
        name    = _fmt_name(t["owner_name"])
        role    = t["role"]
        ticker  = t["ticker"]
        company = _short_company(t.get("issuer_name") or "")
        ctx     = f"{ticker} ({company})" if company else ticker
        n       = t.get("n_ops", 1)
        day     = f"{DIAS_ES[t['date'].weekday()]} {t['date'].day} {MESES_ES[t['date'].month - 1]}"

        if n > 1:
            return (
                f"{icon} *{name}* · {role}\n"
                f"   {ctx}  ·  {action} en {n} lotes → *{_format_value(t['value'])}* total  _{day}_"
            )
        else:
            shares_fmt = f"{int(t['shares']):,}".replace(",", ".")
            return (
                f"{icon} *{name}* · {role}\n"
                f"   {ctx}  ·  {action} {shares_fmt} acc. a ${t['price']:.2f} → *{_format_value(t['value'])}*  _{day}_"
            )

    if buys:
        lines.append("🟢 *COMPRAS — señal directa*\n")
        for t in buys:
            lines.append(_trade_line(t))
        lines.append("")

    if sells:
        lines.append("🔴 *VENTAS DESTACADAS* _(top por volumen)_\n")
        for t in sells:
            lines.append(_trade_line(t))
        lines.append("")

    return "\n".join(lines).strip()


def _ai_interpretation(trades: List[Dict], week_start: date, week_end: date) -> str:
    if not trades:
        return ""

    agg = _aggregate_trades(trades)
    buys  = sorted([t for t in agg if t["code"] == "P"], key=lambda x: -x["value"])
    sells = sorted([t for t in agg if t["code"] == "S"], key=lambda x: -x["value"])[:10]

    def _line(t):
        action  = "Compra" if t["code"] == "P" else "Venta"
        company = _short_company(t.get("issuer_name") or t["ticker"])
        n       = t.get("n_ops", 1)
        suffix  = f" ({n} lotes, posible plan 10b5-1)" if n > 3 else ""
        return (
            f"- {action} {_format_value(t['value'])}{suffix}"
            f" | {_fmt_name(t['owner_name'])} ({t['role']})"
            f" | {t['ticker']} — {company}"
        )

    compact = "\n".join(_line(t) for t in (buys + sells)[:15])

    system = (
        "Eres un analista institucional experto en insider trading. "
        "Escribes en español conciso y accionable para traders profesionales. "
        "No menciones IA ni modelos."
    )
    user = (
        f"Operaciones de insiders semana {week_start}–{week_end}:\n\n"
        f"{compact}\n\n"
        "Redacta un análisis de 3–5 frases:\n"
        "1) Balance neto comprador o vendedor y sectores protagonistas.\n"
        "2) Distingue compras directas (señal fuerte) de ventas con muchos lotes "
        "(suelen ser planes 10b5-1 programados, señal más débil).\n"
        "3) Señala si hay clusters (varios insiders de la misma empresa o sector).\n"
        "4) Cierra con 'Lectura InvestX:' resumiendo qué acciones o sectores "
        "destacan por actividad inusual esta semana."
    )

    try:
        return (call_gpt_mini(system, user, max_tokens=300) or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Entrypoint público
# ---------------------------------------------------------------------------
def run_weekly_insider(force: bool = False) -> None:
    now   = datetime.now(TZ)
    today = now.date()

    if not force and _already_sent_this_week(today):
        print("[insider] Ya enviado esta semana. Skipping.")
        return

    # Semana anterior completa (lunes–viernes)
    days_since_monday = today.weekday()
    if days_since_monday == 0:
        week_start = today - timedelta(days=7)
    else:
        week_start = today - timedelta(days=days_since_monday + 7)
    week_end = week_start + timedelta(days=4)

    print(f"[insider] Form 4s del {week_start} al {week_end} (umbral {_format_value(MIN_VALUE)})...")

    trades = fetch_weekly_insider_trades(week_start, week_end)
    print(f"[insider] {len(trades)} operaciones significativas.")

    msg    = _build_message(trades, week_start, week_end)
    interp = _ai_interpretation(trades, week_start, week_end)
    if interp:
        msg += f"\n\n📌 *Lectura InvestX*\n{interp}"

    send_telegram_message(msg)
    _mark_sent(today)
    print(f"[insider] OK enviado (force={force}).")
