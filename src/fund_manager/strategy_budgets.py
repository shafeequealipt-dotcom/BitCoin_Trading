"""M8 — Per-strategy capital budgets.

Caps how much capital any single strategy can deploy, scaled by
the strategy's historical win rate:

  - Top performers  (WR > 60%):  10% of trading capital
  - Average          (WR 45-60%): 5%
  - Below average    (WR < 45%):  2%
  - New / unknown:                3%

Win rates are sourced from the ``learning_repo`` strategy_performance
table via the database service.  Because the manager calls get_budget()
synchronously, win rates are loaded lazily via load_win_rates() (async)
which should be called during initialization or periodically.
"""

from __future__ import annotations

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import AccountState  # noqa: F401

log = get_logger("fund_manager")

# Budget tiers: (min_win_rate, max_budget_pct)
_TOP_THRESHOLD = 0.60
_AVG_THRESHOLD = 0.45

_TOP_BUDGET_PCT = 0.10
_AVG_BUDGET_PCT = 0.05
_BELOW_BUDGET_PCT = 0.02
_NEW_BUDGET_PCT = 0.03


class StrategyBudgetManager:
    """Per-strategy capital caps based on historical performance."""

    def __init__(self, settings=None, services: dict | None = None) -> None:
        self._settings = settings
        self._services = services or {}
        # In-memory cache: strategy_name -> win_rate (0.0-1.0)
        self._win_rates: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Win-rate loading (async — call periodically or at init)
    # ------------------------------------------------------------------

    async def load_win_rates(self) -> None:
        """Pre-load win rates from the database into the cache.

        Call this during initialization or on a periodic schedule so that
        the synchronous get_budget() has data to work with.
        """
        try:
            db = self._services.get("db")
            if db is None:
                return

            from src.database.repositories.learning_repo import LearningRepository
            repo = LearningRepository(db)
            all_perf = await repo.get_strategy_performance()

            # Aggregate win rates per strategy name
            strategy_trades: dict[str, tuple[int, int]] = {}
            for row in all_perf:
                name = row.get("strategy", "")
                trades = row.get("total_trades", 0)
                wins = row.get("winning_trades", 0)
                if name:
                    prev_trades, prev_wins = strategy_trades.get(name, (0, 0))
                    strategy_trades[name] = (prev_trades + trades, prev_wins + wins)

            for name, (total, wins) in strategy_trades.items():
                if total > 0:
                    self._win_rates[name] = wins / total

            log.info(
                "StrategyBudgets: loaded win rates for {n} strategies",
                n=len(self._win_rates),
            )
        except Exception:
            log.warning("StrategyBudgets: failed to load win rates from database")

    # ------------------------------------------------------------------
    # Budget classification
    # ------------------------------------------------------------------

    def _classify_budget_pct(self, win_rate: float | None) -> float:
        """Determine budget percentage from win rate.

        Args:
            win_rate: Historical win rate (0.0-1.0) or None for unknown.

        Returns:
            Budget as a fraction of trading capital.
        """
        if win_rate is None:
            return _NEW_BUDGET_PCT
        if win_rate > _TOP_THRESHOLD:
            return _TOP_BUDGET_PCT
        if win_rate >= _AVG_THRESHOLD:
            return _AVG_BUDGET_PCT
        return _BELOW_BUDGET_PCT

    # ------------------------------------------------------------------
    # Public API (synchronous — called by manager without await)
    # ------------------------------------------------------------------

    def get_budget(self, strategy_name: str, trading_capital: float) -> float:
        """Return the maximum capital this strategy may deploy.

        Uses cached win rates.  If no win rate is cached for this strategy,
        it is treated as new/unknown and gets 3% of trading capital.

        Args:
            strategy_name: Strategy identifier (e.g. "A1_rsi_reversal").
            trading_capital: Current total trading capital.

        Returns:
            Budget in USD.
        """
        win_rate = self._win_rates.get(strategy_name)
        budget_pct = self._classify_budget_pct(win_rate)
        budget = trading_capital * budget_pct

        log.debug(
            "StrategyBudgets: strategy={strat}, win_rate={wr}, "
            "budget_pct={bp:.0%}, budget={b:.2f}",
            strat=strategy_name,
            wr=f"{win_rate:.2f}" if win_rate is not None else "unknown",
            bp=budget_pct, b=budget,
        )
        return budget

    def set_win_rate(self, strategy_name: str, win_rate: float) -> None:
        """Manually set or override a cached win rate.

        Args:
            strategy_name: Strategy identifier.
            win_rate: Win rate (0.0-1.0).
        """
        self._win_rates[strategy_name] = win_rate

    def snapshot(self) -> dict:
        """Return cached win rates and their corresponding budget tiers."""
        return {
            name: {
                "win_rate": wr,
                "budget_pct": self._classify_budget_pct(wr),
            }
            for name, wr in self._win_rates.items()
        }
