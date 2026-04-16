# === congressional_trades.py ===
# InvestX — Operaciones bursátiles de congresistas USA
#
# Fuente primaria: QuiverQuant API (bulk/congresstrading) con Bearer token
# Fuente fallback:  housestockwatcher.com/api  (Cámara de Representantes)
#                   senatestockwatcher.com/api  (Senado)
# Marco legal: STOCK Act (2012) → plazo de 30–45 días para declarar
#
# Lógica:
#  - Filtra por disclosure_date reciente (últimos 3 días → novedades)
#  - Umbral mínimo configurable (CONGRESS_MIN_AMOUNT, default $50K)
#  - Anti-dup por clave (nombre, ticker, tipo, fecha operación)
#  - IA: detecta patrones por partido/sector/comité

from __future__ import annotations

import json
import os
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from utils import call_gpt_mini, send_telegram_message

TZ         = ZoneInfo("Europe/Madrid")
STATE_FILE = "congressional_trades_state.json"

MIN_AMOUNT   = int(os.getenv("CONGRESS_MIN_AMOUNT", "250000"))  # $250K por defecto
HTTP_TIMEOUT = 15
QUIVER_TOKEN = os.getenv("QUIVER_TOKEN", "")   # Bearer token de QuiverQuant (paid)
FMP_API_KEY  = os.getenv("FMP_API_KEY",  "")   # Financial Modeling Prep (free tier)

_QUIVER_URL = "https://api.quiverquant.com/beta/bulk/congresstrading"
_FMP_BASE   = "https://financialmodelingprep.com/stable"

DIAS_ES  = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun",
            "jul", "ago", "sep", "oct", "nov", "dic"]

# Fuentes primarias y fallback para cada cámara
# housestockwatcher / senatestockwatcher son los agregadores más completos.
# Si el DNS falla desde el datacenter de Render, intentamos las alternativas.
_HOUSE_URLS = [
    "https://housestockwatcher.com/api",
    # S3 virtual-hosted style (formato moderno AWS)
    "https://house-stock-watcher-data.s3.us-east-2.amazonaws.com/data/all_transactions.json",
    # S3 path style (legacy, por si el bucket no admite virtual-hosted)
    "https://s3.us-east-2.amazonaws.com/house-stock-watcher-data/data/all_transactions.json",
]
_SENATE_URLS = [
    "https://senatestockwatcher.com/api",
    # S3 virtual-hosted style
    "https://senate-stock-watcher-data.s3.us-east-2.amazonaws.com/data/all_transactions.json",
    # S3 path style
    "https://s3.us-east-2.amazonaws.com/senate-stock-watcher-data/data/all_transactions.json",
]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; InvestX-Bot/1.0)",
    "Accept":     "application/json",
}

# Cargo y comité principal de los políticos más activos en bolsa.
# Clave: nombre en minúsculas tal como viene en la API.
# "role" = cargo corto | "committee" = comité más relevante para inversión
_CONGRESS_INFO: Dict[str, Dict[str, str]] = {
    # Cámara de Representantes
    "nancy pelosi":          {"role": "Representante",  "committee": "Expresidenta Cámara"},
    "paul pelosi":           {"role": "Cónyuge",        "committee": "— (esposo de N. Pelosi)"},
    "dan crenshaw":          {"role": "Representante",  "committee": "C. Seguridad Nacional"},
    "josh gottheimer":       {"role": "Representante",  "committee": "C. Servicios Financieros"},
    "brian higgins":         {"role": "Representante",  "committee": "C. Medios y Arbitrios"},
    "michael mccaul":        {"role": "Representante",  "committee": "C. Asuntos Exteriores"},
    "ro khanna":             {"role": "Representante",  "committee": "C. Fuerzas Armadas / C&T"},
    "marjorie taylor greene":{"role": "Representante",  "committee": "C. Supervisión"},
    "pete sessions":         {"role": "Representante",  "committee": "C. Reglas"},
    "greg gianforte":        {"role": "Representante",  "committee": "C. Recursos Naturales"},
    "chip roy":              {"role": "Representante",  "committee": "C. Presupuestos"},
    "french hill":           {"role": "Representante",  "committee": "Pdte. C. Servicios Financieros"},
    "bill huizenga":         {"role": "Representante",  "committee": "C. Servicios Financieros"},
    "tim walberg":           {"role": "Representante",  "committee": "C. Trabajo y Educación"},
    # Senado
    "tommy tuberville":      {"role": "Senador",        "committee": "C. Fuerzas Armadas / Agricultura"},
    "richard burr":          {"role": "Ex-Senador",     "committee": "Ex-pdte. C. Inteligencia"},
    "kelly loeffler":        {"role": "Ex-Senadora",    "committee": "Ex-C. Sanidad / Agricultura"},
    "david perdue":          {"role": "Ex-Senador",     "committee": "Ex-C. Banca"},
    "mark warner":           {"role": "Senador",        "committee": "C. Inteligencia"},
    "ron wyden":             {"role": "Senador",        "committee": "C. Finanzas"},
    "john hoeven":           {"role": "Senador",        "committee": "C. Agricultura / Apropiaciones"},
    "shelley moore capito":  {"role": "Senadora",       "committee": "C. Apropiaciones"},
    "pat toomey":            {"role": "Ex-Senador",     "committee": "Ex-C. Banca"},
}

def _get_role(name: str, chamber: str) -> str:
    """Devuelve el cargo y comité formateado, o un genérico según la cámara."""
    info = _CONGRESS_INFO.get(name.lower())
    if info:
        return f"{info['role']} · {info['committee']}"
    return "Representante" if chamber == "house" else "Senador/a"


# ─────────────────────────────────────────────────────────────────────────────
# Estado anti-dup
# ─────────────────────────────────────────────────────────────────────────────

def _load_state() -> Dict[str, Any]:
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


def _already_sent_today(d: date) -> bool:
    return _load_state().get("sent_date") == d.isoformat()


def _get_sent_keys() -> set:
    return set(tuple(k) for k in _load_state().get("sent_keys", []))


def _mark_sent(d: date, new_keys: List[tuple]) -> None:
    st = _load_state()
    existing = set(tuple(k) for k in st.get("sent_keys", []))
    existing.update(new_keys)
    st["sent_keys"] = [list(k) for k in list(existing)[-500:]]
    st["sent_date"] = d.isoformat()
    st["sent_at"]   = datetime.now(TZ).isoformat()
    _save_state(st)


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades de formato
# ─────────────────────────────────────────────────────────────────────────────

def _parse_amount_min(amount_str: str) -> int:
    """Devuelve el límite inferior del rango de importe."""
    if not amount_str:
        return 0
    s = amount_str.replace(",", "").replace("$", "").strip()
    if any(w in s.lower() for w in ("over", ">")):
        return 1_000_001
    parts = s.replace("–", "-").replace(" - ", "-").split("-")
    try:
        return int(float(parts[0].strip()))
    except (ValueError, IndexError):
        return 0


def _format_amount(amount_str: str) -> str:
    """'$50,001 - $100,000' → '$50K–$100K'   |   'Over $1,000,000' → '>$1M'."""
    if not amount_str:
        return "?"
    s = amount_str.strip()
    if any(w in s.lower() for w in ("over", ">")):
        return ">$1M"

    def _abbrev(v: str) -> str:
        v = v.strip().replace("$", "").replace(",", "")
        try:
            n = int(float(v))
            if n >= 1_000_000:
                return f"${n // 1_000_000}M"
            if n >= 1_000:
                return f"${n // 1_000}K"
            return f"${n}"
        except Exception:
            return v

    parts = s.replace("–", " - ").split(" - ")
    if len(parts) == 2:
        return f"{_abbrev(parts[0])}–{_abbrev(parts[1])}"
    return _abbrev(parts[0])


def _fmt_date(d: date) -> str:
    return f"{DIAS_ES[d.weekday()]} {d.day} {MESES_ES[d.month - 1]}"


def _trade_type_es(t: str) -> Tuple[str, str]:
    """Devuelve (icono, texto) en español."""
    tl = (t or "").lower()
    if "purchase" in tl or "buy" in tl:
        return "🟢", "Compra"
    if "sale" in tl or "sell" in tl:
        return "🔴", "Venta"
    if "exchange" in tl:
        return "🔄", "Canje"
    return "⚪", t.capitalize()


def _trade_key(name: str, ticker: str, t_type: str, tx_date: str) -> tuple:
    return (name.upper(), (ticker or "").upper(), (t_type or "").lower(), tx_date)


# ─────────────────────────────────────────────────────────────────────────────
# Fetch APIs
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_item_quiver(item: Dict, disc_from: date, disc_to: date) -> Optional[Dict]:
    """Convierte un registro QuiverQuant al formato interno. None si no encaja."""
    # QuiverQuant: Date = transaction_date, ReportDate = disclosure_date
    disc_str = (item.get("ReportDate") or item.get("report_date") or "").strip()
    try:
        disc_date = date.fromisoformat(disc_str[:10])
    except Exception:
        return None
    if not (disc_from <= disc_date <= disc_to):
        return None

    tx_str = (item.get("Date") or item.get("date") or disc_str).strip()
    try:
        tx_date = date.fromisoformat(tx_str[:10])
    except Exception:
        tx_date = disc_date

    amount = (item.get("Range") or item.get("range") or item.get("Amount") or "").strip()
    if _parse_amount_min(amount) < MIN_AMOUNT:
        return None

    ticker = (item.get("Ticker") or item.get("ticker") or "").strip().upper()
    if not ticker or ticker in ("--", "N/A", ""):
        return None

    chamber_raw = (item.get("Chamber") or item.get("chamber") or "House").lower()
    chamber = "senate" if "senate" in chamber_raw else "house"

    name_raw = (
        item.get("Representative") or item.get("representative")
        or item.get("Senator")     or item.get("senator")
        or item.get("Name")        or item.get("name") or ""
    ).strip()

    return {
        "chamber":    chamber,
        "name":       name_raw,
        "party":      (item.get("Party") or item.get("party") or "").strip(),
        "state":      (item.get("State") or item.get("state") or "").strip(),
        "ticker":     ticker,
        "asset":      (item.get("Asset") or item.get("asset")
                       or item.get("Description") or item.get("description") or "").strip(),
        "type":       (item.get("Transaction") or item.get("transaction") or "").strip(),
        "amount":     amount,
        "amount_min": _parse_amount_min(amount),
        "tx_date":    tx_date,
        "disc_date":  disc_date,
    }


def _fetch_quiverquant(disc_from: date, disc_to: date) -> Optional[List[Dict]]:
    """
    Descarga todas las operaciones desde QuiverQuant y filtra por ventana.
    Devuelve lista (puede ser vacía) si la llamada tuvo éxito, None si falló.
    """
    if not QUIVER_TOKEN:
        print("[congress] QUIVER_TOKEN no configurado; saltando QuiverQuant.")
        return None

    headers = {
        "Authorization": f"Bearer {QUIVER_TOKEN}",
        "Accept":        "application/json",
        "User-Agent":    "InvestX-Bot/1.0",
    }
    try:
        resp = requests.get(_QUIVER_URL, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
        items = raw if isinstance(raw, list) else raw.get("data", [])
        print(f"[congress] QuiverQuant OK: {len(items)} registros descargados.")
    except Exception as e:
        print(f"[congress] QuiverQuant fallo: {e}")
        return None

    results = []
    for item in items:
        norm = _normalise_item_quiver(item, disc_from, disc_to)
        if norm:
            results.append(norm)
    return results


def _fetch_json_fallback(urls: List[str], label: str):
    """Intenta cada URL en orden hasta obtener JSON válido (fuentes fallback)."""
    for url in urls:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=HTTP_TIMEOUT)
            print(f"[congress] {label} HTTP {resp.status_code} desde {url} "
                  f"(Content-Type: {resp.headers.get('Content-Type','?')}, "
                  f"bytes: {len(resp.content)})")
            resp.raise_for_status()
            if not resp.content:
                print(f"[congress] {label} respuesta vacía en {url}")
                continue
            raw = resp.json()
            print(f"[congress] {label} OK: {len(raw) if isinstance(raw, list) else '?'} registros")
            return raw if isinstance(raw, list) else raw.get("data", raw)
        except Exception as e:
            print(f"[congress] {label} fallo {url}: {e}")
    return None


def _parse_fallback_item(item: Dict, chamber: str,
                         disc_from: date, disc_to: date) -> Optional[Dict]:
    """Convierte un item de housestockwatcher/senatestockwatcher al formato interno."""
    disc_str = (item.get("disclosure_date") or item.get("disclosureDate") or "").strip()
    try:
        disc_date = date.fromisoformat(disc_str[:10])
    except Exception:
        return None
    if not (disc_from <= disc_date <= disc_to):
        return None

    tx_str = (item.get("transaction_date") or item.get("transactionDate") or disc_str).strip()
    try:
        tx_date = date.fromisoformat(tx_str[:10])
    except Exception:
        tx_date = disc_date

    amount = item.get("amount") or item.get("transactionAmount") or ""
    if _parse_amount_min(amount) < MIN_AMOUNT:
        return None

    ticker = (item.get("ticker") or "").strip().upper()
    if not ticker or ticker in ("--", "N/A", ""):
        return None

    if chamber == "house":
        name_raw = (item.get("representative") or item.get("name") or "").strip()
    else:
        name_raw = (item.get("senator") or item.get("name") or "").strip()

    return {
        "chamber":    chamber,
        "name":       name_raw,
        "party":      (item.get("party") or "").strip(),
        "state":      (item.get("state") or "").strip(),
        "ticker":     ticker,
        "asset":      (item.get("asset_description") or item.get("assetDescription") or "").strip(),
        "type":       (item.get("type") or item.get("transactionType") or "").strip(),
        "amount":     amount,
        "amount_min": _parse_amount_min(amount),
        "tx_date":    tx_date,
        "disc_date":  disc_date,
    }


def _fetch_fallback(disc_from: date, disc_to: date) -> List[Dict]:
    """Fuente fallback: housestockwatcher + senatestockwatcher (si QuiverQuant falla)."""
    results = []

    house_items = _fetch_json_fallback(_HOUSE_URLS, "Cámara")
    if house_items is None:
        print("[congress] Cámara: todos los endpoints fallaron.")
    else:
        for item in house_items:
            norm = _parse_fallback_item(item, "house", disc_from, disc_to)
            if norm:
                results.append(norm)

    senate_items = _fetch_json_fallback(_SENATE_URLS, "Senado")
    if senate_items is None:
        print("[congress] Senado: todos los endpoints fallaron.")
    else:
        for item in senate_items:
            norm = _parse_fallback_item(item, "senate", disc_from, disc_to)
            if norm:
                results.append(norm)

    return results


def _normalise_fmp_item(item: Dict, chamber: str,
                         disc_from: date, disc_to: date) -> Optional[Dict]:
    """Convierte un registro FMP (senate-latest / house-latest) al formato interno."""
    disc_str = (item.get("disclosureDate") or "").strip()
    try:
        disc_date = date.fromisoformat(disc_str[:10])
    except Exception:
        return None
    if not (disc_from <= disc_date <= disc_to):
        return None

    tx_str = (item.get("transactionDate") or disc_str).strip()
    try:
        tx_date = date.fromisoformat(tx_str[:10])
    except Exception:
        tx_date = disc_date

    amount = (item.get("amount") or "").strip()
    if _parse_amount_min(amount) < MIN_AMOUNT:
        return None

    ticker = (item.get("symbol") or item.get("ticker") or "").strip().upper()
    if not ticker or ticker in ("--", "N/A", ""):
        return None

    first = (item.get("firstName") or "").strip()
    last  = (item.get("lastName")  or "").strip()
    name  = f"{first} {last}".strip() or (item.get("name") or "").strip()

    return {
        "chamber":    chamber,
        "name":       name,
        "party":      (item.get("party") or "").strip(),
        "state":      (item.get("state") or item.get("stateLong") or "").strip(),
        "ticker":     ticker,
        "asset":      (item.get("assetDescription") or item.get("asset") or "").strip(),
        "type":       (item.get("type") or item.get("transactionType") or "").strip(),
        "amount":     amount,
        "amount_min": _parse_amount_min(amount),
        "tx_date":    tx_date,
        "disc_date":  disc_date,
    }


def _fetch_fmp(disc_from: date, disc_to: date) -> Optional[List[Dict]]:
    """
    Descarga operaciones desde FMP (Financial Modeling Prep).
    Free tier: 250 req/día, sin Cloudflare, accesible desde Render.
    Devuelve lista si al menos un endpoint responde, None si ambos fallan.
    """
    if not FMP_API_KEY:
        print("[congress] FMP_API_KEY no configurado; saltando FMP.")
        return None

    results  = []
    any_ok   = False
    chambers = [("senate", "senate-latest"), ("house", "house-latest")]

    for chamber, path in chambers:
        url = f"{_FMP_BASE}/{path}?apikey={FMP_API_KEY}&limit=300"
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            raw   = resp.json()
            items = raw if isinstance(raw, list) else (
                raw.get("data") or raw.get("congressionalTrading") or []
            )
            print(f"[congress] FMP {chamber}: {len(items)} registros descargados.")
            any_ok = True
            for item in items:
                norm = _normalise_fmp_item(item, chamber, disc_from, disc_to)
                if norm:
                    results.append(norm)
        except Exception as e:
            print(f"[congress] FMP {chamber} fallo: {e}")

    return results if any_ok else None


def _fetch_all_trades(disc_from: date, disc_to: date) -> List[Dict]:
    """
    Cadena de fuentes: QuiverQuant → FMP → fallback (housestockwatcher S3).
    Devuelve lista unificada de operaciones filtradas por ventana y umbral.
    """
    # 1. QuiverQuant (paid, si está configurado)
    trades = _fetch_quiverquant(disc_from, disc_to)
    if trades is not None:
        print(f"[congress] QuiverQuant: {len(trades)} ops en ventana.")
        return trades

    # 2. FMP — free tier (FMP_API_KEY env var)
    trades = _fetch_fmp(disc_from, disc_to)
    if trades is not None:
        print(f"[congress] FMP: {len(trades)} ops en ventana (umbral ${MIN_AMOUNT:,}).")
        return trades

    # 3. Fallback histórico (housestockwatcher S3 — probablemente caído)
    print("[congress] FMP no disponible. Usando fuentes fallback legacy...")
    trades = _fetch_fallback(disc_from, disc_to)
    print(f"[congress] Fallback: {len(trades)} ops en ventana.")
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# Mensaje
# ─────────────────────────────────────────────────────────────────────────────

def _build_message(
    trades: List[Dict],
    disc_from: date,
    disc_to: date,
) -> str:
    if disc_from == disc_to:
        disc_str = f"{disc_from.day} {MESES_ES[disc_from.month - 1]}"
    else:
        disc_str = f"{disc_from.day}–{disc_to.day} {MESES_ES[disc_to.month - 1]}"
        if disc_from.month != disc_to.month:
            disc_str = (f"{disc_from.day} {MESES_ES[disc_from.month - 1]}"
                        f"–{disc_to.day} {MESES_ES[disc_to.month - 1]}")

    header = (
        f"🏛️ *Congresistas USA — Operaciones declaradas*\n"
        f"_Declaraciones del {disc_str} · operaciones con hasta 45 días de retraso_\n"
        f"_📋 STOCK Act: plazo legal de 45 días para declarar_"
    )

    if not trades:
        return header + f"\n\nSin operaciones nuevas ≥ {_format_amount(str(MIN_AMOUNT))} declaradas en esta ventana."

    house_trades  = sorted([t for t in trades if t["chamber"] == "house"],
                           key=lambda x: -x["amount_min"])
    senate_trades = sorted([t for t in trades if t["chamber"] == "senate"],
                           key=lambda x: -x["amount_min"])

    n_buys  = sum(1 for t in trades if "purchase" in t["type"].lower() or "buy" in t["type"].lower())
    n_sells = sum(1 for t in trades if "sale" in t["type"].lower() or "sell" in t["type"].lower())
    summary = f"\n_{n_buys} compras · {n_sells} ventas · umbral {_format_amount(str(MIN_AMOUNT))}_\n"

    lines = [header, summary]

    def _trade_line(t: Dict) -> str:
        icon, action = _trade_type_es(t["type"])
        name    = t["name"] or "?"
        party   = t["party"] or "?"
        state   = t["state"] or "?"
        role    = _get_role(name, t["chamber"])
        ticker  = t["ticker"]
        asset   = t["asset"]
        if len(asset) > 28:
            asset = asset[:26] + "…"
        ctx     = f"{ticker} ({asset})" if asset and asset.lower() not in (ticker.lower(), "") else ticker
        amt     = _format_amount(t["amount"])
        tx_day  = _fmt_date(t["tx_date"])
        return (
            f"{icon} *{name}* ({party} · {state})\n"
            f"   _{role}_\n"
            f"   {ctx}  ·  {action}  *{amt}*  _op: {tx_day}_"
        )

    if house_trades:
        lines.append("🏠 *CÁMARA DE REPRESENTANTES*\n")
        for t in house_trades[:12]:
            lines.append(_trade_line(t))
        if len(house_trades) > 12:
            lines.append(f"_… y {len(house_trades) - 12} más_")
        lines.append("")

    if senate_trades:
        lines.append("🏛 *SENADO*\n")
        for t in senate_trades[:8]:
            lines.append(_trade_line(t))
        if len(senate_trades) > 8:
            lines.append(f"_… y {len(senate_trades) - 8} más_")
        lines.append("")

    return "\n".join(lines).strip()


def _ai_interpretation(trades: List[Dict], disc_from: date, disc_to: date) -> str:
    if not trades:
        return ""

    def _line(t: Dict) -> str:
        icon, action = _trade_type_es(t["type"])
        return (
            f"- {action} {_format_amount(t['amount'])}"
            f" | {t['name']} ({t['party']}·{t['chamber']})"
            f" | {t['ticker']} — {(t['asset'] or '')[:30]}"
            f" | op: {t['tx_date']}"
        )

    compact = "\n".join(_line(t) for t in trades[:20])
    disc_str = f"{disc_from}–{disc_to}"

    system = (
        "Eres un analista político-financiero experto en el mercado USA. "
        "Escribes en español conciso para traders profesionales. "
        "No menciones IA ni modelos."
    )
    user = (
        f"Operaciones declaradas por congresistas USA ({disc_str}):\n\n"
        f"{compact}\n\n"
        "Redacta un análisis breve de 3–4 frases:\n"
        "1) Balance neto (más compras o ventas) y sectores protagonistas.\n"
        "2) Si hay clustering por partido o posible vinculación con comités "
        "(ej: senador de energía comprando petrolíferas).\n"
        "3) Nombra 1–2 operaciones más llamativas y por qué.\n"
        "4) Cierra con 'Lectura InvestX:' resumiendo si hay señal accionable."
    )

    try:
        return (call_gpt_mini(system, user, max_tokens=280) or "").strip()
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint público
# ─────────────────────────────────────────────────────────────────────────────

def run_congressional_trades(force: bool = False) -> None:
    now   = datetime.now(TZ)
    today = now.date()

    if not force and _already_sent_today(today):
        print("[congress] Ya enviado hoy. Skipping.")
        return

    # Marcar al inicio para evitar doble ejecución concurrente
    _mark_sent(today, [])

    # Ventana de disclosure: últimos 3 días (los lunes, 5 para cubrir el fin de semana)
    # Override via env (útil en entornos de test con fecha de sistema incorrecta)
    _env_from = os.getenv("CONGRESS_DATE_FROM", "").strip()
    _env_to   = os.getenv("CONGRESS_DATE_TO",   "").strip()
    if _env_from and _env_to:
        try:
            disc_from = date.fromisoformat(_env_from)
            disc_to   = date.fromisoformat(_env_to)
            print(f"[congress] Fechas forzadas por env: {disc_from} – {disc_to}")
        except ValueError:
            _env_from = _env_to = ""

    if not (_env_from and _env_to):
        lookback  = 5 if today.weekday() == 0 else 3
        disc_to   = today
        disc_from = today - timedelta(days=lookback)

    print(f"[congress] Buscando declaraciones del {disc_from} al {disc_to} "
          f"(umbral ${MIN_AMOUNT:,})...")

    all_trades = _fetch_all_trades(disc_from, disc_to)
    print(f"[congress] {len(all_trades)} operaciones en ventana total.")

    # Filtrar ya enviados
    sent_keys = _get_sent_keys()
    new_trades = [
        t for t in all_trades
        if _trade_key(t["name"], t["ticker"], t["type"], t["tx_date"].isoformat()) not in sent_keys
    ]
    print(f"[congress] {len(new_trades)} operaciones nuevas.")

    if not new_trades:
        print("[congress] Sin declaraciones nuevas. Nada enviado.")
        return

    # Ordenar por importe descendente
    new_trades.sort(key=lambda x: -x["amount_min"])

    msg    = _build_message(new_trades, disc_from, disc_to)
    interp = _ai_interpretation(new_trades, disc_from, disc_to)
    if interp:
        msg += f"\n\n📌 *Lectura InvestX*\n{interp}"

    send_telegram_message(msg)
    keys = [_trade_key(t["name"], t["ticker"], t["type"], t["tx_date"].isoformat())
            for t in new_trades]
    _mark_sent(today, keys)
    print(f"[congress] OK enviado {len(new_trades)} operaciones (force={force}).")
