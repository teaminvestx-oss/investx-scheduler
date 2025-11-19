import logging
from datetime import datetime, timedelta

import investpy
import pandas as pd

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)


def _get_calendar_df():
    """Descarga calendario USA de hoy (forzando to_date > from_date)."""
    today = datetime.utcnow().date()
    tomorrow = today + timedelta(days=1)

    from_str = today.strftime("%d/%m/%Y")
    to_str = tomorrow.strftime("%d/%m/%Y")

    logger.info(
        f"econ_calendar:[INFO] econ_calendar: Rango fechas from_date={from_str}, "
        f"to_date={to_str}"
    )

    try:
        df = investpy.economic_calendar(
            countries=["united states"],
            from_date=from_str,
            to_date=to_str,
        )
    except Exception as e:
        raise RuntimeError(f"Error al obtener calendario de investpy: {e}")

    if df is None or df.empty:
        return pd.DataFrame()

    # Nos quedamos solo con USA y quitamos filas sin hora/evento
    df = df[df["country"].str.lower().str.contains("united states")]
    df = df.dropna(subset=["event"])

    # Orden por fecha/hora si las columnas existen
    if "date" in df.columns and "time" in df.columns:
        try:
            dt = pd.to_datetime(df["date"] + " " + df["time"])
            df = df.assign(_dt=dt).sort_values("_dt")
        except Exception:
            pass

    return df


def _stars_from_importance(importance: str) -> str:
    imp = str(importance).lower()
    if "high" in imp or imp.strip() in {"3", "3.0"}:
        return "⭐⭐⭐"
    if "medium" in imp or imp.strip() in {"2", "2.0"}:
        return "⭐⭐"
    return "⭐"


def _format_event_row(row) -> str:
    stars = _stars_from_importance(row.get("importance", ""))
    time = row.get("time", "--:--")
    name = row.get("event", "").strip()

    actual = str(row.get("actual", "") or "").strip()
    forecast = str(row.get("forecast", "") or "").strip()
    previous = str(row.get("previous", "") or "").strip()

    nums = []
    if actual:
        nums.append(f"*Actual:* {actual}")
    if forecast:
        nums.append(f"*Previsión:* {forecast}")
    if previous:
        nums.append(f"*Anterior:* {previous}")

    if nums:
        nums_str = " | ".join(nums)
    else:
        nums_str = "Sin datos numéricos disponibles."

    line = (
        f"{stars} *{name}*\n"
        f"🕒 {time}\n"
        f"💬 {nums_str}"
    )
    return line


def _build_message_with_ai(df: pd.DataFrame) -> str:
    """
    Construye el mensaje final usando la IA para un breve análisis,
    pero si la IA falla se envía solo el listado.
    """
    today = datetime.utcnow().date()
    title = f"📅 Resumen económico USA – {today.strftime('%d/%m/%Y')} (2–3⭐)\n\n"

    # Filtramos solo media/alta importancia para que no sea eterno
    if "importance" in df.columns:
        mask = df["importance"].astype(str).str.lower().isin(
            ["medium", "high", "2", "3", "2.0", "3.0"]
        )
        df_imp = df[mask].copy()
        if df_imp.empty:
            df_imp = df.copy()
    else:
        df_imp = df.copy()

    # Limitamos a los 10–12 eventos más relevantes para el mensaje
    df_imp = df_imp.head(12)

    # Texto resumido para pasar a la IA
    eventos_for_ai = []
    for _, row in df_imp.iterrows():
        eventos_for_ai.append(
            f"- {row.get('time','--:--')} | {row.get('event','').strip()} | "
            f"imp={row.get('importance','')} | "
            f"actual={row.get('actual','')} | "
            f"forecast={row.get('forecast','')} | "
            f"previous={row.get('previous','')}"
        )
    eventos_for_ai_text = "\n".join(eventos_for_ai)

    system_prompt = (
        "Eres un analista macro que explica a traders intradía, en español, "
        "qué eventos de EEUU pueden mover índices USA y el dólar. "
        "Sé concreto, tono natural, máximo 3–4 frases cortas."
    )
    user_prompt = (
        "Con esta lista de eventos macro de EEUU para hoy, explica brevemente "
        "qué vigilarías y dónde puede haber más impacto en índices y USD:\n\n"
        f"{eventos_for_ai_text}"
    )

    resumen = call_gpt_mini(system_prompt, user_prompt, max_tokens=220)

    if resumen:
        resumen_block = f"👉 *Clave del día:*\n{resumen.strip()}\n\n"
    else:
        resumen_block = ""

    # Listado formateado de eventos
    lines = [_format_event_row(row) for _, row in df_imp.iterrows()]
    events_block = "\n\n".join(lines)

    msg = title + resumen_block + events_block
    return msg


def run_econ_calendar():
    """
    Función que llama main.py.
    - Descarga calendario USA.
    - Si no hay nada relevante, manda mensaje indicándolo.
    - Si hay datos, construye mensaje (con IA si se puede) y lo envía por Telegram.
    """
    logger.info("econ_calendar:[INFO] econ_calendar: Obteniendo calendario económico USA...")

    try:
        df = _get_calendar_df()
    except Exception as e:
        # Aquí sí queremos que se vea el error en Telegram
        err_msg = (
            "⚠️ Error al obtener calendario económico:\n"
            f"{e}"
        )
        logger.error(f"ERROR:econ_calendar: {err_msg}")
        send_telegram_message(err_msg)
        return

    if df.empty:
        msg = (
            "📅 Calendario económico USA – hoy\n\n"
            "No hay publicaciones macro de alto impacto previstas para hoy en EEUU."
        )
        logger.info("econ_calendar:[INFO] Sin eventos relevantes, enviando aviso.")
        send_telegram_message(msg)
        return

    msg = _build_message_with_ai(df)
    logger.info("econ_calendar:[INFO] Enviando calendario económico...")
    send_telegram_message(msg)
    logger.info("econ_calendar:[INFO] Calendario económico enviado.")
