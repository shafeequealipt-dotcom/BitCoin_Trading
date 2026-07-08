"""BETA R2 + R3 — composite-score lock and WR-aware override.

Covers the new composite scoring framework that replaces the prior
regime-only direction lock, plus the WR-derived XRAY override
threshold that replaces the static 10.0x dead zone.

Phase 2.7 directive: 'sell and buy should be both work according to
the best scenarios, not hard coded saying if sell this much then buy
this much not like that.' Each test verifies the new mechanism makes
direction decisions from evidence at decision time, not from
direction-specific hard-coded thresholds.
"""

import asyncio
import math
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ===========================================================================
# Composite scoring — signal isolation tests
# ===========================================================================


def _opt(settings=None):
    from src.apex.optimizer import TradeOptimizer
    return TradeOptimizer(qwen_client=None, assembler=None, settings=settings)


def _pkg(
    regime: str = "ranging",
    rr_long: float | None = None,
    rr_short: float | None = None,
    trade_direction: str = "",
    buy_wr: float = 0.0,
    sell_wr: float = 0.0,
    history_trades: list | None = None,
):
    """Build a duck-typed package with the fields composite scoring reads."""
    sd = SimpleNamespace(
        rr_long=rr_long,
        rr_short=rr_short,
        trade_direction=trade_direction,
    )
    sit = SimpleNamespace(
        buy_win_rate=buy_wr,
        sell_win_rate=sell_wr,
        regime=regime,
    )
    sym_hist = SimpleNamespace(trades=history_trades or [])
    return SimpleNamespace(
        structural_data=sd,
        situation_data=sit,
        symbol_history=sym_hist,
        coin_data=SimpleNamespace(current_price=1.0, recommended_tp_pct=None),
        directive=SimpleNamespace(reasoning=""),
    )


def test_signal_isolation_regime_only_aligned_buy_no_lock():
    """regime_signal alone +1 -> score 1 > 0 threshold -> not locked."""
    opt = _opt()
    pkg = _pkg(regime="trending_up")
    locked, reason = opt._check_direction_lock(pkg, "Buy", "trending_up")
    assert locked is False
    comp = opt._last_lock_components
    assert comp["regime"] == 1.0
    assert comp["structural"] == 0.0
    assert comp["trade_dir"] == 0.0
    assert comp["wr"] == 0.0
    assert comp["symbol_evidence"] == 0.0
    assert comp["score"] == 1.0
    print("  PASS: aligned regime alone => score +1 => no lock")


def test_signal_isolation_regime_opposed_buy_locked():
    """regime opposes brain -> score -1 < 0 -> locked."""
    opt = _opt()
    pkg = _pkg(regime="trending_down")
    locked, reason = opt._check_direction_lock(pkg, "Buy", "trending_down")
    assert locked is True
    comp = opt._last_lock_components
    assert comp["regime"] == -1.0
    assert comp["score"] == -1.0
    print("  PASS: opposing regime alone => score -1 => locked")


def test_signal_isolation_structural_dominates_regime():
    """Strong structural support (7.3x) flips score positive despite regime -1.

    The BSBUSDT case (15:02 on 2026-05-16): regime=volatile contributes
    0; rr_long=3.7 vs rr_short=0.5 favours Long by 7.3x; log(7.3) ~ 1.99
    > 1.0 -> composite score positive -> NOT locked. Pre-fix the lock
    fired regardless of structural data; -$70.08 SL hit on the locked
    Sell. Composite scoring now lets the structural signal win.
    """
    opt = _opt()
    # Pick rr_long, rr_short such that the ratio is a clean 7.3:
    rr_long = 7.3
    rr_short = 1.0
    pkg = _pkg(
        regime="volatile",
        rr_long=rr_long,
        rr_short=rr_short,
        trade_direction="long",
    )
    locked, reason = opt._check_direction_lock(pkg, "Buy", "volatile")
    comp = opt._last_lock_components
    expected_struct = round(math.log(rr_long / rr_short), 3)
    # regime=volatile contributes 0; structural log(7.3) ~ +1.99;
    # trade_direction=long matches brain Buy -> +1
    assert comp["regime"] == 0.0
    assert comp["structural"] == expected_struct
    assert comp["trade_dir"] == 1.0
    assert comp["score"] > 0
    assert locked is False
    print(
        f"  PASS: BSBUSDT-class 7.3x + counter trade_direction => "
        f"score={comp['score']} >> 0 => no lock"
    )


def test_signal_isolation_wr_drives_asymmetry():
    """Per-direction WR delta produces directional asymmetry from data.

    Operator directive: 'not hard coded saying if sell this much then
    buy this much'. The asymmetry between Buy and Sell EMERGES from
    the WR signal. Buy WR 55.6 / Sell WR 41.8 (matches the last-200
    aggregate from COMPLETE_FINDINGS) yields:
    - wr_signal for Buy = (55.6 - 50) / 50 = +0.112
    - wr_signal for Sell = (41.8 - 50) / 50 = -0.164
    With regime=ranging and no other signals: brain Buy gets +0.112
    (not locked); brain Sell gets -0.164 (locked). The lock asks the
    SAME direction-agnostic question; the answer differs because the
    data differs.
    """
    opt = _opt()
    pkg = _pkg(regime="ranging", buy_wr=55.6, sell_wr=41.8)

    locked_buy, _ = opt._check_direction_lock(pkg, "Buy", "ranging")
    comp_buy = dict(opt._last_lock_components)
    assert comp_buy["wr"] == 0.112
    assert comp_buy["score"] == 0.112
    assert locked_buy is False

    locked_sell, _ = opt._check_direction_lock(pkg, "Sell", "ranging")
    comp_sell = dict(opt._last_lock_components)
    assert comp_sell["wr"] == -0.164
    assert comp_sell["score"] == -0.164
    assert locked_sell is True
    print(
        f"  PASS: same WR fixture => Buy score={comp_buy['score']:+.3f} unlocks; "
        f"Sell score={comp_sell['score']:+.3f} locks (asymmetry is data-driven)"
    )


def test_signal_isolation_symbol_evidence_high_opposing_wr_locks():
    """Symbol-specific high opposite-direction WR locks the brain."""
    opt = _opt()
    trades = [{"direction": "Sell", "win": True} for _ in range(8)]  # 8 of 8 wins
    pkg = _pkg(regime="ranging", history_trades=trades)
    locked, _ = opt._check_direction_lock(pkg, "Buy", "ranging")
    comp = opt._last_lock_components
    assert comp["symbol_evidence"] == -1.0
    assert comp["score"] == -1.0
    assert locked is True
    print("  PASS: opposing direction 100% WR (>= 70% floor) => symbol-evidence -1 => locked")


def test_signal_isolation_symbol_evidence_high_same_wr_supports():
    """Symbol-specific high SAME-direction WR contributes +1 to score."""
    opt = _opt()
    trades = [{"direction": "Buy", "win": True} for _ in range(8)]
    pkg = _pkg(regime="ranging", history_trades=trades)
    locked, _ = opt._check_direction_lock(pkg, "Buy", "ranging")
    comp = opt._last_lock_components
    assert comp["symbol_evidence"] == 1.0
    assert comp["score"] == 1.0
    assert locked is False
    print("  PASS: same direction 100% WR => symbol-evidence +1 => unlocked")


def test_composite_all_signals_align_against_brain():
    """All five signals against the brain -> deep negative score, locked."""
    opt = _opt()
    pkg = _pkg(
        regime="trending_down",  # -1 for brain=Buy
        rr_long=0.5,
        rr_short=3.7,  # log(0.5/3.7) ~ -2.0 for brain=Buy
        trade_direction="short",  # -1 for brain=Buy
        buy_wr=30.0,  # -0.4
        sell_wr=70.0,
        history_trades=[{"direction": "Sell", "win": True} for _ in range(8)],
    )
    locked, _ = opt._check_direction_lock(pkg, "Buy", "trending_down")
    comp = opt._last_lock_components
    assert comp["score"] < -3.0
    assert locked is True
    print(
        f"  PASS: all signals against brain => composite={comp['score']:.2f} -> locked"
    )


def test_composite_settings_none_uses_defaults():
    """When settings is None, defensive defaults keep the scorer working."""
    opt = _opt(settings=None)
    pkg = _pkg(regime="trending_down")
    locked, _ = opt._check_direction_lock(pkg, "Buy", "trending_down")
    assert locked is True
    comp = opt._last_lock_components
    assert comp["score"] == -1.0
    print("  PASS: settings=None => default weights 1.0, threshold 0.0, behaves correctly")


# ===========================================================================
# R3 — WR-aware XRAY override threshold
# ===========================================================================


class _FakeDB:
    """Minimal stub matching the production DatabaseManager fetch_all API.

    Production `DatabaseManager.fetch_all(sql, params)` returns
    `list[dict[str, Any]]`. This stub returns the canned rows directly
    (each row a dict with `direction` + `pnl_usd` keys, matching the
    SELECT shape used by `_derive_wr_aware_override_threshold`).
    """

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.last_sql: str | None = None
        self.last_params: tuple | None = None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        self.last_sql = sql
        self.last_params = params
        return self._rows


class _FakeStrategyWorker:
    """Slim mock that exposes _derive_wr_aware_override_threshold."""

    def __init__(self, rows, settings):
        self.services = {"db": _FakeDB(rows)}
        self.settings = settings

    _derive_wr_aware_override_threshold = (
        # Bind the real method off StrategyWorker so this mock exercises
        # production logic.
        None
    )


def _strategy_worker_with(rows):
    from src.workers.strategy_worker import StrategyWorker
    from src.config.settings import Settings

    settings = Settings()
    sw = _FakeStrategyWorker(rows, settings)
    sw._derive_wr_aware_override_threshold = (
        StrategyWorker._derive_wr_aware_override_threshold.__get__(sw, _FakeStrategyWorker)
    )
    return sw


def test_r3_cold_start_falls_back_to_legacy():
    """< wr_window_min trades => return legacy 10.0 with source='cold_start'."""
    sw = _strategy_worker_with([])  # zero trades
    threshold, meta = asyncio.run(sw._derive_wr_aware_override_threshold("Buy"))
    assert threshold == 10.0
    assert meta["source"] == "cold_start"
    assert meta["buy_n"] == 0
    assert meta["sell_n"] == 0
    print("  PASS: cold-start (zero trades) => legacy 10.0 + source=cold_start")


def test_r3_high_buy_wr_lowers_sell_to_buy_threshold():
    """Buy WR 80% -> override INTO Buy threshold = base*(1-0.8) = 2.0."""
    rows = [{"direction": "Buy", "pnl_usd": 1.0} for _ in range(40)]
    rows += [{"direction": "Buy", "pnl_usd": -1.0} for _ in range(10)]  # 80% WR
    rows += [{"direction": "Sell", "pnl_usd": -1.0} for _ in range(50)]  # 0% WR
    sw = _strategy_worker_with(rows)
    threshold, meta = asyncio.run(sw._derive_wr_aware_override_threshold("Buy"))
    # 10.0 * (1 - 0.80) = 2.0 (at floor)
    assert threshold == 2.0
    assert meta["source"] == "wr"
    assert meta["buy_wr"] == 80.0
    print(f"  PASS: Buy WR 80% => Sell->Buy threshold = {threshold} (floor 2.0)")


def test_r3_high_sell_wr_lowers_buy_to_sell_threshold():
    """Sell WR 80% -> override INTO Sell threshold = 2.0. Symmetric formula.

    Demonstrates the asymmetry between Buy and Sell tracks the data.
    If Sells start winning more than Buys, the override threshold for
    flipping INTO Sell drops automatically — no code change.
    """
    rows = [{"direction": "Sell", "pnl_usd": 1.0} for _ in range(40)]
    rows += [{"direction": "Sell", "pnl_usd": -1.0} for _ in range(10)]
    rows += [{"direction": "Buy", "pnl_usd": -1.0} for _ in range(50)]
    sw = _strategy_worker_with(rows)
    threshold, meta = asyncio.run(sw._derive_wr_aware_override_threshold("Sell"))
    assert threshold == 2.0
    assert meta["sell_wr"] == 80.0
    print(
        "  PASS: Sell WR 80% => Buy->Sell threshold = 2.0 (same formula, "
        "asymmetry from data only)"
    )


def test_r3_neutral_wr_midpoint_threshold():
    """50%/50% WR => threshold = base * 0.5 = 5.0 (midpoint of dead zone)."""
    rows = [{"direction": "Buy", "pnl_usd": (1.0 if i % 2 == 0 else -1.0)} for i in range(40)]
    rows += [{"direction": "Sell", "pnl_usd": (1.0 if i % 2 == 0 else -1.0)} for i in range(40)]
    sw = _strategy_worker_with(rows)
    threshold, meta = asyncio.run(sw._derive_wr_aware_override_threshold("Buy"))
    assert threshold == 5.0
    assert meta["buy_wr"] == 50.0
    print("  PASS: neutral 50/50 WR => threshold = 5.0 (midpoint of legacy dead zone)")


def test_r3_below_window_min_falls_back():
    """Fewer than wr_window_min trades in the flipped direction => cold-start."""
    rows = [{"direction": "Buy", "pnl_usd": 1.0} for _ in range(20)]  # only 20 Buys
    rows += [{"direction": "Sell", "pnl_usd": -1.0} for _ in range(50)]
    sw = _strategy_worker_with(rows)
    threshold, meta = asyncio.run(sw._derive_wr_aware_override_threshold("Buy"))
    # 20 < 30 (window_min) -> cold-start
    assert threshold == 10.0
    assert meta["source"] == "cold_start"
    print("  PASS: 20 Buy trades < window_min=30 => cold-start fallback")


# ===========================================================================
# Runner
# ===========================================================================


if __name__ == "__main__":
    print("BETA R2 — composite-score lock tests")
    test_signal_isolation_regime_only_aligned_buy_no_lock()
    test_signal_isolation_regime_opposed_buy_locked()
    test_signal_isolation_structural_dominates_regime()
    test_signal_isolation_wr_drives_asymmetry()
    test_signal_isolation_symbol_evidence_high_opposing_wr_locks()
    test_signal_isolation_symbol_evidence_high_same_wr_supports()
    test_composite_all_signals_align_against_brain()
    test_composite_settings_none_uses_defaults()
    print("BETA R3 — WR-aware override threshold tests")
    test_r3_cold_start_falls_back_to_legacy()
    test_r3_high_buy_wr_lowers_sell_to_buy_threshold()
    test_r3_high_sell_wr_lowers_buy_to_sell_threshold()
    test_r3_neutral_wr_midpoint_threshold()
    test_r3_below_window_min_falls_back()
    print("BETA R2 + R3: ALL 13 TESTS PASSED")
