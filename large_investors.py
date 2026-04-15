# === large_investors.py ===
# InvestX — Grandes inversores institucionales (SEC EDGAR 13D/13G)
#
# SC 13D  → inversor activista cruza el 5% (señal fuerte, busca cambios)
# SC 13G  → inversor pasivo cruza el 5% (Berkshire acumulando, fondos índice...)
# SC 13D/A, SC 13G/A → enmiendas (posición sube o baja)
#
# Plazo legal: 10 días naturales desde el cruce del umbral del 5%.
#
# Estrategia:
#  - Busca en EDGAR EFTS todos los 13D/13G de los últimos 3 días (5 lunes)
#  - Prioriza: (a) inversores conocidos siempre, (b) cualquier SC 13D nuevo
#  - Extrae empresa objetivo parseando la cabecera SGML del filing
#  - Anti-dup por clave (inversor, empresa, tipo, fecha)

from __future__ import annotations

import json
import os
import re
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from utils import call_gpt_mini, send_telegram_message

TZ         = ZoneInfo("Europe/Madrid")
STATE_FILE = "large_investors_state.json"
HTTP_TIMEOUT = 15

DIAS_ES  = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun",
            "jul", "ago", "sep", "oct", "nov", "dic"]

_SEC_HEADERS = {
    "User-Agent": "InvestX-Bot/1.0 bot@investx.io",
    "Accept-Encoding": "gzip, deflate",
}

_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

# ─────────────────────────────────────────────────────────────────────────────
# Inversores conocidos
# Se detectan por fragmento de nombre en el campo entity_name de EDGAR.
# ─────────────────────────────────────────────────────────────────────────────
_KNOWN_INVESTORS: Dict[str, str] = {
    # fragmento (mayúsculas) → etiqueta para el mensaje
    "BERKSHIRE HATHAWAY":  "Berkshire Hathaway (Warren Buffett)",
    "PERSHING SQUARE":     "Pershing Square (Bill Ackman)",
    "ICAHN":               "Carl Icahn",
    "ELLIOTT":             "Elliott Management (Paul Singer)",
    "THIRD POINT":         "Third Point (Dan Loeb)",
    "SCION":               "Scion Asset Mgmt. (Michael Burry)",
    "GREENLIGHT":          "Greenlight Capital (David Einhorn)",
    "VALUEACT":            "ValueAct Capital",
    "STARBOARD":           "Starboard Value (Jeff Smith)",
    "TRIAN":               "Trian Partners (Nelson Peltz)",
    "APPALOOSA":           "Appaloosa Mgmt. (David Tepper)",
    "BAUPOST":             "Baupost Group (Seth Klarman)",
    "TIGER GLOBAL":        "Tiger Global (Chase Coleman)",
    "DRUCKENMILLER":       "Duquesne (Stan Druckenmiller)",
    "SOROSFUND":           "Soros Fund Management",
    "TEPPER":              "Appaloosa (David Tepper)",
    "KLARMAN":             "Baupost Group (Seth Klarman)",
    "LOEB":                "Third Point (Dan Loeb)",
    "COOPERMAN":           "Omega Advisors (Leon Cooperman)",
    "PABRAI":              "Pabrai Investment Funds (Mohnish Pabrai)",
}


def _match_investor(entity_name: str) -> Optional[str]:
    """Devuelve la etiqueta del inversor si el nombre EDGAR coincide con alguno conocido."""
    upper = entity_name.upper()
    for key, label in _KNOWN_INVESTORS.items():
        if key in upper:
            return label
    return None


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
    st["sent_keys"] = [list(k) for k in list(existing)[-400:]]
    st["sent_date"] = d.isoformat()
    st["sent_at"]   = datetime.now(TZ).isoformat()
    _save_state(st)


# ─────────────────────────────────────────────────────────────────────────────
# Caché de CIK → ticker (SEC company_tickers.json)
# ─────────────────────────────────────────────────────────────────────────────
_CIK_TICKER_CACHE: Dict[str, str] = {}
_CIK_NAME_CACHE:   Dict[str, str] = {}


def _load_cik_maps() -> None:
    global _CIK_TICKER_CACHE, _CIK_NAME_CACHE
    if _CIK_TICKER_CACHE:
        return
    try:
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_SEC_HEADERS, timeout=20,
        )
        r.raise_for_status()
        for entry in r.json().values():
            cik = str(entry["cik_str"])
            _CIK_TICKER_CACHE[cik] = entry["ticker"]
            _CIK_NAME_CACHE[cik]   = entry["title"]
    except Exception as e:
        print(f"[investors] No se pudo cargar CIK map: {e}")


def _cik_to_ticker(cik: str) -> Optional[str]:
    _load_cik_maps()
    return _CIK_TICKER_CACHE.get(cik.lstrip("0"))


def _cik_to_name(cik: str) -> Optional[str]:
    _load_cik_maps()
    return _CIK_NAME_CACHE.get(cik.lstrip("0"))


# ─────────────────────────────────────────────────────────────────────────────
# Parseo del filing: empresa objetivo + % del capital + acciones
# ─────────────────────────────────────────────────────────────────────────────

def _parse_filing(filer_cik: str, accession: str) -> Dict:
    """
    Descarga los primeros 12 KB del filing .txt y extrae:
      - CIK de la empresa objetivo (SUBJECT-COMPANY, cabecera SGML)
      - Porcentaje del capital (Item 11 del formulario 13D/13G)
      - Número de acciones (Item 9)
    Devuelve dict con claves: subject_cik, pct, shares (None si no se encuentran).
    """
    acc_clean = accession.replace("-", "")
    cik_clean = filer_cik.lstrip("0")
    url = (f"https://www.sec.gov/Archives/edgar/data/"
           f"{cik_clean}/{acc_clean}/{accession}.txt")
    result: Dict = {"subject_cik": None, "pct": None, "shares": None}
    try:
        resp = requests.get(url, headers=_SEC_HEADERS, stream=True, timeout=12)
        raw = b""
        for chunk in resp.iter_content(1024):
            raw += chunk
            if len(raw) >= 12288:   # 12 KB — suficiente para cabecera + portada del form
                break
        text = raw.decode("utf-8", errors="replace")

        # ── 1) CIK de la empresa objetivo (cabecera SGML) ────────────────────
        m = re.search(
            r"<SUBJECT-COMPANY>.*?<CIK>\s*(\d+)\s*</CIK>",
            text, re.DOTALL | re.IGNORECASE,
        )
        if m:
            result["subject_cik"] = m.group(1).lstrip("0") or m.group(1)
        else:
            # Formato antiguo (sin XML tags)
            m2 = re.search(
                r"SUBJECT COMPANY[\s\S]{0,200}?CENTRAL INDEX KEY[:\s]+(\d+)",
                text, re.IGNORECASE,
            )
            if m2:
                result["subject_cik"] = m2.group(1).lstrip("0") or m2.group(1)

        # ── 2) Porcentaje del capital (Item 11 / Row 11) ─────────────────────
        pct_patterns = [
            # Formato tabla: "11." seguido de % en la misma línea o la siguiente
            r'(?:^|\n)\s*11[\.\)]\s*[\s\S]{0,80}?(\d{1,3}\.?\d{0,3})\s*%',
            # Texto libre: "percent of class ... X%"
            r'percent\s+of\s+class[^%\n]{0,80}?(\d{1,3}\.?\d{0,3})\s*%',
        ]
        for pat in pct_patterns:
            mp = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
            if mp:
                try:
                    pct = float(mp.group(1))
                    if 0 < pct <= 100:
                        result["pct"] = pct
                        break
                except ValueError:
                    pass

        # ── 3) Número de acciones (Item 9 / Row 9) ───────────────────────────
        shares_patterns = [
            r'(?:^|\n)\s*9[\.\)]\s*[\s\S]{0,80}?([\d,]+)\s*(?:shares|acciones)',
            r'aggregate\s+amount[^0-9\n]{0,60}?([\d,]+)',
        ]
        for pat in shares_patterns:
            ms = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
            if ms:
                try:
                    result["shares"] = int(ms.group(1).replace(",", ""))
                    break
                except ValueError:
                    pass

    except Exception as e:
        print(f"[investors] Error parseando {accession}: {e}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Búsqueda EDGAR EFTS
# ─────────────────────────────────────────────────────────────────────────────

def _search_filings(date_from: date, date_to: date) -> List[Dict]:
    """
    Busca en EDGAR EFTS todos los 13D/13G presentados en el rango de fechas.
    Filtra: (a) inversores conocidos en cualquier tipo, (b) SC 13D nuevos de cualquier inversor.

    IMPORTANTE: requests urlencodea las comas en el param 'forms', convirtiéndolas
    en %2C. EDGAR EFTS espera comas literales como separador. Por eso construimos
    la URL manualmente para controlar el encoding exacto.
    """
    # Construir URL con comas literales y espacios como + pero slashes como %2F
    forms_raw   = "SC 13D,SC 13G,SC 13D/A,SC 13G/A"
    forms_enc   = forms_raw.replace(" ", "+").replace("/", "%2F")
    url = (
        f"{_EFTS_URL}"
        f"?q=&forms={forms_enc}"
        f"&dateRange=custom"
        f"&startdt={date_from.isoformat()}"
        f"&enddt={date_to.isoformat()}"
        f"&hits.hits.total=true"    # pedir total de resultados en respuesta
    )
    print(f"[investors] EFTS URL: {url}")
    try:
        resp = requests.get(url, headers=_SEC_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[investors] Error EFTS: {e}")
        return []

    total = data.get("hits", {}).get("total", {})
    hits  = data.get("hits", {}).get("hits", [])
    print(f"[investors] EFTS: {len(hits)} filings (total={total}) en {date_from}–{date_to}")

    results = []
    for hit in hits:
        src = hit.get("_source", {})
        entity  = src.get("entity_name", "")
        form    = src.get("form_type", "")
        filed   = src.get("file_date", "")
        period  = src.get("period_of_report", "")
        acc     = hit.get("_id", "")           # accession number con guiones

        investor_label = _match_investor(entity)
        is_new_13d     = form in ("SC 13D",)   # nueva posición activista

        if not investor_label and not is_new_13d:
            continue                            # no es relevante

        # CIK del filer = primera parte del accession number
        filer_cik = acc.split("-")[0].lstrip("0") if acc else ""

        results.append({
            "entity":         entity,
            "investor_label": investor_label or entity,
            "form":           form,
            "filed":          filed,
            "period":         period,
            "accession":      acc,
            "filer_cik":      filer_cik,
            "known":          bool(investor_label),
        })
    return results


def _enrich_with_subject(filings: List[Dict]) -> List[Dict]:
    """Añade ticker, nombre, % del capital y acciones a cada filing."""
    import time
    _load_cik_maps()
    enriched = []
    for f in filings:
        info = _parse_filing(f["filer_cik"], f["accession"])
        cik  = info["subject_cik"]
        if cik:
            f["ticker"]  = _cik_to_ticker(cik) or "?"
            f["company"] = _cik_to_name(cik)   or cik
        else:
            f["ticker"]  = "?"
            f["company"] = "?"
        f["pct"]    = info["pct"]     # float o None
        f["shares"] = info["shares"]  # int o None
        enriched.append(f)
        time.sleep(0.12)   # respeta límite SEC 10 req/s
    return enriched


# ─────────────────────────────────────────────────────────────────────────────
# Mensaje
# ─────────────────────────────────────────────────────────────────────────────

def _action_label(form: str, pct: Optional[float]) -> str:
    """
    Devuelve icono + texto de acción.

    Lógica:
      13D / 13G (sin /A) → apertura de posición (cruce del 5%)
      13D/A / 13G/A      → actualización; si pct~0 es cierre, si no es ajuste
      ⚡ = activista (busca influir en la dirección)
      📦 = posición pasiva (acumulación estratégica sin intención activista)
    """
    is_amendment = form.endswith("/A")
    is_activist  = "13D" in form

    icon = "⚡" if is_activist else "📦"

    if not is_amendment:
        kind = "Abre posición activista" if is_activist else "Abre posición"
    else:
        if pct is not None and pct < 0.5:
            kind = "Cierra posición"
        else:
            kind = "Actualiza posición activista" if is_activist else "Actualiza posición"

    return icon, kind


def _fmt_date(d_str: str) -> str:
    try:
        d = date.fromisoformat(d_str[:10])
        return f"{DIAS_ES[d.weekday()]} {d.day} {MESES_ES[d.month - 1]}"
    except Exception:
        return d_str


def _trade_key(f: Dict) -> tuple:
    return (f["entity"].upper(), f["ticker"], f["form"], f["period"][:10])


def _build_message(filings: List[Dict], date_from: date, date_to: date) -> str:
    if date_from == date_to:
        rng = f"{date_from.day} {MESES_ES[date_from.month - 1]}"
    else:
        rng = f"{date_from.day}–{date_to.day} {MESES_ES[date_to.month - 1]}"
        if date_from.month != date_to.month:
            rng = (f"{date_from.day} {MESES_ES[date_from.month - 1]}"
                   f"–{date_to.day} {MESES_ES[date_to.month - 1]}")

    header = (
        f"🐳 *Grandes inversores — Nuevas posiciones SEC*\n"
        f"_Filings del {rng} · plazo legal 10 días desde cruce del 5%_\n"
        f"_⚡ = activista (13D)  ·  📦 = pasivo (13G)_"
    )

    if not filings:
        return header + "\n\nSin filings 13D/13G relevantes en esta ventana."

    n_13d = sum(1 for f in filings if "13D" in f["form"])
    n_13g = sum(1 for f in filings if "13G" in f["form"])
    summary = f"\n_{n_13d} activistas · {n_13g} pasivos_\n"
    lines = [header, summary]

    # Primero activistas (13D), luego pasivos (13G)
    ordered = (
        sorted([f for f in filings if "13D" in f["form"]], key=lambda x: x["filed"], reverse=True) +
        sorted([f for f in filings if "13G" in f["form"]], key=lambda x: x["filed"], reverse=True)
    )

    for f in ordered:
        icon, kind = _action_label(f["form"], f.get("pct"))
        investor   = f["investor_label"]
        ticker     = f["ticker"]
        company    = f["company"]
        if len(company) > 28:
            company = company[:26] + "…"
        ctx        = f"{ticker} ({company})" if company and company != "?" else ticker
        filed_day  = _fmt_date(f["filed"])
        period_day = _fmt_date(f["period"]) if f["period"] else "?"

        # Línea de posición: % y acciones si disponibles
        pct    = f.get("pct")
        shares = f.get("shares")
        if pct is not None:
            pos_str = f"*{pct:.1f}% del capital*"
            if shares:
                s_fmt = f"{shares:,}".replace(",", ".")
                pos_str += f" ({s_fmt} acc.)"
        elif shares:
            s_fmt   = f"{shares:,}".replace(",", ".")
            pos_str = f"*{s_fmt} acciones*"
        else:
            pos_str = "_posición no disponible_"

        lines.append(
            f"{icon} *{investor}*\n"
            f"   {ctx}  ·  {kind}\n"
            f"   Posición: {pos_str}  ·  _op: {period_day}_"
        )
        lines.append("")

    return "\n".join(lines).strip()


def _ai_interpretation(filings: List[Dict], date_from: date, date_to: date) -> str:
    if not filings:
        return ""

    lines = []
    for f in filings[:15]:
        lines.append(
            f"- {f['form']} | {f['investor_label']} | {f['ticker']} ({f['company'][:30]})"
            f" | filed {f['filed']}"
        )
    compact = "\n".join(lines)
    rng = f"{date_from}–{date_to}"

    system = (
        "Eres un analista institucional experto en movimientos de grandes fondos. "
        "Escribes en español conciso para traders profesionales. "
        "No menciones IA ni modelos."
    )
    user = (
        f"Nuevos filings 13D/13G ante la SEC ({rng}):\n\n"
        f"{compact}\n\n"
        "Redacta un análisis de 3–4 frases:\n"
        "1) Cuáles son las posiciones activistas más relevantes y qué pueden buscar.\n"
        "2) Si hay clustering sectorial (varios fondos entrando en el mismo sector).\n"
        "3) Contexto: ¿por qué puede ser interesante la empresa objetivo ahora?\n"
        "4) Cierra con 'Lectura InvestX:' con la señal más accionable."
    )

    try:
        return (call_gpt_mini(system, user, max_tokens=280) or "").strip()
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint público
# ─────────────────────────────────────────────────────────────────────────────

def run_large_investors(force: bool = False) -> None:
    now   = datetime.now(TZ)
    today = now.date()

    if not force and _already_sent_today(today):
        print("[investors] Ya enviado hoy. Skipping.")
        return

    # Marcar inmediatamente para evitar doble ejecución concurrente
    _mark_sent(today, [])

    lookback  = 5 if today.weekday() == 0 else 3
    date_to   = today
    date_from = today - timedelta(days=lookback)

    print(f"[investors] Buscando 13D/13G del {date_from} al {date_to}...")

    raw_filings = _search_filings(date_from, date_to)
    print(f"[investors] {len(raw_filings)} filings relevantes antes de enriquecer.")

    if not raw_filings:
        print("[investors] Sin filings relevantes. Nada enviado.")
        return

    filings = _enrich_with_subject(raw_filings)

    # Filtrar ya enviados
    sent_keys = _get_sent_keys()
    new_filings = [f for f in filings if _trade_key(f) not in sent_keys]
    print(f"[investors] {len(new_filings)} filings nuevos (no enviados antes).")

    if not new_filings:
        print("[investors] Todo ya enviado. Nada enviado.")
        return

    msg    = _build_message(new_filings, date_from, date_to)
    interp = _ai_interpretation(new_filings, date_from, date_to)
    if interp:
        msg += f"\n\n📌 *Lectura InvestX*\n{interp}"

    send_telegram_message(msg)
    _mark_sent(today, [_trade_key(f) for f in new_filings])
    print(f"[investors] OK enviado {len(new_filings)} filings (force={force}).")
