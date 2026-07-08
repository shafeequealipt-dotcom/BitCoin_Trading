"""M9: Momentum Allocator.

Adjusts capital allocation based on recent strategy performance. Strategies
with high win rates get more capital; underperforming strategies get less.
"""

from src.core.logging import get_logger

log = get_logger("fund_manager")

# ── Win rate thresholds and multipliers ──────────────────────────────────────
# win_rate > 60% -> 1.2 (boost allocation)
# win_rate > 50% -> 1.0 (normal)
# win_rate < 40% -> 0.7 (reduce allocation)
# win_rate < 30% -> 0.5 (significant reduction)

# Number of recent trades to analyze
LOOKBACK_TRADES = 10

# Minimum trades needed for a valid assessment
MIN_TRADES_FOR_ASSESSMENT = 3


class MomentumAllocator:
    """Strategy momentum-based capital allocation adjustment.

    Analyzes the last N trades for each strategy to determine if it is
    on a hot streak or cold streak, and adjusts allocation accordingly.

    Args:
        db: DatabaseManager for querying trade history.
    """

    def __init__(self, settings=None, db=None) -> None:
        self._db = db

    async def get_multiplier(self, strategy_name: str) -> float:
        """Get allocation multiplier based on strategy's recent performance.

        Looks at the last 10 trades for the given strategy and calculates
        win rate to determine the multiplier.

        Args:
            strategy_name: Name of the strategy to evaluate.

        Returns:
            Multiplier: 1.2 (hot), 1.0 (normal), 0.7 (cold), 0.5 (very cold).
        """
        try:
            if self._db is None:
                log.debug("No DB available, momentum multiplier defaulting to 1.0")
                return 1.0

            # Query recent trades for this strategy
            rows = await self._db.fetch_all(
                """
                SELECT pnl FROM trade_history
                WHERE strategy = ?
                ORDER BY exit_time DESC
                LIMIT ?
                """,
                (strategy_name, LOOKBACK_TRADES),
            )

            if len(rows) < MIN_TRADES_FOR_ASSESSMENT:
                log.debug(
                    "Insufficient trades for strategy {strategy} "
                    "({n}/{min} needed), defaulting to 1.0",
                    strategy=strategy_name,
                    n=len(rows),
                    min=MIN_TRADES_FOR_ASSESSMENT,
                )
                return 1.0

            # Calculate win rate
            wins = sum(1 for r in rows if r.get("pnl", 0) > 0)
            total = len(rows)
            win_rate = wins / total

            # Determine multiplier
            if win_rate > 0.6:
                mult = 1.2
            elif win_rate > 0.5:
                mult = 1.0
            elif win_rate < 0.3:
                mult = 0.5
            elif win_rate < 0.4:
                mult = 0.7
            else:
                mult = 1.0

            log.info(
                "Momentum for {strategy}: {wins}/{total} wins "
                "(rate={rate:.0f}%) -> multiplier={mult}",
                strategy=strategy_name,
                wins=wins,
                total=total,
                rate=win_rate * 100,
                mult=mult,
            )

            return mult

        except Exception:
            log.warning(
                "Momentum allocation failed for {strategy}, defaulting to 1.0",
                strategy=strategy_name,
            )
            return 1.0
