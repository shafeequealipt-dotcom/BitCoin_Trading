"""Layer 3 — ensemble shadow + live integration tests.

Verifies the EnsembleVoter integration with StrategyWeightDeriver:
- Without a deriver: vote() behavior is byte-equivalent to today
- With a deriver, flag OFF: shadow log fires; live consensus unchanged
- With a deriver, flag ON: live consensus uses regime-weighted result
"""
from __future__ import annotations

import io
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


@contextmanager
def capture_logs():
    from loguru import logger
    buf = io.StringIO()
    hid = logger.add(buf, level="DEBUG", format="{level} | {message}")
    try:
        yield buf
    finally:
        logger.remove(hid)


def _build_setup_and_regime(direction_buy: bool = True):
    from src.core.types import Side
    from src.strategies.models.regime_types import MarketRegime, RegimeState
    from src.strategies.models.signal_types import RawSignal, ScoredSetup
    rs = RawSignal(
        strategy_name="ORIG", strategy_category="momentum",
        symbol="BTCUSDT",
        direction=Side.BUY if direction_buy else Side.SELL,
        entry_price=100.0,
        suggested_stop_loss=98.0,
        suggested_take_profit=104.0,
        timeframe="5",
    )
    ss = ScoredSetup(
        raw_signal=rs, total_score=70.0,
        base_score=70.0, confluence_score=0.0,
        context_score=0.0, quality_score=0.0, grade="B",
    )
    rg = RegimeState(
        regime=MarketRegime.TRENDING_UP,
        confidence=0.7, adx=25.0, choppiness=50.0,
        atr_percentile=0.5, volume_ratio=1.0, trend_direction="up",
    )
    return ss, rg


def _make_strategy(name: str, vote_value: str, confidence: float = 0.7):
    """A minimal strategy stub matching the registry contract used by
    EnsembleVoter.vote() — only the attributes vote() reads."""
    s = MagicMock()
    s.name = name
    s.category = "momentum"
    s.enabled = True
    s.vote.return_value = (vote_value, confidence, f"{name} says {vote_value}")
    return s


@pytest.mark.asyncio
async def test_vote_without_deriver_is_byte_equivalent_to_today() -> None:
    """With regime_weighter=None, vote() must not log shadow lines and
    consensus must match the equal-weight baseline."""
    from src.config.settings import Settings
    from src.strategies.ensemble import EnsembleStateCache, EnsembleVoter
    from src.strategies.models.signal_types import StrategyPerformance

    registry = MagicMock()
    registry.get_active_for_regime.return_value = [
        _make_strategy("S1", "BUY"),
        _make_strategy("S2", "BUY"),
        _make_strategy("S3", "SELL"),
    ]
    registry.get_performance.return_value = StrategyPerformance(
        strategy_name="X", ensemble_weight=1.0,
    )
    settings = Settings.load()
    # E28 (2026-05-28): this test exercises the regime-weighting / shadow
    # mechanism, not the single-strategy dominance cap (enabled at 0.4 by E28).
    # Pin the cap disabled so these small balanced voter sets are not clamped
    # and the weighting assertions stay isolated to what they test.
    settings.strategy_engine.single_strategy_max_share = 1.0
    cache = EnsembleStateCache()
    voter = EnsembleVoter(registry=registry, settings=settings,
                          state_cache=cache, regime_weighter=None)

    setup, regime = _build_setup_and_regime()
    with capture_logs() as buf:
        result = voter.vote(
            setup=setup, candles_map={"BTCUSDT": []}, ta_map={"BTCUSDT": {}},
            sentiment_data=None, altdata=None, regime=regime,
        )
    log_text = buf.getvalue()
    assert "STRAT_VOTE_TRACE_SHADOW" not in log_text
    assert result.buy_votes == pytest.approx(2 * 1.0 * 0.7, abs=0.01)
    assert result.sell_votes == pytest.approx(1 * 1.0 * 0.7, abs=0.01)


@pytest.mark.asyncio
async def test_vote_with_deriver_flag_off_logs_shadow_keeps_live() -> None:
    """Deriver wired + flag OFF: shadow log fires, live consensus
    unchanged (equal-weight)."""
    from src.config.settings import Settings
    from src.strategies.ensemble import EnsembleStateCache, EnsembleVoter
    from src.strategies.models.signal_types import StrategyPerformance
    from src.strategies.regime_weighter import (
        CellPerformance, StrategyWeightDeriver,
    )

    registry = MagicMock()
    registry.get_active_for_regime.return_value = [
        _make_strategy("S1", "BUY"),
        _make_strategy("S2", "BUY"),
        _make_strategy("S3", "SELL"),
    ]
    registry.get_performance.return_value = StrategyPerformance(
        strategy_name="X", ensemble_weight=1.0,
    )
    settings = Settings.load()
    settings.strategy_engine.single_strategy_max_share = 1.0  # E28: isolate from the dominance cap (this test exercises weighting, not the cap)
    settings.strategy_engine.regime_weighting_enabled = False

    # Pre-seed deriver: S1 gets 2.0x in trending, S3 gets 0.5x
    rw = StrategyWeightDeriver(cold_start_n=1)
    rw._regime_weights["trending_up"] = {"S1": 2.0, "S2": 1.0, "S3": 0.5}
    rw._cells[("trending_up", "S1")] = CellPerformance(
        strategy_name="S1", regime="trending_up", sample_size=100, factor_smoothed=2.0,
    )

    cache = EnsembleStateCache()
    voter = EnsembleVoter(registry=registry, settings=settings,
                          state_cache=cache, regime_weighter=rw)
    setup, regime = _build_setup_and_regime()

    with capture_logs() as buf:
        result = voter.vote(
            setup=setup, candles_map={"BTCUSDT": []}, ta_map={"BTCUSDT": {}},
            sentiment_data=None, altdata=None, regime=regime,
        )
    log_text = buf.getvalue()
    assert "STRAT_VOTE_TRACE_SHADOW" in log_text
    assert "live_uses=equal" in log_text
    # Live result is equal-weighted (deriver factors NOT applied)
    assert result.buy_votes == pytest.approx(2 * 1.0 * 0.7, abs=0.01)
    assert result.sell_votes == pytest.approx(1 * 1.0 * 0.7, abs=0.01)


@pytest.mark.asyncio
async def test_vote_with_deriver_flag_on_uses_shadow_for_live() -> None:
    """Deriver wired + flag ON: live consensus uses regime-weighted
    result; shadow log fires showing live_uses=regime."""
    from src.config.settings import Settings
    from src.strategies.ensemble import EnsembleStateCache, EnsembleVoter
    from src.strategies.models.signal_types import StrategyPerformance
    from src.strategies.regime_weighter import (
        CellPerformance, StrategyWeightDeriver,
    )

    registry = MagicMock()
    registry.get_active_for_regime.return_value = [
        _make_strategy("S1", "BUY"),
        _make_strategy("S2", "BUY"),
        _make_strategy("S3", "SELL"),
    ]
    registry.get_performance.return_value = StrategyPerformance(
        strategy_name="X", ensemble_weight=1.0,
    )
    settings = Settings.load()
    settings.strategy_engine.single_strategy_max_share = 1.0  # E28: isolate from the dominance cap (this test exercises weighting, not the cap)
    settings.strategy_engine.regime_weighting_enabled = True

    # S1 gets 2.0x in trending; S3 gets 0.5x — buy side amplified, sell suppressed
    rw = StrategyWeightDeriver(cold_start_n=1)
    rw._regime_weights["trending_up"] = {"S1": 2.0, "S2": 1.0, "S3": 0.5}

    cache = EnsembleStateCache()
    voter = EnsembleVoter(registry=registry, settings=settings,
                          state_cache=cache, regime_weighter=rw)
    setup, regime = _build_setup_and_regime()

    with capture_logs() as buf:
        result = voter.vote(
            setup=setup, candles_map={"BTCUSDT": []}, ta_map={"BTCUSDT": {}},
            sentiment_data=None, altdata=None, regime=regime,
        )
    log_text = buf.getvalue()
    assert "STRAT_VOTE_TRACE_SHADOW" in log_text
    assert "live_uses=regime" in log_text
    # Live buy_votes should be the regime-weighted total:
    # S1: 1.0 * 2.0 * 0.7 = 1.4; S2: 1.0 * 1.0 * 0.7 = 0.7 → 2.1
    assert result.buy_votes == pytest.approx(2.1, abs=0.01)
    # Live sell_votes: S3: 1.0 * 0.5 * 0.7 = 0.35
    assert result.sell_votes == pytest.approx(0.35, abs=0.01)


@pytest.mark.asyncio
async def test_shadow_failure_does_not_break_live_path() -> None:
    """If the deriver raises during shadow computation, the live vote
    must still return cleanly (Rule 7 non-fatal observability)."""
    from src.config.settings import Settings
    from src.strategies.ensemble import EnsembleStateCache, EnsembleVoter
    from src.strategies.models.signal_types import StrategyPerformance

    registry = MagicMock()
    registry.get_active_for_regime.return_value = [
        _make_strategy("S1", "BUY"),
        _make_strategy("S2", "BUY"),
    ]
    registry.get_performance.return_value = StrategyPerformance(
        strategy_name="X", ensemble_weight=1.0,
    )
    settings = Settings.load()
    settings.strategy_engine.single_strategy_max_share = 1.0  # E28: isolate from the dominance cap (this test exercises weighting, not the cap)
    settings.strategy_engine.regime_weighting_enabled = False

    # Deriver whose get_factor raises
    rw_broken = MagicMock()
    rw_broken.get_factor.side_effect = RuntimeError("simulated deriver failure")

    cache = EnsembleStateCache()
    voter = EnsembleVoter(registry=registry, settings=settings,
                          state_cache=cache, regime_weighter=rw_broken)
    setup, regime = _build_setup_and_regime()
    # Must not raise
    result = voter.vote(
        setup=setup, candles_map={"BTCUSDT": []}, ta_map={"BTCUSDT": {}},
        sentiment_data=None, altdata=None, regime=regime,
    )
    # Live result is still computed (equal-weight)
    assert result.buy_votes == pytest.approx(2 * 1.0 * 0.7, abs=0.01)
