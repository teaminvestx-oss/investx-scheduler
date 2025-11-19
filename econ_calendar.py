import investpy
import datetime
import logging
from utils import send_telegram_message, call_gpt_mini

logger = logging.getLogger(__name__)


def run_econ_calendar(force=False):
    """Obtiene calendario económico USA para HOY e interpreta cada evento."""
    hoy = datetime.date.today()
    fecha = hoy.strftime("%d/%m/%Y")

    logger.info("Obteniendo calendario económico USA...")

    try:
        data = investpy.economic_calendar.get_economic_calendar(
            countries=["united states"],
            from_date=fecha,
            to_date=fecha
        )
    except Exception as e:
        msg = f"⚠️ Error al obtener calendario económico:\n{e}"
        send_telegram_message(msg)
        logger.error(msg)
        return

    if data.empty:
        send_telegram_message("📭 *Hoy no hay eventos económicos relevantes en USA.*")
        return

    eventos = []

    for _, row in data.iterrows():
        hora = row["time"] or "Sin hora"
        nombre = row["event"]
        anterior = row["previous"] if not str(row["previous"]) == "nan" else "-"
        actual = row["actual"] if not str(row["actual"]) == "nan" else "-"
        forecast = row["forecast"] if not str(row["forecast"]) == "nan" else "-"
        impacto = row["importance"]

        estrellas = "⭐" * impacto if impacto > 0 else ""

        texto_base = (
            f"{estrellas} *{nombre}*\n"
            f"🕒 {hora}\n"
            f"*Actual:* {actual} | *Previsión:* {forecast} | *Anterior:* {anterior}\n"
        )

        prompt = (
            "Interpreta en 2–3 líneas el impacto en mercados USA y USD de este dato "
            "económico. Estilo claro, profesional. No digas que eres IA:\n\n"
            f"Evento: {nombre}\n"
            f"Actual: {actual}\n"
            f"Previsión: {forecast}\n"
            f"Anterior: {anterior}"
        )

        interpretacion = call_gpt_mini(prompt)
        if not interpretacion:
            interpretacion = "Sin interpretación disponible."

        eventos.append(texto_base + f"💬 {interpretacion}\n")

    mensaje_final = "🇺🇸 *Calendario económico USA — Hoy*\n\n" + "\n".join(eventos)
    send_telegram_message(mensaje_final)
    logger.info("Calendario económico enviado.")
