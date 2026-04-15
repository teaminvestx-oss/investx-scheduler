# === congressional_trades.py ===
# InvestX — Operaciones bursátiles de congresistas USA
#
# Fuente:  housestockwatcher.com/api  (Cámara de Representantes)
#          senatestockwatcher.com/api  (Senado)
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

DIAS_ES  = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun",
            "jul", "ago", "sep", "oct", "nov", "dic"]

_HOUSE_URL  = "https://housestockwatcher.com/api"
_SENATE_URL = "https://senatestockwatcher.com/api"

_HEADERS = {
    "User-Agent": "InvestX-Bot/1.0 bot@investx.io",
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

def _fetch_house(disc_from: date, disc_to: date) -> List[Dict]:
    """Descarga operaciones de la Cámara con disclosure_date en el rango."""
    try:
        resp = requests.get(_HOUSE_URL, headers=_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
        # La API devuelve una lista directa o {"data": [...]}
        items = raw if isinstance(raw, list) else raw.get("data", [])
    except Exception as e:
        print(f"[congress] Error Cámara: {e}")
        return []

    results = []
    for item in items:
        disc_str = (item.get("disclosure_date") or item.get("disclosureDate") or "").strip()
        try:
            disc_date = date.fromisoformat(disc_str[:10])
        except Exception:
            continue
        if not (disc_from <= disc_date <= disc_to):
            continue

        tx_str = (item.get("transaction_date") or item.get("transactionDate") or disc_str).strip()
        try:
            tx_date = date.fromisoformat(tx_str[:10])
        except Exception:
            tx_date = disc_date

        amount = item.get("amount") or item.get("transactionAmount") or ""
        if _parse_amount_min(amount) < MIN_AMOUNT:
            continue

        ticker = (item.get("ticker") or "").strip().upper()
        if not ticker or ticker in ("--", "N/A", ""):
            continue

        results.append({
            "chamber":    "house",
            "name":       (item.get("representative") or item.get("name") or "").strip(),
            "party":      (item.get("party") or "").strip(),
            "state":      (item.get("state") or "").strip(),
            "ticker":     ticker,
            "asset":      (item.get("asset_description") or item.get("assetDescription") or "").strip(),
            "type":       (item.get("type") or item.get("transactionType") or "").strip(),
            "amount":     amount,
            "amount_min": _parse_amount_min(amount),
            "tx_date":    tx_date,
            "disc_date":  disc_date,
        })
    return results


def _fetch_senate(disc_from: date, disc_to: date) -> List[Dict]:
    """Descarga operaciones del Senado con disclosure_date en el rango."""
    try:
        resp = requests.get(_SENATE_URL, headers=_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
        items = raw if isinstance(raw, list) else raw.get("data", [])
    except Exception as e:
        print(f"[congress] Error Senado: {e}")
        return []

    results = []
    for item in items:
        disc_str = (item.get("disclosure_date") or item.get("disclosureDate") or "").strip()
        try:
            disc_date = date.fromisoformat(disc_str[:10])
        except Exception:
            continue
        if not (disc_from <= disc_date <= disc_to):
            continue

        tx_str = (item.get("transaction_date") or item.get("transactionDate") or disc_str).strip()
        try:
            tx_date = date.fromisoformat(tx_str[:10])
        except Exception:
            tx_date = disc_date

        amount = item.get("amount") or item.get("transactionAmount") or ""
        if _parse_amount_min(amount) < MIN_AMOUNT:
            continue

        ticker = (item.get("ticker") or "").strip().upper()
        if not ticker or ticker in ("--", "N/A", ""):
            continue

        results.append({
            "chamber":    "senate",
            "name":       (item.get("senator") or item.get("name") or "").strip(),
            "party":      (item.get("party") or "").strip(),
            "state":      (item.get("state") or "").strip(),
            "ticker":     ticker,
            "asset":      (item.get("asset_description") or item.get("assetDescription") or "").strip(),
            "type":       (item.get("type") or item.get("transactionType") or "").strip(),
            "amount":     amount,
            "amount_min": _parse_amount_min(amount),
            "tx_date":    tx_date,
            "disc_date":  disc_date,
        })
    return results


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
    lookback  = 5 if today.weekday() == 0 else 3
    disc_to   = today
    disc_from = today - timedelta(days=lookback)

    print(f"[congress] Buscando declaraciones del {disc_from} al {disc_to} "
          f"(umbral ${MIN_AMOUNT:,})...")

    house_raw  = _fetch_house(disc_from, disc_to)
    senate_raw = _fetch_senate(disc_from, disc_to)
    all_trades = house_raw + senate_raw

    print(f"[congress] {len(house_raw)} Cámara + {len(senate_raw)} Senado = {len(all_trades)} total.")

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
