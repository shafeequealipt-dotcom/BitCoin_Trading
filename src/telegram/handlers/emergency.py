"""Emergency command handler: close all positions with double confirmation."""

from src.core.logging import get_logger
from src.database.connection import DatabaseManager

log = get_logger("telegram")


class EmergencyHandler:
    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.s = services

    async def execute(self, update, context) -> None:
        """Show emergency confirmation with inline buttons."""
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            positions = await self.s["position_service"].get_positions()
            if not positions:
                await update.message.reply_text("No open positions to close.")
                return

            msg = (
                f"\U0001f6a8 <b>EMERGENCY CLOSE ALL</b>\n\n"
                f"This will close ALL {len(positions)} positions at market price.\n"
                f"This action CANNOT be undone.\n\n"
            )
            for p in positions:
                side = "LONG" if p.side.value == "Buy" else "SHORT"
                msg += f"\u2022 {p.symbol} {side} PnL=${p.unrealized_pnl:+,.2f}\n"

            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("\U0001f6a8 CONFIRM CLOSE ALL", callback_data="confirm_emergency"),
                InlineKeyboardButton("\u274c Cancel", callback_data="cancel_emergency"),
            ]])
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)
        except ImportError:
            await update.message.reply_text("Type CONFIRM EMERGENCY to close all positions.")
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def confirm_execute(self, update, context, params=None) -> None:
        """Actually close all positions after confirmation."""
        try:
            orders = await self.s["position_service"].close_all_positions()
            await update.callback_query.edit_message_text(
                f"\U0001f6a8 EMERGENCY: Closed {len(orders)} positions at market."
            )
            log.warning("EMERGENCY: closed {n} positions via Telegram", n=len(orders))
        except Exception as e:
            await update.callback_query.edit_message_text(f"\u274c Emergency close failed: {e}")

    async def cancel(self, update, context, params=None) -> None:
        await update.callback_query.edit_message_text("\u2705 Emergency cancelled. Positions unchanged.")
