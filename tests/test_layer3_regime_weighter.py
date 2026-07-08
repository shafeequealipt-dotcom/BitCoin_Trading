"""Layer 3 — StrategyWeightDeriver unit tests.

Verifies the cold-start contract, factor formula, smoothing, bounds, and
the Rule 16 audit signals. Uses a tempfile DB + the real Layer 2 schema
so the JOIN behaves exactly as in production.
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.mark.asyncio
async def test_cold_start_returns_one_for_unknown_cells() -> None:
    from src.strategies.regime_weighter import StrategyWeightDeriver

    rw = StrategyWeightDeriver(cold_start_n=20, floor=0.3, ceil=3.0)
    # No refresh; cell is unknown
    assert rw.get_factor("trending_up", "B1_volume_breakout") == 1.0


@pytest.mark.asyncio
async def test_refresh_returns_zero_on_empty_db() -> None:
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.strategies.regime_weighter import StrategyWeightDeriver

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "rw.db"))
        await db.connect()
        try:
            await run_migrations(db)
            rw = StrategyWeightDeriver()
            n = await rw.refresh(db)
            assert n == 0  # no ensemble_votes / trade_intelligence rows
            assert rw.get_factor("trending_up", "B1_volume_breakout") == 1.0
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_cell_below_threshold_stays_at_one() -> None:
    """A (strategy, regime) cell with sample_size < cold_start_n must
    keep factor = 1.0 even if supporting trades exist."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.strategies.regime_weighter import StrategyWeightDeriver

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "rw.db"))
        await db.connect()
        try:
            await run_migrations(db)
            # Seed 5 supporting trades for B1 in trending_up — below N=20
            for i in range(5):
                sid = f"sid_{i}"
                await db.execute(
                    "INSERT INTO ensemble_votes (setup_id, symbol, direction, "
                    "strategy_name, vote, confidence, weight) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, "BTCUSDT", "Buy", "B1", "BUY", 0.7, 1.0),
                )
                await db.execute(
                    "INSERT INTO trade_intelligence (symbol, direction, strategy_name, "
                    "strategy_category, source, closed_by, entry_price, exit_price, "
                    "pnl_pct, pnl_usd, win, hold_seconds, setup_id, entry_regime, "
                    "exchange_mode, trade_closed_at, captured_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("BTCUSDT", "Buy", "B1", "momentum", "test", "tp",
                     100.0, 102.0, 2.0, 10.0, 1, 120.0, sid, "trending_up", "shadow",
                     "2026-05-22T12:00:00+00:00", "2026-05-22T12:00:00+00:00"),
                )
            rw = StrategyWeightDeriver(cold_start_n=20)
            await rw.refresh(db)
            # 5 supporting trades — still cold-start
            assert rw.get_factor("trending_up", "B1") == 1.0
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_past_threshold_derives_factor_in_bounds() -> None:
    """A cell with sample_size >= cold_start_n must derive a factor in
    [floor, ceil] from supporting_avg_pnl_pct."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.strategies.regime_weighter import StrategyWeightDeriver

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "rw.db"))
        await db.connect()
        try:
            await run_migrations(db)
            # Seed 25 winning trades for B1 in trending_up — past N=20
            # Avg pnl = 2.0% → factor = 1.0 + 0.3 * 2.0 = 1.6
            for i in range(25):
                sid = f"sid_b1_{i}"
                await db.execute(
                    "INSERT INTO ensemble_votes (setup_id, symbol, direction, "
                    "strategy_name, vote, confidence, weight) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, "BTCUSDT", "Buy", "B1", "BUY", 0.7, 1.0),
                )
                await db.execute(
                    "INSERT INTO trade_intelligence (symbol, direction, strategy_name, "
                    "strategy_category, source, closed_by, entry_price, exit_price, "
                    "pnl_pct, pnl_usd, win, hold_seconds, setup_id, entry_regime, "
                    "exchange_mode, trade_closed_at, captured_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("BTCUSDT", "Buy", "B1", "momentum", "test", "tp",
                     100.0, 102.0, 2.0, 10.0, 1, 120.0, sid, "trending_up", "shadow",
                     "2026-05-22T12:00:00+00:00", "2026-05-22T12:00:00+00:00"),
                )
            rw = StrategyWeightDeriver(
                cold_start_n=20, floor=0.3, ceil=3.0,
                sensitivity=0.3, ema_alpha=1.0,  # alpha=1 disables smoothing for the test
            )
            await rw.refresh(db)
            factor = rw.get_factor("trending_up", "B1")
            # 1.0 + 0.3 * 2.0 = 1.6
            assert factor == pytest.approx(1.6, abs=0.01)
            # And in bounds
            assert 0.3 <= factor <= 3.0
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_negative_pnl_clamped_to_floor() -> None:
    """A strategy with strongly negative supporting_avg_pnl must clamp to
    the floor, never below — enforcing Rule 5 'no strategy ever silenced'."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.strategies.regime_weighter import StrategyWeightDeriver

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "rw.db"))
        await db.connect()
        try:
            await run_migrations(db)
            # Seed 25 losing trades for momentum strategy in ranging — past N
            # Avg pnl = -5.0% → raw factor = 1.0 + 0.3 * -5.0 = -0.5 → clamped to 0.3
            for i in range(25):
                sid = f"sid_loss_{i}"
                await db.execute(
                    "INSERT INTO ensemble_votes (setup_id, symbol, direction, "
                    "strategy_name, vote, confidence, weight) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, "ETHUSDT", "Buy", "B2", "BUY", 0.7, 1.0),
                )
                await db.execute(
                    "INSERT INTO trade_intelligence (symbol, direction, strategy_name, "
                    "strategy_category, source, closed_by, entry_price, exit_price, "
                    "pnl_pct, pnl_usd, win, hold_seconds, setup_id, entry_regime, "
                    "exchange_mode, trade_closed_at, captured_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("ETHUSDT", "Buy", "B2", "momentum", "test", "sl",
                     100.0, 95.0, -5.0, -10.0, 0, 60.0, sid, "ranging", "shadow",
                     "2026-05-22T12:00:00+00:00", "2026-05-22T12:00:00+00:00"),
                )
            rw = StrategyWeightDeriver(
                cold_start_n=20, floor=0.3, ceil=3.0,
                sensitivity=0.3, ema_alpha=1.0,
            )
            await rw.refresh(db)
            factor = rw.get_factor("ranging", "B2")
            assert factor == 0.3  # clamped to floor; not zero (no silencing)
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_same_strategy_different_regimes_different_factors() -> None:
    """The core aim test: the same strategy must get DIFFERENT factors
    in different regimes based on its per-regime performance."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.strategies.regime_weighter import StrategyWeightDeriver

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "rw.db"))
        await db.connect()
        try:
            await run_migrations(db)
            # Same strategy "MOM1" — winning in trending, losing in ranging
            for i in range(25):  # winning trend
                sid = f"sid_trend_{i}"
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
                     100.0, 103.0, 3.0, 15.0, 1, 120.0, sid, "trending_up", "shadow",
                     "2026-05-22T12:00:00+00:00", "2026-05-22T12:00:00+00:00"),
                )
            for i in range(25):  # losing ranging
                sid = f"sid_range_{i}"
                await db.execute(
                    "INSERT INTO ensemble_votes (setup_id, symbol, direction, "
                    "strategy_name, vote, confidence, weight) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, "ETHUSDT", "Buy", "MOM1", "BUY", 0.7, 1.0),
                )
                await db.execute(
                    "INSERT INTO trade_intelligence (symbol, direction, strategy_name, "
                    "strategy_category, source, closed_by, entry_price, exit_price, "
                    "pnl_pct, pnl_usd, win, hold_seconds, setup_id, entry_regime, "
                    "exchange_mode, trade_closed_at, captured_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ("ETHUSDT", "Buy", "MOM1", "momentum", "test", "sl",
                     100.0, 98.0, -2.0, -10.0, 0, 60.0, sid, "ranging", "shadow",
                     "2026-05-22T12:00:00+00:00", "2026-05-22T12:00:00+00:00"),
                )
            rw = StrategyWeightDeriver(
                cold_start_n=20, floor=0.3, ceil=3.0,
                sensitivity=0.3, ema_alpha=1.0,
            )
            await rw.refresh(db)
            f_trend = rw.get_factor("trending_up", "MOM1")
            f_range = rw.get_factor("ranging", "MOM1")
            # Trending: 1.0 + 0.3 * 3.0 = 1.9
            assert f_trend == pytest.approx(1.9, abs=0.01)
            # Ranging: 1.0 + 0.3 * -2.0 = 0.4
            assert f_range == pytest.approx(0.4, abs=0.01)
            # The regime-conditional contract — same strategy, different factors
            assert f_trend != f_range
            # And the audit reports regime-dependent weights
            audit = rw.audit()
            assert not audit["regime_independent"]
            assert audit["permanent_silence_violations"] == []
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_audit_detects_permanent_silence() -> None:
    """If a strategy has factor=floor in every regime, audit flags it."""
    from src.strategies.regime_weighter import StrategyWeightDeriver

    rw = StrategyWeightDeriver(cold_start_n=1, floor=0.3, ceil=3.0)
    # Forge cells directly: BAD strategy at floor in all 3 regimes
    from src.strategies.regime_weighter import CellPerformance
    for regime in ("trending_up", "ranging", "volatile"):
        rw._regime_weights.setdefault(regime, {})["BAD"] = 0.3
        rw._cells[(regime, "BAD")] = CellPerformance(
            strategy_name="BAD", regime=regime, sample_size=100,
            factor_smoothed=0.3,
        )
    # GOOD strategy with mixed factors
    for regime, f in [("trending_up", 2.0), ("ranging", 1.0)]:
        rw._regime_weights.setdefault(regime, {})["GOOD"] = f
        rw._cells[(regime, "GOOD")] = CellPerformance(
            strategy_name="GOOD", regime=regime, sample_size=100,
            factor_smoothed=f,
        )
    audit = rw.audit()
    assert "BAD" in audit["permanent_silence_violations"]
    assert "GOOD" not in audit["permanent_silence_violations"]


@pytest.mark.asyncio
async def test_audit_detects_regime_independence() -> None:
    """If every regime's weight vector is identical, the mechanism has
    degenerated to flat — audit must flag it."""
    from src.strategies.regime_weighter import StrategyWeightDeriver

    rw = StrategyWeightDeriver()
    # Same weights in two regimes
    rw._regime_weights["trending_up"] = {"S1": 1.0, "S2": 2.0}
    rw._regime_weights["ranging"] = {"S1": 1.0, "S2": 2.0}
    audit = rw.audit()
    assert audit["regime_independent"] is True


@pytest.mark.asyncio
async def test_ema_smoothing_dampens_swings() -> None:
    """EMA smoothing prevents single-cycle factor swings from whipsawing
    the cached factor."""
    from src.strategies.regime_weighter import StrategyWeightDeriver, CellPerformance

    rw = StrategyWeightDeriver(
        cold_start_n=1, floor=0.3, ceil=3.0,
        sensitivity=1.0, ema_alpha=0.3,
    )
    # Seed previous factor = 1.0
    rw._regime_weights["trending_up"] = {"S1": 1.0}
    rw._cells[("trending_up", "S1")] = CellPerformance(
        strategy_name="S1", regime="trending_up", sample_size=100,
        factor_smoothed=1.0,
    )
    # Manually compute what one refresh would produce vs the EMA-blended
    # Computed raw = 1.0 + 1.0 * 2.0 = 3.0
    # Smoothed = 0.3 * 3.0 + 0.7 * 1.0 = 0.9 + 0.7 = 1.6
    raw = max(0.3, min(3.0, 1.0 + 1.0 * 2.0))
    smoothed = 0.3 * raw + 0.7 * 1.0
    assert smoothed == pytest.approx(1.6, abs=0.01)


# --- Grain-mismatch fix (2026-06-09) -----------------------------------------
# trade_intelligence holds multiple analysis rows per setup_id and
# ensemble_votes re-writes a strategy's vote for a setup each cycle. The prior
# query summed win/pnl over the JOIN-duplicated rows but divided by distinct
# setups, inflating win_rate / avg_pnl. These tests pin the de-dup.

_TI_COLS = (
    "INSERT INTO trade_intelligence (symbol, direction, strategy_name, "
    "strategy_category, source, closed_by, entry_price, exit_price, "
    "pnl_pct, pnl_usd, win, hold_seconds, setup_id, entry_regime, "
    "exchange_mode, trade_closed_at, captured_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


@pytest.mark.asyncio
async def test_grain_dedup_no_inflation_from_duplicate_rows() -> None:
    """Duplicate ti rows per setup AND duplicate ensemble_votes per
    (setup, strategy) must NOT inflate the metric. 25 winning setups (pnl 2.0),
    each with 3 duplicate votes and 3 duplicate analysis rows. De-duped this is
    25 setups, win_rate 1.0, avg_pnl 2.0 -> factor 1.6. Pre-fix it inflated to
    sample/9 and avg_pnl ~18 (factor clamped to the ceil)."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.strategies.regime_weighter import StrategyWeightDeriver

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "rw.db"))
        await db.connect()
        try:
            await run_migrations(db)
            for i in range(25):
                sid = f"dup_{i}"
                for _ in range(3):
                    await db.execute(
                        "INSERT INTO ensemble_votes (setup_id, symbol, direction, "
                        "strategy_name, vote, confidence, weight) VALUES (?,?,?,?,?,?,?)",
                        (sid, "BTCUSDT", "Buy", "B1", "BUY", 0.7, 1.0),
                    )
                for _ in range(3):
                    await db.execute(
                        _TI_COLS,
                        ("BTCUSDT", "Buy", "B1", "momentum", "test", "tp", 100.0,
                         102.0, 2.0, 10.0, 1, 120.0, sid, "trending_up", "shadow",
                         "2026-05-22T12:00:00+00:00", "2026-05-22T12:00:00+00:00"),
                    )
            rw = StrategyWeightDeriver(cold_start_n=20, floor=0.3, ceil=3.0,
                                       sensitivity=0.3, ema_alpha=1.0)
            await rw.refresh(db)
            assert rw.get_factor("trending_up", "B1") == pytest.approx(1.6, abs=0.01)
            cell = rw._cells[("trending_up", "B1")]
            assert cell.sample_size == 25          # distinct setups, not 25*9
            assert cell.win_rate == pytest.approx(1.0, abs=0.001)  # not inflated >1
            assert cell.avg_pnl_pct == pytest.approx(2.0, abs=0.01)  # not ~18
        finally:
            await db.disconnect()


@pytest.mark.asyncio
async def test_grain_dedup_uses_canonical_latest_outcome() -> None:
    """When a setup's duplicate ti rows conflict on win, the de-dup uses the
    CANONICAL latest (by rowid) row — the final analysis — not an earlier one.
    Each setup: an earlier LOSS row then a later WIN row -> win_rate 1.0."""
    from src.database.connection import DatabaseManager
    from src.database.migrations import run_migrations
    from src.strategies.regime_weighter import StrategyWeightDeriver

    with tempfile.TemporaryDirectory() as td:
        db = DatabaseManager(os.path.join(td, "rw.db"))
        await db.connect()
        try:
            await run_migrations(db)
            for i in range(25):
                sid = f"canon_{i}"
                await db.execute(
                    "INSERT INTO ensemble_votes (setup_id, symbol, direction, "
                    "strategy_name, vote, confidence, weight) VALUES (?,?,?,?,?,?,?)",
                    (sid, "BTCUSDT", "Buy", "B1", "BUY", 0.7, 1.0),
                )
                # earlier: loss; later (higher rowid): win+2.0 (canonical)
                await db.execute(
                    _TI_COLS,
                    ("BTCUSDT", "Buy", "B1", "momentum", "test", "sl", 100.0, 98.0,
                     -2.0, -10.0, 0, 120.0, sid, "trending_up", "shadow",
                     "2026-05-22T11:00:00+00:00", "2026-05-22T11:00:00+00:00"),
                )
                await db.execute(
                    _TI_COLS,
                    ("BTCUSDT", "Buy", "B1", "momentum", "test", "tp", 100.0, 102.0,
                     2.0, 10.0, 1, 120.0, sid, "trending_up", "shadow",
                     "2026-05-22T12:00:00+00:00", "2026-05-22T12:00:00+00:00"),
                )
            rw = StrategyWeightDeriver(cold_start_n=20, floor=0.3, ceil=3.0,
                                       sensitivity=0.3, ema_alpha=1.0)
            await rw.refresh(db)
            cell = rw._cells[("trending_up", "B1")]
            assert cell.sample_size == 25
            assert cell.win_rate == pytest.approx(1.0, abs=0.001)   # canonical win
            assert cell.avg_pnl_pct == pytest.approx(2.0, abs=0.01)
        finally:
            await db.disconnect()
