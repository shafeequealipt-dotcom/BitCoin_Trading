"""Shadow Telegram bot — setup, lifecycle, and trade alerts.

Creates and manages the Telegram bot that provides the "exchange view"
of Shadow: wallet dashboard, positions, trade history, stats, and controls.
"""

import os
from typing import Any

from telegram import BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from src.telegram.handlers import (
    wallet_handler,
    positions_handler,
    history_handler,
    history_page_callback,
    stats_handler,
    market_handler,
    daily_handler,
    health_handler,
    reset_handler,
    reset_callback,
    pause_handler,
    resume_handler,
    settings_handler,
    export_handler,
    cleanup_handler,
    start_handler,
)
from src.utils.logging import get_logger

log = get_logger("telegram")


async def create_bot(
    config: Any,
    wallet: Any,
    order_engine: Any,
    position_monitor: Any,
    ws_manager: Any,
    db: Any,
) -> Application | None:
    """Create and configure the Telegram bot application.

    Returns None if the bot token is not configured.
    """
    token = os.environ.get("SHADOW_TELEGRAM_BOT_TOKEN", "")
    if not token or token == "your_bot_token_here":
        log.warning("Telegram bot token not configured — bot will not start")
        return None

    if not config.telegram.chat_id:
        log.error("Telegram chat_id not configured — bot will not start")
        return None

    app = Application.builder().token(token).build()

    # Store service references for handlers
    app.bot_data["wallet"] = wallet
    app.bot_data["order_engine"] = order_engine
    app.bot_data["monitor"] = position_monitor
    app.bot_data["ws_manager"] = ws_manager
    app.bot_data["db"] = db
    app.bot_data["config"] = config
    app.bot_data["chat_id"] = config.telegram.chat_id

    # Register command handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("wallet", wallet_handler))
    app.add_handler(CommandHandler("positions", positions_handler))
    app.add_handler(CommandHandler("history", history_handler))
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(CommandHandler("market", market_handler))
    app.add_handler(CommandHandler("daily", daily_handler))
    app.add_handler(CommandHandler("health", health_handler))
    app.add_handler(CommandHandler("reset", reset_handler))
    app.add_handler(CommandHandler("pause", pause_handler))
    app.add_handler(CommandHandler("resume", resume_handler))
    app.add_handler(CommandHandler("settings", settings_handler))
    app.add_handler(CommandHandler("export", export_handler))
    app.add_handler(CommandHandler("cleanup", cleanup_handler))

    # Callback query handlers (for inline buttons)
    app.add_handler(CallbackQueryHandler(history_page_callback, pattern=r"^history_page_"))
    app.add_handler(CallbackQueryHandler(reset_callback, pattern=r"^reset_"))

    log.info("Telegram bot configured with {n} commands", n=13)
    return app


async def start_bot(app: Application) -> None:
    """Initialize and start the bot polling."""
    await app.initialize()

    # Set bot menu commands
    commands = [
        BotCommand("wallet", "Wallet dashboard — equity, PnL, balance"),
        BotCommand("positions", "Open positions with live PnL"),
        BotCommand("history", "Trade history with pagination"),
        BotCommand("stats", "Performance report — win rate, profit factor"),
        BotCommand("market", "Market overview — top gainers & losers"),
        BotCommand("daily", "Today's trading summary"),
        BotCommand("health", "System health — WebSocket, DB, monitor"),
        BotCommand("settings", "Exchange settings — fees, slippage"),
        BotCommand("export", "Export trade history as CSV"),
        BotCommand("pause", "Pause exchange — stop new orders"),
        BotCommand("resume", "Resume exchange — accept orders"),
        BotCommand("reset", "Reset wallet to a new balance"),
        BotCommand("cleanup", "Run data retention cleanup"),
    ]
    await app.bot.set_my_commands(commands)
    log.info("Telegram bot menu set with {n} commands", n=len(commands))

    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("Telegram bot started polling")


async def stop_bot(app: Application) -> None:
    """Stop the bot gracefully."""
    try:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        log.info("Telegram bot stopped")
    except Exception as e:
        log.warning("Bot shutdown error: {err}", err=str(e))


async def send_trade_open_alert(app: Application, trade_data: dict) -> None:
    """Send automatic alert when a trade opens."""
    chat_id = app.bot_data.get("chat_id")
    symbol = trade_data.get("symbol", "?")
    side = trade_data.get("side", "?")
    price = trade_data.get("price", 0)
    fee = trade_data.get("fee", 0)
    margin = trade_data.get("margin", 0)
    leverage = trade_data.get("leverage", 1)
    notional = trade_data.get("notional", 0)

    coin = symbol.replace("USDT", "")
    direction = "long" if side == "Buy" else "short"

    text = (
        f"📈 <b>Position opened</b>\n"
        f"{coin} {direction} {leverage}x\n"
        f"Entry: ${price:,.2f}\n"
        f"Size: ${notional:,.2f} (margin: ${margin:,.2f})\n"
        f"Fee: ${fee:,.4f}"
    )

    try:
        await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as e:
        log.warning("Failed to send trade open alert: {err}", err=str(e))


async def send_trade_close_alert(app: Application, close_data: dict) -> None:
    """Send automatic alert when a trade closes."""
    chat_id = app.bot_data.get("chat_id")
    symbol = close_data.get("symbol", "?")
    side = close_data.get("side", "?")
    result = close_data.get("result", "loss")
    entry = close_data.get("entry_price", 0)
    exit_p = close_data.get("exit_price", 0)
    net_pnl = close_data.get("net_pnl_usd", 0)
    gross_pct = close_data.get("gross_pnl_pct", 0)
    trigger = close_data.get("close_trigger", "manual")
    hold = close_data.get("hold_duration_seconds", 0)
    exit_fee = close_data.get("exit_fee", 0)

    coin = symbol.replace("USDT", "")
    direction = "long" if side == "Buy" else "short"
    emoji = "🟢 WIN" if result == "win" else "🔴 LOSS"
    hold_str = _format_duration(hold)

    text = (
        f"{emoji} — {coin} {direction} closed\n"
        f"Entry: ${entry:,.2f} → Exit: ${exit_p:,.2f}\n"
        f"PnL: {gross_pct:+.2f}%\n"
        f"Fee: ${exit_fee:,.4f} | Net: ${net_pnl:+,.2f}\n"
        f"Held: {hold_str}\n"
        f"Trigger: {trigger}"
    )

    try:
        await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as e:
        log.warning("Failed to send trade close alert: {err}", err=str(e))


def _format_duration(seconds: int) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds} seconds"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes // 60
    mins = minutes % 60
    if hours < 24:
        return f"{hours}h {mins}m"
    days = hours // 24
    hrs = hours % 24
    return f"{days}d {hrs}h"
