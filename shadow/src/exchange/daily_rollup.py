"""Daily rollup — computes daily performance summary at midnight UTC.

Runs as a background task, checking every 60 seconds if a new day has
started. When it detects yesterday hasn't been rolled up yet, it computes
30+ performance metrics from trade_history and wallet_snapshots, writes
them to daily_summary, and runs data retention cleanup.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from src.database.connection import DatabaseManager
from src.exchange.wallet import VirtualWallet
from src.utils.config import ShadowConfig
from src.utils.logging import get_logger

log = get_logger("exchange.rollup")


class DailyRollup:
    """Computes daily performance summaries and manages data retention.

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
        self._config = config

    async def run(self) -> None:
        """Main loop — checks every 60s if a daily rollup is needed."""
        log.info("Daily rollup service started")
        try:
            while True:
                try:
                    await self._check_and_rollup()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error("Daily rollup error: {err}", err=str(e))
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            log.info("Daily rollup service stopped")

    async def _check_and_rollup(self) -> None:
        """Check if yesterday needs to be rolled up."""
        now = datetime.now(timezone.utc)
        yesterday = (now - timedelta(days=1)).date()

        # Check if already rolled up
        existing = await self._db.fetch_one(
            "SELECT date FROM daily_summary WHERE date = ?",
            (str(yesterday),),
        )
        if existing:
            return

        # Check if there's data from yesterday
        yesterday_start = datetime.combine(yesterday, datetime.min.time()).isoformat()
        today_start = datetime.combine(now.date(), datetime.min.time()).isoformat()

        trades = await self._db.fetch_all(
            "SELECT * FROM trade_history WHERE closed_at >= ? AND closed_at < ?",
            (yesterday_start, today_start),
        )

        snapshots = await self._db.fetch_all(
            "SELECT * FROM wallet_snapshots WHERE timestamp >= ? AND timestamp < ?",
            (yesterday_start, today_start),
        )

        if not trades and not snapshots:
            # Still run retention cleanup even with no trades
            await self._run_retention()
            return

        await self._compute_rollup(yesterday, trades, snapshots)
        await self._run_cleanup()

    async def _compute_rollup(
        self,
        date: Any,
        trades: list[dict[str, Any]],
        snapshots: list[dict[str, Any]],
    ) -> None:
        """Compute all daily summary fields and insert into daily_summary."""
        # Basic counts
        total_trades = len(trades)
        wins = [t for t in trades if t.get("result") == "win"]
        losses = [t for t in trades if t.get("result") == "loss"]
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0

        # PnL breakdown
        gross_profit = sum(_f(t.get("net_pnl_usd")) for t in wins)
        gross_loss = sum(_f(t.get("net_pnl_usd")) for t in losses)
        net_profit = gross_profit + gross_loss
        total_fees = sum(_f(t.get("total_fees_usd")) for t in trades)
        total_slippage = sum(_f(t.get("total_slippage_usd")) for t in trades)
        total_volume = sum(_f(t.get("notional_value")) for t in trades)

        # Best/worst trades
        best_pnl = max((_f(t.get("net_pnl_pct")) for t in trades), default=0)
        worst_pnl = min((_f(t.get("net_pnl_pct")) for t in trades), default=0)
        best_sym = next((t["symbol"] for t in trades if _f(t.get("net_pnl_pct")) == best_pnl), None) if trades else None
        worst_sym = next((t["symbol"] for t in trades if _f(t.get("net_pnl_pct")) == worst_pnl), None) if trades else None

        # Averages
        avg_win_pnl = (sum(_f(t.get("net_pnl_pct")) for t in wins) / win_count) if win_count else None
        avg_loss_pnl = (sum(_f(t.get("net_pnl_pct")) for t in losses) / loss_count) if loss_count else None
        avg_hold_winners = (sum(t.get("hold_duration_seconds", 0) for t in wins) // max(win_count, 1)) if win_count else None
        avg_hold_losers = (sum(t.get("hold_duration_seconds", 0) for t in losses) // max(loss_count, 1)) if loss_count else None

        # Streaks
        max_wins, max_losses = _compute_streaks(trades)

        # Direction analysis
        long_trades = [t for t in trades if t.get("side") == "Buy"]
        short_trades = [t for t in trades if t.get("side") == "Sell"]
        long_wins = len([t for t in long_trades if t.get("result") == "win"])
        short_wins = len([t for t in short_trades if t.get("result") == "win"])
        long_wr = (long_wins / len(long_trades) * 100) if long_trades else None
        short_wr = (short_wins / len(short_trades) * 100) if short_trades else None

        # Coins traded
        coins = list(set(t["symbol"] for t in trades))
        coins_json = json.dumps(sorted(coins))

        # Equity from snapshots
        starting_eq = snapshots[0]["total_equity"] if snapshots else 0
        ending_eq = snapshots[-1]["total_equity"] if snapshots else 0
        daily_pnl_usd = ending_eq - starting_eq if snapshots else net_profit
        daily_pnl_pct = (daily_pnl_usd / starting_eq * 100) if starting_eq > 0 else 0

        # Max drawdown from snapshots
        max_dd = _compute_drawdown(snapshots)

        # Profit factor
        if gross_loss < 0:
            profit_factor = abs(gross_profit / gross_loss)
        elif gross_profit > 0:
            profit_factor = 999.0
        else:
            profit_factor = 0.0

        # Trades opened today
        date_start = datetime.combine(date, datetime.min.time()).isoformat()
        date_end = datetime.combine(date + timedelta(days=1), datetime.min.time()).isoformat()
        opened_row = await self._db.fetch_one(
            "SELECT COUNT(*) as cnt FROM virtual_positions WHERE opened_at >= ? AND opened_at < ?",
            (date_start, date_end),
        )
        trades_opened = opened_row["cnt"] if opened_row else 0

        # Insert
        await self._db.execute(
            """INSERT OR REPLACE INTO daily_summary (
                date, starting_equity, ending_equity, daily_pnl_usd, daily_pnl_pct,
                total_trades, trades_opened, trades_closed, wins, losses, win_rate,
                gross_profit_usd, gross_loss_usd, net_profit_usd,
                total_fees_usd, total_slippage_usd, total_volume_usd,
                best_trade_pnl_pct, best_trade_symbol,
                worst_trade_pnl_pct, worst_trade_symbol,
                avg_win_pnl_pct, avg_loss_pnl_pct,
                avg_hold_winners_seconds, avg_hold_losers_seconds,
                max_consecutive_wins, max_consecutive_losses,
                max_drawdown_pct, profit_factor,
                long_trades, short_trades, long_win_rate, short_win_rate,
                coins_traded
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?
            )""",
            (
                str(date), starting_eq, ending_eq, daily_pnl_usd, daily_pnl_pct,
                total_trades, trades_opened, total_trades, win_count, loss_count, win_rate,
                gross_profit, gross_loss, net_profit,
                total_fees, total_slippage, total_volume,
                best_pnl, best_sym,
                worst_pnl, worst_sym,
                avg_win_pnl, avg_loss_pnl,
                avg_hold_winners, avg_hold_losers,
                max_wins, max_losses,
                max_dd, profit_factor,
                len(long_trades), len(short_trades), long_wr, short_wr,
                coins_json,
            ),
        )

        log.info(
            "Daily rollup: {date} | {n} trades | WR {wr:.0f}% | net ${net:+,.2f} | PF {pf:.2f}",
            date=str(date), n=total_trades, wr=win_rate, net=net_profit, pf=profit_factor,
        )

    async def _run_cleanup(self) -> None:
        """Run data retention cleanup after daily rollup (Phase 5 basic version)."""
        await self._run_retention()

    async def _run_retention(self) -> None:
        """Run the full retention engine (Phase 9)."""
        try:
            from src.utils.retention import RetentionEngine
            retention = RetentionEngine(db=self._db, config=self._config)
            result = await retention.run_cleanup()
            total = sum(v for k, v in result.items() if isinstance(v, int))
            log.info("Retention: {n} rows cleaned, DB {mb}MB", n=total, mb=result.get("db_size_after_mb", 0))
        except Exception as e:
            log.error("Retention cleanup failed: {err}", err=str(e))

    async def compute_rollup_for_date(self, date) -> None:
        """Manually trigger rollup for a specific date (for testing)."""
        date_start = datetime.combine(date, datetime.min.time()).isoformat()
        date_end = datetime.combine(date + timedelta(days=1), datetime.min.time()).isoformat()

        trades = await self._db.fetch_all(
            "SELECT * FROM trade_history WHERE closed_at >= ? AND closed_at < ?",
            (date_start, date_end),
        )
        snapshots = await self._db.fetch_all(
            "SELECT * FROM wallet_snapshots WHERE timestamp >= ? AND timestamp < ?",
            (date_start, date_end),
        )
        await self._compute_rollup(date, trades, snapshots)


def _f(val: Any) -> float:
    """Safely convert to float, defaulting to 0.0."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _compute_streaks(trades: list[dict[str, Any]]) -> tuple[int, int]:
    """Calculate max consecutive wins and losses."""
    if not trades:
        return 0, 0

    sorted_trades = sorted(trades, key=lambda t: t.get("closed_at", ""))
    max_wins = max_losses = 0
    cur_wins = cur_losses = 0

    for t in sorted_trades:
        if t.get("result") == "win":
            cur_wins += 1
            cur_losses = 0
            max_wins = max(max_wins, cur_wins)
        else:
            cur_losses += 1
            cur_wins = 0
            max_losses = max(max_losses, cur_losses)

    return max_wins, max_losses


def _compute_drawdown(snapshots: list[dict[str, Any]]) -> float:
    """Calculate max drawdown from equity snapshots."""
    if not snapshots:
        return 0.0

    peak = 0.0
    max_dd = 0.0

    for s in snapshots:
        equity = _f(s.get("total_equity"))
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (equity - peak) / peak * 100
            if dd < max_dd:
                max_dd = dd

    return max_dd
