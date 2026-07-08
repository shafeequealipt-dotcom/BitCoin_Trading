"""CALL_B Framing Fix Phase 2B (2026-05-06) — convert SURVIVAL RR floor
from BLOCK to TP-scale ADJUSTMENT.

Three smoke tests covering the new contract on PerformanceEnforcer:
  1. RR=2.5 in SURVIVAL with feasible TP scaling → adjusted.
  2. RR=2.5 with TP scaling that would exceed structural ceiling → blocked.
  3. RR=3.0 (already at floor) → no adjustment needed.

Per the operator's pace memory, surgical smoke tests only.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.config.settings import EnforcerSettings
from src.strategies.performance_enforcer import PerformanceEnforcer


def _make_enforcer(*, level: int = 0, pnl: float = 0.0) -> PerformanceEnforcer:
    """Construct a PerformanceEnforcer skipping I/O dependencies."""
    pe = PerformanceEnforcer.__new__(PerformanceEnforcer)
    cfg = EnforcerSettings()
    pe.settings = type("S", (), {"enforcer": cfg})()
    pe._enforcement_level = level
    pe._profit_today_pct = pnl
    pe._l1_max_pos = cfg.level_1_max_positions
    pe._l1_max_lev = cfg.level_1_max_leverage
    pe._l1_min_score = cfg.level_1_min_score
    pe._l2_max_pos = cfg.level_2_max_positions
    pe._l2_max_lev = cfg.level_2_max_leverage
    pe._l2_min_score = cfg.level_2_min_score
    pe._l2_min_confluence = cfg.level_2_min_confluence
    pe._l2_min_rr = cfg.level_2_min_rr
    pe._streak_boost_threshold = cfg.streak_boost_threshold
    pe._streak_boost_pnl_floor_pct = cfg.streak_boost_pnl_floor_pct
    pe._pnl_caution_pct = cfg.pnl_caution_pct
    pe._pnl_survival_pct = cfg.pnl_survival_pct
    pe._pnl_halted_pct = cfg.pnl_halted_pct
    pe._size_reduction_enabled = cfg.size_reduction_enabled
    pe._size_reduction_at_pnl_pct = cfg.size_reduction_at_pnl_pct
    pe._size_reduction_factor = cfg.size_reduction_factor
    pe._recovery_stage = 0
    pe._recent_results = []
    return pe


def _make_structure_cache(
    *,
    rr_long: float = 2.5,
    rr_short: float = 0.5,
    long_sl: float = 99.0,
    long_tp: float = 102.5,
    short_sl: float = 102.0,
    short_tp: float = 99.0,
    entry_low: float = 99.5,
    entry_high: float = 100.5,
    setup_quality: str = "A",
    confluence: int = 8,
):
    """Mock structure_cache with the bare minimum surface
    qualify_survival_trade + try_adjust_for_survival_rr touch.
    """
    placement = SimpleNamespace(
        rr_long=rr_long,
        rr_short=rr_short,
        rr_ratio=max(rr_long, rr_short),
        long_sl_price=long_sl,
        long_tp_price=long_tp,
        short_sl_price=short_sl,
        short_tp_price=short_tp,
        entry_zone_low=entry_low,
        entry_zone_high=entry_high,
        structural_sl=long_sl,
        structural_tp=long_tp,
    )
    mtf = SimpleNamespace(score=confluence)
    analysis = SimpleNamespace(
        structural_placement=placement,
        mtf_confluence=mtf,
        setup_quality=setup_quality,
    )
    cache = SimpleNamespace()
    cache.get = lambda symbol: analysis
    return cache


# ─── Phase 2B regression guards ─────────────────────────────────────


def test_phase2b_rr_25_in_survival_scales_tp_to_floor() -> None:
    """X-RAY structural RR=2.5 for Buy in SURVIVAL.
    Buy: entry≈100, SL=99 (risk=1), structural TP=102.5 (RR=2.5).
    target_rr=3.0 → reward_target=3.0, new_tp=103. Ceiling=102.5 + 1.25
    = 103.75. 103 <= 103.75 → adjusted.
    """
    pe = _make_enforcer(level=2, pnl=-12.5)
    cache = _make_structure_cache(rr_long=2.5)
    new_tp, reason, old_rr, new_rr = pe.try_adjust_for_survival_rr(
        "BTCUSDT", "Buy", cache
    )
    assert new_tp is not None, f"expected scaling to succeed; got reason={reason}"
    assert reason == "rr_scaled_to_floor"
    assert old_rr == 2.5
    assert new_rr >= 3.0 - 1e-3  # within rounding tolerance
    assert new_tp == 103.0


def test_phase2b_rr_25_with_aggressive_target_exceeds_ceiling_blocks() -> None:
    """Same setup but target_rr=10.0 → reward_target=10, new_tp=110.
    Ceiling=102.5 + 1.25 = 103.75. 110 > 103.75 → block.
    """
    pe = _make_enforcer(level=2, pnl=-12.5)
    cache = _make_structure_cache(rr_long=2.5)
    new_tp, reason, old_rr, new_rr = pe.try_adjust_for_survival_rr(
        "BTCUSDT", "Buy", cache, target_rr=10.0
    )
    assert new_tp is None
    assert reason.startswith("rr_scale_exceeds_ceiling:")
    assert old_rr == 2.5
    assert new_rr == 0.0


def test_phase2b_halted_blocks_adjustment_attempt() -> None:
    """At HALTED (level 3) the adjustment helper refuses regardless of
    structural buffer. The emergency stop overrides aggressive-exploitation.
    """
    pe = _make_enforcer(level=3, pnl=-16.0)
    cache = _make_structure_cache(rr_long=2.5)
    new_tp, reason, old_rr, new_rr = pe.try_adjust_for_survival_rr(
        "BTCUSDT", "Buy", cache
    )
    assert new_tp is None
    assert reason == "halted"


def test_phase2b_not_in_survival_returns_no_adjustment() -> None:
    """If the trade isn't in SURVIVAL, adjustment is a no-op.
    Caller should never have invoked the helper, but the guard is here
    so a future call-site mistake doesn't silently scale TP.
    """
    pe = _make_enforcer(level=0, pnl=0.0)
    cache = _make_structure_cache(rr_long=2.5)
    new_tp, reason, _, _ = pe.try_adjust_for_survival_rr(
        "BTCUSDT", "Buy", cache
    )
    assert new_tp is None
    assert reason == "rr_not_in_survival"


def test_phase2b_scale_tp_helper_handles_zero_risk() -> None:
    """The low-level helper returns (None, "rr_scale_zero_risk") when
    entry == sl. Catches a regression where the helper would silently
    divide by zero or scale to infinity.
    """
    pe = _make_enforcer(level=2, pnl=-12.5)
    new_tp, reason = pe._scale_tp_for_rr(
        side="Buy", entry=100.0, sl=100.0, target_rr=3.0,
        structural_ceiling=110.0,
    )
    assert new_tp is None
    assert reason == "rr_scale_zero_risk"


def test_phase2b_scale_tp_helper_buy_within_ceiling() -> None:
    """Direct helper test for Buy direction with feasible scaling."""
    pe = _make_enforcer(level=2, pnl=-12.5)
    new_tp, reason = pe._scale_tp_for_rr(
        side="Buy", entry=100.0, sl=99.0, target_rr=3.0,
        structural_ceiling=110.0,
    )
    assert new_tp == 103.0
    assert reason == "rr_scaled_to_floor"


def test_phase2b_scale_tp_helper_sell_within_floor() -> None:
    """Direct helper test for Sell direction. For a sell, ceiling is
    a price BELOW entry; new_tp must remain above the price-floor.
    """
    pe = _make_enforcer(level=2, pnl=-12.5)
    new_tp, reason = pe._scale_tp_for_rr(
        side="Sell", entry=100.0, sl=101.0, target_rr=3.0,
        structural_ceiling=90.0,  # i.e., we won't scale TP below 90
    )
    assert new_tp == 97.0  # 100 - 3*1
    assert reason == "rr_scaled_to_floor"
