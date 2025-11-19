# econ_calendar.py
import logging
from datetime import datetime, timedelta

import investpy
import pandas as pd

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)


def _get_today_range():
    """
    Devuelve from_date y to_date en formato dd/mm/yyyy.
    Ponemos to_date = hoy + 1 día para evitar el error:
    'to_date should be greater than from_date'.
    """
    today = datetime.now()
    tomorrow = today + timedelta(days=1)
    from_date = today.strftime("%d/%m/%Y")
    to_date = tomorrow.strftime("%d/%m/%Y")
    return from_date, to_date


def _format_event_line(row: pd.Series) -> str:
    """
    Devuelve una línea de texto legible para Telegram para un evento.
    """
    # importancia en estrellas
    importance = str(row.get("importance", "")).lower()
    if "high" in importance:
        stars = "⭐⭐⭐"
    elif "medium" in importance:
        stars = "⭐⭐"
    else:
        stars = "⭐"

    time_str = str(row.get("time", "")).strip()
    event = str(row.get("event", "")).strip()

    actual = str(row.get("actual", "")).strip()
    forecast = str(row.get("forecast", "")).strip()
    previous = str(row.get("previous", "")).strip()

    parts = []

    # cabecera
    if time_str and time_str != "NaN":
        header = f"{stars} {time_str} – {event}"
    else:
        header = f"{stars} {event}"
    parts.append(header)

    # detalles numéricos si existen
    sub = []
    if actual and actual != "nan":
        sub.append(f"Actual: {actual}")
    if forecast and forecast != "nan":
        sub.append(f"Previsión: {forecast}")
    if previous and previous != "nan":
        sub.append(f"Anterior: {previous}")

    if sub:
        parts.append(" | " + " · ".join(sub))

    return "".join(parts)


def run_econ_calendar():
    """
    Obtiene el calendario económico de USA para hoy (impacto medio/alto)
    y lo envía a Telegram con una breve interpretación usando GPT mini.
    """
    logger.info("[INFO] econ_calendar: Obteniendo calendario económico USA...")

    try:
        from_date, to_date = _get_today_range()
        logger.info(
            f"[INFO] econ_calendar: Rango fechas from_date={from_date}, to_date={to_date}"
        )

        # llamada correcta a investpy (OJO: es una función, no .get_economic_calendar)
        df = investpy.economic_calendar(
            countries=["united states"],
            from_date=from_date,
            to_date=to_date,
            time_zone="GMT",
        )

        if df is None or df.empty:
            msg = (
                "🗓️ Calendario económico USA – hoy\n\n"
                "⚠️ No hay eventos económicos relevantes de impacto medio/alto en EE. UU."
            )
            send_telegram_message(msg)
            logger.info("[INFO] econ_calendar: Sin eventos relevantes.")
            return

        # Nos quedamos solo con importancia media/alta
        df["importance"] = df["importance"].astype(str).str.lower()
        df = df[df["importance"].isin(["medium", "high"])]

        if df.empty:
            msg = (
                "🗓️ Calendario económico USA – hoy\n\n"
                "⚠️ No hay eventos de impacto medio/alto en EE. UU."
            )
            send_telegram_message(msg)
            logger.info("[INFO] econ_calendar: Solo había importancia baja, filtrado.")
            return

        # Ordenamos por hora si hay columna time
        if "time" in df.columns:
            df["time"] = df["time"].astype(str)
            try:
                df = df.sort_values("time")
            except Exception:
                pass

        # Construimos líneas para Telegram y para la IA
        lines_tg = []
        lines_ai = []

        for _, row in df.iterrows():
            line = _format_event_line(row)
            lines_tg.append("• " + line)

            lines_ai.append(
                f"{row.get('time', '')} - {row.get('event', '')} | "
                f"importance={row.get('importance', '')}, "
                f"actual={row.get('actual', '')}, "
                f"forecast={row.get('forecast', '')}, "
                f"previous={row.get('previous', '')}"
            )

        today_str = datetime.now().strftime("%d/%m/%Y")
        header = f"🗓️ Calendario económico USA – {today_str} (impacto medio/alto)\n\n"
        body_events = "\n".join(lines_tg)

        # Interpretación con GPT mini (gpt-4o-mini en utils)
        prompt = (
            "Eres analista macro para un canal de trading en español. "
            "Te doy los eventos del calendario económico de HOY en EE. UU. "
            "Quiero un resumen muy breve (máx 4 líneas) sobre cómo pueden impactar "
            "en índices USA (especialmente S&P 500 y Nasdaq) y en el USD. "
            "No menciones que eres IA ni hables de 'datos proporcionados'. "
            "Va directo al grano.\n\n"
            "Eventos:\n"
            + "\n".join(lines_ai)
        )

        interpretacion = call_gpt_mini(prompt, max_tokens=220)

        if interpretacion:
            texto_final = (
                header
                + body_events
                + "\n\n📌 Interpretación del día:\n"
                + interpretacion.strip()
            )
        else:
            texto_final = header + body_events

        send_telegram_message(texto_final)
        logger.info("[INFO] econ_calendar: Calendario económico enviado.")

    except Exception as e:
        logger.error(f"[ERROR] econ_calendar: ⚠️ Error al obtener calendario económico: {e}")
        send_telegram_message(
            f"⚠️ Error al obtener calendario económico:\n{e}"
        )
