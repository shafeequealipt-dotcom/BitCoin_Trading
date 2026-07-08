"""Order engine — processes trades against real prices with simulated fees/slippage.

Receives orders, fills them at the current real price (from WebSocket cache),
applies slippage and taker fees, creates virtual positions, and updates the wallet.
"""

import random
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from src.database.connection import DatabaseManager
from src.exchange.wallet import VirtualWallet
from src.utils.config import ShadowConfig
from src.utils.logging import get_logger

log = get_logger("exchange.order")


class OrderEngine:
    """Virtual exchange order processor.

    Args:
        db: Connected DatabaseManager instance.
        config: Shadow configuration.
        wallet: VirtualWallet instance.
        price_fn: Callable that takes a symbol and returns price data dict or None.
    """

    def __init__(
        self,
        db: DatabaseManager,
        config: ShadowConfig,
        wallet: VirtualWallet,
        price_fn: Callable[[str], dict[str, Any] | None],
        trade_recorder: Any = None,
    ) -> None:
        self._db = db
        self._config = config
        self._wallet = wallet
        self._price_fn = price_fn
        self._trade_recorder = trade_recorder

        # Fee and slippage config
        self._taker_fee_rate = config.exchange.taker_fee_rate
        self._slippage_pct = config.exchange.slippage_pct
        self._slippage_mode = config.exchange.slippage_mode
        self._slippage_min = config.exchange.slippage_min
        self._slippage_max = config.exchange.slippage_max
        self._paused = False
        self._on_trade_open = None  # async callback for trade open alerts
        self._on_trade_close = None  # async callback for trade close alerts

    # ─── Place Order ────────────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        leverage: int,
        sl_price: float | None = None,
        tp_price: float | None = None,
    ) -> dict[str, Any]:
        """Process a market order: fill at real price with slippage + fees.

        Args:
            symbol: Trading pair (e.g., "ETHUSDT").
            side: "Buy" or "Sell".
            qty: Order quantity.
            leverage: Position leverage.
            sl_price: Stop loss price (optional).
            tp_price: Take profit price (optional).

        Returns:
            Dict with order result. status="Filled" on success, "Rejected" on failure.
        """
        # Step 0: Check if exchange is paused
        if self._paused:
            return _reject("Exchange is paused. Use /resume to resume.")

        # Step 1: Validate inputs
        if side not in ("Buy", "Sell"):
            return _reject(f"Invalid side: {side}. Must be 'Buy' or 'Sell'")
        if qty <= 0:
            return _reject(f"Invalid quantity: {qty}. Must be > 0")
        if leverage < 1:
            return _reject(f"Invalid leverage: {leverage}. Must be >= 1")

        # Check symbol is tracked
        tracked = await self._db.fetch_one(
            "SELECT symbol FROM tracked_coins WHERE symbol = ? AND is_active = 1",
            (symbol,),
        )
        if not tracked:
            return _reject(f"Symbol not tracked: {symbol}")

        log.info("Order received: {side} {qty} {sym} {lev}x", side=side, qty=qty, sym=symbol, lev=leverage)

        # Step 2: Get real price
        price_data = self._price_fn(symbol)
        if price_data is None:
            return _reject(f"No price available for {symbol}")

        last_price = float(price_data["last"])
        bid_price = _safe_float(price_data.get("bid"))
        ask_price = _safe_float(price_data.get("ask"))
        volume_24h = _safe_float(price_data.get("volume"))
        funding_rate = _safe_float(price_data.get("funding"))

        # Step 3: Simulate fill with slippage
        slippage = self._get_slippage_pct()
        if side == "Buy":
            fill_price = last_price * (1 + slippage / 100)
        else:
            fill_price = last_price * (1 - slippage / 100)

        slippage_usd = abs(fill_price - last_price) * qty

        # Step 4: Calculate notional and margin
        notional_value = qty * fill_price
        margin_required = notional_value / leverage

        # Step 5: Calculate entry fee
        entry_fee = notional_value * self._taker_fee_rate

        # Step 6: Check wallet can afford
        can_afford, reason = await self._wallet.can_afford(margin_required, entry_fee)
        if not can_afford:
            return _reject(reason)

        # Step 7: Create virtual position
        position_id = str(uuid.uuid4())
        now = _now_iso()
        spread_pct = 0.0
        if bid_price and ask_price and last_price > 0:
            spread_pct = (ask_price - bid_price) / last_price * 100

        await self._db.execute(
            """INSERT INTO virtual_positions (
                position_id, symbol, side, entry_price, quantity, leverage,
                notional_value, margin_used, stop_loss_price, take_profit_price,
                initial_stop_loss, initial_take_profit,
                status, opened_at, entry_fee_usd,
                entry_bid_price, entry_ask_price, entry_spread_pct,
                entry_slippage_pct, entry_slippage_usd,
                entry_funding_rate, entry_volume_24h,
                peak_pnl_pct, max_drawdown_pct,
                time_in_profit_seconds, time_in_loss_seconds,
                sl_modification_count, tp_modification_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0)""",
            (
                position_id, symbol, side, fill_price, qty, leverage,
                notional_value, margin_required, sl_price, tp_price,
                sl_price, tp_price,
                "open", now, entry_fee,
                bid_price, ask_price, spread_pct,
                slippage, slippage_usd,
                funding_rate, volume_24h,
            ),
        )

        # Step 8: Deduct entry fee
        await self._wallet.deduct_entry_fee(entry_fee)

        log.info(
            "Position opened: {sym} {side} {qty} @ ${price:,.2f} | margin=${margin:,.2f} fee=${fee:,.4f}",
            sym=symbol, side=side, qty=qty, price=fill_price,
            margin=margin_required, fee=entry_fee,
        )

        result_data = {
            "order_id": position_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": fill_price,
            "status": "Filled",
            "fee": entry_fee,
            "leverage": leverage,
            "margin": margin_required,
            "notional": notional_value,
        }

        # Send trade open alert
        if self._on_trade_open:
            try:
                import asyncio
                asyncio.create_task(self._on_trade_open(result_data))
            except Exception:
                pass

        return result_data

    # ─── Close Position ─────────────────────────────────────────────────

    async def close_position(
        self,
        symbol: str,
        close_trigger: str = "manual",
        close_price: float | None = None,
    ) -> dict[str, Any]:
        """Close an open position at the current market price or a specified price.

        Args:
            symbol: Trading pair to close.
            close_trigger: What caused the close (manual, sl_hit, tp_hit, etc.).
            close_price: If provided, use this as the exit price instead of
                         looking up the market price. Used by position monitor
                         for SL (current price) and TP (target price) fills.

        Returns:
            Dict with close result.
        """
        # Step 1: Find open position
        position = await self._db.fetch_one(
            "SELECT * FROM virtual_positions WHERE symbol = ? AND status = 'open' ORDER BY opened_at ASC LIMIT 1",
            (symbol,),
        )
        if not position:
            return _reject(f"No open position for {symbol}")

        # Step 2: Get current price (or use explicit close_price)
        price_data = self._price_fn(symbol)
        exit_bid = None
        exit_ask = None
        exit_volume = None
        exit_funding = None

        if close_price is not None:
            current_price = close_price
            if price_data:
                exit_bid = _safe_float(price_data.get("bid"))
                exit_ask = _safe_float(price_data.get("ask"))
                exit_volume = _safe_float(price_data.get("volume"))
                exit_funding = _safe_float(price_data.get("funding"))
        else:
            if price_data is None:
                return _reject(f"No price available for {symbol}")
            current_price = float(price_data["last"])
            exit_bid = _safe_float(price_data.get("bid"))
            exit_ask = _safe_float(price_data.get("ask"))
            exit_volume = _safe_float(price_data.get("volume"))
            exit_funding = _safe_float(price_data.get("funding"))

        # Step 3: Simulate exit slippage
        slippage = self._get_slippage_pct()
        side = position["side"]
        if side == "Buy":
            # Closing a long = selling → price goes down
            exit_price = current_price * (1 - slippage / 100)
        else:
            # Closing a short = buying → price goes up
            exit_price = current_price * (1 + slippage / 100)

        exit_slippage_usd = abs(exit_price - current_price) * position["quantity"]

        # Step 4: Calculate PnL
        entry_price = position["entry_price"]
        notional = position["notional_value"]

        if side == "Buy":
            gross_pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:
            gross_pnl_pct = (entry_price - exit_price) / entry_price * 100

        gross_pnl_usd = gross_pnl_pct / 100 * notional

        # Step 5: Calculate exit fee
        exit_fee = notional * self._taker_fee_rate

        # Step 6: Calculate net PnL
        net_pnl_usd = gross_pnl_usd - exit_fee
        net_pnl_pct = net_pnl_usd / notional * 100
        result = "win" if net_pnl_usd > 0 else "loss"

        # Step 7: Update wallet
        await self._wallet.apply_trade_close(gross_pnl_usd, exit_fee, result == "win")

        # Get wallet balance after close for recording
        balance = await self._wallet.get_balance()
        wallet_after = balance["total_equity"]

        # Step 8: Update position record
        now = _now_iso()
        opened_at = position["opened_at"]
        hold_seconds = _calc_hold_seconds(opened_at, now)

        await self._db.execute(
            """UPDATE virtual_positions SET
               status = 'closed',
               exit_price = ?, exit_fee_usd = ?,
               gross_pnl_pct = ?, gross_pnl_usd = ?,
               net_pnl_pct = ?, net_pnl_usd = ?,
               close_trigger = ?, closed_at = ?,
               hold_duration_seconds = ?,
               exit_bid_price = ?, exit_ask_price = ?,
               exit_slippage_pct = ?, exit_slippage_usd = ?,
               exit_funding_rate = ?, exit_volume_24h = ?,
               result = ?, wallet_balance_after = ?
               WHERE position_id = ?""",
            (
                exit_price, exit_fee,
                gross_pnl_pct, gross_pnl_usd,
                net_pnl_pct, net_pnl_usd,
                close_trigger, now,
                hold_seconds,
                exit_bid, exit_ask,
                slippage, exit_slippage_usd,
                exit_funding, exit_volume,
                result, wallet_after,
                position["position_id"],
            ),
        )

        hold_str = _format_duration(hold_seconds)
        log.info(
            "Position closed: {sym} {side} {pnl:+.2f}% net=${net:+,.2f} {result} (held {dur}) trigger={trig}",
            sym=symbol, side=side, pnl=gross_pnl_pct, net=net_pnl_usd,
            result=result.upper(), dur=hold_str, trig=close_trigger,
        )

        # Step 10: Record trade to trade_history (Phase 5)
        if self._trade_recorder:
            try:
                # Re-fetch position row with all exit data written
                updated_position = await self._db.fetch_one(
                    "SELECT * FROM virtual_positions WHERE position_id = ?",
                    (position["position_id"],),
                )
                exit_data = {
                    "exit_price": exit_price,
                    "exit_fee_usd": exit_fee,
                    "exit_slippage_pct": slippage,
                    "exit_slippage_usd": exit_slippage_usd,
                    "gross_pnl_pct": gross_pnl_pct,
                    "gross_pnl_usd": gross_pnl_usd,
                    "net_pnl_pct": net_pnl_pct,
                    "net_pnl_usd": net_pnl_usd,
                    "close_trigger": close_trigger,
                    "closed_at": now,
                    "hold_duration_seconds": hold_seconds,
                    "result": result,
                    "exit_bid_price": exit_bid,
                    "exit_ask_price": exit_ask,
                    "exit_funding_rate": exit_funding,
                    "exit_volume_24h": exit_volume,
                }
                await self._trade_recorder.record_trade(
                    position=updated_position,
                    exit_data=exit_data,
                    wallet_balance_after=wallet_after,
                )
            except Exception as e:
                log.warning("Trade recorder failed: {err}", err=str(e))

        close_result = {
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "qty": position["quantity"],
            "gross_pnl_pct": gross_pnl_pct,
            "gross_pnl_usd": gross_pnl_usd,
            "exit_fee": exit_fee,
            "net_pnl_pct": net_pnl_pct,
            "net_pnl_usd": net_pnl_usd,
            "result": result,
            "close_trigger": close_trigger,
            "hold_duration_seconds": hold_seconds,
        }

        # Send trade close alert
        if self._on_trade_close:
            try:
                import asyncio
                asyncio.create_task(self._on_trade_close(close_result))
            except Exception:
                pass

        return close_result

    # ─── Modify Position ────────────────────────────────────────────────

    async def set_stop_loss(self, symbol: str, new_sl: float) -> dict[str, Any]:
        """Update the stop loss price on an open position."""
        pos = await self._db.fetch_one(
            "SELECT position_id, stop_loss_price, sl_modification_count FROM virtual_positions WHERE symbol = ? AND status = 'open' LIMIT 1",
            (symbol,),
        )
        if not pos:
            return _reject(f"No open position for {symbol}")

        old_sl = pos["stop_loss_price"]
        count = pos["sl_modification_count"] + 1

        await self._db.execute(
            "UPDATE virtual_positions SET stop_loss_price = ?, sl_modification_count = ? WHERE position_id = ?",
            (new_sl, count, pos["position_id"]),
        )
        log.info(
            "SL modified: {sym} ${old} → ${new} (modification #{cnt})",
            sym=symbol, old=old_sl, new=new_sl, cnt=count,
        )
        return {"symbol": symbol, "old_sl": old_sl, "new_sl": new_sl, "status": "OK"}

    async def set_take_profit(self, symbol: str, new_tp: float) -> dict[str, Any]:
        """Update the take profit price on an open position."""
        pos = await self._db.fetch_one(
            "SELECT position_id, take_profit_price, tp_modification_count FROM virtual_positions WHERE symbol = ? AND status = 'open' LIMIT 1",
            (symbol,),
        )
        if not pos:
            return _reject(f"No open position for {symbol}")

        old_tp = pos["take_profit_price"]
        count = pos["tp_modification_count"] + 1

        await self._db.execute(
            "UPDATE virtual_positions SET take_profit_price = ?, tp_modification_count = ? WHERE position_id = ?",
            (new_tp, count, pos["position_id"]),
        )
        log.info(
            "TP modified: {sym} ${old} → ${new} (modification #{cnt})",
            sym=symbol, old=old_tp, new=new_tp, cnt=count,
        )
        return {"symbol": symbol, "old_tp": old_tp, "new_tp": new_tp, "status": "OK"}

    # ─── Query Positions ────────────────────────────────────────────────

    async def get_positions(self) -> list[dict[str, Any]]:
        """Get all open positions with live unrealized PnL."""
        rows = await self._db.fetch_all(
            "SELECT * FROM virtual_positions WHERE status = 'open' ORDER BY opened_at ASC"
        )
        positions = []
        now = _now_iso()

        for row in rows:
            price_data = self._price_fn(row["symbol"])
            current_price = float(price_data["last"]) if price_data else row["entry_price"]

            entry_price = row["entry_price"]
            notional = row["notional_value"]

            if row["side"] == "Buy":
                unrealized_pct = (current_price - entry_price) / entry_price * 100
            else:
                unrealized_pct = (entry_price - current_price) / entry_price * 100

            unrealized_usd = unrealized_pct / 100 * notional
            hold_seconds = _calc_hold_seconds(row["opened_at"], now)

            positions.append({
                "position_id": row["position_id"],
                "symbol": row["symbol"],
                "side": row["side"],
                "entry_price": entry_price,
                "current_price": current_price,
                "qty": row["quantity"],
                "leverage": row["leverage"],
                "notional_value": notional,
                "margin_used": row["margin_used"],
                "unrealized_pnl_pct": unrealized_pct,
                "unrealized_pnl_usd": unrealized_usd,
                "stop_loss_price": row["stop_loss_price"],
                "take_profit_price": row["take_profit_price"],
                "opened_at": row["opened_at"],
                "hold_duration_seconds": hold_seconds,
            })

        return positions

    async def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Get a single open position by symbol, or None."""
        positions = await self.get_positions()
        for p in positions:
            if p["symbol"] == symbol:
                return p
        return None

    # ─── Internal helpers ───────────────────────────────────────────────

    def _get_slippage_pct(self) -> float:
        """Get slippage percentage based on config mode."""
        if self._slippage_mode == "random":
            return random.uniform(self._slippage_min, self._slippage_max)
        return self._slippage_pct


def _reject(reason: str) -> dict[str, Any]:
    """Create a rejection response."""
    log.warning("Order rejected: {reason}", reason=reason)
    return {"order_id": None, "status": "Rejected", "reason": reason}


def _safe_float(val: Any) -> float | None:
    """Safely convert to float."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _calc_hold_seconds(opened_at: str, closed_at: str) -> int:
    """Calculate hold duration in seconds between two ISO timestamps."""
    try:
        t_open = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        t_close = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
        return max(0, int((t_close - t_open).total_seconds()))
    except Exception:
        return 0


def _format_duration(seconds: int) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m{secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h{mins}m"
