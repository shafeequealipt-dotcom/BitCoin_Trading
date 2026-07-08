"""Trading repository: save and query orders, positions, trade history, and account snapshots."""

from datetime import datetime
from typing import Any

from src.core.logging import get_logger
from src.core.types import (
    AccountInfo,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    TradeRecord,
)
from src.core.utils import now_utc
from src.database.connection import DatabaseManager

log = get_logger("database")


class TradingRepository:
    """Repository for trading data persistence.

    Args:
        db: Active DatabaseManager instance.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    # --- Orders ---

    async def save_order(self, order: Order, *, exchange_mode: str = "") -> None:
        """Upsert an order.

        HIGH-2 fix (2026-05-09): added optional exchange_mode kwarg so
        callers can tag rows correctly. Empty default falls through to
        the column DEFAULT 'shadow' (preserves back-compat for callers
        not yet updated). bybit_demo callers MUST pass
        exchange_mode='bybit_demo' to avoid the audit-flagged tag drift.

        Args:
            order: Order dataclass.
            exchange_mode: Mode tag to insert; empty string preserves
                legacy DEFAULT 'shadow' behavior.
        """
        if exchange_mode:
            await self._db.execute(
                """
                INSERT OR REPLACE INTO orders
                (order_id, symbol, side, order_type, price, qty, status,
                 filled_qty, avg_fill_price, stop_loss, take_profit,
                 created_at, updated_at, exchange_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.order_id,
                    order.symbol,
                    order.side.value,
                    order.order_type.value,
                    order.price,
                    order.qty,
                    order.status.value,
                    order.filled_qty,
                    order.avg_fill_price,
                    order.stop_loss,
                    order.take_profit,
                    order.created_at.isoformat() if hasattr(order.created_at, "isoformat") else order.created_at,
                    order.updated_at.isoformat() if hasattr(order.updated_at, "isoformat") else order.updated_at,
                    exchange_mode,
                ),
            )
            return
        await self._db.execute(
            """
            INSERT OR REPLACE INTO orders
            (order_id, symbol, side, order_type, price, qty, status,
             filled_qty, avg_fill_price, stop_loss, take_profit, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.order_id,
                order.symbol,
                order.side.value,
                order.order_type.value,
                order.price,
                order.qty,
                order.status.value,
                order.filled_qty,
                order.avg_fill_price,
                order.stop_loss,
                order.take_profit,
                order.created_at.isoformat(),
                order.updated_at.isoformat(),
            ),
        )

    async def get_order(self, order_id: str) -> Order | None:
        """Fetch a single order by ID.

        Args:
            order_id: The order identifier.

        Returns:
            Order dataclass or None.
        """
        row = await self._db.fetch_one(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        )
        if row is None:
            return None
        return _row_to_order(row)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Fetch all open (non-terminal) orders.

        Args:
            symbol: Optional filter by symbol.

        Returns:
            List of Order dataclasses.
        """
        if symbol:
            rows = await self._db.fetch_all(
                "SELECT * FROM orders WHERE symbol = ? AND status IN ('New', 'PartiallyFilled') ORDER BY created_at DESC",
                (symbol,),
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM orders WHERE status IN ('New', 'PartiallyFilled') ORDER BY created_at DESC"
            )
        return [_row_to_order(r) for r in rows]

    async def get_order_history(self, symbol: str | None = None, limit: int = 50) -> list[Order]:
        """Fetch recent order history.

        Args:
            symbol: Optional filter by symbol.
            limit: Max rows.

        Returns:
            List of Order dataclasses.
        """
        if symbol:
            rows = await self._db.fetch_all(
                "SELECT * FROM orders WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
                (symbol, limit),
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [_row_to_order(r) for r in rows]

    # --- Positions ---

    async def save_position(
        self, position: Position, *, exchange_mode: str = "",
    ) -> None:
        """Upsert a position.

        I4 of cascade-fix series (2026-05-10): added optional
        ``exchange_mode`` kwarg to mirror the
        :meth:`save_order` / :meth:`save_trade` pattern (HIGH-2 fix).
        Closes the schema-v32 parity gap where ``positions`` was the
        only trade-data table without per-mode tagging. Empty default
        falls through to the column DEFAULT 'shadow' (preserves
        back-compat for callers not yet updated). bybit_demo callers
        MUST pass ``exchange_mode='bybit_demo'`` so the audit trail
        and any consumer that filters by mode stays correct.

        Args:
            position: Position dataclass. ``position.size == 0`` deletes
                the row (zeroed-out close path).
            exchange_mode: Mode tag to insert; empty string preserves
                the legacy DEFAULT 'shadow' behaviour.
        """
        if position.size == 0:
            await self._db.execute(
                "DELETE FROM positions WHERE symbol = ?", (position.symbol,)
            )
            return

        if exchange_mode:
            await self._db.execute(
                """
                INSERT OR REPLACE INTO positions
                (symbol, side, size, entry_price, mark_price, unrealized_pnl,
                 realized_pnl, leverage, liquidation_price, stop_loss,
                 take_profit, updated_at, exchange_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position.symbol,
                    position.side.value,
                    position.size,
                    position.entry_price,
                    position.mark_price,
                    position.unrealized_pnl,
                    position.realized_pnl,
                    position.leverage,
                    position.liquidation_price,
                    position.stop_loss,
                    position.take_profit,
                    position.updated_at.isoformat(),
                    exchange_mode,
                ),
            )
            return

        await self._db.execute(
            """
            INSERT OR REPLACE INTO positions
            (symbol, side, size, entry_price, mark_price, unrealized_pnl,
             realized_pnl, leverage, liquidation_price, stop_loss, take_profit, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position.symbol,
                position.side.value,
                position.size,
                position.entry_price,
                position.mark_price,
                position.unrealized_pnl,
                position.realized_pnl,
                position.leverage,
                position.liquidation_price,
                position.stop_loss,
                position.take_profit,
                position.updated_at.isoformat(),
            ),
        )

    async def get_position(self, symbol: str) -> Position | None:
        """Fetch a position by symbol.

        Args:
            symbol: Trading pair.

        Returns:
            Position dataclass or None.
        """
        row = await self._db.fetch_one(
            "SELECT * FROM positions WHERE symbol = ?", (symbol,)
        )
        if row is None:
            return None
        return _row_to_position(row)

    async def get_all_positions(self) -> list[Position]:
        """Fetch all open positions.

        Returns:
            List of Position dataclasses.
        """
        rows = await self._db.fetch_all(
            "SELECT * FROM positions WHERE size > 0 ORDER BY symbol"
        )
        return [_row_to_position(r) for r in rows]

    async def delete_position(self, symbol: str) -> None:
        """Issue 2 fix (2026-05-11) — delete a position row by symbol.

        Explicit cleanup entry point for close-callback-driven row
        removal. Pre-fix the only DELETE trigger was
        :meth:`save_position` with ``size==0`` (line 180-184), which
        only fires when callers construct a zero-size Position dataclass.
        External SL/TP closes never went through ``close_position`` (the
        only path that built a zero-size Position pre-fix), so those
        rows leaked into the zombie set today (100% leak rate observed).

        Idempotent: DELETE WHERE symbol=? on an empty row is a no-op.

        Args:
            symbol: Symbol to clear from the positions table.
        """
        await self._db.execute(
            "DELETE FROM positions WHERE symbol = ?", (symbol,)
        )

    async def prune_positions_not_in_set(
        self, mode: str, live_symbols: set[str]
    ) -> list[str]:
        """J1 Phase 3 Step A (2026-05-14) — adapter-level symmetric prune.

        Deletes positions-table rows tagged with ``mode`` whose symbol
        is not in ``live_symbols``. Returns the list of symbols that
        were pruned (caller emits per-symbol POSITIONS_CACHE_PRUNE).

        Rationale: the bybit_demo adapter writes every present position
        via INSERT OR REPLACE on every confirmed-true response, but pre-J1
        relied on the watchdog's vanished-detection plus the close-callback
        chain to delete rows for symbols that dropped out of the response.
        That chain only fires for symbols the watchdog had tracked at
        least once (``_last_known_symbols`` is empty on first boot tick).
        Pre-c4eef5c stale rows from before 2026-05-14 10:54 UTC sit
        forever because they are never in any tick's vanished set.

        This method closes the asymmetry: write and prune are now
        bytes-equivalent operations on every confirmed-true response.

        Caller responsibility:
          * Only invoke when the upstream call confirmed exchange truth
            (``confirmed=True``). On ``confirmed=False`` the live set
            is unknown; preserve current cache state instead.
          * Dwell-time guard for the ``confirmed=True, live_symbols=set()``
            case lives in the caller (two consecutive empties required
            before pruning everything).

        Idempotent: DELETE on already-empty row is a no-op.

        Args:
            mode: ``exchange_mode`` tag to scope the prune. Pass the
                concrete tag (e.g., ``"bybit_demo"``) — empty string
                falls through to no-op (returns empty list) so a
                caller without a mode does not accidentally prune.
            live_symbols: Set of symbols present in the current confirmed
                response. Any symbol with this mode tag NOT in the set
                is treated as stale and pruned.

        Returns:
            Sorted list of pruned symbols. Empty when no rows were stale.
        """
        if not mode:
            return []
        rows = await self._db.fetch_all(
            "SELECT symbol FROM positions WHERE exchange_mode = ? AND size > 0",
            (mode,),
        )
        cached = {r["symbol"] for r in (rows or [])}
        stale = sorted(cached - live_symbols)
        for sym in stale:
            await self._db.execute(
                "DELETE FROM positions WHERE symbol = ? AND exchange_mode = ?",
                (sym, mode),
            )
        return stale

    # --- Trade History ---

    async def save_trade(self, trade: TradeRecord, *, exchange_mode: str = "") -> None:
        """Save a completed trade record.

        HIGH-2 fix (2026-05-09): added optional exchange_mode kwarg so
        callers can tag rows correctly. Empty default falls through to
        the column DEFAULT 'shadow' (preserves back-compat). The new
        CRITICAL-3 _trade_history_close_callback in workers/manager.py
        passes the resolved transformer.current_mode here.

        Args:
            trade: TradeRecord dataclass.
            exchange_mode: Mode tag to insert; empty string preserves
                legacy DEFAULT 'shadow' behavior.
        """
        if exchange_mode:
            await self._db.execute(
                """
                INSERT OR REPLACE INTO trade_history
                (trade_id, symbol, side, entry_price, exit_price, qty, pnl, pnl_pct,
                 strategy, signal_confidence, notes, entry_time, exit_time, exchange_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.trade_id,
                    trade.symbol,
                    trade.side.value,
                    trade.entry_price,
                    trade.exit_price,
                    trade.qty,
                    trade.pnl,
                    trade.pnl_pct,
                    trade.strategy,
                    trade.signal_confidence,
                    trade.notes,
                    trade.entry_time.isoformat(),
                    trade.exit_time.isoformat(),
                    exchange_mode,
                ),
            )
            return
        await self._db.execute(
            """
            INSERT OR REPLACE INTO trade_history
            (trade_id, symbol, side, entry_price, exit_price, qty, pnl, pnl_pct,
             strategy, signal_confidence, notes, entry_time, exit_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.trade_id,
                trade.symbol,
                trade.side.value,
                trade.entry_price,
                trade.exit_price,
                trade.qty,
                trade.pnl,
                trade.pnl_pct,
                trade.strategy,
                trade.signal_confidence,
                trade.notes,
                trade.entry_time.isoformat(),
                trade.exit_time.isoformat(),
            ),
        )

    async def get_trade_history(self, symbol: str | None = None, limit: int = 50) -> list[TradeRecord]:
        """Fetch recent trade history.

        Args:
            symbol: Optional filter by symbol.
            limit: Max rows.

        Returns:
            List of TradeRecord dataclasses.
        """
        if symbol:
            rows = await self._db.fetch_all(
                "SELECT * FROM trade_history WHERE symbol = ? ORDER BY exit_time DESC LIMIT ?",
                (symbol, limit),
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM trade_history ORDER BY exit_time DESC LIMIT ?",
                (limit,),
            )
        return [_row_to_trade(r) for r in rows]

    # --- Account Snapshots ---

    async def save_account_snapshot(self, account: AccountInfo) -> None:
        """Save an account balance snapshot.

        Args:
            account: AccountInfo dataclass.
        """
        await self._db.execute(
            """
            INSERT INTO account_snapshots
            (total_equity, available_balance, used_margin, unrealized_pnl, margin_level_pct, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                account.total_equity,
                account.available_balance,
                account.used_margin,
                account.unrealized_pnl,
                account.margin_level_pct,
                account.updated_at.isoformat(),
            ),
        )


# =============================================================================
# Row-to-dataclass mappers
# =============================================================================

def _row_to_order(row: dict[str, Any]) -> Order:
    """Convert a database row to an Order dataclass."""
    return Order(
        order_id=row["order_id"],
        symbol=row["symbol"],
        side=Side(row["side"]),
        order_type=OrderType(row["order_type"]),
        price=row["price"],
        qty=row["qty"],
        status=OrderStatus(row["status"]),
        filled_qty=row["filled_qty"],
        avg_fill_price=row["avg_fill_price"],
        stop_loss=row["stop_loss"],
        take_profit=row["take_profit"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_position(row: dict[str, Any]) -> Position:
    """Convert a database row to a Position dataclass."""
    return Position(
        symbol=row["symbol"],
        side=Side(row["side"]),
        size=row["size"],
        entry_price=row["entry_price"],
        mark_price=row["mark_price"],
        unrealized_pnl=row["unrealized_pnl"],
        realized_pnl=row["realized_pnl"],
        leverage=row["leverage"],
        liquidation_price=row["liquidation_price"],
        stop_loss=row["stop_loss"],
        take_profit=row["take_profit"],
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_trade(row: dict[str, Any]) -> TradeRecord:
    """Convert a database row to a TradeRecord dataclass."""
    return TradeRecord(
        trade_id=row["trade_id"],
        symbol=row["symbol"],
        side=Side(row["side"]),
        entry_price=row["entry_price"],
        exit_price=row["exit_price"],
        qty=row["qty"],
        pnl=row["pnl"],
        pnl_pct=row["pnl_pct"],
        strategy=row["strategy"],
        signal_confidence=row["signal_confidence"],
        notes=row["notes"],
        entry_time=datetime.fromisoformat(row["entry_time"]),
        exit_time=datetime.fromisoformat(row["exit_time"]),
    )
