"""Layer 3 — full DI + data-flow end-to-end integration test.

Exercises the entire Layer 3 path through the real production code paths
against a temp DB with the live config:
- Settings loaded with operator-approved knobs
- StrategyWeightDeriver constructed with those knobs
- EnsembleVoter constructed with the deriver wired
- vote() runs and emits STRAT_VOTE_TRACE_SHADOW
- refresh() runs against seeded Layer 2 data and updates factors
- Audit log emits on the healthy path; loud signal on synthetic
  degenerate state (every strategy at floor in every regime)

Mocks only the per-strategy vote() callable; everything else is real
production code from settings/DI to ensemble to deriver to audit.
"""
from __future__ import annotations

import io
import os
import tempfile
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


def _build_setup_and_regime(symbol: str = "BTCUSDT"):
    from src.core.types import Side
    from src.strategies.models.regime_types import MarketRegime, RegimeState
    from src.strategies.models.signal_types import RawSignal, ScoredSetup
    rs = RawSignal(
        strategy_name="ORIG", strategy_category="momentum",
        symbol=symbol, direction=Side.BUY, entry_price=100.0,
        suggested_stop_loss=98.0, suggested_take_profit=104.0,
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


def _make_strategy(name: str, vote_value: str):
    s = MagicMock()
    s.name = name
    s.category = "momentum"
    s.enabled = True
    s.vote.return_value = (vote_value, 0.7, f"{name}:{vote_value}")
    return s


@pytest.mark.asyncio
async def test_full_layer3_path_with_cold_start_data() -> None:
    """End-to-end against an empty DB: deriver refresh returns 0 rows;
    shadow log fires with shadow_consensus == live_consensus (cold-start
    means factor=1.0 for every strategy)."""
    from src.config.settings import Settings
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.strategies.ensemble import EnsembleStateCache, EnsembleVoter
    from src.strategies.models.signal_types import StrategyPerformance
    from src.strategies.regime_weighter import StrategyWeightDeriver

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "l3.db"))
        await db.connect()
        try:
            await run_migrations(db)

            settings = Settings.load()
            # E28 (2026-05-28): isolate this end-to-end weighting test from the
            # single-strategy dominance cap (enabled at 0.4 by E28) so the small
            # voter sets are not clamped; the cap has its own dedicated test.
            settings.strategy_engine.single_strategy_max_share = 1.0
            # Construct the deriver using operator-approved settings
            rw = StrategyWeightDeriver(
                cold_start_n=settings.strategy_engine.regime_weighting_cold_start_n,
                floor=settings.strategy_engine.regime_weighting_floor,
                ceil=settings.strategy_engine.regime_weighting_ceil,
                sensitivity=settings.strategy_engine.regime_weighting_sensitivity,
                ema_alpha=settings.strategy_engine.regime_weighting_ema_alpha,
            )

            # Cold-start refresh: no rows in ensemble_votes / trade_intelligence
            n = await rw.refresh(db)
            assert n == 0

            # Construct ensemble voter with the wired deriver
            registry = MagicMock()
            registry.get_active_for_regime.return_value = [
                _make_strategy("MOM1", "BUY"),
                _make_strategy("MOM2", "BUY"),
                _make_strategy("MR1", "SELL"),
            ]
            registry.get_performance.return_value = StrategyPerformance(
                strategy_name="X", ensemble_weight=1.0,
            )
            cache = EnsembleStateCache()
            voter = EnsembleVoter(
                registry=registry, settings=settings,
                state_cache=cache, regime_weighter=rw,
            )

            setup, regime = _build_setup_and_regime()
            with capture_logs() as buf:
                result = voter.vote(
                    setup=setup, candles_map={"BTCUSDT": []},
                    ta_map={"BTCUSDT": {}}, sentiment_data=None,
                    altdata=None, regime=regime,
                )
            log_text = buf.getvalue()

            # Shadow log MUST fire even at cold-start
            assert "STRAT_VOTE_TRACE_SHADOW" in log_text
            # At cold-start factor=1.0, shadow MUST equal live
            assert "would_change=False" in log_text
            # Issue #19 (2026-05-27): regime weighting is now enabled by
            # default, so live uses the regime-weighted path. At cold-start all
            # factors are 1.0, so the live RESULT still equals equal-weight
            # (asserted below) and would_change stays False.
            assert "live_uses=regime" in log_text
            # Result values match equal-weight: 2*1.0*0.7 BUY, 1*1.0*0.7 SELL
            assert result.buy_votes == pytest.approx(1.4, abs=0.01)
            assert result.sell_votes == pytest.approx(0.7, abs=0.01)

            # Audit at cold-start: no permanent silence, no regime independence
            audit = rw.audit()
            assert audit["permanent_silence_violations"] == []
            assert audit["data_derived_cells"] == 0
            assert audit["cold_start_cells"] == 0  # no cells yet
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_full_layer3_path_with_matured_data() -> None:
    """End-to-end after seeding past-threshold Layer 2 data: the deriver
    derives real factors, vote() shadow log shows divergence from live."""
    from src.config.settings import Settings
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.strategies.ensemble import EnsembleStateCache, EnsembleVoter
    from src.strategies.models.signal_types import StrategyPerformance
    from src.strategies.regime_weighter import StrategyWeightDeriver

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "l3.db"))
        await db.connect()
        try:
            await run_migrations(db)

            # Seed: MOM1 winning in trending_up (25 trades, avg +3% pnl)
            for i in range(25):
                sid = f"sid_mom1_{i}"
                await db.execute(
                    "INSERT INTO ensemble_votes (setup_id, symbol, direction, "
                    "strategy_name, vote, confidence, weight) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, "BTCUSDT", "Buy", "MOM1", "BUY", 0.7, 1.0),
                )
                await db.execute(
                    "INSERT INTO trade_intelligence (symbol, direction, strategy_name, "
                    "strategy_category, source, closed_by, entry_price, exit_price, "
                    "pnl_pct, pnl_usd, win, hold_seconds, setup_id, entry_regime, "
                    "exchange_mode, trade_closed_at, captured_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("BTCUSDT", "Buy", "MOM1", "momentum", "test", "tp",
                     100.0, 103.0, 3.0, 15.0, 1, 120.0, sid, "trending_up",
                     "shadow", "2026-05-22T12:00:00+00:00", "2026-05-22T12:00:00+00:00"),
                )

            settings = Settings.load()
            settings.strategy_engine.single_strategy_max_share = 1.0  # E28: isolate from the dominance cap (this test exercises weighting, not the cap)
            # Disable EMA for the test so the derived factor matches the formula directly
            rw = StrategyWeightDeriver(
                cold_start_n=20, floor=0.3, ceil=3.0,
                sensitivity=0.3, ema_alpha=1.0,
            )
            n = await rw.refresh(db)
            assert n >= 1  # at least the MOM1 cell

            # MOM1 in trending: 1.0 + 0.3 * 3.0 = 1.9
            mom1_factor = rw.get_factor("trending_up", "MOM1")
            assert mom1_factor == pytest.approx(1.9, abs=0.01)
            # Unknown cell (MR1 in trending) still cold-start = 1.0
            assert rw.get_factor("trending_up", "MR1") == 1.0

            # Now vote with the matured deriver — shadow log should show
            # divergence because MOM1's BUY now carries 1.9x weight
            registry = MagicMock()
            registry.get_active_for_regime.return_value = [
                _make_strategy("MOM1", "BUY"),
                _make_strategy("MOM2", "BUY"),  # cold-start, factor=1.0
                _make_strategy("MR1", "SELL"),
            ]
            registry.get_performance.return_value = StrategyPerformance(
                strategy_name="X", ensemble_weight=1.0,
            )
            cache = EnsembleStateCache()
            voter = EnsembleVoter(
                registry=registry, settings=settings,
                state_cache=cache, regime_weighter=rw,
            )
            setup, regime = _build_setup_and_regime()
            with capture_logs() as buf:
                result = voter.vote(
                    setup=setup, candles_map={"BTCUSDT": []},
                    ta_map={"BTCUSDT": {}}, sentiment_data=None,
                    altdata=None, regime=regime,
                )
            log_text = buf.getvalue()
            assert "STRAT_VOTE_TRACE_SHADOW" in log_text

            # Issue #19 (2026-05-27): regime weighting is enabled by default, so
            # the LIVE result IS the regime-weighted value — MOM1's BUY carries
            # 1.9x: 1.0*1.9*0.7 + 1.0*1.0*0.7 = 1.33 + 0.7 = 2.03.
            assert result.buy_votes == pytest.approx(2.03, abs=0.05)
            # The shadow trace line shows the same 2.03 vs the equal-weight 1.4
            assert "shadow_buy=2.03" in log_text or "shadow_buy=2.02" in log_text
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_audit_log_emits_loud_on_synthetic_degenerate_state() -> None:
    """Forge a state where every strategy is at floor in every regime;
    log_audit must emit RULE16_RW_PERMANENT_SILENCE at ERROR."""
    from src.strategies.regime_weighter import (
        CellPerformance, StrategyWeightDeriver,
    )

    rw = StrategyWeightDeriver(cold_start_n=1, floor=0.3, ceil=3.0)
    for regime in ("trending_up", "ranging", "volatile"):
        for s_name in ("S1", "S2"):
            rw._regime_weights.setdefault(regime, {})[s_name] = 0.3
            rw._cells[(regime, s_name)] = CellPerformance(
                strategy_name=s_name, regime=regime,
                sample_size=100, factor_smoothed=0.3,
            )

    with capture_logs() as buf:
        rw.log_audit()
    log_text = buf.getvalue()
    assert "RULE16_RW_PERMANENT_SILENCE" in log_text
    assert "ERROR" in log_text
    # S1 and S2 should both be silenced
    assert "silenced_count=2" in log_text


@pytest.mark.asyncio
async def test_audit_log_emits_loud_on_regime_independence() -> None:
    """Forge a state where every regime has IDENTICAL weights — the
    mechanism has degenerated to flat. log_audit must emit
    RULE16_RW_REGIME_INDEPENDENT at ERROR."""
    from src.strategies.regime_weighter import StrategyWeightDeriver

    rw = StrategyWeightDeriver()
    # Identical weight vectors across two regimes
    rw._regime_weights["trending_up"] = {"S1": 1.0, "S2": 2.0}
    rw._regime_weights["ranging"] = {"S1": 1.0, "S2": 2.0}
    with capture_logs() as buf:
        rw.log_audit()
    log_text = buf.getvalue()
    assert "RULE16_RW_REGIME_INDEPENDENT" in log_text
    assert "ERROR" in log_text


@pytest.mark.asyncio
async def test_audit_log_is_quiet_on_healthy_state() -> None:
    """Healthy state: different factors across regimes, no strategy at
    permanent floor. Only the RW_AUDIT_OK info log fires."""
    from src.strategies.regime_weighter import StrategyWeightDeriver

    rw = StrategyWeightDeriver()
    rw._regime_weights["trending_up"] = {"S1": 1.5, "S2": 0.8}
    rw._regime_weights["ranging"] = {"S1": 0.9, "S2": 1.4}
    with capture_logs() as buf:
        rw.log_audit()
    log_text = buf.getvalue()
    assert "RW_AUDIT_OK" in log_text
    assert "RULE16_RW_PERMANENT_SILENCE" not in log_text
    assert "RULE16_RW_REGIME_INDEPENDENT" not in log_text
