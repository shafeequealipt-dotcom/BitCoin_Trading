"""Strategy registry: manages all trading strategies, performance tracking, and filtering."""

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.strategies.base_strategy import BaseStrategy
from src.strategies.models.regime_types import (
    REGIME_ACTIVE_CATEGORIES,
    MarketRegime,
)
from src.strategies.models.signal_types import StrategyPerformance

log = get_logger("strategies")


class StrategyRegistry:
    """Central registry for all trading strategies.

    Strategies register themselves here. The Scanner, Ensemble, and
    Optimizer all query the registry to find applicable strategies.

    Layer 1 Defect 1 (2026-05-21) — the legacy ``get_active_for_regime``
    silently ignored its ``regime`` argument and returned every enabled
    strategy regardless of market regime. Three callers
    (strategy_worker.py:201, :573; ensemble.py:171) pass regime
    expecting filtering and historically got the full set back.

    The new contract: when ``regime_filter_enabled`` is True (the
    default per operator decision 2026-05-21), the function filters
    by ``REGIME_ACTIVE_CATEGORIES[regime]`` AND ``.enabled`` — so
    momentum-family strategies stop voting in ranging/dead regimes,
    contrarian-family strategies stop voting in trending regimes, etc.
    When the flag is False, the function returns every enabled
    strategy (legacy behavior; emergency rollback). Each call emits
    a structured ``REGISTRY_REGIME_FILTER`` log so operators can see
    the per-regime active count and verify the contract is honored.
    """

    def __init__(self, regime_filter_enabled: bool = True) -> None:
        self._strategies: dict[str, BaseStrategy] = {}
        self._performance: dict[str, StrategyPerformance] = {}
        # Layer 1 Defect 1 — flag controlling whether
        # ``get_active_for_regime`` honors its regime argument. Default
        # True per operator decision (the function should filter by
        # regime today). Settable via constructor; rollback to legacy
        # uniform-strategy behavior by setting False.
        self._regime_filter_enabled: bool = bool(regime_filter_enabled)

    def set_regime_filter_enabled(self, enabled: bool) -> None:
        """Operator-tunable flag for the regime filter (Defect 1).

        Set True to filter strategies by regime via
        ``REGIME_ACTIVE_CATEGORIES``; set False to return every
        enabled strategy regardless (legacy rollback). Logged so
        the change is visible.
        """
        old = self._regime_filter_enabled
        self._regime_filter_enabled = bool(enabled)
        log.info(
            f"REGISTRY_REGIME_FILTER_FLAG | old={old} new={self._regime_filter_enabled} | {ctx()}"
        )

    def register(self, strategy: BaseStrategy) -> None:
        """Register a strategy. Called during initialization."""
        name = strategy.name
        if name in self._strategies:
            log.warning("Strategy '{n}' already registered, overwriting", n=name)
        self._strategies[name] = strategy
        if name not in self._performance:
            self._performance[name] = StrategyPerformance(strategy_name=name)
        log.info(
            "Registered strategy: {n} ({cat}) [{tf}]",
            n=name, cat=strategy.category, tf=strategy.timeframe.value,
        )

    def get(self, name: str) -> BaseStrategy | None:
        """Get strategy by name."""
        return self._strategies.get(name)

    def get_all(self) -> list[BaseStrategy]:
        """Get all registered strategies."""
        return list(self._strategies.values())

    def get_active_for_regime(self, regime: MarketRegime) -> list[BaseStrategy]:
        """Return strategies active for the given market regime.

        Contract (Layer 1 Defect 1, 2026-05-21):

        - When ``self._regime_filter_enabled`` is True (default):
          filter to strategies whose ``.category`` is in
          ``REGIME_ACTIVE_CATEGORIES[regime]`` AND whose performance
          ``.enabled`` is True. Momentum strategies are silenced in
          ranging/dead regimes, contrarian strategies in trending,
          per the operator's project aim of finding the best trade
          for each situation via the categories table.

        - When ``self._regime_filter_enabled`` is False: returns
          every enabled strategy regardless of regime (the legacy
          pre-Defect-1 behavior, kept as an emergency rollback).

        Always: emits a structured ``REGISTRY_REGIME_FILTER`` log
        line so the operator can verify the contract is honored per
        call (regime in, count out, flag state, category set used).
        """
        all_enabled = [
            s for s in self._strategies.values()
            if self._performance.get(
                s.name, StrategyPerformance(strategy_name=s.name),
            ).enabled
        ]
        if not self._regime_filter_enabled:
            log.info(
                f"REGISTRY_REGIME_FILTER | flag=False regime={getattr(regime, 'value', regime)} "
                f"in={len(all_enabled)} out={len(all_enabled)} mode=legacy_uniform | {ctx()}"
            )
            return all_enabled
        active_categories = REGIME_ACTIVE_CATEGORIES.get(regime, [])
        filtered = [s for s in all_enabled if s.category in active_categories]
        log.info(
            f"REGISTRY_REGIME_FILTER | flag=True regime={getattr(regime, 'value', regime)} "
            f"in={len(all_enabled)} out={len(filtered)} "
            f"categories={','.join(active_categories)} | {ctx()}"
        )
        return filtered

    def get_by_category(self, category: str) -> list[BaseStrategy]:
        """Get all strategies in a category."""
        return [s for s in self._strategies.values() if s.category == category]

    def get_enabled(self) -> list[BaseStrategy]:
        """Get only enabled strategies (not disabled by optimizer)."""
        return [
            s for s in self._strategies.values()
            if self._performance.get(s.name, StrategyPerformance(strategy_name=s.name)).enabled
        ]

    def get_performance(self, name: str) -> StrategyPerformance:
        """Get performance stats for a strategy."""
        if name not in self._performance:
            self._performance[name] = StrategyPerformance(strategy_name=name)
        return self._performance[name]

    def update_performance(self, name: str, pnl_pct: float, was_win: bool) -> None:
        """Update strategy performance after a trade closes."""
        perf = self.get_performance(name)
        perf.update(pnl_pct, was_win)
        result_str = "win" if was_win else "loss"
        log.debug(f"REG_PERF | str={name} result={'W' if was_win else 'L'} pnl={pnl_pct:+.2f}% wr={perf.win_rate:.1%} | {ctx()}")
        log.info(
            "Strategy {n}: trade {result} ({pnl:+.2f}%) "
            "| WR={wr:.0%} PF={pf:.2f} streak={s}",
            n=name, result=result_str, pnl=pnl_pct, wr=perf.win_rate,
            pf=perf.profit_factor, s=perf.current_streak,
        )

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Enable/disable a strategy."""
        perf = self.get_performance(name)
        perf.enabled = enabled
        status_str = "enabled" if enabled else "disabled"
        if enabled:
            log.info(f"REG_ENABLE | str={name} active={sum(1 for p in self._performance.values() if p.enabled)}/{len(self._performance)} | {ctx()}")
        else:
            log.info(f"REG_DISABLE | str={name} active={sum(1 for p in self._performance.values() if p.enabled)}/{len(self._performance)} | {ctx()}")
        log.info("Strategy {n} {status}", n=name, status=status_str)

    def set_ensemble_weight(self, name: str, weight: float) -> None:
        """Adjust a strategy's voting weight in the ensemble."""
        perf = self.get_performance(name)
        old = perf.ensemble_weight
        perf.ensemble_weight = max(0.1, min(weight, 3.0))
        log.info(
            "Strategy {n} weight: {old:.2f} -> {new:.2f}",
            n=name, old=old, new=perf.ensemble_weight,
        )

    def get_registry_summary(self) -> dict:
        """Summary of all strategies, their status, and performance."""
        strategies = []
        for name, strat in self._strategies.items():
            perf = self.get_performance(name)
            strategies.append({
                "name": name,
                "category": strat.category,
                "timeframe": strat.timeframe.value,
                "risk_level": strat.risk_level,
                "enabled": perf.enabled,
                "total_trades": perf.total_trades,
                "win_rate": round(perf.win_rate, 3),
                "profit_factor": round(perf.profit_factor, 2),
                "ensemble_weight": round(perf.ensemble_weight, 2),
                "streak": perf.current_streak,
                "regimes": [r.value for r in strat.applicable_regimes],
            })
        return {
            "total_strategies": len(self._strategies),
            "enabled": sum(1 for s in strategies if s["enabled"]),
            "disabled": sum(1 for s in strategies if not s["enabled"]),
            "strategies": strategies,
        }

    @property
    def count(self) -> int:
        return len(self._strategies)
