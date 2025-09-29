# buenosdias.py
import os, random, argparse, datetime, requests
from dateutil import tz

def get_env(name, *fallbacks):
    """Lee una env var con posibles alias (por compatibilidad)."""
    for k in (name, *fallbacks):
        v = os.environ.get(k)
        if v:
            return v
    raise RuntimeError(f"Falta la variable de entorno: {name} (o alias {fallbacks})")

def telegram_send(text: str) -> None:
    # Usa tus nombres en Render
    token  = get_env("INVESTX_TOKEN", "TELEGRAM_BOT_TOKEN")  # alias opcional
    chat_id = get_env("CHAT_ID", "TELEGRAM_CHAT_ID")         # alias opcional

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, data=payload, timeout=20)
    r.raise_for_status()

def build_message(dt_local: datetime.datetime) -> str:
    dias = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    d = dias[dt_local.weekday()]

    mensajes = [
        "🌞 Buenos días equipo, arrancamos este {dia} con InvestX. Mercados listos, foco y disciplina.",
        "📊 ¡Buenos días traders! Hoy es {dia}. En InvestX seguimos marcando niveles clave.",
        "☕ Café en mano y gráficos en pantalla: así empieza el {dia} en InvestX.",
        "🚀 Buenos días 👋. Recuerda: menos teoría, más acción. Filosofía InvestX.",
        "📈 Arrancamos este {dia} con setups claros. La oportunidad está ahí, InvestX te la acerca.",
        "🔔 Buenos días desde InvestX. Mercado abierto, cabeza fría y estrategia por delante.",
        "⚡ El trading nunca fue tan simple: buenos días y feliz {dia} con InvestX.",
        "💡 Buenos días. Hoy en InvestX toca constancia y paciencia, claves para ganar.",
    ]

    # Evita repetición: “elige” por fecha (pero con aspecto aleatorio)
    idx = (dt_local.toordinal() * 7 + dt_local.hour) % len(mensajes)
    return mensajes[idx].format(dia=d)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tz", default="Europe/Madrid", help="Zona horaria local")
    args = ap.parse_args()

    now_utc = datetime.datetime.utcnow().replace(tzinfo=tz.UTC)
    local_tz = tz.gettz(args.tz)
    now_local = now_utc.astimezone(local_tz)

    # Solo L–V (0=lunes … 4=viernes)
    if now_local.weekday() > 4:
        return

    msg = build_message(now_local)
    telegram_send(msg)

if __name__ == "__main__":
    main()
