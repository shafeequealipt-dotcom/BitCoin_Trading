"""Trading command handlers: buy, sell, close with risk checking."""

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import OrderType, Side
from src.database.connection import DatabaseManager
from src.telegram.features.risk_checker import RiskChecker
from src.telegram.router import MessageRouter
from src.telegram.ui.cards import risk_check_card

log = get_logger("telegram")


class TradingHandler:
    def __init__(self, db: DatabaseManager, services: dict) -> None:
        self.db = db
        self.s = services
        self.risk_checker = RiskChecker(services)

    async def handle(self, update, context, intent: dict, conv_state) -> None:
        action = intent.get("action", "")
        symbol = intent.get("symbol", "")
        if not symbol:
            await update.message.reply_text("Which coin? Example: `buy BTC 100`", parse_mode="Markdown")
            return

        if action in ("buy", "long"):
            amount = float(intent.get("amount", 0))
            leverage = int(str(intent.get("leverage", "3")).replace("x", ""))
            if amount <= 0:
                await update.message.reply_text(f"How much? Example: `buy {symbol.replace('USDT','')} 100`", parse_mode="Markdown")
                return
            await self._show_risk_check(update, symbol, "Buy", amount, leverage)

        elif action in ("sell", "short"):
            amount = float(intent.get("amount", 0))
            leverage = int(str(intent.get("leverage", "3")).replace("x", ""))
            if amount <= 0:
                await update.message.reply_text(f"How much? Example: `sell {symbol.replace('USDT','')} 100`", parse_mode="Markdown")
                return
            await self._show_risk_check(update, symbol, "Sell", amount, leverage)

        elif action == "close":
            if symbol.upper() == "ALL":
                await update.message.reply_text("Use /emergency to close all positions.")
            else:
                await self._close_position(update, symbol)

    async def _show_risk_check(self, update, symbol: str, side: str, amount: float, leverage: int) -> None:
        report = await self.risk_checker.check(symbol, side, amount, leverage)
        card = risk_check_card(
            symbol, side, amount, leverage,
            report["checks"], report["warnings"],
            report["rsi"], report["signal"], report["confidence"],
            report["suggested_leverage"],
        )

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        buttons = []
        if report["all_passed"]:
            buttons.append(InlineKeyboardButton(
                f"\u2705 {side} {leverage}x",
                callback_data=f"risk_accept:{symbol}:{side}:{amount}:{leverage}",
            ))
        suggested = report["suggested_leverage"]
        if suggested != leverage:
            buttons.append(InlineKeyboardButton(
                f"\u2705 Safer {suggested}x",
                callback_data=f"risk_accept:{symbol}:{side}:{amount}:{suggested}",
            ))
        buttons.append(InlineKeyboardButton("\u274c Cancel", callback_data="risk_cancel"))
        kb = InlineKeyboardMarkup([buttons])
        await update.message.reply_text(card, parse_mode="HTML", reply_markup=kb)

    async def execute_after_risk_check(self, update, context, params: list) -> None:
        if len(params) < 4:
            return
        symbol, side_str, amount, leverage = params[0], params[1], float(params[2]), int(params[3])
        side = Side.BUY if side_str == "Buy" else Side.SELL

        try:
            ticker = await self.s["market_service"].get_ticker(symbol)
            qty = (amount * leverage) / ticker.last_price
            risk_mgr = self.s.get("risk_manager")
            sl_data = {}
            if risk_mgr:
                sl_data = await risk_mgr.calculate_stop_loss(symbol, side, ticker.last_price)

            order = await self.s["order_service"].place_order(
                symbol=symbol, side=side, order_type=OrderType.MARKET,
                qty=qty, leverage=leverage,
                stop_loss=sl_data.get("stop_loss"),
                take_profit=sl_data.get("take_profit"),
                purpose="telegram_manual",
            )

            side_emoji = "\U0001f4c8" if side == Side.BUY else "\U0001f4c9"
            # Exact exchange tick-size precision for displayed prices, with a
            # magnitude-aware fallback when the formatter isn't wired. SL/TP
            # may be non-numeric ('N/A') so guard before formatting.
            _pf = self.s.get("price_formatter")
            from src.telegram.ui.formatters import format_price as _ui_fp

            def _fp(v):
                if not isinstance(v, (int, float)):
                    return str(v)
                return _pf.format(v, symbol) if _pf is not None else _ui_fp(v)

            msg = (
                f"\u2705 <b>TRADE EXECUTED</b>\n\n"
                f"{side_emoji} {symbol} {side_str.upper()}\n"
                f"\U0001f4b0 ${amount:,.0f} at {leverage}x\n"
                f"\U0001f4ca Entry: {_fp(ticker.last_price)}\n"
                f"\U0001f6d1 SL: {_fp(sl_data.get('stop_loss', 'N/A'))}\n"
                f"\U0001f3af TP: {_fp(sl_data.get('take_profit', 'N/A'))}\n"
                f"\U0001f4dd Order: {order.order_id[:12]}..."
            )
            log.info(
                "Trade executed via Telegram: {sym} {side} qty={qty:.4f} lev={lev}x order={oid}",
                sym=symbol, side=side_str, qty=qty, lev=leverage, oid=str(order.order_id)[:12],
            )
            await update.callback_query.edit_message_text(msg, parse_mode="HTML")
        except Exception as e:
            log.error("Trade execution failed via Telegram: {sym} {side} — {err}", sym=symbol, side=side_str, err=str(e))
            await update.callback_query.edit_message_text(f"\u274c Trade failed: {e}")

    async def _close_position(self, update, symbol: str) -> None:
        try:
            # Phase 12.7 (lifecycle-logging-audit Gap 7.8-G1): MANUAL_CLOSE.
            log.info(f"MANUAL_CLOSE | sym={symbol} source=telegram | {ctx()}")
            order = await self.s["position_service"].close_position(symbol, close_trigger="manual_telegram")
            log.info(
                f"MANUAL_CLOSE_OK | sym={symbol} source=telegram "
                f"order_id={str(order.order_id)[:12]} | {ctx()}"
            )
            await update.message.reply_text(f"\u2705 Closed {symbol} position. Order: {order.order_id[:12]}")
        except Exception as e:
            log.error(f"MANUAL_CLOSE_FAIL | sym={symbol} source=telegram err='" + str(e)[:120] + "' | {ctx()}")  # Phase 12.7 Gap 7.8-G1
            await update.message.reply_text(f"\u274c Close failed: {e}")

    async def close_position_callback(self, update, symbol: str) -> None:
        """Close a position from an inline button callback."""
        query = update.callback_query
        try:
            # Phase 12.7 (Gap 7.8-G1): MANUAL_CLOSE entry log.
            log.info(f"MANUAL_CLOSE | sym={symbol} source=telegram_callback | {ctx()}")
            order = await self.s["position_service"].close_position(symbol, close_trigger="manual_telegram")
            # Phase 12.7 (Gap 7.8-G1): MANUAL_CLOSE_OK for callback path.
            log.info(f"MANUAL_CLOSE_OK | sym={symbol} source=telegram_callback order_id={str(order.order_id)[:12]} | {ctx()}")
            await query.edit_message_text(f"\u2705 Closed {symbol} position. Order: {order.order_id[:12]}")
        except Exception as e:
            log.error(f"MANUAL_CLOSE_FAIL | sym={symbol} source=telegram_callback err='" + str(e)[:120] + "' | {ctx()}")  # Phase 12.7 Gap 7.8-G1
            await query.edit_message_text(f"\u274c Close failed: {e}")

    async def cancel_trade(self, update, context, params) -> None:
        await update.callback_query.edit_message_text("\u274c Trade cancelled.")
