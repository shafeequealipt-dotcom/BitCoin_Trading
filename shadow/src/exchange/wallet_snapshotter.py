"""Wallet snapshotter — captures equity curve every 60 seconds.

Saves the wallet's state (equity, available, margin, unrealized PnL,
today's stats) to the wallet_snapshots table, creating a minute-by-minute
record of how the wallet's value changes over time.
"""

import asyncio
from datetime import datetime, timezone

from src.database.connection import DatabaseManager
from src.exchange.wallet import VirtualWallet
from src.utils.config import ShadowConfig
from src.utils.logging import get_logger

log = get_logger("exchange.snapshotter")


class WalletSnapshotter:
    """Periodically snapshots wallet state for equity curve tracking.

    Args:
        db: Connected DatabaseManager instance.
        wallet: VirtualWallet instance.
        config: Shadow configuration.
    """

    def __init__(
        self, db: DatabaseManager, wallet: VirtualWallet, config: ShadowConfig
    ) -> None:
        self._db = db
        self._wallet = wallet
        self._interval = 60  # seconds between snapshots
        self._snapshot_count = 0

    async def run(self) -> None:
        """Main loop — snapshot wallet state at configured interval."""
        log.info("Wallet snapshotter started (interval: {sec}s)", sec=self._interval)
        try:
            while True:
                try:
                    await asyncio.sleep(self._interval)
                    await self._take_snapshot()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error("Snapshot error: {err}", err=str(e))
        except asyncio.CancelledError:
            # Final snapshot on shutdown
            try:
                await self._take_snapshot()
            except Exception:
                pass
            log.info(
                "Wallet snapshotter stopped. Total snapshots: {n}",
                n=self._snapshot_count,
            )

    async def _take_snapshot(self) -> None:
        """Capture current wallet state and today's trading stats."""
        # Step 1: Get wallet balance
        balance = await self._wallet.get_balance()

        # Step 2: Get today's trading stats
        today_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00")
        today_stats = await self._db.fetch_one(
            """SELECT
                COUNT(*) as total_trades,
                COALESCE(SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END), 0) as wins,
                COALESCE(SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END), 0) as losses,
                COALESCE(SUM(net_pnl_usd), 0) as realized_pnl,
                COALESCE(SUM(total_fees_usd), 0) as fees
            FROM trade_history WHERE closed_at >= ?""",
            (today_start,),
        )

        # Fallback if trade_history is empty
        if today_stats is None:
            today_stats = {"total_trades": 0, "wins": 0, "losses": 0, "realized_pnl": 0, "fees": 0}

        # Step 3: Count open positions
        open_count = await self._db.fetch_one(
            "SELECT COUNT(*) as cnt FROM virtual_positions WHERE status = 'open'"
        )
        positions = open_count["cnt"] if open_count else 0

        # Step 4: Write snapshot
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO wallet_snapshots (
                timestamp, total_equity, available_balance, margin_in_use,
                unrealized_pnl, realized_pnl_today, open_position_count,
                total_trades_today, wins_today, losses_today, fees_today
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now,
                balance["total_equity"],
                balance["available_balance"],
                balance["margin_in_use"],
                balance["total_unrealized_pnl"],
                today_stats["realized_pnl"],
                positions,
                today_stats["total_trades"],
                today_stats["wins"],
                today_stats["losses"],
                today_stats["fees"],
            ),
        )

        self._snapshot_count += 1
        log.debug("Snapshot #{n}: eq=${eq:,.2f}", n=self._snapshot_count, eq=balance["total_equity"])

        # Log every 10th snapshot (every ~10 minutes)
        if self._snapshot_count % 10 == 0:
            log.info(
                "Wallet snapshot #{n}: equity=${eq:,.2f} pnl_today=${pnl:+,.2f}",
                n=self._snapshot_count,
                eq=balance["total_equity"],
                pnl=today_stats["realized_pnl"],
            )
