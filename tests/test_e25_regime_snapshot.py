"""E25 — single fresh per-cycle scoring regime shared scorer -> brain.

The strategy worker scored each coin under a fresh per-cycle regime; the brain
rendered a regime label re-read from the detector cache, which could have
drifted from the regime the displayed scores were computed under. E25 tags the
scoring regime onto the per-coin consensus, carries it on the CoinPackage, and
the brain renders it (preferring it over the cache) so the label matches the
votes beside it.
"""

from __future__ import annotations

from types import SimpleNamespace


def _sw(symbol: str, score: float):
    """A minimal EnsembleResult-like wrapper for _build_per_coin_consensus."""
    setup = SimpleNamespace(
        raw_signal=SimpleNamespace(symbol=symbol,
                                   direction=SimpleNamespace(value="Buy")),
        total_score=score,
    )
    return SimpleNamespace(scored_setup=setup, consensus_strength="STRONG",
                           size_multiplier=1.0, votes=[1, 2, 3])


def test_scorer_tags_per_coin_scoring_regime() -> None:
    from src.workers.strategy_worker import StrategyWorker
    from src.strategies.models.regime_types import MarketRegime

    wk = StrategyWorker.__new__(StrategyWorker)
    coin_regimes = {"BTCUSDT": SimpleNamespace(regime=MarketRegime.TRENDING_UP)}
    global_regime = SimpleNamespace(regime=MarketRegime.RANGING)

    out = wk._build_per_coin_consensus(
        [_sw("BTCUSDT", 10.0)], coin_regimes=coin_regimes, global_regime=global_regime,
    )
    # The regime tagged is the SAME per-coin override the scoring loop used.
    assert out["BTCUSDT"]["scoring_regime"] == "trending_up"


def test_scorer_falls_back_to_unknown_not_global() -> None:
    """Per-coin-authority Phase 2 (2026-05-29): a coin with NO per-coin regime
    falls back to an explicit UNKNOWN, NEVER the global regime — so the brain
    cannot inherit BTC's directional bias for an untagged coin."""
    from src.workers.strategy_worker import StrategyWorker
    from src.strategies.models.regime_types import MarketRegime

    wk = StrategyWorker.__new__(StrategyWorker)
    coin_regimes = {}  # no per-coin override
    global_regime = SimpleNamespace(regime=MarketRegime.TRENDING_DOWN)

    out = wk._build_per_coin_consensus(
        [_sw("ETHUSDT", 7.0)], coin_regimes=coin_regimes, global_regime=global_regime,
    )
    # 'unknown', NOT 'trending_down' (the global) — the back-door is removed.
    assert out["ETHUSDT"]["scoring_regime"] == "unknown"


def test_scorer_unknown_when_no_regime_snapshot() -> None:
    """Per-coin-authority Phase 2 (2026-05-29): with no per-coin snapshot the
    scoring_regime is tagged UNKNOWN (not left empty to fall back to the global
    cache downstream)."""
    from src.workers.strategy_worker import StrategyWorker

    wk = StrategyWorker.__new__(StrategyWorker)
    out = wk._build_per_coin_consensus([_sw("SOLUSDT", 5.0)])
    assert out["SOLUSDT"]["scoring_regime"] == "unknown"


def test_scoring_regime_carries_on_package() -> None:
    from src.core.coin_package import CoinPackage, StrategiesBlock
    pkg = CoinPackage(
        symbol="BTCUSDT", qualified=True, opportunity_score=0.5,
        strategies=StrategiesBlock(ensemble_consensus="STRONG",
                                   scoring_regime="trending_up"),
    )
    assert pkg.strategies.scoring_regime == "trending_up"
    # Default is empty (legacy/unscored coins).
    assert StrategiesBlock().scoring_regime == ""


def test_brain_label_precedence_contract() -> None:
    """The brain render uses ``_reg_str = _score_reg or _cache_reg_str``:
    the scoring regime wins for the displayed label when present, else the
    cache regime is used. This is the contract that makes the label match the
    votes; assert it on the actual rule + confirm the code carries it."""
    def displayed(score_reg: str, cache_reg: str) -> str:
        return score_reg or cache_reg

    # Drift case: scores under trending_up, cache drifted to ranging -> label
    # follows the scores.
    assert displayed("trending_up", "ranging") == "trending_up"
    # No scoring tag (unscored coin) -> fall back to the cache regime.
    assert displayed("", "ranging") == "ranging"

    src = open("src/brain/strategist.py").read()
    assert "_reg_str = _score_reg or _cache_reg_str" in src
    assert "E25_REGIME_SNAPSHOT" in src
