# econ_calendar.py – InvestX v2.1
# Calendario económico USA con resumen corto por IA y envío a Telegram

import os
import json
import logging
from datetime import datetime, timedelta
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

# Offset opcional de hora para mostrar (por si Investing/investpy viene 1h desplazado)
# Por defecto 0 (NO cambia nada respecto a como lo tienes ahora).
# Si ves siempre +1h, puedes poner ECON_TIME_OFFSET=-1 en Render.
TIME_OFFSET_HOURS = int(os.environ.get("ECON_TIME_OFFSET", "0"))

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

def _get_investpy_calendar(country: str, from_date: datetime, to_date: datetime) -> pd.DataFrame:
    """
    Obtiene calendario económico desde investpy para un país concreto
    entre from_date (incluido) y to_date (incluido).
    Fechas en formato dd/mm/yyyy como requiere investpy.
    """
    f_str = from_date.strftime("%d/%m/%Y")
    t_str = to_date.strftime("%d/%m/%Y")
    logger.info("econ_calendar:[INFO] econ_calendar: Rango fechas from_date=%s, to_date=%s", f_str, t_str)

    df = investpy.economic_calendar(
        from_date=f_str,
        to_date=t_str,
        countries=[country.title()]  # "United States"
    )

    if df.empty:
        logger.info("econ_calendar:[INFO] econ_calendar: Sin eventos para el rango dado.")
        return df

    # Normalizamos columnas que nos interesan
    expected_cols = [
        "date", "time", "country", "event",
        "importance", "actual", "forecast", "previous"
    ]

    for col in expected_cols:
        if col not in df.columns:
            df[col] = ""

    # Convertimos a datetime para ordenar por fecha/hora
    df["datetime"] = pd.to_datetime(
        df["date"] + " " + df["time"],
        errors="coerce",
        dayfirst=True
    )
    df = df.dropna(subset=["datetime"])
    df = df.sort_values("datetime")

    # Solo el país que queremos por si vinieran mezclados
    df = df[df["country"].str.contains(country.split()[0], case=False, na=False)]

    return df


def _importance_to_stars(importance: str) -> int:
    """
    Convierte la importancia de investpy a número de estrellas (1–3).
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
    t = title.lower()
    import re
    t = re.sub(r"\(.*?\)", "", t)
    t = " ".join(t.split())
    return t


def _filter_and_group_events(df: pd.DataFrame) -> List[Dict]:
    """
    - Convierte importancia a estrellas.
    - Se queda con >= 2⭐.
    - Agrupa eventos con título similar.
    - Selecciona los más relevantes (máx. 6).
    Devuelve lista de dicts ordenados por fecha/hora.
    """
    if df.empty:
        return []

    df = df.copy()
    df["stars"] = df["importance"].apply(_importance_to_stars)

    # Sólo 2 y 3 estrellas
    df = df[df["stars"] >= 2]
    if df.empty:
        return []

    # Normalizar título para agrupar
    df["title_norm"] = df["event"].astype(str).apply(_normalize_title)

    grouped_rows = []
    for _, g in df.groupby("title_norm"):
        # Nos quedamos con:
        # - más estrellas
        # - y si empatan, el más temprano
        g = g.sort_values(["stars", "datetime"], ascending=[False, True])
        row = g.iloc[0]
        grouped_rows.append(row)

    if not grouped_rows:
        return []

    grouped_df = pd.DataFrame(grouped_rows)

    # Palabras clave para priorizar eventos realmente gordos
    KEYWORDS_PRIORITY = [
        "fed", "fomc", "rate decision", "interest rate",
        "nonfarm", "payrolls", "jobless", "unemployment",
        "cpi", "inflation", "pce", "core",
        "gdp", "gross domestic product",
        "retail sales", "ism", "manufacturing", "services",
        "housing starts", "building permits",
        "cftc", "crude oil", "oil inventories", "eia",
        "trump"  # por si metes titulares de Trump en el calendario
    ]

    def _is_priority(ev: str, stars: int) -> bool:
        ev_l = ev.lower()
        if stars == 3:
            return True
        return any(k in ev_l for k in KEYWORDS_PRIORITY)

    grouped_df["is_priority"] = grouped_df.apply(
        lambda r: _is_priority(str(r["event"]), int(r["stars"])), axis=1
    )

    # Ordenamos por:
    # 1) prioridad
    # 2) estrellas
    # 3) hora
    grouped_df = grouped_df.sort_values(
        ["is_priority", "stars", "datetime"],
        ascending=[False, False, True]
    )

    # Limitamos a máx 6 eventos
    MAX_EVENTS = 6
    grouped_df = grouped_df.head(MAX_EVENTS)

    # Orden final por fecha/hora para mostrar
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
# Interpretación IA de cada evento
# ---------------------------------------------------------------------

def _interpret_event(event: Dict) -> str:
    """
    Devuelve 2–3 líneas (máx. ~260 caracteres) con interpretación del dato
    en castellano, centrado en impacto para índices USA y USD.
    Si la llamada a OpenAI falla, devuelve un texto genérico pero útil.
    """
    dt = event["datetime"]
    hora = dt.strftime("%H:%M")
    titulo = event["event"]
    stars = "⭐" * event["stars"]
    actual = event["actual"] or "—"
    forecast = event["forecast"] or "—"
    previous = event["previous"] or "—"

    prompt = f"""
Eres analista macro en un canal de trading en español (InvestX).
Explica en 2–3 frases cortas cómo puede afectar este dato a índices USA y al USD.

Evento: {titulo}
Hora local aprox: {hora}
Importancia: {stars}
Actual: {actual}
Previsión: {forecast}
Anterior: {previous}

Instrucciones:
- Responde en 2–3 líneas como mucho, ~260 caracteres en total.
- Tono profesional y directo, sin adornos ni jerga rara.
- Di si el dato es potencialmente positivo, negativo o mixto para índices USA.
- Comenta si el impacto probable sobre el USD es de apoyo, presión o neutral.
- No repitas literalmente el título ni la hora.
""".strip()

    try:
        texto = call_gpt_mini(prompt, max_tokens=140)
        if texto:
            return texto.strip()
    except Exception as e:
        logger.warning("econ_calendar: fallo interpretando evento con OpenAI: %s", e)

    # Fallback si falla la IA
    return (
        "Dato relevante para índices USA y el USD: puede generar volatilidad "
        "según se aleje de la previsión, afectando a bonos, bolsas y divisa."
    )


# ---------------------------------------------------------------------
# Construcción del mensaje
# ---------------------------------------------------------------------

def _build_message(events: List[Dict], today: datetime) -> str:
    if not events:
        return "📅 Hoy no hay referencias macro importantes en EE. UU."

    fecha_str = today.strftime("%a %d/%m").replace(".", "")
    # calculamos rango de estrellas para cabecera
    min_stars = min(e["stars"] for e in events)
    max_stars = max(e["stars"] for e in events)
    stars_range = f"{min_stars}–{max_stars}⭐"

    lines = []
    lines.append(f"📅 Calendario económico USA — {fecha_str} ({stars_range})")
    lines.append("Solo los datos más relevantes que pueden mover índices USA y el USD.\n")

    # Cuerpo: un bloque por evento
    for ev in events:
        # Ajuste opcional de hora (por si ves siempre +1/-1h)
        dt = ev["datetime"] + timedelta(hours=TIME_OFFSET_HOURS)
        hora = dt.strftime("%H:%M")
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

    # Clave del día (resumen final por IA)
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
        resumen = "Los datos de hoy marcarán el sesgo de la sesión en índices USA y en el USD."

    lines.append(f"\n👉 Clave del día: {resumen}")

    mensaje = "\n".join(lines)
    # Seguridad adicional por si nos pasamos del límite de Telegram (4096)
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
    - Obtiene calendario USA para hoy (o rango que ya tengas configurado).
    - Filtra y agrupa eventos clave.
    - Genera mensaje con interpretaciones cortas.
    - Envía a Telegram.
    """
    now = datetime.now()
    today = now.date()
    today_str = today.isoformat()

    if not force:
        if _already_sent_today(today_str):
            logger.info("econ_calendar:[INFO] econ_calendar: Ya enviado hoy, no se vuelve a enviar (force=False).")
            return

    logger.info("econ_calendar:[INFO] econ_calendar: Obteniendo calendario económico USA...")

    try:
        # IMPORTANTE: no tocar la lógica de fechas que ahora te funciona
        df = _get_investpy_calendar(
            country=DEFAULT_COUNTRY,
            from_date=datetime.combine(today, datetime.min.time()),
            to_date=datetime.combine(today, datetime.min.time())
        )
    except Exception as e:
        logger.error("econ_calendar:ERROR econ_calendar: Error al obtener calendario de investpy: %s", e)
        send_telegram_message(
            f"⚠️ Error al obtener calendario económico:\n{e}"
        )
        return

    events = _filter_and_group_events(df)
    message = _build_message(events, today=now)

    try:
        send_telegram_message(message)
        logger.info("econ_calendar:[INFO] econ_calendar: Calendario económico enviado.")
        if not force:
            _mark_sent_today(today_str)
    except Exception as e:
        logger.error("econ_calendar:ERROR econ_calendar: fallo enviando a Telegram: %s", e)
