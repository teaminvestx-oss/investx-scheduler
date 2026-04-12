# === insider_trading.py ===
# InvestX — Insider Trading semanal (SEC EDGAR Form 4)
# - Fuente: data.sec.gov (API pública, sin auth, sin bloqueo datacenter)
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

MIN_VALUE   = float(os.getenv("INSIDER_MIN_VALUE", "500000"))   # $500K por defecto
HTTP_TIMEOUT = int(os.getenv("INSIDER_HTTP_TIMEOUT", "15"))
_REQ_DELAY  = 0.12   # segundos entre llamadas SEC (límite: 10 req/s)

# SEC EDGAR exige User-Agent con nombre/contacto
_SEC_HEADERS = {
    "User-Agent": "InvestX-Bot/1.0 bot@investx.io",
    "Accept-Encoding": "gzip, deflate",
}

DIAS_ES  = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

# ---------------------------------------------------------------------------
# CIK map — empresas notables (~80 compañías del S&P 500 y otras relevantes)
# CIKs zero-padded a 10 dígitos, tal como los exige la submissions API.
# ---------------------------------------------------------------------------
CIK_MAP: Dict[str, str] = {
    # Tech / Growth
    "AAPL":  "0000320193",
    "MSFT":  "0000789019",
    "AMZN":  "0001018724",
    "NVDA":  "0001045810",
    "GOOGL": "0001652044",
    "META":  "0001326801",
    "TSLA":  "0001318605",
    "NFLX":  "0001065280",
    "ADBE":  "0000796343",
    "CRM":   "0001108524",
    "ORCL":  "0001341439",
    "IBM":   "0000051143",
    "CSCO":  "0000858877",
    "INTU":  "0000896878",
    "AMD":   "0000002488",
    "INTC":  "0000050863",
    "AVGO":  "0001054374",
    "QCOM":  "0000804328",
    "TXN":   "0000097476",
    "MU":    "0000723125",
    "NOW":   "0001373715",
    "COIN":  "0001679273",
    "PYPL":  "0001633917",
    "UBER":  "0001543151",
    "ABNB":  "0001559720",
    "BKNG":  "0001075531",
    "SPOT":  "0001639920",
    # Finance
    "JPM":   "0000019617",
    "BAC":   "0000070858",
    "C":     "0000831001",
    "GS":    "0000886982",
    "MS":    "0000895421",
    "WFC":   "0000072971",
    "AXP":   "0000004962",
    "V":     "0001403161",
    "MA":    "0001141391",
    "BLK":   "0001364742",
    "SCHW":  "0000316888",
    "COF":   "0000927628",
    # Health
    "JNJ":   "0000200406",
    "LLY":   "0000059478",
    "ABBV":  "0001551152",
    "UNH":   "0000731766",
    "PFE":   "0000078003",
    "MRK":   "0000310158",
    "BMY":   "0000014272",
    "AMGN":  "0000820081",
    "GILD":  "0000882095",
    "REGN":  "0000872589",
    "VRTX":  "0000875320",
    "ABT":   "0000001800",
    # Energy
    "XOM":   "0000034088",
    "CVX":   "0000093410",
    "COP":   "0001163165",
    "SLB":   "0000087347",
    "EOG":   "0000821189",
    # Consumer
    "PG":    "0000080424",
    "KO":    "0000021344",
    "PEP":   "0000077476",
    "WMT":   "0000104169",
    "COST":  "0000909832",
    "MCD":   "0000063908",
    "NKE":   "0000320187",
    "SBUX":  "0000829224",
    "HD":    "0000354950",
    "TGT":   "0000027419",
    "PM":    "0001413329",
    # Industrial
    "CAT":   "0000018230",
    "GE":    "0000040533",
    "HON":   "0000773840",
    "LMT":   "0000936468",
    "BA":    "0000012927",
    "UPS":   "0001090727",
    "FDX":   "0000230011",
    # Media / Telecom
    "DIS":   "0001001039",
    "CMCSA": "0001166691",
    "T":     "0000732717",
    "VZ":    "0000732712",
}


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


# ---------------------------------------------------------------------------
# Fetch SEC EDGAR
# ---------------------------------------------------------------------------
def _get_form4_filings(cik: str, week_start: date, week_end: date) -> List[Dict]:
    """
    Consulta submissions API y devuelve Form 4s presentados en el rango de fechas.
    Cada dict: {accession, primary_doc, filing_date}
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
    forms    = recent.get("form", [])
    dates    = recent.get("filingDate", [])
    accs     = recent.get("accessionNumber", [])
    pdocs    = recent.get("primaryDocument", [])

    filings = []
    for form, d_str, acc, pdoc in zip(forms, dates, accs, pdocs):
        if form != "4":
            continue
        try:
            fd = date.fromisoformat(d_str)
        except Exception:
            continue
        if not (week_start <= fd <= week_end):
            continue
        if not (pdoc or "").lower().endswith(".xml"):
            continue   # ignorar filings antiguos en HTML
        filings.append({"accession": acc, "primary_doc": pdoc, "filing_date": fd})

    return filings


def _parse_form4_xml(cik: str, accession: str, primary_doc: str, filing_date: date) -> List[Dict]:
    """
    Descarga y parsea el XML de un Form 4.
    Devuelve solo transacciones open-market (código P o S) de officers/directors.
    """
    cik_int   = int(cik)
    acc_clean = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{primary_doc}"

    try:
        resp = requests.get(url, headers=_SEC_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(_strip_ns(resp.content))
    except Exception as e:
        print(f"[insider] Error parsing XML {url}: {e}")
        return []

    # ---- Emisor ----
    issuer_ticker = (root.findtext(".//issuerTradingSymbol") or "").strip().upper()
    issuer_name   = (root.findtext(".//issuerName") or "").strip()

    # ---- Insider ----
    owner_name  = (root.findtext(".//rptOwnerName") or "").strip()
    is_officer  = root.findtext(".//isOfficer")  == "1"
    is_director = root.findtext(".//isDirector") == "1"
    if not is_officer and not is_director:
        return []   # ignoramos accionistas >10% sin cargo directivo

    role = (root.findtext(".//officerTitle") or "").strip() or ("Director" if is_director else "Insider")

    # ---- Transacciones no-derivadas ----
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
            "code":        code,      # P=compra, S=venta
            "shares":      shares,
            "price":       price,
            "value":       value,
            "date":        filing_date,
        })

    return transactions


# ---------------------------------------------------------------------------
# Fetch semanal
# ---------------------------------------------------------------------------
def fetch_weekly_insider_trades(week_start: date, week_end: date) -> List[Dict]:
    """
    Recorre el CIK_MAP, descarga Form 4s de la semana y filtra por valor mínimo.
    Respeta el rate limit de SEC (≤10 req/s) con un sleep entre llamadas.
    """
    all_trades: List[Dict] = []
    total_companies = len(CIK_MAP)

    for idx, (ticker, cik) in enumerate(CIK_MAP.items(), 1):
        print(f"[insider] {idx}/{total_companies} {ticker} ...", end="\r")
        filings = _get_form4_filings(cik, week_start, week_end)
        time.sleep(_REQ_DELAY)

        for filing in filings:
            txns = _parse_form4_xml(cik, filing["accession"], filing["primary_doc"], filing["filing_date"])
            time.sleep(_REQ_DELAY)
            for t in txns:
                if t["value"] >= MIN_VALUE:
                    all_trades.append(t)

    print()  # nueva línea tras el \r
    # Ordenar: primero compras, luego ventas; dentro de cada grupo por valor desc
    all_trades.sort(key=lambda x: (x["code"] != "P", -x["value"]))
    return all_trades


# ---------------------------------------------------------------------------
# Formateo del mensaje
# ---------------------------------------------------------------------------
def _build_message(trades: List[Dict], week_start: date, week_end: date) -> str:
    ws = f"{week_start.day} {MESES_ES[week_start.month - 1]}"
    we = f"{week_end.day} {MESES_ES[week_end.month - 1]}"

    header = f"🕵️ *Insider Trading — Semana {ws}–{we}*\n"

    if not trades:
        return (
            header +
            f"\nSin operaciones open-market significativas esta semana "
            f"(umbral: {_format_value(MIN_VALUE)})."
        )

    lines = [header]

    # Agrupar por fecha de presentación
    by_date: Dict[date, List[Dict]] = {}
    for t in trades:
        by_date.setdefault(t["date"], []).append(t)

    for d in sorted(by_date.keys()):
        day_label = f"{DIAS_ES[d.weekday()]} {d.day} {MESES_ES[d.month - 1]}"
        lines.append(f"\n📅 *{day_label}*")
        for t in by_date[d]:
            icon   = "🟢" if t["code"] == "P" else "🔴"
            action = "Compra" if t["code"] == "P" else "Venta"
            shares_fmt = f"{int(t['shares']):,}".replace(",", ".")
            lines.append(
                f"{icon} *{t['owner_name']}* ({t['role']}) — {t['ticker']}\n"
                f"   {action} {shares_fmt} acc. a ${t['price']:.2f} → *{_format_value(t['value'])}*"
            )

    return "\n".join(lines)


def _ai_interpretation(trades: List[Dict], week_start: date, week_end: date) -> str:
    if not trades:
        return ""

    compact = "\n".join(
        f"- {'Compra' if t['code'] == 'P' else 'Venta'} {_format_value(t['value'])}"
        f" | {t['owner_name']} ({t['role']}) | {t['ticker']}"
        for t in trades[:15]
    )

    system = (
        "Eres un analista institucional experto en insider trading. "
        "Escribes en español conciso y accionable para traders profesionales. "
        "No menciones IA ni modelos."
    )
    user = (
        f"Operaciones de insiders de la semana {week_start} – {week_end}:\n\n"
        f"{compact}\n\n"
        "Redacta un análisis de 3–5 frases que:\n"
        "1) Indique si el balance neto es comprador o vendedor.\n"
        "2) Identifique clusters (varios insiders del mismo sector comprando a la vez).\n"
        "3) Distinga compras directas (señal alcista fuerte) de ventas de CEO "
        "(suelen ser planes 10b5-1 programados, señal más débil).\n"
        "4) Cierra con 'Lectura InvestX:' y una frase sobre qué acciones o sectores "
        "destacan por actividad inusual de insiders esta semana."
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
    days_since_monday = today.weekday()  # 0=lunes
    if days_since_monday == 0:
        week_start = today - timedelta(days=7)
    else:
        week_start = today - timedelta(days=days_since_monday + 7)
    week_end = week_start + timedelta(days=4)

    print(f"[insider] Buscando Form 4s del {week_start} al {week_end} (umbral {_format_value(MIN_VALUE)})...")

    trades = fetch_weekly_insider_trades(week_start, week_end)
    print(f"[insider] {len(trades)} operaciones significativas encontradas.")

    msg = _build_message(trades, week_start, week_end)

    interp = _ai_interpretation(trades, week_start, week_end)
    if interp:
        msg += f"\n\n📌 *Lectura InvestX*\n{interp}"

    send_telegram_message(msg)
    _mark_sent(today)
    print(f"[insider] OK enviado (force={force}).")
