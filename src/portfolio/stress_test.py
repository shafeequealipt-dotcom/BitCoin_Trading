"""Stress Tester: tests portfolio resilience under adverse scenarios."""

from src.core.logging import get_logger
from src.portfolio.models.portfolio_types import StressTestResult
from src.strategies.registry import StrategyRegistry

log = get_logger("portfolio")

SCENARIOS = [
    {"name": "btc_flash_crash", "desc": "BTC Flash Crash -15%", "btc_drop": 15, "alt_drop": 20},
    {"name": "market_dump", "desc": "Market-wide dump -20%", "btc_drop": 20, "alt_drop": 30},
    {"name": "volatility_explosion", "desc": "ATR triples", "atr_mult": 3, "sl_hit_rate": 0.6},
    {"name": "liquidity_crisis", "desc": "Spreads widen 10x", "spread_mult": 10, "slippage_mult": 5},
    {"name": "funding_spike", "desc": "Funding rate +0.1%", "funding_rate": 0.001},
    {"name": "exchange_downtime", "desc": "2-hour exchange outage", "downtime_hours": 2},
    {"name": "cluster_failure", "desc": "Correlated strategies fail together", "cluster_loss_pct": 5},
]


class StressTester:
    """Tests portfolio against standard adverse scenarios.

    Args:
        registry: Strategy registry for portfolio composition.
    """

    def __init__(self, registry: StrategyRegistry) -> None:
        self.registry = registry

    def run_scenarios(self, account_equity: float) -> list[StressTestResult]:
        """Run all standard stress test scenarios."""
        results: list[StressTestResult] = []
        strategies = self.registry.get_enabled()
        num_strategies = len(strategies) or 1

        for scenario in SCENARIOS:
            impact = self._estimate_impact(scenario, account_equity, num_strategies)
            results.append(impact)

        log.info(
            "Stress test: {n} scenarios, {s} survived, {f} failed",
            n=len(results),
            s=sum(1 for r in results if r.survival),
            f=sum(1 for r in results if not r.survival),
        )
        return results

    def _estimate_impact(
        self, scenario: dict, equity: float, num_strategies: int,
    ) -> StressTestResult:
        """Estimate portfolio impact for a scenario."""
        name = scenario["name"]
        desc = scenario["desc"]

        if "btc_drop" in scenario:
            impact_pct = scenario["btc_drop"] * 0.3  # ~30% of BTC drop affects leveraged portfolio
            loss = equity * impact_pct / 100
        elif "atr_mult" in scenario:
            sl_hit_rate = scenario.get("sl_hit_rate", 0.5)
            impact_pct = sl_hit_rate * 2 * num_strategies * 0.1  # rough estimate
            impact_pct = min(impact_pct, 15)
            loss = equity * impact_pct / 100
        elif "spread_mult" in scenario:
            impact_pct = 2.0  # Extra cost from wider spreads
            loss = equity * impact_pct / 100
        elif "funding_rate" in scenario:
            impact_pct = scenario["funding_rate"] * 100 * 3  # 3 funding periods
            loss = equity * impact_pct / 100
        elif "downtime_hours" in scenario:
            impact_pct = 3.0  # 2 hours of unmanaged risk
            loss = equity * impact_pct / 100
        elif "cluster_loss_pct" in scenario:
            impact_pct = scenario["cluster_loss_pct"]
            loss = equity * impact_pct / 100
        else:
            impact_pct = 5.0
            loss = equity * impact_pct / 100

        survival = equity - loss > equity * 0.5  # Survive if >50% equity remains
        margin_risk = impact_pct > 20

        return StressTestResult(
            scenario_name=name,
            description=desc,
            estimated_portfolio_impact_pct=round(impact_pct, 2),
            estimated_loss_usd=round(loss, 2),
            survival=survival,
            margin_call_risk=margin_risk,
        )
