import os
import datetime as dt

import investpy
import pandas as pd

from utils import send_telegram_message, call_gpt_mini

# ===========================
#  CONFIG DESDE ENV VARS
# ===========================
COUNTRY = os.getenv("COUNTRY", "united states")
IMPACT_MIN = int(os.getenv("IMPACT_MIN", "2"))  # 2–3 estrellas

FORCE_DATE_FROM = os.getenv("FORCE_DATE_FROM", "").strip()  # dd/mm/yyyy o vacío
FORCE_DATE_TO = os.getenv("FORCE_DATE_TO", "").strip()      # dd/mm/yyyy o vacío


# ===========================
#  FECHAS SEGURAS PARA INVESTPY
# ===========================
def _parse_date_or_none(s: str):
    if not s:
        return None
    return dt.datetime.strptime(s, "%d/%m/%Y").date()


def get_date_range():
    """Devuelve (from_date_str, to_date_str) en formato dd/mm/yyyy,
    garantizando SIEMPRE que to_date > from_date.
    """

    today = dt.date.today()

    from_dt = _parse_date_or_none(FORCE_DATE_FROM) or today
    to_dt = _parse_date_or_none(FORCE_DATE_TO)

    if to_dt is None:
        # Si no hay TO, usamos from + 1 día
        to_dt = from_dt + dt.timedelta(days=1)

    # 🔒 Blindaje: investpy exige to_date > from_date
    if to_dt <= from_dt:
        to_dt = from_dt + dt.timedelta(days=1)

    from_str = from_dt.strftime("%d/%m/%Y")
    to_str = to_dt.strftime("%d/%m/%Y")

    print(f"[INFO] Rango fechas econ_calendar: {from_str} -> {to_str}")
    return from_str, to_str, from_dt


# ===========================
#  OBTENER DATAFRAME CALENDARIO
# ===========================
def get_calendar_df():
    from_str, to_str, from_dt = get_date_range()

    try:
        df = investpy.economic_calendar(
            countries=[COUNTRY],
            from_date=from_str,
            to_date=to_str,
        )
    except Exception as e:
        raise RuntimeError(f"Error al obtener calendario de investpy: {e}")

    if df is None or df.empty:
        return None

    # Normalizar columnas
    df = df.copy()
    if "date" in df.columns:
        # investpy devuelve date en formato dd/mm/yyyy (string)
        # Nos quedamos solo con el primer día del rango (from_date)
        df = df[df["date"] == from_str]

    if df.empty:
        return None

    # ===========================
    #  FILTRO IMPACTO 2–3 ESTRELLAS
    # ===========================
    if "importance" in df.columns:
        imp = df["importance"].astype(str).str.lower()
        mask = imp.isin(["2", "3", "medium", "high"])
        df = df[mask]
    elif "impact" in df.columns:
        imp = df["impact"].astype(str)
        df = df[imp.isin(["2", "3"])]  # por si usa columna impact
    else:
        raise RuntimeError("La respuesta de investpy no tiene columna 'importance' ni 'impact'.")

    if df.empty:
        return None

    # ===========================
    #  ORDENAR Y LIMITAR EVENTOS
    # ===========================
    # importancia numérica: high/3 primero
    def _rank_importance(x):
        x = str(x).lower()
        if x in ("3", "high"):
            return 0
        if x in ("2", "medium"):
            return 1
        return 2

    df["imp_rank"] = df["importance"].apply(_rank_importance) if "importance" in df.columns else 1

    # Aseguramos columnas de tiempo
    if "time" not in df.columns:
        df["time"] = ""

    df = df.sort_values(by=["imp_rank", "date", "time"])

    # Quitamos duplicados por evento
    df = df.drop_duplicates(subset=["event", "date", "time"], keep="first")

    # Limite duro para no pasarnos de longitud en Telegram
    df = df.head(8)

    return df


# ===========================
#  INTERPRETACIÓN DE CADA EVENTO (IA)
# ===========================
def interpret_event_short(title: str):
    """Una frase corta explicando por qué importa el dato."""
    prompt = (
        "Resume en una sola frase, clara y concreta, por qué este dato económico "
        "puede ser importante para los mercados (índices USA y USD). "
        "No menciones IA ni que estás analizando nada técnico.\n\n"
        f"Dato: {title}"
    )

    try:
        txt = call_gpt_mini(prompt).strip()
        # Por si acaso la respuesta viene muy larga, recortamos a ~200 caracteres
        if len(txt) > 220:
            txt = txt[:217].rstrip() + "..."
        return txt
    except Exception as e:
        print("[WARN] Error interpretando evento con IA:", e)
        return "Dato relevante que puede mover índices USA o el dólar."


# ===========================
#  CONSTRUIR MENSAJE TELEGRAM
# ===========================
def build_calendar_message(df):
    if df is None or df.empty:
        return "⚠️ Hoy no hay eventos económicos relevantes en EE. UU. (2–3⭐)."

    # Fecha cabecera = primer día del DF
    fecha = df["date"].iloc[0]
    msg_lines = []
    msg_lines.append(f"📅 <b>Calendario económico USA — {fecha}</b>")
    msg_lines.append("")  # línea en blanco

    for _, row in df.iterrows():
        title = str(row.get("event", "")).strip()
        time = str(row.get("time", "")).strip()
        importance = str(row.get("importance", "")).lower()

        star = "⭐⭐⭐" if importance in ("3", "high") else "⭐⭐"

        actual = row.get("actual", "")
        forecast = row.get("forecast", "")
        previous = row.get("previous", "")

        line = []
        line.append(f"{star} <b>{title}</b>")
        if time:
            line.append(f"🕒 {time}")

        # Datos numéricos si existen
        num_parts = []
        if actual not in ("", None, "-", "nan"):
            num_parts.append(f"Actual: {actual}")
        if forecast not in ("", None, "-", "nan"):
            num_parts.append(f"Previsión: {forecast}")
        if previous not in ("", None, "-", "nan"):
            num_parts.append(f"Anterior: {previous}")

        if num_parts:
            line.append(" | ".join(num_parts))

        # Interpretación IA
        comentario = interpret_event_short(title)
        line.append(f"💬 {comentario}")

        msg_lines.append("\n".join(line))
        msg_lines.append("")  # línea en blanco entre eventos

    msg = "\n".join(msg_lines).strip()
    # Seguridad por si nos acercamos al límite de Telegram (4096 chars)
    if len(msg) > 3800:
        msg = msg[:3790].rstrip() + "\n\n[Mensaje recortado por longitud.]"

    return msg


# ===========================
#  FUNCIÓN PÚBLICA: RUN
# ===========================
def run_econ_calendar():
    print("[INFO] Obteniendo calendario económico USA...")

    try:
        df = get_calendar_df()
    except Exception as e:
        err_msg = f"⚠️ Error al obtener calendario económico:\n{e}"
        print("[ERROR]", err_msg)
        send_telegram_message(err_msg)
        return

    if df is None or df.empty:
        print("[INFO] No hay eventos relevantes hoy.")
        send_telegram_message("📅 Hoy no hay eventos económicos relevantes en EE. UU. (2–3⭐).")
        return

    msg = build_calendar_message(df)
    send_telegram_message(msg)
    print("[INFO] Calendario económico enviado correctamente.")
