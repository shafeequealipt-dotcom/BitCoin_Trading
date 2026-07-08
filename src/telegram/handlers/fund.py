"""Fund Manager Telegram handler — /fund, /floor, /setwallet commands."""

from src.core.log_context import ctx
from src.core.logging import get_logger

log = get_logger("telegram")


class FundManagerHandler:
    def __init__(self, db, services: dict) -> None:
        self.db = db
        self.s = services

    async def floor_status(self, update, context) -> None:
        """Handle /floor command — show profit floor status with action buttons."""
        fm = self.s.get("fund_manager")
        if not fm or not fm._account_state:
            await update.message.reply_text("Fund Manager not active.")
            return

        state = fm._account_state
        equity = state.total_equity
        floor = state.profit_floor or 0
        is_paused = getattr(fm, "_floor_breach_paused", False)
        override_until = getattr(fm, "_floor_override_until", None)

        import time
        status_str = "Trading active"
        if is_paused:
            status_str = "Trading PAUSED (below floor)"
        elif override_until and time.time() < override_until:
            remaining = int((override_until - time.time()) / 60)
            status_str = f"Override active ({remaining}min remaining)"

        if floor > 0:
            diff = equity - floor
            diff_pct = (diff / floor) * 100 if floor > 0 else 0
            label = "Surplus" if diff >= 0 else "Deficit"
            msg = (
                f"<b>PROFIT FLOOR STATUS</b>\n\n"
                f"<b>Status:</b> {status_str}\n"
                f"<b>Current equity:</b> ${equity:,.2f}\n"
                f"<b>Locked floor:</b> ${floor:,.2f}\n"
                f"<b>{label}:</b> ${abs(diff):,.2f} ({diff_pct:+.1f}%)\n"
            )
        else:
            msg = (
                f"<b>PROFIT FLOOR STATUS</b>\n\n"
                f"<b>Status:</b> {status_str}\n"
                f"<b>Current equity:</b> ${equity:,.2f}\n"
                f"<b>Locked floor:</b> None (no protection active)\n"
            )

        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Reset to current", callback_data="floor_reset"),
                    InlineKeyboardButton("Lower 10%", callback_data="floor_lower10"),
                ],
                [
                    InlineKeyboardButton("Remove floor", callback_data="floor_remove"),
                    InlineKeyboardButton("Override 1h", callback_data="floor_override_1h"),
                ],
            ])
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=keyboard)
        except ImportError:
            await update.message.reply_text(msg, parse_mode="HTML")

    async def handle_floor_callback(self, query) -> None:
        """Handle profit floor inline button presses."""
        fm = self.s.get("fund_manager")
        if not fm:
            await query.edit_message_text("Fund Manager not available.")
            return

        action = query.data.replace("floor_", "")
        response = await fm.handle_floor_action(action)
        await query.edit_message_text(response, parse_mode="HTML")
        log.info("Floor action: {act} by user {uid}", act=action, uid=query.from_user.id)

    async def status(self, update, context) -> None:
        fm = self.s.get("fund_manager")
        if not fm:
            await update.message.reply_text("Fund Manager not active.")
            return

        try:
            status = await fm.get_full_status()
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")
            return

        acc = status["account"]
        pools = status["pools"]
        w = status["risk_weather"]
        weather_emoji = {
            "clear": "\U0001f7e2", "cloudy": "\U0001f7e1",
            "stormy": "\U0001f7e0", "hurricane": "\U0001f534",
            "nuclear": "\U0001f480",
        }

        msg = "\U0001f4b0 <b>INTELLIGENT FUND MANAGER</b>\n\n"
        msg += f"\U0001f4ca Level: <b>{acc['level'].upper()}</b> ({acc['growth']} growth)\n"
        msg += f"\U0001f4b5 Equity: ${acc['equity']:,.0f}\n"
        msg += f"\U0001f513 Unlocked: {acc['unlock_pct']}% = ${acc['trading_capital']:,.0f}\n"
        msg += f"\U0001f4c8 In Use: ${acc['in_use']:,.0f}\n"
        msg += f"\U0001f4b0 Available: ${acc['available']:,.0f}\n"
        msg += f"\U0001f512 Locked Profits: ${acc['locked_profits']:,.0f}\n"
        msg += f"\U0001f6e1\ufe0f Profit Floor: ${acc['profit_floor']:,.0f}\n\n"

        msg += "<b>Capital Pools:</b>\n"
        msg += f"  Active: ${pools['active']:,.0f}\n"
        msg += f"  A+ Reserve: ${pools['aplus_reserve']:,.0f}\n"
        msg += f"  Emergency: ${pools['emergency']:,.0f}\n\n"

        we = weather_emoji.get(w["level"], "\u26aa")
        msg += f"{we} Risk Weather: <b>{w['level'].upper()}</b> (x{w['multiplier']:.1f})\n"
        msg += f"\U0001f631 Emotion: {status['market_emotion'].upper()}\n"
        eh = status["ecosystem_health"]
        msg += f"\U0001f3e5 Ecosystem: {eh['status']} ({eh['score']}/100)\n"

        cv = status["capital_velocity"]
        msg += f"\U0001f3ce\ufe0f Velocity: {cv['current']:.1f}x ({cv['status']})\n"

        if status["recovery"]["active"]:
            msg += f"\n\u26a0\ufe0f <b>RECOVERY MODE: {status['recovery']['progress']}</b>\n"

        nl = status["next_level"]
        if nl["target"]:
            msg += (
                f"\n\U0001f4c8 Next level at {nl['target']}x "
                f"(currently {nl['current_multiplier']:.2f}x)"
            )

        await update.message.reply_text(msg, parse_mode="HTML")

    async def set_wallet(self, update, context) -> None:
        """Handle /setwallet <amount> — update fund manager starting balance.

        Usage: /setwallet 5000
        This resets the reference point for growth calculations (level, unlock %,
        capital tiers). Does NOT change actual exchange wallet balance.
        """
        fm = self.s.get("fund_manager")
        if not fm:
            await update.message.reply_text("Fund Manager not active.")
            return

        args = context.args
        if not args or len(args) < 1:
            state = fm._account_state
            current_starting = state.starting_balance if state else 0
            current_equity = state.total_equity if state else 0
            await update.message.reply_text(
                "<b>SET WALLET STARTING BALANCE</b>\n\n"
                f"<b>Current starting balance:</b> ${current_starting:,.2f}\n"
                f"<b>Current live equity:</b> ${current_equity:,.2f}\n\n"
                "<b>Usage:</b> <code>/setwallet 5000</code>\n"
                "Sets the reference point for growth calculations.\n"
                "Use <code>/setwallet reset</code> to reset to current equity.",
                parse_mode="HTML",
            )
            return

        try:
            if args[0].lower() == "reset":
                new_balance = fm._account_state.total_equity
            else:
                new_balance = float(args[0].replace(",", "").replace("$", ""))
                if new_balance <= 0:
                    await update.message.reply_text("Amount must be positive.")
                    return
        except ValueError:
            await update.message.reply_text(
                "Invalid amount. Usage: <code>/setwallet 5000</code>",
                parse_mode="HTML",
            )
            return

        old_balance = fm._account_state.starting_balance
        await fm._save_starting_balance(new_balance)
        fm._account_state.starting_balance = new_balance
        await fm.update_state()

        state = fm._account_state
        log.info(
            f"SETWALLET | old=${old_balance:.2f} new=${new_balance:.2f} "
            f"equity=${state.total_equity:.2f} growth={state.growth_multiplier:.2f}x "
            f"level={state.level.value} | {ctx()}"
        )

        await update.message.reply_text(
            "<b>WALLET UPDATED</b>\n\n"
            f"<b>Starting balance:</b> ${old_balance:,.2f} -> ${new_balance:,.2f}\n"
            f"<b>Current equity:</b> ${state.total_equity:,.2f}\n"
            f"<b>Growth:</b> {state.growth_multiplier:.2f}x\n"
            f"<b>Level:</b> {state.level.value.upper()}\n"
            f"<b>Trading capital:</b> ${state.trading_capital:,.2f} "
            f"({state.unlock_pct:.0f}% unlocked)",
            parse_mode="HTML",
        )
