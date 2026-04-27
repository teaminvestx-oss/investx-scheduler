import logging
import os
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from research.access_control import (
    check_and_consume_query,
    get_limit_message,
    get_user,
    register_user_if_new,
)
from research.excel_reader import get_all_active_tickers, get_ticker_data
from research.formatter import format_analysis

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _register(user) -> None:
    register_user_if_new(user.id, user.username or "", user.first_name or "")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _register(update.effective_user)
    await update.message.reply_text(
        "👋 Bienvenido a InvestX Research.\n\n"
        "Tengo cobertura de 100+ activos del S&P y Nasdaq.\n\n"
        "Comandos disponibles:\n"
        "/analiza [ticker] — Análisis completo de un activo\n"
        "/restantes — Consultas disponibles este mes\n"
        "/portfolio — Lista de tickers cubiertos\n\n"
        "Tienes 10 análisis gratuitos al mes."
    )


async def cmd_analiza(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # In groups, redirect to private chat
    if update.message.chat.type != "private":
        bot_username = (await context.bot.get_me()).username
        await update.message.reply_text(
            f"🔒 Los análisis se envían en privado.\n"
            f"Escríbeme aquí: @{bot_username}"
        )
        return

    user = update.effective_user
    _register(user)

    if not context.args:
        await update.message.reply_text(
            "Uso: /analiza [TICKER]\nEjemplo: /analiza AAPL"
        )
        return

    ticker = context.args[0].upper()

    # Check ticker coverage before consuming a query slot
    data = get_ticker_data(ticker)
    if data is None:
        await update.message.reply_text(
            f"📊 ${ticker} no está en el portfolio InvestX.\n\n"
            "Para solicitar cobertura contacta con @investx_admin.\n"
            "Los tickers más solicitados se incorporan cada semana."
        )
        return

    allowed, db_user = check_and_consume_query(user.id)
    if not allowed:
        await update.message.reply_text(get_limit_message(db_user))
        return

    queries_limit = db_user.get("queries_limit", 10)
    queries_remaining = queries_limit - db_user.get("queries_this_month", 0)

    await update.message.reply_text("⏳ Generando análisis...")

    try:
        analysis = format_analysis(data, queries_remaining, queries_limit)
        logger.info(
            "query telegram_id=%s ticker=%s ts=%s",
            user.id,
            ticker,
            datetime.utcnow().isoformat(),
        )
        await update.message.reply_text(analysis)
    except Exception as e:
        logger.error("Error generating analysis for %s: %s", ticker, e)
        await update.message.reply_text(
            "❌ Error generando el análisis. Inténtalo de nuevo."
        )


async def cmd_restantes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    _register(user)

    db_user = get_user(user.id)
    if not db_user:
        await update.message.reply_text("❌ Error obteniendo tus datos.")
        return

    used = db_user.get("queries_this_month", 0)
    limit = db_user.get("queries_limit", 10)
    remaining = limit - used
    reset = db_user.get("reset_date", "próximo mes")

    await update.message.reply_text(
        f"📊 Consultas restantes: {remaining}/{limit}\n"
        f"🔄 Renovación: {reset}"
    )


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tickers = get_all_active_tickers()
    if not tickers:
        await update.message.reply_text(
            "📊 No hay tickers activos en el portfolio actualmente."
        )
        return

    # Send in chunks to respect Telegram's 4096-char limit
    chunk_size = 50
    header = "📊 Portfolio InvestX — Tickers cubiertos:\n\n"
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        prefix = header if i == 0 else ""
        await update.message.reply_text(prefix + " · ".join(chunk))


def run_bot() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("analiza", cmd_analiza))
    app.add_handler(CommandHandler("restantes", cmd_restantes))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    logger.info("InvestX research bot polling…")
    app.run_polling()


if __name__ == "__main__":
    run_bot()
