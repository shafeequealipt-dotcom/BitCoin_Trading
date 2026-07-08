"""Correlation Tracker: tracks how strategy returns correlate with each other."""

from collections import defaultdict

from src.config.settings import Settings
from src.core.logging import get_logger
from src.database.connection import DatabaseManager
from src.portfolio.models.portfolio_types import CorrelationPair

log = get_logger("portfolio")


class CorrelationTracker:
    """Tracks correlation between strategy returns to optimize diversification.

    Args:
        db: Database manager.
        settings: Application settings.
    """

    def __init__(self, db: DatabaseManager, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._matrix: dict[tuple[str, str], float] = {}

    async def calculate_correlation_matrix(self, days: int = 30) -> dict:
        """Calculate pairwise correlation between strategy daily returns."""
        rows = await self.db.fetch_all(
            "SELECT strategy_name, created_at, pnl_pct FROM strategy_trades "
            "WHERE created_at > datetime('now', ? || ' days') ORDER BY created_at",
            (f"-{days}",),
        )

        # Group daily returns by strategy
        daily_returns: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for r in (rows or []):
            name = r["strategy_name"]
            day = str(r["created_at"])[:10]
            daily_returns[name][day] += float(r.get("pnl_pct", 0))

        strategies = list(daily_returns.keys())
        matrix: dict[tuple[str, str], float] = {}

        for i, a in enumerate(strategies):
            for b in strategies[i + 1:]:
                common_days = set(daily_returns[a].keys()) & set(daily_returns[b].keys())
                if len(common_days) < 10:
                    matrix[(a, b)] = 0.0
                    continue
                a_returns = [daily_returns[a][d] for d in sorted(common_days)]
                b_returns = [daily_returns[b][d] for d in sorted(common_days)]
                corr = self._pearson(a_returns, b_returns)
                matrix[(a, b)] = corr

        self._matrix = matrix
        return matrix

    def get_correlation_clusters(self) -> list[list[str]]:
        """Group strategies with correlation > threshold."""
        threshold = self.settings.portfolio.high_correlation_threshold
        adj: dict[str, set[str]] = defaultdict(set)

        for (a, b), corr in self._matrix.items():
            if corr > threshold:
                adj[a].add(b)
                adj[b].add(a)

        visited: set[str] = set()
        clusters: list[list[str]] = []

        for node in adj:
            if node in visited:
                continue
            cluster: list[str] = []
            stack = [node]
            while stack:
                n = stack.pop()
                if n in visited:
                    continue
                visited.add(n)
                cluster.append(n)
                stack.extend(adj[n] - visited)
            if len(cluster) > 1:
                clusters.append(sorted(cluster))

        return clusters

    def calculate_correlation_penalty(self, strategy_name: str) -> float:
        """How correlated is this strategy with the rest of the portfolio."""
        correlations = []
        for (a, b), corr in self._matrix.items():
            if a == strategy_name or b == strategy_name:
                correlations.append(abs(corr))

        if not correlations:
            return 0.0

        avg_corr = sum(correlations) / len(correlations)
        if avg_corr > 0.7:
            return 0.4
        if avg_corr > 0.5:
            return 0.2
        if avg_corr > 0.3:
            return 0.1
        if avg_corr < 0.1:
            return -0.05  # Bonus for low correlation
        return 0.0

    async def get_diversification_score(self) -> float:
        """Portfolio-level diversification: 0 (all correlated) to 1 (diversified)."""
        if not self._matrix:
            return 0.5
        avg = sum(1 - abs(c) for c in self._matrix.values()) / len(self._matrix)
        return round(max(0, min(avg, 1)), 4)

    @staticmethod
    def _pearson(x: list[float], y: list[float]) -> float:
        """Pearson correlation coefficient."""
        n = len(x)
        if n < 2:
            return 0.0
        mx = sum(x) / n
        my = sum(y) / n
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
        sx = (sum((xi - mx) ** 2 for xi in x) / n) ** 0.5
        sy = (sum((yi - my) ** 2 for yi in y) / n) ** 0.5
        if sx == 0 or sy == 0:
            return 0.0
        return max(-1.0, min(cov / (sx * sy), 1.0))
