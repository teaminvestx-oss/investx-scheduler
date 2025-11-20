import os
import logging
from datetime import datetime, timedelta
from pathlib import Path
from dateutil import tz

import pandas as pd
import investpy

from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger("econ_calendar")

LOCAL_TZ = os.getenv("LOCAL_TZ", "Europe/Madrid")
STATE_FILE = Path(__file__).resolve().parent / ".econ_calendar_last_date"


# ---------------------- FECHAS Y CONTROL ----------------------

def _today_local():
    tzinfo = tz.gettz(LOCAL_TZ)
    return datetime.now(tzinfo).date()


def _already_sent_today() -> bool:
    today = _today_local().isoformat()
    if STATE_FILE.exists():
        try:
            if STATE_FILE.read_text().strip() == today:
                return True
        except:
            pass
    return False


def _mark_sent_today():
    today = _today_local().isoformat()
    try:
        STATE_FILE.write_text(today)
    except Exception as e:
        logger.warning("No se pudo escribir STATE_FILE: %s", e)


# ---------------------- AGRUPAR EVENTOS DUPLICADOS ----------------------

def _normalize_title(event: str) -> str:
    """
    Normaliza los nombres de eventos para agrupar duplicados:
    - Housing Starts (MoM)
    - Housing Starts (YoY)
    - Monthly Housing Starts
    → "Housing Starts"
    """
    if not event:
        return "Evento"

    e = event.lower()

    # Housing Starts — varias versiones
    if "housing starts" in e:
        return "Housing Starts"

    # Initial Jobless Claims
    if "jobless" in e:
        return "Jobless Claims"

    # Average Hourly Earnings
    if "hourly earnings" in e:
        return "Average Hourly Earnings"

    # Manufacturing Index
    if "manufacturing" in e:
        return "Manufacturing Index"

    # Participation Rate
    if "participation rate" in e:
        return "Participation Rate"

    # Otros → primer bloque sin paréntesis
    return event.split("(")[0].strip()


# ---------------------- FORMATEO DE EVENTOS ----------------------

def _importance_to_stars(importance):
    imp = str(importance).lower()
    if "high" in imp or imp in ["3", "3.0"]:
        return "⭐⭐⭐"
    if "medium" in imp or imp in ["2", "2.0"]:
        return "⭐⭐"
    return "⭐"


def _event_block(title, row_group):
    """
    row_group: conjunto de filas investpy que pertenecen al mismo título base
    """
    # Tomamos la primera fila para hora
    row0 = row_group.iloc[0]

    stars = _importance_to_stars(row0.get("importance", ""))
    time = row0.get("time", "--:--")

    # Consolidar números
    datos = []

    # Nivel
    if any(str(x) not in ["", "None", "nan"] for x in row_group["actual"]):
        val = row_group["actual"].dropna().iloc[0]
        datos.append(f"Nivel: {val}")

    # Forecast
    if any(str(x) not in ["", "None", "nan"] for x in row_group["forecast"]):
        val = row_group["forecast"].dropna().iloc[0]
        datos.append(f"Previsión: {val}")

    # Previous
    if any(str(x) not in ["", "None", "nan"] for x in row_group["previous"]):
        val = row_group["previous"].dropna().iloc[0]
        datos.append(f"Anterior: {val}")

    if not datos:
        datos_str = "Sin datos disponibles"
    else:
        datos_str = " | ".join(datos)

    # Interpretación individual con GPT-mini
    system_prompt = (
        "Eres un analista macro. Explica en 1–2 frases el impacto del evento "
        "en índices USA y en el USD. Tono profesional y natural."
    )
    user_prompt = f"Evento: {title}\nHora: {time}\nDatos: {datos_str}"
    interpretacion = call_gpt_mini(system_prompt, user_prompt) or "Impacto moderado."

    block = (
        f"{stars} {time} – *{title}*\n"
        f"• Datos: {datos_str}\n"
        f"• Impacto: {interpretacion}"
    )
    return block


# ---------------------- MENSAJE COMPLETO ----------------------

def _build_message(df: pd.DataFrame, today):
    """
    Construye el mensaje final:
    - Título
    - Resumen clave
    - Lista de eventos formateados (sin duplicados)
    """
    title = f"📅 Calendario económico USA – {today.strftime('%d/%m')} (2–3⭐)\n\n"

    # Resumen general con IA
    eventos_texto = []
    for _, row in df.iterrows():
        eventos_texto.append(
            f"- {row.get('time','--:--')} | {row.get('event','').strip()} | "
            f"Actual={row.get('actual','')} | Forecast={row.get('forecast','')} | Prev={row.get('previous','')}"
        )
    joined = "\n".join(eventos_texto)

    resumen = call_gpt_mini(
        "Eres analista macro. Resume en 2 frases breves el foco del día "
        "para traders de índices USA. Sé claro y natural.",
        f"Eventos de hoy:\n{joined}",
        max_tokens=120,
    ) or "Jornada centrada en eventos macro de interés moderado."

    resumen_block = f"👉 *Clave del día:*\n{resumen}\n\n"

    # Agrupar por título normalizado
    df["title_norm"] = df["event"].apply(_normalize_title)

    blocks = []
    for title_norm, group in df.groupby("title_norm"):
        block = _event_block(title_norm, group)
        blocks.append(block)

    final_text = title + resumen_block + "\n\n".join(blocks)
    return final_text


# ---------------------- FUNCIÓN PRINCIPAL ----------------------

def run_econ_calendar(force: bool = False):
    today = _today_local()

    if not force and _already_sent_today():
        logger.info("Calendario YA enviado hoy. No se repite.")
        return

    # Fechas para investpy (evita error 0032)
    from_date = today.strftime("%d/%m/%Y")
    to_date = (today + timedelta(days=1)).strftime("%d/%m/%Y")

    try:
        df = investpy.economic_calendar(
            from_date=from_date,
            to_date=to_date,
            countries=["united states"],
        )
    except Exception as e:
        err = f"⚠️ Error al obtener calendario económico:\n{e}"
        logger.error(err)
        send_telegram_message(err)
        return

    if df is None or df.empty:
        msg = f"📅 Calendario económico USA – {today.strftime('%d/%m')}\n\nNo hay eventos relevantes hoy."
        send_telegram_message(msg)
        _mark_sent_today()
        return

    # Limpieza básica
    if "event" not in df.columns:
        send_telegram_message("⚠️ Calendario económico vacío o mal formado.")
        _mark_sent_today()
        return

    # Orden por fecha/hora
    try:
        df["_dt"] = pd.to_datetime(df["date"] + " " + df["time"], errors="coerce")
        df = df.sort_values("_dt")
    except:
        pass

    # Filtrar solo importancia media/alta
    if "importance" in df.columns:
        df = df[df["importance"].astype(str).str.lower().isin(["2", "3", "medium", "high", "2.0", "3.0"])]

    if df.empty:
        send_telegram_message("📅 Hoy no hay eventos de relevancia (2–3⭐) en EEUU.")
        _mark_sent_today()
        return

    # Construir mensaje final
    text = _build_message(df, today)

    # Enviar
    send_telegram_message(text)
    logger.info("Calendario enviado.")
    _mark_sent_today()
