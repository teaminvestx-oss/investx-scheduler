# econ_calendar.py – InvestX v2.1
# Calendario económico USA con resumen corto por IA y envío a Telegram

import os
import json
import logging
from datetime import datetime, timedelta, date
from typing import List, Dict

import pandas as pd
import investpy  # asegúrate de tenerlo en requirements.txt

from utils import send_telegram_message, call_gpt_mini

# ---------------------------------------------------------------------
# Configuración general
# ---------------------------------------------------------------------

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Fichero local para controlar "solo 1 vez al día"
STATE_FILE = "econ_calendar_state.json"

# País por defecto
DEFAULT_COUNTRY = os.environ.get("ECON_COUNTRY", "united states")

# ---------------------------------------------------------------------
# Utilidades de estado diario
# ---------------------------------------------------------------------

def _load_state() -> Dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("econ_calendar: no se pudo leer STATE_FILE: %s", e)
        return {}


def _save_state(state: Dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        logger.warning("econ_calendar: no se pudo guardar STATE_FILE: %s", e)


def _already_sent_today(today_str: str) -> bool:
    state = _load_state()
    return state.get("last_sent_date") == today_str


def _mark_sent_today(today_str: str) -> None:
    state = _load_state()
    state["last_sent_date"] = today_str
    _save_state(state)


# ---------------------------------------------------------------------
# Lógica de calendario
# ---------------------------------------------------------------------

def _get_investpy_calendar_for_day(country: str, target_day: date) -> pd.DataFrame:
    """
    Obtiene calendario económico desde investpy para un país concreto
    alrededor de target_day (target_day-1, target_day+1) y luego
    filtra SOLO los eventos cuya fecha sea exactamente target_day.
    Esto evita el ERR#0032 ("to_date should be greater than from_date")
    y pequeños problemas de zona horaria.
    """
    # Rango amplio para contentar a investpy
    from_day = target_day - timedelta(days=1)
    to_day = target_day + timedelta(days=1)

    f_str = from_day.strftime("%d/%m/%Y")
    t_str = to_day.strftime("%d/%m/%Y")
    logger.info(
        "econ_calendar:[INFO] econ_calendar: Rango bruto investpy "
        "from_date=%s, to_date=%s", f_str, t_str
    )

    df = investpy.economic_calendar(
        from_date=f_str,
        to_date=t_str,
        countries=[country.title()]  # "United States"
    )

    if df.empty:
        logger.info("econ_calendar:[INFO] econ_calendar: Sin eventos para el rango bruto.")
        return df

    # Aseguramos columnas esperadas
    expected_cols = [
        "date", "time", "country", "event",
        "importance", "actual", "forecast", "previous",
    ]
    for col in expected_cols:
        if col not in df.columns:
            df[col] = ""

    # Normalizamos a datetime
    df["datetime"] = pd.to_datetime(
        df["date"].astype(str) + " " + df["time"].astype(str),
        errors="coerce"
    )
    df = df.dropna(subset=["datetime"])

    # Filtramos SOLO el día objetivo
    df = df[df["datetime"].dt.date == target_day]

    # Solo el país deseado (por si se cuela algo)
    df = df[df["country"].astype(str).str.contains(country.split()[0], case=False, na=False)]

    df = df.sort_values("datetime")

    logger.info(
        "econ_calendar:[INFO] econ_calendar: %d eventos tras filtrar por día y país.",
        len(df)
    )

    return df


def _importance_to_stars(importance: str) -> int:
    """
    Convierte la importancia de investpy a número de estrellas (1–3).
    Muy defensivo: si no entiende el texto, devuelve 2⭐.
    """
    if isinstance(importance, str):
        text = importance.lower()
        if "high" in text or "3" in text:
            return 3
        if "medium" in text or "2" in text:
            return 2
        if "low" in text or "1" in text:
            return 1
    # fallback genérico
    return 2


def _normalize_title(title: str) -> str:
    """
    Normaliza títulos para agrupar eventos similares
    (ej. Housing Starts (MoM) / Housing Starts).
    """
    t = str(title).lower()
    import re
    t = re.sub(r"\(.*?\)", "", t)   # quitamos paréntesis
    t = " ".join(t.split())         # espacios duplicados
    return t


def _filter_and_group_events(df: pd.DataFrame) -> List[Dict]:
    """
    - Convierte importancia a estrellas.
    - Se queda con >= 2⭐.
    - Agrupa eventos con título similar.
    - Da prioridad a eventos clave (Fed, FOMC, empleo, inflación, etc.).
    - Selecciona máx. 6 eventos.
    Devuelve lista de dicts ordenados por fecha/hora.
    """
    if df.empty:
        return []

    df = df.copy()
    df["stars"] = df["importance"].apply(_importance_to_stars)

    # Solo 2 y 3 estrellas
    df = df[df["stars"] >= 2]
    if df.empty:
        return []

    df["title_norm"] = df["event"].astype(str).apply(_normalize_title)

    grouped_rows = []
    for _, g in df.groupby("title_norm"):
        # nos quedamos con el de más estrellas y, si empatan, el más temprano
        g = g.sort_values(["stars", "datetime"], ascending=[False, True])
        row = g.iloc[0]
        grouped_rows.append(row)

    if not grouped_rows:
        return []

    grouped_df = pd.DataFrame(grouped_rows)

    # Palabras clave gordas (añadimos FOMC / Trump etc.)
    KEYWORDS_PRIORITY = [
        "fed", "fomc", "rate decision", "interest rate",
        "nonfarm", "payrolls", "jobless", "unemployment",
        "cpi", "inflation", "pce", "core",
        "gdp", "gross domestic product",
        "retail sales", "ism", "manufacturing", "services",
        "housing starts", "building permits",
        "cftc", "crude oil", "oil inventories", "eia",
        "trump", "powell", "yellen"
    ]

    def _is_priority(ev: str, stars: int) -> bool:
        ev_l = str(ev).lower()
        if stars == 3:
            return True
        return any(k in ev_l for k in KEYWORDS_PRIORITY)

    grouped_df["is_priority"] = grouped_df.apply(
        lambda r: _is_priority(r["event"], int(r["stars"])),
        axis=1
    )

    grouped_df = grouped_df.sort_values(
        ["is_priority", "stars", "datetime"],
        ascending=[False, False, True]
    )

    MAX_EVENTS = 6
    grouped_df = grouped_df.head(MAX_EVENTS)

    # Orden final por fecha/hora
    grouped_df = grouped_df.sort_values("datetime")

    events = []
    for _, r in grouped_df.iterrows():
        events.append(
            {
                "datetime": r["datetime"],
                "event": str(r["event"]),
                "stars": int(r["stars"]),
                "actual": str(r.get("actual", "")) if pd.notna(r.get("actual", "")) else "",
                "forecast": str(r.get("forecast", "")) if pd.notna(r.get("forecast", "")) else "",
                "previous": str(r.get("previous", "")) if pd.notna(r.get("previous", "")) else "",
            }
        )
    return events


# ---------------------------------------------------------------------
# Llamada a OpenAI para interpretar cada evento
# ---------------------------------------------------------------------

def _interpret_event(event: Dict) -> str:
    """
    Devuelve 1–3 líneas (máx. ~220 caracteres) con interpretación del dato
    en castellano, centrado en impacto para índices USA y USD.
    """
    dt_ = event["datetime"]
    hora = dt_.strftime("%H:%M")
    titulo = event["event"]
    stars = "⭐" * event["stars"]
    actual = event["actual"] or "—"
    forecast = event["forecast"] or "—"
    previous = event["previous"] or "—"

    prompt = f"""
Eres analista macro en un canal de trading en español (InvestX). Resume muy brevemente el impacto POTENCIAL de este dato en índices USA y el dólar.

Evento: {titulo}
Hora: {hora}
Estrellas: {stars}
Actual: {actual}
Previsión: {forecast}
Anterior: {previous}

Instrucciones:
- Responde SOLO con 1–3 líneas de texto en español.
- Máximo 2 frases cortas (≈220 caracteres en total).
- Tono profesional, claro y directo.
- Comenta el impacto potencial: positivo/negativo/mixto para índices USA y USD.
- No repitas literalmente el título ni la hora, ni uses frases tipo "este dato".
Ejemplo de estilo: "Dato fuerte de empleo; favorece subidas en índices USA y refuerza al USD."
""".strip()

    try:
        texto = call_gpt_mini(prompt, max_tokens=120)
        return texto.strip()
    except Exception as e:
        logger.warning("econ_calendar: fallo interpretando evento con OpenAI: %s", e)
        return "Dato relevante que puede mover índices USA y el dólar."


def _build_message(events: List[Dict], today: datetime) -> str:
    if not events:
        return "📅 Hoy no hay referencias macro importantes en EE. UU."

    fecha_str = today.strftime("%a %d/%m").replace(".", "")
    min_stars = min(e["stars"] for e in events)
    max_stars = max(e["stars"] for e in events)
    stars_range = f"{min_stars}–{max_stars}⭐"

    lines = []
    lines.append(f"📅 Calendario económico USA — {fecha_str} ({stars_range})")
    lines.append("Solo los datos más relevantes que pueden mover índices USA y el USD.\n")

    for ev in events:
        dt_ = ev["datetime"]
        hora = dt_.strftime("%H:%M")
        titulo = ev["event"]
        stars = "⭐" * ev["stars"]
        actual = ev["actual"] or "—"
        forecast = ev["forecast"] or "—"
        previous = ev["previous"] or "—"

        interpretacion = _interpret_event(ev)

        bloque = (
            f"{stars} {hora} – {titulo}\n"
            f"   Actual: {actual} | Previsión: {forecast} | Anterior: {previous}\n"
            f"   {interpretacion}"
        )
        lines.append(bloque)

    # Resumen clave del día (1 frase, máx 160 caracteres aprox.)
    resumen_prompt = f"""
Eres analista macro. Resume en 1 frase (máx. 160 caracteres) cuál es la CLAVE DEL DÍA
para índices USA y USD, dados estos eventos (en español, tono profesional):

Eventos:
{chr(10).join(f"- {e['datetime'].strftime('%H:%M')} {e['event']} ({'⭐'*e['stars']})" for e in events)}
""".strip()

    try:
        resumen = call_gpt_mini(resumen_prompt, max_tokens=60).strip()
    except Exception as e:
        logger.warning("econ_calendar: fallo generando clave del día con OpenAI: %s", e)
        resumen = "Los datos macro de hoy marcarán el tono de la sesión en índices USA y USD."

    lines.append(f"\n👉 Clave del día: {resumen}")

    mensaje = "\n".join(lines)
    if len(mensaje) > 3900:
        mensaje = mensaje[:3900] + "\n\n(Resumen recortado por longitud.)"

    return mensaje


# ---------------------------------------------------------------------
# Función pública llamada desde main.py
# ---------------------------------------------------------------------

def run_econ_calendar(force: bool = False) -> None:
    """
    Ejecuta todo el flujo:
    - Control una sola vez al día (salvo force=True).
    - Obtiene calendario USA para HOY (filtrando sobre un rango amplio).
    - Filtra y agrupa eventos clave.
    - Genera mensaje con interpretaciones cortas.
    - Envía a Telegram.
    """
    now = datetime.now()
    today = now.date()
    today_str = today.isoformat()

    if not force and _already_sent_today(today_str):
        logger.info(
            "econ_calendar:[INFO] econ_calendar: Ya enviado hoy, no se vuelve a enviar (force=False)."
        )
        return

    logger.info("econ_calendar:[INFO] econ_calendar: Obteniendo calendario económico USA...")

    try:
        df = _get_investpy_calendar_for_day(
            country=DEFAULT_COUNTRY,
            target_day=today,
        )
    except Exception as e:
        logger.error(
            "econ_calendar:ERROR econ_calendar: Error al obtener calendario de investpy: %s", e
        )
        send_telegram_message(f"⚠️ Error al obtener calendario económico:\n{e}")
        return

    events = _filter_and_group_events(df)
    message = _build_message(events, today=now)

    try:
        send_telegram_message(message)
        logger.info("econ_calendar:[INFO] econ_calendar: Calendario económico enviado.")
        if not force:
            _mark_sent_today(today_str)
    except Exception as e:
        logger.error(
            "econ_calendar:ERROR econ_calendar: fallo enviando a Telegram: %s", e
        )
