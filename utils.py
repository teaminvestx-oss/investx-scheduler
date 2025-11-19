import os
import logging
import requests
from openai import OpenAI

logger = logging.getLogger(__name__)

# -------- TELEGRAM --------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")


def send_telegram_message(text: str):
    """
    Envía texto a Telegram. Si es muy largo, lo trocea en varios mensajes.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram: faltan TELEGRAM_TOKEN o CHAT_ID / TELEGRAM_CHAT_ID.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    # Límite duro de Telegram ~4096; dejamos margen
    max_len = 3500
    if not text:
        text = "(Mensaje vacío)"

    parts = [text[i:i + max_len] for i in range(0, len(text), max_len)]

    for idx, part in enumerate(parts, start=1):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": part,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=10)

        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}

        if not resp.ok or not data.get("ok"):
            logger.warning(
                "Telegram: error al enviar parte %s/%s: status=%s resp=%s",
                idx, len(parts), resp.status_code, data,
            )
        else:
            logger.info(
                "Telegram: mensaje %s/%s enviado correctamente.",
                idx, len(parts)
            )


# -------- OPENAI (mini) --------
_openai_api_key = os.getenv("OPENAI_API_KEY")
_client = OpenAI(api_key=_openai_api_key) if _openai_api_key else None


def call_gpt_mini(system_prompt: str, user_prompt: str, max_tokens: int = 600) -> str:
    """
    Llama a un modelo ligero de OpenAI para generar texto breve.
    Si hay cualquier error, devuelve cadena vacía y se loguea.
    """
    if not _client:
        logger.warning("OpenAI: falta OPENAI_API_KEY; no se llama a la IA.")
        return ""

    try:
        resp = _client.responses.create(
            model="gpt-4.1-mini",  # <- modelo ligero disponible
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_output_tokens=max_tokens,
        )
        text = resp.output[0].content[0].text
        return text.strip()
    except Exception as e:
        logger.warning(f"OpenAI: error llamando a gpt mini: {e}")
        return ""
