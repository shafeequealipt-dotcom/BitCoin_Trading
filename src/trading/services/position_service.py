"""Position management service: open/close positions, PnL tracking, SL/TP management.

When closing a position, creates a TradeRecord for historical tracking.
"""

import uuid

from src.config.settings import Settings
from src.core.decorators import retry, timed
from src.core.exceptions import (
    InvalidOrderError,
    PositionError,
    RiskLimitExceededError,
)
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import (
    Order,
    OrderType,
    Position,
    Side,
    TradeRecord,
)
from src.core.utils import generate_id, now_utc, pct_change, safe_divide
from src.database.connection import DatabaseManager
from src.database.repositories.trading_repo import TradingRepository
from src.trading.client import BybitClient

log = get_logger("trading")


class PositionService:
    """Service for position management and PnL tracking.

    Args:
        client: Connected BybitClient.
        db: Database manager.
        settings: Application settings.
    """

    def __init__(
        self,
        client: BybitClient,
        db: DatabaseManager,
        settings: Settings,
    ) -> None:
        self._client = client
        self._db = db
        self._settings = settings
        self._trading_repo = TradingRepository(db)

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        """Get all open positions from the exchange.

        Args:
            symbol: Optional filter by symbol.

        Returns:
            List of Position dataclasses (only positions with size > 0).
        """
        params: dict = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol

        result = await self._client.call("get_positions", **params)

        positions = []
        for item in result.get("list", []):
            size = float(item.get("size", "0"))
            if size == 0:
                continue

            pos = _parse_position(item)
            # I4 of cascade-fix series (2026-05-10): pass
            # exchange_mode='shadow' so the new schema-v32
            # ``positions.exchange_mode`` column is tagged correctly
            # for live/Shadow callers. Symmetric with the bybit_demo
            # adapter's get_positions site which passes
            # ``exchange_mode='bybit_demo'``. Pre-fix this method
            # called save_position without the kwarg, falling through
            # to the column DEFAULT of 'shadow' — already correct
            # for this caller, but the explicit kwarg makes the
            # intent visible and fails loudly if the column default
            # ever changes.
            await self._trading_repo.save_position(pos, exchange_mode="shadow")
            positions.append(pos)

        log.debug("Fetched {n} open positions", n=len(positions))
        return positions

    @retry(max_attempts=3, delay=1.0)
    @timed
    async def get_position(self, symbol: str) -> Position | None:
        """Get a single position for a symbol.

        Args:
            symbol: Trading pair.

        Returns:
            Position dataclass, or None if no open position.
        """
        positions = await self.get_positions(symbol=symbol)
        if not positions:
            return None
        return positions[0]

    @retry(max_attempts=2, delay=0.5)
    @timed
    async def close_position(
        self,
        symbol: str,
        *,
        purpose: str = "layer4_close",
        close_trigger: str = "system_close",
    ) -> Order:
        """Close an open position by placing an opposite market order.

        Creates a TradeRecord and saves to trade history.

        Phase 2 (Layer 3 enforcement). Carries a ``purpose`` field for
        observability symmetry with ``OrderService.place_order``. Default
        is ``layer4_close`` because every native caller is a Layer 4
        action (sniper close, watchdog forced close, manual operator
        close). Layer 4 closes intentionally bypass the L3 gate by
        design — the gate is enforced at OrderService.place_order, not
        here.

        Phase 12.7 (lifecycle-logging-audit Gap 7.4-G1 follow-up): added
        ``close_trigger`` parameter that propagates to BybitDemo's
        close_position, surfacing in BYBIT_DEMO_POSITION_CLOSE log.
        Recommended values: "sniper_p9", "sniper_m4", "callb_close",
        "wd_hard_stop", "wd_emergency", "wd_timeout", "wd_profit_take",
        "wd_plan_timer", "time_decay_age", "time_decay_mae",
        "time_decay_struct", "manual_telegram".

        Args:
            symbol: Trading pair to close.
            purpose: Classification for the audit log
                (``layer4_close``, ``layer4_sl``, ``manual``, etc.).
            close_trigger: Source-specific reason flowing through to
                BYBIT_DEMO_POSITION_CLOSE for downstream attribution.

        Returns:
            The closing Order.

        Raises:
            PositionError: If no open position found.
        """
        position = await self.get_position(symbol)
        if position is None:
            raise PositionError(
                f"No open position for {symbol}",
                details={"symbol": symbol},
            )

        # Opposite side to close
        close_side = Side.SELL if position.side == Side.BUY else Side.BUY

        # Phase 5 follow-up (post-Layer-1 fix): generate an
        # ``orderLinkId`` so close attempts are traceable in Bybit's
        # dashboard and the workers log. The reduceOnly flag plus
        # position-size constraint already bound the duplicate-close
        # blast radius (cannot over-close), so this is observability +
        # belt-and-suspenders rather than the hard safety guarantee
        # that ``OrderService.place_order`` provides.
        close_link_id = f"tic-{uuid.uuid4().hex[:24]}"

        # Place closing market order (no stop_loss needed since we're closing)
        order_params: dict = {
            "category": "linear",
            "symbol": symbol,
            "side": close_side.value,
            "orderType": "Market",
            "qty": str(position.size),
            "reduceOnly": True,
            "orderLinkId": close_link_id,
        }

        log.info(
            f"POS_CLOSE_START | link_id={close_link_id} sym={symbol} "
            f"side={close_side.value} qty={position.size} purpose={purpose} "
            f"close_trigger={close_trigger} | {ctx()}"
        )
        result = await self._client.call("place_order", **order_params)

        order_id = result.get("orderId", generate_id("ord"))
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=close_side,
            order_type=OrderType.MARKET,
            price=0.0,
            qty=position.size,
            created_at=now_utc(),
            updated_at=now_utc(),
        )
        await self._trading_repo.save_order(order)

        # Create trade record
        exit_price = position.mark_price
        pnl = position.unrealized_pnl
        pnl_pct_val = pct_change(position.entry_price, exit_price)
        if position.side == Side.SELL:
            pnl_pct_val = -pnl_pct_val

        trade = TradeRecord(
            trade_id=generate_id("trd"),
            symbol=symbol,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            qty=position.size,
            pnl=pnl,
            pnl_pct=pnl_pct_val,
            strategy="manual_close",
            entry_time=position.updated_at,
            exit_time=now_utc(),
        )
        await self._trading_repo.save_trade(trade)

        # Remove position from DB. I4 of cascade-fix series threads
        # exchange_mode='shadow' for symmetry with the get_positions
        # call site; on the delete-on-zero path the kwarg is ignored
        # but threading it keeps the contract consistent.
        position.size = 0
        await self._trading_repo.save_position(position, exchange_mode="shadow")

        log.info(
            "Position closed: {sym} {side} {qty} @ {ep} -> {xp} PnL={pnl:.2f} ({pct:+.2f}%)",
            sym=symbol,
            side=position.side.value,
            qty=position.size,
            ep=position.entry_price,
            xp=exit_price,
            pnl=pnl,
            pct=pnl_pct_val,
        )

        # Notify TradeCoordinator of close
        coordinator = getattr(self, "coordinator", None)
        if coordinator:
            coordinator.on_trade_closed(
                symbol=symbol,
                pnl_pct=pnl_pct_val,
                pnl_usd=pnl,
                was_win=pnl > 0,
                closed_by="position_service",
            )

        return order

    @retry(max_attempts=2, delay=0.5)
    @timed
    async def reduce_position(self, symbol: str, qty: float) -> Order:
        """Reduce an open position by a specified quantity.

        Uses reduceOnly=True to bypass stop-loss requirements.
        Creates a partial TradeRecord for history.

        Args:
            symbol: Trading pair.
            qty: Quantity to close.

        Returns:
            The reducing Order.

        Raises:
            PositionError: If no open position found.
        """
        position = await self.get_position(symbol)
        if position is None:
            raise PositionError(
                f"No open position for {symbol}",
                details={"symbol": symbol},
            )

        close_side = Side.SELL if position.side == Side.BUY else Side.BUY
        actual_qty = min(qty, position.size)

        # Phase 5 follow-up: idempotency key for the reduce path. Same
        # rationale as ``close_position`` — observability + audit trail.
        reduce_link_id = f"tir-{uuid.uuid4().hex[:24]}"

        order_params: dict = {
            "category": "linear",
            "symbol": symbol,
            "side": close_side.value,
            "orderType": "Market",
            "qty": str(actual_qty),
            "reduceOnly": True,
            "orderLinkId": reduce_link_id,
        }

        log.info(
            f"POS_REDUCE_START | link_id={reduce_link_id} sym={symbol} "
            f"side={close_side.value} qty={actual_qty} | {ctx()}"
        )
        result = await self._client.call("place_order", **order_params)

        order_id = result.get("orderId", generate_id("ord"))
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=close_side,
            order_type=OrderType.MARKET,
            price=0.0,
            qty=actual_qty,
            created_at=now_utc(),
            updated_at=now_utc(),
        )
        await self._trading_repo.save_order(order)

        # Create partial trade record
        exit_price = position.mark_price
        fraction = actual_qty / position.size if position.size > 0 else 1.0
        partial_pnl = position.unrealized_pnl * fraction
        pnl_pct_val = pct_change(position.entry_price, exit_price)
        if position.side == Side.SELL:
            pnl_pct_val = -pnl_pct_val

        trade = TradeRecord(
            trade_id=generate_id("trd"),
            symbol=symbol,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            qty=actual_qty,
            pnl=partial_pnl,
            pnl_pct=pnl_pct_val,
            strategy="watchdog_partial_close",
            entry_time=position.updated_at,
            exit_time=now_utc(),
        )
        await self._trading_repo.save_trade(trade)

        log.info(
            "Position reduced: {sym} {side} {qty}/{total} @ {xp} PnL={pnl:.2f}",
            sym=symbol,
            side=close_side.value,
            qty=actual_qty,
            total=position.size,
            xp=exit_price,
            pnl=partial_pnl,
        )
        return order

    @timed
    async def close_all_positions(self) -> list[Order]:
        """Emergency close all open positions.

        Returns:
            List of closing Orders.
        """
        positions = await self.get_positions()
        orders = []

        for pos in positions:
            try:
                order = await self.close_position(pos.symbol)
                orders.append(order)
            except Exception as e:
                log.error(
                    "Failed to close position {sym}: {err}",
                    sym=pos.symbol,
                    err=str(e),
                )

        log.info("Emergency close: closed {n}/{total} positions",
                 n=len(orders), total=len(positions))
        return orders

    @retry(max_attempts=2, delay=0.5)
    @timed
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """Set leverage for a symbol's position.

        Args:
            symbol: Trading pair.
            leverage: Desired leverage multiplier.

        Returns:
            True if successful.

        Raises:
            RiskLimitExceededError: If leverage exceeds max allowed.
        """
        max_lev = self._settings.risk.max_leverage
        if leverage > max_lev:
            raise RiskLimitExceededError(
                f"Leverage {leverage}x exceeds max allowed {max_lev}x",
                details={"requested": leverage, "max_allowed": max_lev},
            )

        try:
            await self._client.call(
                "set_leverage",
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
            log.info("Leverage set to {lev}x for {sym}", lev=leverage, sym=symbol)
            return True
        except Exception as e:
            if "leverage not modified" in str(e).lower() or "110043" in str(e):
                log.debug("Leverage already at {lev}x for {sym}", lev=leverage, sym=symbol)
                return True
            raise

    @retry(max_attempts=2, delay=0.5)
    @timed
    async def set_stop_loss(self, symbol: str, stop_loss: float) -> bool:
        """Set or update stop-loss for an open position.

        Args:
            symbol: Trading pair.
            stop_loss: Stop-loss price.

        Returns:
            True if successful.
        """
        await self._client.call(
            "set_trading_stop",
            category="linear",
            symbol=symbol,
            stopLoss=str(stop_loss),
            positionIdx=0,
        )
        log.info("Stop-loss set to {sl} for {sym}", sl=stop_loss, sym=symbol)
        return True

    @retry(max_attempts=2, delay=0.5)
    @timed
    async def set_take_profit(self, symbol: str, take_profit: float) -> bool:
        """Set or update take-profit for an open position.

        Args:
            symbol: Trading pair.
            take_profit: Take-profit price.

        Returns:
            True if successful.
        """
        await self._client.call(
            "set_trading_stop",
            category="linear",
            symbol=symbol,
            takeProfit=str(take_profit),
            positionIdx=0,
        )
        log.info("Take-profit set to {tp} for {sym}", tp=take_profit, sym=symbol)
        return True

    @timed
    async def get_pnl_summary(self) -> dict:
        """Get aggregate PnL summary across all open positions.

        Returns:
            Dict with total_unrealized_pnl, total_realized_pnl,
            position_count, and per-symbol breakdown.
        """
        positions = await self.get_positions()

        total_unrealized = 0.0
        total_realized = 0.0
        breakdown = []

        for pos in positions:
            total_unrealized += pos.unrealized_pnl
            total_realized += pos.realized_pnl
            breakdown.append({
                "symbol": pos.symbol,
                "side": pos.side.value,
                "size": pos.size,
                "entry_price": pos.entry_price,
                "mark_price": pos.mark_price,
                "unrealized_pnl": pos.unrealized_pnl,
                "leverage": pos.leverage,
            })

        return {
            "total_unrealized_pnl": total_unrealized,
            "total_realized_pnl": total_realized,
            "position_count": len(positions),
            "positions": breakdown,
        }


# =============================================================================
# Response parsing
# =============================================================================

def _parse_position(data: dict) -> Position:
    """Parse a Bybit position response into a Position dataclass."""
    from src.core.utils import timestamp_to_datetime

    updated_ms = data.get("updatedTime", "0")

    sl_val = data.get("stopLoss", "")
    tp_val = data.get("takeProfit", "")

    return Position(
        symbol=data.get("symbol", ""),
        side=Side(data.get("side", "Buy")),
        size=float(data.get("size", "0")),
        entry_price=float(data.get("avgPrice", "0")),
        mark_price=float(data.get("markPrice", "0")),
        unrealized_pnl=float(data.get("unrealisedPnl", "0")),
        realized_pnl=float(data.get("cumRealisedPnl", "0")),
        leverage=int(float(data.get("leverage", "1"))),
        liquidation_price=float(data.get("liqPrice", "0") or "0"),
        stop_loss=float(sl_val) if sl_val and sl_val != "0" else None,
        take_profit=float(tp_val) if tp_val and tp_val != "0" else None,
        updated_at=timestamp_to_datetime(int(updated_ms)) if updated_ms != "0" else now_utc(),
    )
