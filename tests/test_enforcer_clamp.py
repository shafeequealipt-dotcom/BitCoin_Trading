"""Phase 4 of dir-block-fix (2026-05-05) — Performance Enforcer
recalibration: raise PnL thresholds, gate the streak path with a
PnL floor, and clamp leverage instead of blocking.

Three smoke tests covering the new contract on PerformanceEnforcer.
"""

from __future__ import annotations

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


def test_clamp_leverage_at_level_1_reduces_5x_to_3x() -> None:
    """At enforcement level 1 with pnl below caution, the previous
    behavior was to BLOCK trades requesting leverage > 3. Phase 4
    converts the BLOCK to a CLAMP so the trade can proceed at 3x.
    """
    pe = _make_enforcer(level=1, pnl=-3.5)
    clamped, reason = pe.clamp_leverage(5)
    assert clamped == 3, "Leverage 5 must clamp to level-1 cap 3"
    assert "PRESERVATION_CLAMP" in reason
    assert "5->3" in reason
    # And should_allow_trade must always return True now.
    allowed, _ = pe.should_allow_trade(leverage=5)
    assert allowed is True


def test_clamp_leverage_no_op_when_within_cap() -> None:
    """Leverage at or below the level cap is untouched, reason empty."""
    pe = _make_enforcer(level=1, pnl=-3.5)
    clamped, reason = pe.clamp_leverage(3)
    assert clamped == 3
    assert reason == ""
    pe2 = _make_enforcer(level=0, pnl=0.0)
    clamped, reason = pe2.clamp_leverage(5)
    assert clamped == 5
    assert reason == ""


def test_thresholds_raised_and_streak_pnl_floor_present() -> None:
    """Default EnforcerSettings reflect the Phase 4 raises + the
    Phase 2A SURVIVAL/HALTED recalibration so a config-touch can't
    silently revert them.
    """
    cfg = EnforcerSettings()
    assert cfg.pnl_caution_pct == -3.0
    # CALL_B Framing Fix Phase 2A (2026-05-06) — survival raised
    # -7.0 -> -12.0; HALTED introduced at -15.0.
    assert cfg.pnl_survival_pct == -12.0
    assert cfg.pnl_halted_pct == -15.0
    assert cfg.streak_boost_threshold == -8
    assert cfg.streak_boost_pnl_floor_pct == -1.0


# ─── Phase 2A regression guards (CALL_B Framing Fix, 2026-05-06) ─────


def test_phase2a_survival_triggers_at_minus_12_not_minus_7() -> None:
    """At PnL=-8.5% (between -7 and -12) the enforcer no longer enters
    SURVIVAL — it stays at CAPITAL_PRESERVATION (level 1). Regression
    guard for the operator-decided raise from -7 to -12.
    """
    pe = _make_enforcer(level=0, pnl=0.0)
    # Walk the PnL into the new survival band.
    pe._profit_today_pct = -8.5
    # Reproduce the level transition logic without the full
    # check_and_enforce machinery (DB stats etc.).
    pnl = pe._profit_today_pct
    if pnl >= 0:
        new_level = 0
    elif pnl > pe._pnl_caution_pct:
        new_level = 0
    elif pnl > pe._pnl_survival_pct:
        new_level = 1
    elif pnl > pe._pnl_halted_pct:
        new_level = 2
    else:
        new_level = 3
    assert new_level == 1, (
        f"PnL={pnl}% must map to level 1 (CAPITAL_PRESERVATION) "
        f"post-Phase-2A; got level {new_level}"
    )


def test_phase2a_halted_triggers_at_minus_15() -> None:
    """At PnL=-16% the enforcer enters HALTED (level 3). RR floor
    via qualify_survival_trade returns (False, "halted")."""
    pe = _make_enforcer(level=3, pnl=-16.0)
    ok, reason = pe.qualify_survival_trade("BTCUSDT", structure_cache=None)
    assert ok is False
    assert reason == "halted"


def test_phase2a_clamp_leverage_at_halted_drops_to_one() -> None:
    """At level 3 (HALTED), defense-in-depth clamp_leverage drops
    requested leverage to 1 regardless of input.
    """
    pe = _make_enforcer(level=3, pnl=-16.0)
    clamped, reason = pe.clamp_leverage(5)
    assert clamped == 1
    assert "HALTED_CLAMP" in reason
    assert "5->1" in reason


def test_phase2a_get_max_positions_zero_at_halted() -> None:
    """At HALTED, get_max_positions_override returns 0 so any caller
    that consults it sees "no new positions" — same gate as
    qualify_survival_trade returning (False, "halted")."""
    pe = _make_enforcer(level=3, pnl=-16.0)
    assert pe.get_max_positions_override() == 0
