# buenosdias.py (versión mejorada)
import os, argparse, datetime, logging
import requests
from requests.adapters import HTTPAdapter, Retry
from dateutil import tz

# ------------------------- Utilidades -------------------------
def get_env(name, *aliases) -> str:
    """Lee una env var con posibles alias (y error claro si falta)."""
    for k in (name, *aliases):
        v = os.environ.get(k)
        if v:
            return v
    alias_txt = (", alias: " + ", ".join(aliases)) if aliases else ""
    raise RuntimeError(f"Falta la variable de entorno: {name}{alias_txt}")

def make_session(timeout=20) -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["POST"])
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.request_timeout = timeout
    return s

def telegram_send(text: str, *, token: str, chat_id: str, session: requests.Session, disable_preview=True) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    r = session.post(url, data=payload, timeout=session.request_timeout)
    r.raise_for_status()

# ------------------------- Mensajes -------------------------
MENSAJES = [
    "🌞 Buenos días equipo, arrancamos este {dia} con InvestX. Mercados listos, foco y disciplina.",
    "📊 ¡Buenos días traders! Hoy es {dia}. En InvestX seguimos marcando niveles clave.",
    "☕ Café en mano y gráficos en pantalla: así empieza el {dia} en InvestX.",
    "🚀 Buenos días 👋. Recuerda: menos teoría, más acción. Filosofía InvestX.",
    "📈 Arrancamos este {dia} con setups claros. La oportunidad está ahí, InvestX te la acerca.",
    "🔔 Buenos días desde InvestX. Mercado abierto, cabeza fría y estrategia por delante.",
    "⚡ El trading nunca fue tan simple: buenos días y feliz {dia} con InvestX.",
    "💡 Buenos días. Hoy en InvestX toca constancia y paciencia, claves para ganar.",
]

DIAS = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]

def build_message(dt_local: datetime.datetime, *, override_text: str | None = None) -> str:
    """Mensaje final. Permite sobreescribir con --message."""
    if override_text:
        return override_text
    d = DIAS[dt_local.weekday()]
    idx = stable_index(dt_local, len(MENSAJES))
    return MENSAJES[idx].format(dia=d)

def stable_index(dt: datetime.datetime, n: int) -> int:
    """
    Selección determinista que cambia cada día y *tramo horario*,
    reduciendo repeticiones si el cron corre varias veces.
    """
    # Tramo horario (ej.: cada 3h un bloque distinto)
    bucket = dt.hour // 3
    # Combinación de ordinal, semana ISO y bucket
    base = (dt.toordinal() * 17 + dt.isocalendar().week * 7 + bucket) % n
    return base

# ------------------------- Main -------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tz", default="Europe/Madrid", help="Zona horaria local (p.ej. Europe/Madrid)")
    ap.add_argument("--allow-weekend", action="store_true", help="Permite enviar también Sábado y Domingo")
    ap.add_argument("--force", action="store_true", help="Fuerza envío aunque no sea L–V")
    ap.add_argument("--dry-run", action="store_true", help="No envía, solo imprime el mensaje")
    ap.add_argument("--message", help="Mensaje custom (HTML permitido)")
    ap.add_argument("--verbose", action="store_true", help="Log INFO")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s: %(message)s")

    now_utc = datetime.datetime.utcnow().replace(tzinfo=tz.UTC)
    local_tz = tz.gettz(args.tz)
    now_local = now_utc.astimezone(local_tz)
    wd = now_local.weekday()  # 0=lunes … 6=domingo

    if not args.force and not args.allow_weekend and wd > 4:
        logging.info("Fin de semana: no se envía (use --allow-weekend o --force para enviar).")
        return

    msg = build_message(now_local, override_text=args.message)

    if args.dry_run:
        print(msg)
        return

    token  = get_env("INVESTX_TOKEN", "TELEGRAM_BOT_TOKEN")
    chat_id = get_env("CHAT_ID", "TELEGRAM_CHAT_ID")

    session = make_session()
    try:
        telegram_send(msg, token=token, chat_id=chat_id, session=session)
        logging.info("Mensaje enviado correctamente.")
    except Exception as e:
        logging.error(f"Error enviando el mensaje: {e}")
        raise

if __name__ == "__main__":
    main()
