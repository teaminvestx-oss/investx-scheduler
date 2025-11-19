import os
import logging
import requests
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

client = OpenAI()

# -------------------------
# FUNCIONES GENERALES
# -------------------------

def send_telegram_message(text: str):
    """Envia un mensaje a Telegram y maneja errores de longitud."""
    try:
        if len(text) > 3900:
            partes = []
            while len(text) > 3900:
                partes.append(text[:3900])
                text = text[3900:]
            partes.append(text)

            for p in partes:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    data={"chat_id": TELEGRAM_CHAT_ID, "text": p, "parse_mode": "Markdown"},
                )
            return

        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        )

    except Exception as e:
        logger.error(f"Telegram error: {e}")


def call_gpt_mini(prompt: str, max_tokens: int = 600) -> str:
    """Llama a GPT-4o-mini para obtener una interpretación."""
    try:
        resp = client.responses.create(
            model="gpt-4o-mini",   # ← MODELO CORRECTO
            input=prompt,
            max_output_tokens=max_tokens,
        )
        return resp.output[0].content[0].text
    except Exception as e:
        logger.warning(f"[WARN] Error llamando a OpenAI: {e}")
        return None
