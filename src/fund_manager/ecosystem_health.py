"""M17 — Strategy ecosystem health scoring.

Assesses the overall health of the strategy ecosystem on a 0-100 scale
composed of four 25-point sub-scores:

  diversity_score (0-25):
    More active strategy categories = higher score.

  concentration_score (0-25):
    Penalises when a single strategy handles >30% of all trades.

  correlation_score (0-25):
    Positions should not all be in the same direction.

  win_distribution_score (0-25):
    Wins should be spread across strategies, not concentrated.

Health status thresholds:
  80+ = "thriving", 60-80 = "ok", 40-60 = "stressed", <40 = "critical"
"""

from __future__ import annotations

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import EcosystemHealth

log = get_logger("fund_manager")

# Known strategy categories in the system
_ALL_CATEGORIES = [
    "scalping", "momentum", "mean_reversion", "funding_arb",
    "sentiment", "advanced", "predatory", "microstructure",
    "time_based", "cross_market", "ai_enhanced",
]

_HEALTH_THRESHOLDS: list[tuple[int, str]] = [
    (80, "thriving"),
    (60, "ok"),
    (40, "stressed"),
]
# Below 40 = "critical"

_MAX_CONCENTRATION_PCT = 0.30  # Flag if any strategy > 30% of trades


class EcosystemHealthMonitor:
    """Assesses strategy ecosystem health."""

    def __init__(self, settings=None, services: dict | None = None) -> None:
        self._settings = settings
        self._services = services or {}

    # ------------------------------------------------------------------
    # Sub-scores
    # ------------------------------------------------------------------

    @staticmethod
    def _diversity_score(active_categories: set[str]) -> int:
        """Score 0-25 based on how many unique categories are active.

        6+ categories = 25, 4-5 = 18, 2-3 = 10, 1 = 5, 0 = 0.
        """
        n = len(active_categories)
        if n >= 6:
            return 25
        if n >= 4:
            return 18
        if n >= 2:
            return 10
        if n == 1:
            return 5
        return 0

    @staticmethod
    def _concentration_score(strategy_trade_counts: dict[str, int]) -> tuple[int, float]:
        """Score 0-25 based on trade concentration.

        Returns:
            (score, dominant_strategy_pct)
        """
        total = sum(strategy_trade_counts.values())
        if total == 0:
            return 15, 0.0  # No trades = neutral

        max_count = max(strategy_trade_counts.values())
        dominant_pct = max_count / total

        if dominant_pct <= 0.20:
            score = 25  # Well distributed
        elif dominant_pct <= _MAX_CONCENTRATION_PCT:
            score = 20
        elif dominant_pct <= 0.50:
            score = 12
        elif dominant_pct <= 0.70:
            score = 6
        else:
            score = 2  # Dangerously concentrated

        return score, dominant_pct

    @staticmethod
    def _correlation_score(long_count: int, short_count: int) -> tuple[int, float]:
        """Score 0-25 based on directional balance.

        Returns:
            (score, avg_correlation_proxy)
        """
        total = long_count + short_count
        if total == 0:
            return 15, 0.0  # No positions = neutral

        # Ideal: roughly balanced long/short
        balance = min(long_count, short_count) / max(long_count, short_count) if max(long_count, short_count) > 0 else 0
        # balance of 1.0 = perfectly balanced, 0.0 = all one direction

        if balance >= 0.6:
            score = 25
        elif balance >= 0.3:
            score = 18
        elif balance >= 0.1:
            score = 10
        else:
            score = 4  # All positions same direction

        # Correlation proxy: 1.0 = all same, 0.0 = balanced
        correlation = 1.0 - balance

        return score, correlation

    @staticmethod
    def _win_distribution_score(strategy_wins: dict[str, int]) -> tuple[int, float]:
        """Score 0-25 based on how well wins are spread across strategies.

        Returns:
            (score, profitable_strategies_pct)
        """
        total_strategies = len(strategy_wins)
        if total_strategies == 0:
            return 15, 0.0

        profitable = sum(1 for w in strategy_wins.values() if w > 0)
        profitable_pct = profitable / total_strategies

        if profitable_pct >= 0.7:
            score = 25
        elif profitable_pct >= 0.5:
            score = 18
        elif profitable_pct >= 0.3:
            score = 12
        else:
            score = 5

        return score, profitable_pct

    @staticmethod
    def _classify_health(score: int) -> str:
        """Map total score to health status string."""
        for threshold, status in _HEALTH_THRESHOLDS:
            if score >= threshold:
                return status
        return "critical"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def assess(self) -> EcosystemHealth:
        """Assess the strategy ecosystem health.

        Returns:
            EcosystemHealth dataclass with all sub-scores and recommendations.
        """
        active_categories: set[str] = set()
        strategy_trade_counts: dict[str, int] = {}
        strategy_wins: dict[str, int] = {}
        long_count = 0
        short_count = 0

        # Gather data from services
        try:
            db = self._services.get("db")
            if db is not None:
                from src.database.repositories.learning_repo import LearningRepository
                repo = LearningRepository(db)
                all_perf = await repo.get_strategy_performance()

                for row in all_perf:
                    name = row.get("strategy", "")
                    trades = row.get("total_trades", 0)
                    wins = row.get("winning_trades", 0)

                    if trades > 0:
                        strategy_trade_counts[name] = strategy_trade_counts.get(name, 0) + trades
                        strategy_wins[name] = strategy_wins.get(name, 0) + wins

                        # Infer category from strategy name prefix
                        # Convention: "A1_rsi_reversal" → first letter maps to category
                        category = self._infer_category(name)
                        if category:
                            active_categories.add(category)
        except Exception:
            log.warning("EcosystemHealth: failed to load strategy performance data")

        # Count position directions
        try:
            position_svc = self._services.get("position")
            if position_svc is not None:
                positions = await position_svc.get_positions()
                for pos in positions:
                    side_val = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
                    if side_val.lower() in ("buy", "long"):
                        long_count += 1
                    else:
                        short_count += 1
        except Exception:
            log.warning("EcosystemHealth: failed to load position data")

        # Calculate sub-scores
        div_score = self._diversity_score(active_categories)
        conc_score, dominant_pct = self._concentration_score(strategy_trade_counts)
        corr_score, avg_correlation = self._correlation_score(long_count, short_count)
        win_dist_score, profitable_pct = self._win_distribution_score(strategy_wins)

        total_score = div_score + conc_score + corr_score + win_dist_score
        health_status = self._classify_health(total_score)

        # Build recommendations
        recommendations: list[str] = []
        if div_score < 15:
            recommendations.append("Increase strategy diversity — enable more categories")
        if conc_score < 15:
            recommendations.append(
                f"Reduce concentration — dominant strategy at {dominant_pct:.0%} of trades"
            )
        if corr_score < 15:
            recommendations.append("Improve directional balance — positions too correlated")
        if win_dist_score < 15:
            recommendations.append("Spread wins — too few strategies are profitable")

        result = EcosystemHealth(
            score=total_score,
            diversity_score=div_score,
            concentration_score=conc_score,
            correlation_score=corr_score,
            win_distribution_score=win_dist_score,
            active_strategies=len(strategy_trade_counts),
            dominant_strategy_pct=dominant_pct,
            avg_correlation=avg_correlation,
            profitable_strategies_pct=profitable_pct,
            health_status=health_status,
            recommendations=recommendations,
        )

        log.info(
            "EcosystemHealth: score={sc}, status={st}, diversity={d}, "
            "concentration={c}, correlation={cr}, win_dist={w}",
            sc=total_score, st=health_status, d=div_score,
            c=conc_score, cr=corr_score, w=win_dist_score,
        )
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_category(strategy_name: str) -> str | None:
        """Infer category from strategy name prefix convention.

        A/B/C = scalping/momentum/mean_reversion etc.
        """
        prefix_map = {
            "A": "scalping",
            "B": "momentum",
            "C": "mean_reversion",
            "D": "funding_arb",
            "E": "sentiment",
            "F": "advanced",
            "G": "predatory",
            "H": "microstructure",
            "I": "time_based",
            "J": "cross_market",
            "K": "ai_enhanced",
        }
        if not strategy_name:
            return None
        first_char = strategy_name[0].upper()
        return prefix_map.get(first_char)
