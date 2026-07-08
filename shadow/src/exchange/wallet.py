"""Virtual wallet — tracks balance, margin, PnL, and fees.

The wallet reads its persisted state from the virtual_wallet table (single row)
and calculates live values (unrealized PnL, margin_in_use) from open positions
and real-time prices.
"""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from src.database.connection import DatabaseManager
from src.utils.config import ShadowConfig
from src.utils.logging import get_logger

log = get_logger("exchange.wallet")


class VirtualWallet:
    """Virtual wallet accounting system.

    Args:
        db: Connected DatabaseManager instance.
        config: Shadow configuration.
        price_fn: Callable that takes a symbol and returns a dict with
                  {last, bid, ask, volume, funding} or None if unavailable.
    """

    def __init__(
        self,
        db: DatabaseManager,
        config: ShadowConfig,
        price_fn: Callable[[str], dict[str, Any] | None],
    ) -> None:
        self._db = db
        self._config = config
        self._price_fn = price_fn

        # Cached wallet state (from DB)
        self._starting_balance: float = 0.0
        self._total_realized_pnl: float = 0.0
        self._total_fees_paid: float = 0.0
        self._total_trades: int = 0
        self._total_wins: int = 0
        self._total_losses: int = 0

    async def initialize(self) -> None:
        """Load wallet state from database into memory cache."""
        row = await self._db.fetch_one(
            "SELECT * FROM virtual_wallet WHERE id = 1"
        )
        if row is None:
            # Should have been created by migrations, but handle gracefully
            self._starting_balance = self._config.exchange.starting_balance
            await self._db.execute(
                "INSERT INTO virtual_wallet (id, starting_balance) VALUES (1, ?)",
                (self._starting_balance,),
            )
            log.info("Wallet created: ${bal:,.2f}", bal=self._starting_balance)
        else:
            self._starting_balance = row["starting_balance"]
            self._total_realized_pnl = row["total_realized_pnl"]
            self._total_fees_paid = row["total_fees_paid"]
            self._total_trades = row["total_trades"]
            self._total_wins = row["total_wins"]
            self._total_losses = row["total_losses"]

        log.info(
            "Wallet initialized: ${bal:,.2f} | PnL: ${pnl:+,.2f} | Trades: {t}",
            bal=self._starting_balance,
            pnl=self._total_realized_pnl,
            t=self._total_trades,
        )

    # ─── Read operations ────────────────────────────────────────────────

    async def get_balance(self) -> dict[str, Any]:
        """Get the complete wallet state with live calculations.

        Returns:
            Dict with total_equity, available_balance, margin_in_use,
            unrealized_pnl, realized_pnl, fees, trades, wins, losses.
        """
        margin_in_use = await self.get_margin_in_use()
        unrealized_pnl = await self._calculate_unrealized_pnl()

        total_equity = (
            self._starting_balance
            + self._total_realized_pnl
            + unrealized_pnl
            - self._total_fees_paid
        )
        available_balance = total_equity - margin_in_use

        return {
            "total_equity": total_equity,
            "available_balance": available_balance,
            "margin_in_use": margin_in_use,
            "total_unrealized_pnl": unrealized_pnl,
            "total_realized_pnl": self._total_realized_pnl,
            "total_fees_paid": self._total_fees_paid,
            "starting_balance": self._starting_balance,
            "total_trades": self._total_trades,
            "total_wins": self._total_wins,
            "total_losses": self._total_losses,
        }

    async def get_margin_in_use(self) -> float:
        """Sum of margin locked by all open positions."""
        row = await self._db.fetch_one(
            "SELECT COALESCE(SUM(margin_used), 0) as total FROM virtual_positions WHERE status = 'open'"
        )
        return row["total"] if row else 0.0

    async def can_afford(self, margin_required: float, entry_fee: float) -> tuple[bool, str]:
        """Check if wallet has enough available balance for a new trade.

        Args:
            margin_required: Margin to lock for the position.
            entry_fee: Entry fee to deduct.

        Returns:
            Tuple of (can_afford, reason_string).
        """
        balance = await self.get_balance()
        available = balance["available_balance"]
        needed = margin_required + entry_fee

        if needed > available:
            return (
                False,
                f"Insufficient margin: need ${needed:,.2f}, have ${available:,.2f}",
            )
        return (True, "OK")

    # ─── Write operations ───────────────────────────────────────────────

    async def deduct_entry_fee(self, fee_amount: float) -> None:
        """Deduct entry fee from wallet. Called when an order is placed.

        Args:
            fee_amount: Fee amount in USD.
        """
        self._total_fees_paid += fee_amount
        await self._db.execute(
            """UPDATE virtual_wallet SET
               total_fees_paid = total_fees_paid + ?,
               last_updated_at = ?
               WHERE id = 1""",
            (fee_amount, _now_iso()),
        )
        log.debug("Entry fee deducted: ${fee:,.4f}", fee=fee_amount)

    async def apply_trade_close(
        self, gross_pnl: float, exit_fee: float, is_win: bool
    ) -> None:
        """Apply a closed trade's PnL and fee to the wallet.

        Args:
            gross_pnl: Gross PnL before exit fee.
            exit_fee: Exit fee amount.
            is_win: True if net PnL > 0.
        """
        net_pnl = gross_pnl - exit_fee
        self._total_realized_pnl += net_pnl
        self._total_fees_paid += exit_fee
        self._total_trades += 1
        if is_win:
            self._total_wins += 1
        else:
            self._total_losses += 1

        await self._db.execute(
            """UPDATE virtual_wallet SET
               total_realized_pnl = total_realized_pnl + ?,
               total_fees_paid = total_fees_paid + ?,
               total_trades = total_trades + 1,
               total_wins = total_wins + ?,
               total_losses = total_losses + ?,
               last_updated_at = ?
               WHERE id = 1""",
            (net_pnl, exit_fee, 1 if is_win else 0, 0 if is_win else 1, _now_iso()),
        )

        result_str = "WIN" if is_win else "LOSS"
        log.info(
            "Trade closed: gross=${gpnl:+,.2f} fee=${fee:,.2f} net=${npnl:+,.2f} {result}",
            gpnl=gross_pnl,
            fee=exit_fee,
            npnl=net_pnl,
            result=result_str,
        )

    async def reset(self, new_starting_balance: float) -> None:
        """Reset wallet to fresh state. Closes all open positions.

        Args:
            new_starting_balance: New starting balance.
        """
        # Force-close all open positions
        await self._db.execute(
            "UPDATE virtual_positions SET status = 'closed', close_trigger = 'wallet_reset', closed_at = ? WHERE status = 'open'",
            (_now_iso(),),
        )

        # Reset wallet
        now = _now_iso()
        self._starting_balance = new_starting_balance
        self._total_realized_pnl = 0.0
        self._total_fees_paid = 0.0
        self._total_trades = 0
        self._total_wins = 0
        self._total_losses = 0

        await self._db.execute(
            """UPDATE virtual_wallet SET
               starting_balance = ?,
               total_realized_pnl = 0,
               total_fees_paid = 0,
               total_trades = 0,
               total_wins = 0,
               total_losses = 0,
               last_reset_at = ?,
               last_updated_at = ?
               WHERE id = 1""",
            (new_starting_balance, now, now),
        )
        log.info("Wallet reset to ${bal:,.2f}", bal=new_starting_balance)

    # ─── Internal helpers ───────────────────────────────────────────────

    async def _calculate_unrealized_pnl(self) -> float:
        """Calculate total unrealized PnL across all open positions."""
        positions = await self._db.fetch_all(
            "SELECT symbol, side, entry_price, notional_value FROM virtual_positions WHERE status = 'open'"
        )
        total_pnl = 0.0
        for pos in positions:
            price_data = self._price_fn(pos["symbol"])
            if price_data is None:
                continue

            current_price = (
                float(price_data["last"])
                if isinstance(price_data, dict)
                else float(price_data)
            )
            entry_price = pos["entry_price"]
            notional = pos["notional_value"]

            if pos["side"] == "Buy":
                pnl = (current_price - entry_price) / entry_price * notional
            else:
                pnl = (entry_price - current_price) / entry_price * notional

            total_pnl += pnl

        return total_pnl


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
