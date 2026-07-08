"""Alert command handlers: /alert, /alerts, /cancelalert."""

from src.core.logging import get_logger
from src.core.utils import format_price, generate_id
from src.database.connection import DatabaseManager
from src.telegram.features.price_alerts import PriceAlertEngine
from src.telegram.router import MessageRouter

log = get_logger("telegram")


class AlertHandler:
    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.s = services
        self.engine = PriceAlertEngine(db)

    async def set_alert(self, update, context) -> None:
        """Usage: /alert BTC above 72000"""
        args = context.args if context.args else []
        if len(args) < 3:
            await update.message.reply_text("Usage: /alert BTC above 72000")
            return
        symbol = MessageRouter._normalize_symbol(args[0])
        condition = args[1].lower()
        if condition not in ("above", "below"):
            await update.message.reply_text("Condition must be 'above' or 'below'")
            return
        try:
            target = float(args[2])
        except ValueError:
            await update.message.reply_text("Invalid price")
            return

        ticker = None
        try:
            ticker = await self.s["market_service"].get_ticker(symbol)
        except Exception:
            pass

        current = ticker.last_price if ticker else 0
        alert = await self.engine.create_alert(
            update.effective_chat.id, symbol, condition, target, current,
        )
        await update.message.reply_text(
            f"\U0001f514 Alert set: {symbol} {condition} ${format_price(target)}\n"
            f"Current: ${current:,.2f}\nID: {alert.id}"
        )

    async def list_alerts(self, update, context) -> None:
        alerts = await self.engine.get_user_alerts(update.effective_chat.id)
        if not alerts:
            await update.message.reply_text("No active alerts. Set one with /alert BTC above 72000")
            return
        msg = "\U0001f514 <b>YOUR ALERTS</b>\n\n"
        for a in alerts:
            msg += f"\u2022 {a['symbol']} {a['condition']} ${format_price(a['target_price'])} (ID: {a['id'][:8]})\n"
        await update.message.reply_text(msg, parse_mode="HTML")

    async def cancel_alert(self, update, context) -> None:
        args = context.args if context.args else []
        if not args:
            await update.message.reply_text("Usage: /cancelalert pa_xxxxx")
            return
        await self.engine.cancel_alert(args[0])
        await update.message.reply_text(f"\u274c Alert {args[0]} cancelled.")
