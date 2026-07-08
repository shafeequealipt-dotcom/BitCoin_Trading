"""Trade recorder — writes comprehensive trade records to trade_history.

Called once per trade at the moment of close. Assembles entry data,
life tracking data, exit data, and calculated fields into a single
50+ field row in the trade_history table.
"""

from typing import Any

from src.database.connection import DatabaseManager
from src.utils.logging import get_logger

log = get_logger("exchange.recorder")


class TradeRecorder:
    """Records complete trade data to trade_history table.

    Args:
        db: Connected DatabaseManager instance.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def record_trade(
        self,
        position: dict[str, Any],
        exit_data: dict[str, Any],
        wallet_balance_after: float,
    ) -> None:
        """Write a comprehensive trade record to trade_history.

        Args:
            position: Full row from virtual_positions (with exit data written).
            exit_data: Dict with exit-specific fields from close_position.
            wallet_balance_after: Wallet equity after this trade.
        """
        # Validate core required fields
        symbol = position.get("symbol")
        side = position.get("side")
        entry_price = position.get("entry_price")
        exit_price = exit_data.get("exit_price")

        if not all([symbol, side, entry_price, exit_price]):
            log.error(
                "Cannot record trade — missing core fields: sym={sym} side={side} entry={ep} exit={xp}",
                sym=symbol, side=side, ep=entry_price, xp=exit_price,
            )
            return

        # Assemble all fields with safe defaults
        trade_id = position.get("position_id", "")
        entry_fee = _f(position.get("entry_fee_usd"))
        exit_fee = _f(exit_data.get("exit_fee_usd"))
        total_fees = entry_fee + exit_fee

        entry_slippage = _f(position.get("entry_slippage_usd"))
        exit_slippage = _f(exit_data.get("exit_slippage_usd"))
        total_slippage = entry_slippage + exit_slippage

        # Initial vs final SL/TP
        initial_sl = position.get("initial_stop_loss") or position.get("stop_loss_price")
        initial_tp = position.get("initial_take_profit") or position.get("take_profit_price")
        final_sl = position.get("stop_loss_price")
        final_tp = position.get("take_profit_price")

        await self._db.execute(
            """INSERT OR REPLACE INTO trade_history (
                trade_id, symbol, side, entry_price, exit_price,
                quantity, leverage, notional_value, margin_used,
                initial_stop_loss, initial_take_profit,
                final_stop_loss, final_take_profit,
                opened_at, closed_at, hold_duration_seconds,
                entry_fee_usd, exit_fee_usd, total_fees_usd,
                entry_slippage_usd, exit_slippage_usd, total_slippage_usd,
                gross_pnl_pct, gross_pnl_usd,
                net_pnl_pct, net_pnl_usd,
                peak_pnl_pct, max_drawdown_pct,
                time_in_profit_seconds, time_in_loss_seconds,
                sl_modification_count, tp_modification_count,
                close_trigger, result,
                entry_bid_price, entry_ask_price,
                exit_bid_price, exit_ask_price,
                entry_funding_rate, exit_funding_rate,
                entry_volume_24h, exit_volume_24h,
                wallet_balance_after
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?
            )""",
            (
                trade_id,
                symbol,
                side,
                entry_price,
                exit_price,
                _f(position.get("quantity")),
                position.get("leverage", 1),
                _f(position.get("notional_value")),
                _f(position.get("margin_used")),
                initial_sl,
                initial_tp,
                final_sl,
                final_tp,
                position.get("opened_at", ""),
                exit_data.get("closed_at", ""),
                exit_data.get("hold_duration_seconds", 0),
                entry_fee,
                exit_fee,
                total_fees,
                entry_slippage,
                exit_slippage,
                total_slippage,
                _f(exit_data.get("gross_pnl_pct")),
                _f(exit_data.get("gross_pnl_usd")),
                _f(exit_data.get("net_pnl_pct")),
                _f(exit_data.get("net_pnl_usd")),
                _f(position.get("peak_pnl_pct")),
                _f(position.get("max_drawdown_pct")),
                position.get("time_in_profit_seconds", 0),
                position.get("time_in_loss_seconds", 0),
                position.get("sl_modification_count", 0),
                position.get("tp_modification_count", 0),
                exit_data.get("close_trigger", "unknown"),
                exit_data.get("result", "loss"),
                position.get("entry_bid_price"),
                position.get("entry_ask_price"),
                exit_data.get("exit_bid_price"),
                exit_data.get("exit_ask_price"),
                position.get("entry_funding_rate"),
                exit_data.get("exit_funding_rate"),
                position.get("entry_volume_24h"),
                exit_data.get("exit_volume_24h"),
                wallet_balance_after,
            ),
        )

        net_pnl = _f(exit_data.get("net_pnl_pct"))
        net_usd = _f(exit_data.get("net_pnl_usd"))
        trigger = exit_data.get("close_trigger", "unknown")
        hold = exit_data.get("hold_duration_seconds", 0)
        result = exit_data.get("result", "loss")

        log.info(
            "Trade recorded: {sym} {side} {pnl:+.2f}% net=${net:+,.2f} held={hold}s trigger={trig} [{result}]",
            sym=symbol, side=side, pnl=net_pnl, net=net_usd,
            hold=hold, trig=trigger, result=result.upper(),
        )


def _f(val: Any) -> float:
    """Safely convert to float, defaulting to 0.0."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0
