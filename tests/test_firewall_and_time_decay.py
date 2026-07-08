"""Tests for the two coupled changes (spec-aligned):

  A. SENTINEL firewall source gate — ensures trusted sources bypass the
     _BLOCKED_ACTIONS guard while legacy callers retain pre-change behavior.
  B. Loser-lane Time-Decay SL — 5-model pipeline tests covering: grace
     period, tighter-only, direction symmetry, original_sl_pct cap,
     MIN_ALLOWED_LOSS floor, Model 3 recovery multiplier, Model 4 four-case
     momentum switch, Model 5 Bayesian p_win prior + updates + thresholds,
     force-close sentinel, observe() state update.

No IO, no mocks. The calculator is pure math; the firewall is a pure function.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ═════════════════════════════════════════════════════════════════════════
# A. Firewall source-gate tests
# ═════════════════════════════════════════════════════════════════════════


def test_firewall_default_blocks_close():
    """With no source, the legacy block for 'close' must still fire."""
    from src.sentinel.firewall import should_allow_strategic_action

    allowed, _ = should_allow_strategic_action("close", "TESTUSDT", "thesis")
    assert allowed is False


def test_firewall_default_blocks_take_profit():
    """Legacy 'take_profit' block preserved."""
    from src.sentinel.firewall import should_allow_strategic_action

    allowed, _ = should_allow_strategic_action("take_profit", "TESTUSDT", "r")
    assert allowed is False


def test_firewall_call_b_allows_close():
    """Call B (position review) is trusted — bypass the block."""
    from src.sentinel.firewall import should_allow_strategic_action

    allowed, explanation = should_allow_strategic_action(
        "close", "TESTUSDT", "thesis invalid", source="call_b",
    )
    assert allowed is True
    assert "call_b" in explanation


def test_firewall_call_a_urgent_allows_close():
    """Call A urgent position_actions (watchdog concerns) are trusted."""
    from src.sentinel.firewall import should_allow_strategic_action

    allowed, explanation = should_allow_strategic_action(
        "close", "TESTUSDT", "urgent", source="call_a_urgent",
    )
    assert allowed is True
    assert "call_a_urgent" in explanation


def test_firewall_unknown_source_still_blocks():
    """A made-up source does not get trust — keeps the block."""
    from src.sentinel.firewall import should_allow_strategic_action

    allowed, _ = should_allow_strategic_action(
        "close", "TESTUSDT", "r", source="evil_caller",
    )
    assert allowed is False


def test_firewall_non_blocked_action_always_allowed():
    """'tighten_stop' / 'set_exit' / 'hold' pass regardless of source."""
    from src.sentinel.firewall import should_allow_strategic_action

    for act in ("tighten_stop", "set_exit", "hold", "custom_nonblocked"):
        allowed, _ = should_allow_strategic_action(act, "TESTUSDT", "r")
        assert allowed is True, f"action {act} should pass with default source"


# ═════════════════════════════════════════════════════════════════════════
# B. Time-Decay calculator tests (spec-aligned)
# ═════════════════════════════════════════════════════════════════════════


def _make_state(**overrides):
    """Factory for isolated test state. Uses config defaults."""
    from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator

    calc = TimeDecaySLCalculator(TimeDecayConfig())
    defaults = dict(
        symbol="TEST",
        direction="Buy",
        entry_price=100.0,
        original_sl_pct=2.0,
        max_hold_seconds=2700,
        atr_5m_pct=0.5,
        regime_confidence=0.6,
        tick_seconds=5.0,
    )
    defaults.update(overrides)
    state = calc.create_state(**defaults)
    return calc, state


# --- Grace period & basic flow ---------------------------------------------


def test_grace_period_returns_none():
    """Within grace_seconds (120s), calculate returns None unconditionally."""
    calc, state = _make_state()
    result = calc.calculate(
        state, current_pnl_pct=-1.0, position_age_seconds=60,
        regime_still_supports=True, velocity_pct_per_s=0.0,
        acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert result is None


def test_past_grace_buy_tightens_below_entry():
    """Past grace AND past min-age guardrail (Phase 1, 2026-05-06):
    a Buy loser gets a new SL below entry. PnL chosen so MAE/SL ratio
    is 0.55 (above Phase 2's 0.50 threshold so the gate releases)."""
    calc, state = _make_state()
    result = calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=True, velocity_pct_per_s=0.0,
        acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert result is not None and result != -1.0
    assert 0 < result < state.entry_price


def test_past_grace_sell_tightens_above_entry():
    """Direction symmetry — Sell loser gets SL above entry. Age above
    Phase 1 min-age guardrail (300 s); MAE/SL ratio above Phase 2 (0.50)."""
    calc, state = _make_state(direction="Sell")
    result = calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=True, velocity_pct_per_s=0.0,
        acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert result is not None and result != -1.0
    assert result > state.entry_price


# --- Model 5 Bayesian p_win ------------------------------------------------


def test_p_win_prior_uses_regime_confidence():
    """Spec: prior = p_win_prior_base + regime_confidence * p_win_prior_regime_weight.

    Bug 3 fix raised p_win_prior_base from 0.40 to 0.55 so the starting
    p_win at regime_conf=1.0 is 0.80 (was 0.65). Using the config values
    keeps this test robust to future tuning.
    """
    from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator

    cfg = TimeDecayConfig()
    calc = TimeDecaySLCalculator(cfg)

    # 95% trending_up
    s95 = calc.create_state(
        symbol="A", direction="Buy", entry_price=100, original_sl_pct=2.0,
        max_hold_seconds=2700, atr_5m_pct=0.5, regime_confidence=0.95,
    )
    expected_s95 = cfg.p_win_prior_base + 0.95 * cfg.p_win_prior_regime_weight
    assert abs(s95.p_win - expected_s95) < 1e-9, (
        f"p_win={s95.p_win:.4f} expected={expected_s95:.4f}"
    )

    # 50% ranging
    s50 = calc.create_state(
        symbol="B", direction="Buy", entry_price=100, original_sl_pct=2.0,
        max_hold_seconds=2700, atr_5m_pct=0.5, regime_confidence=0.50,
    )
    expected_s50 = cfg.p_win_prior_base + 0.50 * cfg.p_win_prior_regime_weight
    assert abs(s50.p_win - expected_s50) < 1e-9, (
        f"p_win={s50.p_win:.4f} expected={expected_s50:.4f}"
    )


def test_p_win_clamped_to_bounds():
    """p_win must always stay within [p_win_min, p_win_max]."""
    calc, state = _make_state()
    # Drive aggressively in both directions; p_win must never escape.
    for pnl in (-10, -20, -30, -0.05, 0, -50):
        for rs in (True, False):
            calc._update_p_win(
                state, current_pnl_pct=pnl, regime_still_supports=rs,
            )
            assert calc.cfg.p_win_min <= state.p_win <= calc.cfg.p_win_max


def test_p_win_atr_penalty_requires_deepening():
    """1/2 ATR penalties only fire when price moves deeper this tick."""
    calc, state = _make_state(atr_5m_pct=0.5)
    state.p_win = 0.60
    state.prev_pnl_pct = -0.8  # previous tick pnl
    state.last_pnl_pct = -0.8  # current pnl same (no deepening)

    # Same pnl → no deepening → no ATR penalty, only regime bonus
    calc._update_p_win(
        state, current_pnl_pct=-0.8, regime_still_supports=True,
    )
    # Only regime bonus should apply: 0.60 * 1.05 = 0.63
    assert abs(state.p_win - 0.60 * calc.cfg.p_win_regime_bonus) < 1e-9


def test_p_win_atr1_penalty_fires_when_loss_deepens_past_1_atr():
    """Spec: 1 ATR deeper this tick → p_win *= 0.85."""
    calc, state = _make_state(atr_5m_pct=0.5)
    state.p_win = 0.70
    state.prev_pnl_pct = -0.5          # previous tick
    # |current_pnl| / atr = 0.8 / 0.5 = 1.6 (> 1 ATR, < 2 ATR)
    # current < prev → deeper this tick
    calc._update_p_win(
        state, current_pnl_pct=-0.8, regime_still_supports=True,
    )
    # Expected: 0.70 * 0.85 (atr1) * 1.05 (regime bonus)
    expected = 0.70 * calc.cfg.p_win_atr1_penalty * calc.cfg.p_win_regime_bonus
    assert abs(state.p_win - expected) < 1e-9, (
        f"p_win={state.p_win:.4f} expected={expected:.4f}"
    )


def test_p_win_atr2_penalty_fires_when_loss_deepens_past_2_atr():
    """Spec: 2 ATR deeper this tick → p_win *= 0.70."""
    calc, state = _make_state(atr_5m_pct=0.5)
    state.p_win = 0.70
    state.prev_pnl_pct = -0.5
    # |current_pnl| / atr = 1.5 / 0.5 = 3.0 (> 2 ATR)
    calc._update_p_win(
        state, current_pnl_pct=-1.5, regime_still_supports=True,
    )
    # Expected: 0.70 * 0.70 (atr2) * 1.05 (regime bonus)
    expected = 0.70 * calc.cfg.p_win_atr2_penalty * calc.cfg.p_win_regime_bonus
    assert abs(state.p_win - expected) < 1e-9


def test_observe_captures_prev_pnl_before_update():
    """observe() must snapshot the OLD last_pnl_pct into prev_pnl_pct before
    overwriting last_pnl_pct — otherwise the ATR penalty can never see that
    the loss deepened this tick."""
    from src.risk.time_decay_sl import observe

    _, state = _make_state(tick_seconds=5.0)
    state.last_pnl_pct = -0.5
    state.prev_pnl_pct = 0.0  # not yet initialized
    observe(state, -0.8)
    # prev_pnl_pct should hold the OLD last_pnl_pct (the previous tick's pnl)
    assert state.prev_pnl_pct == -0.5
    # last_pnl_pct should hold the new current pnl
    assert state.last_pnl_pct == -0.8
    # And _update_p_win must now see deepening correctly
    from src.risk.time_decay_sl import TimeDecaySLCalculator
    c = TimeDecaySLCalculator()
    state.p_win = 0.70
    state.atr_5m_pct = 0.5
    c._update_p_win(state, current_pnl_pct=-0.8, regime_still_supports=True)
    # |-0.8| / 0.5 = 1.6 > 1.0 ATR → atr1 penalty must fire
    # Expected: 0.70 * 0.85 (atr1) * 1.05 (regime) = 0.625
    expected = 0.70 * c.cfg.p_win_atr1_penalty * c.cfg.p_win_regime_bonus
    assert abs(state.p_win - expected) < 1e-9, (
        f"ATR penalty did not fire after observe()+update_p_win chain. "
        f"p_win={state.p_win:.4f} expected={expected:.4f}"
    )


def test_p_win_regime_reversed_penalty_fires():
    """When regime does not support, p_win *= p_win_regime_penalty (0.60)."""
    calc, state = _make_state()
    state.p_win = 0.80
    state.last_pnl_pct = -0.2  # no deepening, no atr penalty

    calc._update_p_win(
        state, current_pnl_pct=-0.2, regime_still_supports=False,
    )
    # 0.80 * 0.60 = 0.48
    assert abs(state.p_win - 0.80 * calc.cfg.p_win_regime_penalty) < 1e-9


def test_force_close_when_pwin_low():
    """Sustained bad signals drive p_win below p_win_force_close → -1.0 sentinel.

    Bug 3 fix lowered the force-close threshold from 0.25 to 0.15. We read
    the threshold from config and set p_win a hair below it so the test
    survives further tuning.
    """
    calc, state = _make_state()
    # Force the p_win below the configured threshold, then invoke calculate.
    state.p_win = max(calc.cfg.p_win_min, calc.cfg.p_win_force_close - 0.01)
    state.mae_pct = -1.0
    state.last_pnl_pct = -1.0

    out = calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=False,
        velocity_pct_per_s=-0.05,
        acceleration_pct_per_s2=-0.01,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out == -1.0


# --- Model 3 MAE recovery multiplier ---------------------------------------


def test_recovery_multiplier_bonus_above_threshold():
    """recovery > 0.5 → multiplier = 1.2 (Model 3 bonus)."""
    calc, state = _make_state()
    state.mae_pct = -2.14  # worst loss seen
    # current = -0.63 → recovery = (-0.63 - -2.14) / 2.14 = 0.706
    r = calc._recovery_multiplier(state, current_pnl_pct=-0.63)
    assert r == calc.cfg.mae_bonus  # 1.2


def test_recovery_multiplier_penalty_below_threshold():
    """recovery < 0.2 → multiplier = 0.8 (Model 3 penalty for stagnation)."""
    calc, state = _make_state()
    state.mae_pct = -2.0
    # current = -1.8 → recovery = (-1.8 - -2.0) / 2.0 = 0.10 (< 0.2)
    r = calc._recovery_multiplier(state, current_pnl_pct=-1.8)
    assert r == calc.cfg.mae_penalty  # 0.8


def test_recovery_multiplier_neutral_band():
    """0.2 ≤ recovery ≤ 0.5 → multiplier = 1.0."""
    calc, state = _make_state()
    state.mae_pct = -2.0
    # current = -1.3 → recovery = (-1.3 - -2.0) / 2.0 = 0.35
    r = calc._recovery_multiplier(state, current_pnl_pct=-1.3)
    assert r == 1.0


def test_recovery_multiplier_no_mae_yet():
    """When MAE is shallow (|mae| < 0.1), multiplier is neutral."""
    calc, state = _make_state()
    state.mae_pct = -0.05
    r = calc._recovery_multiplier(state, current_pnl_pct=-0.02)
    assert r == 1.0


# --- Model 4 four-case momentum switch -------------------------------------


def test_momentum_multiplier_four_cases():
    """Spec: 4-case switch on (velocity, accel) signs."""
    calc, _ = _make_state()
    c = calc.cfg
    assert calc._momentum_multiplier(+0.1, +0.01) == c.momentum_favorable   # 1.3
    assert calc._momentum_multiplier(+0.1, -0.01) == c.momentum_slow_rise   # 1.1
    assert calc._momentum_multiplier(-0.1, +0.01) == c.momentum_slow_fall   # 0.9
    assert calc._momentum_multiplier(-0.1, -0.01) == c.momentum_danger      # 0.7
    # Exact zero on either axis → neutral
    assert calc._momentum_multiplier(0.0, 0.0) == 1.0
    assert calc._momentum_multiplier(0.0, +0.01) == 1.0


# --- Phase 1: Minimum-age guardrail (Time-Decay Force-Close Definitive Fix) -
#
# The min-age guardrail at time_decay_sl.calculate() is symmetric: when
# `position_age_seconds < cfg.min_age_seconds`, BOTH the force-close
# sentinel AND the tighter-SL push are suppressed (return None). This
# plugs the bypass of the strategic-action minimum-hold guardrail —
# time-decay closes call position_service.close_position() directly at
# position_watchdog:977 and never pass through _execute_strategic_actions.


def test_age_guard_blocks_force_close_under_min_age():
    """Phase 1: position younger than min_age (300 s default) cannot be
    force-closed even when p_win is below the force-close threshold."""
    calc, state = _make_state()
    state.p_win = max(calc.cfg.p_win_min, calc.cfg.p_win_force_close - 0.01)
    state.mae_pct = -1.0
    state.last_pnl_pct = -1.0
    # age=200 < min_age=300 → guardrail must block
    out = calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=200,
        regime_still_supports=False,
        velocity_pct_per_s=-0.05, acceleration_pct_per_s2=-0.01,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out is None, (
        f"Phase 1 guardrail must block force-close at age 200 s; got {out}"
    )


def test_age_guard_passes_force_close_above_min_age():
    """Phase 1: at min_age boundary the guardrail releases."""
    calc, state = _make_state()
    state.p_win = max(calc.cfg.p_win_min, calc.cfg.p_win_force_close - 0.01)
    state.mae_pct = -1.0
    state.last_pnl_pct = -1.0
    # age=400 >= min_age=300 → guardrail allows the existing force-close test
    out = calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=False,
        velocity_pct_per_s=-0.05, acceleration_pct_per_s2=-0.01,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out == -1.0, (
        f"Phase 1 guardrail must release at age 400 s; got {out}"
    )


def test_age_guard_blocks_sl_tighten_under_min_age():
    """Phase 1: symmetric — even SL-tighten is suppressed below min_age."""
    calc, state = _make_state()
    # age=200 < min_age=300, p_win healthy; without the guardrail this
    # would compute a tighter SL float. With the guardrail it returns None.
    out = calc.calculate(
        state, current_pnl_pct=-0.5, position_age_seconds=200,
        regime_still_supports=True,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out is None, (
        f"Phase 1 guardrail must suppress SL-tighten at age 200 s; got {out}"
    )


def test_age_guard_respects_configured_override():
    """Phase 1: a longer min_age_seconds suppresses force-close at higher ages."""
    from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator
    cfg = TimeDecayConfig(min_age_seconds=600.0)
    calc = TimeDecaySLCalculator(cfg)
    state = calc.create_state(
        symbol="AGE", direction="Buy", entry_price=100.0,
        original_sl_pct=2.0, max_hold_seconds=2700,
        atr_5m_pct=0.5, regime_confidence=0.6,
    )
    state.p_win = max(cfg.p_win_min, cfg.p_win_force_close - 0.01)
    state.mae_pct = -1.0
    state.last_pnl_pct = -1.0
    # age=400 above default 300 but below configured 600 → blocked
    out = calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=False,
        velocity_pct_per_s=-0.05, acceleration_pct_per_s2=-0.01,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out is None, (
        f"Phase 1 guardrail with min_age=600 must block at age 400 s; got {out}"
    )


# --- T1-1: AGE_GUARD MAE-init bypass fix (2026-05-12) ----------------------
#
# Pre-fix the calculator returned from the AGE_GUARD block at line 349
# BEFORE the MAE tracking code (then at lines 351-353) executed.
# Worst-PnL excursions during the 0-300 s immunity window were silently
# discarded; the first MAE_GUARD evaluation initialised mae_pct from
# CURRENT pnl, not from the dead-zone peak. The fix moves MAE
# measurement above the AGE_GUARD early-return so MAE is tracked on
# every tick that passes the grace window.


def test_t1_1_mae_tracked_during_age_guard_window():
    """T1-1: MAE must be recorded during the 120-300 s AGE_GUARD window
    (after grace=120 s for class=medium, before min_age=300 s) so Phase
    2's MAE/SL gate sees the true worst-excursion floor when AGE_GUARD
    releases. Bug-replication: position dives to -2.5 % at 200 s past
    grace; state.mae_pct must be -2.5 %, not 0.0."""
    calc, state = _make_state(original_sl_pct=2.0)
    out = calc.calculate(
        state, current_pnl_pct=-2.5, position_age_seconds=200,
        regime_still_supports=True,
        velocity_pct_per_s=-0.05, acceleration_pct_per_s2=-0.01,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out is None, "AGE_GUARD must still block at age 200 s"
    assert state.mae_pct == -2.5, (
        f"T1-1: MAE must be tracked during AGE_GUARD window; got {state.mae_pct}"
    )


def test_t1_1_mae_not_overwritten_to_shallower_after_age_guard():
    """T1-1 regression: under the OLD ordering the first post-guard tick
    at -1.0 % would set state.mae_pct=-1.0 % (because the assignment
    block fired with state.mae_pct still 0.0 from the dataclass default).
    The fix preserves the deeper guard-window MAE through the release."""
    calc, state = _make_state(original_sl_pct=2.0)
    # Two ticks inside AGE_GUARD, both at -1.5 %
    calc.calculate(
        state, current_pnl_pct=-1.5, position_age_seconds=50,
        regime_still_supports=True, velocity_pct_per_s=0.0,
        acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    calc.calculate(
        state, current_pnl_pct=-1.5, position_age_seconds=200,
        regime_still_supports=True, velocity_pct_per_s=0.0,
        acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert state.mae_pct == -1.5
    # First post-guard tick at SHALLOWER pnl
    calc.calculate(
        state, current_pnl_pct=-0.8, position_age_seconds=400,
        regime_still_supports=True, velocity_pct_per_s=+0.05,
        acceleration_pct_per_s2=+0.01,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert state.mae_pct == -1.5, (
        "T1-1: MAE must remain at the deeper guard-window value"
    )


def test_t1_1_mae_unchanged_when_grace_active():
    """T1-1: ordering invariant — grace -> MAE -> AGE_GUARD. While grace
    is active, the calculator returns BEFORE the moved MAE block, so
    state.mae_pct stays at default 0.0 during the grace window. This is
    intentional: grace is a per-class "wait for first signal" window;
    MAE measurement starts after it."""
    calc, state = _make_state()  # default grace_seconds_by_class[medium]=120
    out = calc.calculate(
        state, current_pnl_pct=-3.0, position_age_seconds=60,
        regime_still_supports=True, velocity_pct_per_s=0.0,
        acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out is None
    assert state.mae_pct == 0.0, "MAE tracking begins AFTER grace, not during"


def test_t1_1_existing_mae_guard_threshold_unchanged():
    """T1-1 hard constraint: do not change Phase 2 MAE_GUARD threshold
    behaviour. With original_sl_pct=2.0 and mae_pct=-1.5 (ratio 0.75),
    the gate must still release post-AGE_GUARD."""
    calc, state = _make_state(original_sl_pct=2.0)
    state.p_win = max(calc.cfg.p_win_min, calc.cfg.p_win_force_close - 0.01)
    state.mae_pct = -1.5
    state.last_pnl_pct = -1.5
    out = calc.calculate(
        state, current_pnl_pct=-1.5, position_age_seconds=600,
        regime_still_supports=False, velocity_pct_per_s=-0.01,
        acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out == -1.0, (
        f"T1-1 must not change Phase 2 release behaviour for ratio 0.75; got {out}"
    )


# --- Phase 2: MAE-relative-to-SL gate (Time-Decay Force-Close Definitive Fix)
#
# Force-close suppressed when |state.mae_pct| / state.original_sl_pct is
# below `mae_to_sl_ratio_threshold` (default 0.5). All Phase 2 tests use
# `position_age_seconds >= 300` to bypass Phase 1's min-age guardrail
# and isolate the MAE gate.


def test_mae_guard_blocks_force_close_below_ratio():
    """Phase 2: MAE/SL ratio 0.20 (well below 0.50 threshold) blocks force-close."""
    calc, state = _make_state(original_sl_pct=2.0)
    state.p_win = max(calc.cfg.p_win_min, calc.cfg.p_win_force_close - 0.01)
    state.mae_pct = -0.4   # ratio = 0.4 / 2.0 = 0.20
    state.last_pnl_pct = -0.4
    out = calc.calculate(
        state, current_pnl_pct=-0.4, position_age_seconds=600,
        regime_still_supports=False,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out is None, (
        f"Phase 2 guardrail must block at MAE/SL ratio 0.20; got {out}"
    )


def test_mae_guard_blocks_just_below_ratio():
    """Phase 2: ratio 0.495 (just below 0.50) still blocks."""
    calc, state = _make_state(original_sl_pct=2.0)
    state.p_win = max(calc.cfg.p_win_min, calc.cfg.p_win_force_close - 0.01)
    state.mae_pct = -0.99   # ratio ≈ 0.495
    state.last_pnl_pct = -0.99
    out = calc.calculate(
        state, current_pnl_pct=-0.99, position_age_seconds=600,
        regime_still_supports=False,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out is None, (
        f"Phase 2 guardrail must block at MAE/SL ratio 0.495; got {out}"
    )


def test_mae_guard_passes_force_close_above_ratio():
    """Phase 2: ratio 0.75 (above 0.50) lets force-close fire."""
    calc, state = _make_state(original_sl_pct=2.0)
    state.p_win = max(calc.cfg.p_win_min, calc.cfg.p_win_force_close - 0.01)
    state.mae_pct = -1.5   # ratio = 0.75
    state.last_pnl_pct = -1.5
    out = calc.calculate(
        state, current_pnl_pct=-1.5, position_age_seconds=600,
        regime_still_supports=False,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out == -1.0, (
        f"Phase 2 guardrail must release at MAE/SL ratio 0.75; got {out}"
    )


def test_mae_guard_uses_configured_threshold():
    """Phase 2: a tighter mae_to_sl_ratio_threshold (0.8) still blocks at 0.5."""
    from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator
    cfg = TimeDecayConfig(mae_to_sl_ratio_threshold=0.8)
    calc = TimeDecaySLCalculator(cfg)
    state = calc.create_state(
        symbol="MAE", direction="Buy", entry_price=100.0,
        original_sl_pct=2.0, max_hold_seconds=2700,
        atr_5m_pct=0.5, regime_confidence=0.6,
    )
    state.p_win = max(cfg.p_win_min, cfg.p_win_force_close - 0.01)
    state.mae_pct = -1.0   # ratio 0.50, below configured 0.80
    state.last_pnl_pct = -1.0
    out = calc.calculate(
        state, current_pnl_pct=-1.0, position_age_seconds=600,
        regime_still_supports=False,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out is None, (
        f"Phase 2 with threshold 0.80 must block at MAE/SL ratio 0.50; got {out}"
    )


def test_mae_guard_blocks_sl_tighten_below_ratio():
    """Phase 2: symmetric — even SL-tighten suppressed when MAE/SL ratio is low."""
    calc, state = _make_state(original_sl_pct=2.0)
    # p_win healthy, age above min, but MAE shallow → SL-tighten would
    # otherwise produce a tighter float; with the gate it returns None.
    state.mae_pct = -0.3   # ratio 0.15
    state.last_pnl_pct = -0.3
    out = calc.calculate(
        state, current_pnl_pct=-0.3, position_age_seconds=600,
        regime_still_supports=True,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out is None, (
        f"Phase 2 guardrail must suppress SL-tighten at MAE/SL ratio 0.15; got {out}"
    )


# --- Combined formula invariants -------------------------------------------


def test_allowed_capped_at_original_sl_pct():
    """Spec cap: allowed_loss never exceeds original_sl_pct."""
    # Pick conditions where all multipliers would push allowed > original.
    calc, state = _make_state(
        original_sl_pct=1.0, atr_5m_pct=1.0,  # atr_room = 2.0
    )
    # Past Phase 1 min-age (300s) and Phase 2 MAE/SL gate (ratio >= 0.5
    # of original_sl_pct=1.0 → |pnl| >= 0.5).
    result = calc.calculate(
        state, current_pnl_pct=-0.6, position_age_seconds=400,
        regime_still_supports=True,
        velocity_pct_per_s=+0.05, acceleration_pct_per_s2=+0.01,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert result is not None and result != -1.0
    # state.last_allowed_loss is the capped value
    assert state.last_allowed_loss <= state.original_sl_pct + 1e-9


def test_min_allowed_loss_floor():
    """Spec floor: allowed_loss clamped from below to min_allowed_loss_pct."""
    calc, state = _make_state(
        original_sl_pct=0.20, atr_5m_pct=0.05,
    )
    # Very late age + all-bad multipliers → allowed wants to go below 0.15%.
    result = calc.calculate(
        state, current_pnl_pct=-0.2, position_age_seconds=2600,
        regime_still_supports=False,
        velocity_pct_per_s=-0.20, acceleration_pct_per_s2=-0.10,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    if result is not None and result != -1.0:
        assert state.last_allowed_loss >= calc.cfg.min_allowed_loss_pct - 1e-9


def test_tighter_only_invariant():
    """Once a budget is set, a subsequent larger budget must be rejected.
    Initial PnL chosen so MAE/SL ratio (1.1/2.0 = 0.55) clears Phase 2."""
    calc, state = _make_state()
    first = calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=2000,
        regime_still_supports=False,
        velocity_pct_per_s=-0.02, acceleration_pct_per_s2=-0.01,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert first is not None and first != -1.0
    last_allowed_1 = state.last_allowed_loss

    # Next tick with conditions that would produce a LARGER allowed:
    # younger age (impossible), but we can force it by feeding healing momentum.
    # MAE is sticky at -1.1 from the first tick (state.mae_pct does not
    # widen back to a smaller |value|), so Phase 2's ratio remains 0.55.
    result = calc.calculate(
        state, current_pnl_pct=-0.1, position_age_seconds=2005,
        regime_still_supports=True,
        velocity_pct_per_s=+0.10, acceleration_pct_per_s2=+0.05,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    # If allowed wants to grow, calculate must return None (no-op).
    # If allowed legitimately shrinks (very late age), the new value still
    # must be strictly less than the prior.
    if result is not None and result != -1.0:
        assert state.last_allowed_loss <= last_allowed_1 + 1e-9


# --- observe() derivative tracker ------------------------------------------


def test_observe_updates_state():
    """observe() mutates last_pnl_pct and prev_velocity; returns derivatives."""
    from src.risk.time_decay_sl import observe

    _, state = _make_state(tick_seconds=5.0)
    state.last_pnl_pct = 0.0
    state.prev_velocity = 0.0

    v1, a1 = observe(state, -0.5)
    assert state.last_pnl_pct == -0.5
    # velocity = (-0.5 - 0.0) / 5.0 = -0.1
    assert abs(v1 - (-0.1)) < 1e-9
    # acceleration = v1 - prev_velocity(0) = -0.1
    assert abs(a1 - (-0.1)) < 1e-9
    assert abs(state.prev_velocity - (-0.1)) < 1e-9

    # Steady velocity → acceleration = 0
    v2, a2 = observe(state, -1.0)
    assert abs(v2 - (-0.1)) < 1e-9
    assert abs(a2 - 0.0) < 1e-9


# --- MAE tracking ----------------------------------------------------------


def test_force_close_does_not_re_init_during_cooldown():
    """After a Time-Decay force-close, the symbol enters coordinator cooldown.
    The watchdog can still call _handle_time_decay on the same symbol (race
    between close and exchange position-sync). Without a cooldown guard, the
    next tick would lazy-init fresh state with p_win=prior, then immediately
    crash it again → duplicate close attempts and noisy logs. This test
    verifies the cooldown guard short-circuits _handle_time_decay entirely."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from dataclasses import dataclass
    from src.config.settings import Settings
    from src.core.trade_plan import TradePlan
    from src.core.trade_coordinator import TradeCoordinator
    from src.core.types import Position, Side
    from src.workers.position_watchdog import PositionWatchdog

    async def run():
        settings = Settings._load_fresh()
        pos_svc = MagicMock()
        pos_svc.close_position = AsyncMock(return_value=True)
        pos_svc.set_stop_loss = AsyncMock(return_value=True)

        @dataclass
        class FakeProfile:
            atr_pct_5m: float = 1.5
        vp = MagicMock()
        vp.get_profile = AsyncMock(return_value=FakeProfile())

        class FakeEnum:
            def __init__(self, v): self.value = v
        @dataclass
        class FakeRegime:
            regime: object
            confidence: float
        rd = MagicMock()
        rd.get_coin_regime = MagicMock(
            return_value=FakeRegime(FakeEnum("trending_down"), 0.5),
        )

        coord = TradeCoordinator()
        wd = PositionWatchdog(
            settings=settings, db=MagicMock(),
            position_service=pos_svc, market_service=MagicMock(),
            volatility_profiler=vp, regime_detector=rd,
            trade_coordinator=coord,
        )

        plan = TradePlan(
            symbol="COOLUSDT", direction="Buy", entry_price=1.0,
            target_price=1.05, stop_loss_price=0.96,
            max_hold_minutes=30,
        )
        # Both are needed: register_trade populates _trades (so on_trade_closed
        # doesn't hit the double-close guard), register_trade_plan stores the
        # plan for _handle_time_decay to read.
        coord.register_trade(
            symbol="COOLUSDT", strategy_category="default",
            strategy_name="test", entry_price=1.0, side="Buy",
        )
        coord.register_trade_plan("COOLUSDT", plan)
        import time as _t
        plan.opened_at = _t.time() - 300  # past grace

        # Simulate that the symbol was just closed by Time-Decay (sets cooldown)
        # Issue 3 (2026-05-18) — direction-aware cooldown: the close
        # registers (COOLUSDT, Buy) into the 300s window.
        coord.on_trade_closed(
            symbol="COOLUSDT", pnl_pct=-2.0, pnl_usd=-20.0,
            was_win=False, closed_by="time_decay_p_win_low",
        )
        blocked, _ = coord.is_reentry_blocked("COOLUSDT", "Buy")
        assert blocked

        # Now simulate the watchdog calling _handle_time_decay for the same
        # symbol on the next tick (position still visible due to exchange lag)
        pos = Position(
            symbol="COOLUSDT", side=Side.BUY, size=100, entry_price=1.0,
            mark_price=0.97, unrealized_pnl=-3.0,
            stop_loss=0.96, take_profit=1.05,
        )
        pre_close = pos_svc.close_position.call_count
        closed = await wd._handle_time_decay(pos, plan, pnl_pct=-3.0, current_price=0.97)

        # Guard must have fired: no close attempt, no state created
        assert closed is False, "Cooldown guard should return False"
        assert pos_svc.close_position.call_count == pre_close, \
            "close_position must NOT be called during cooldown"
        assert "COOLUSDT" not in wd._td_states, \
            "No state should be created during cooldown"

    asyncio.run(run())


def test_mae_tracked_as_negative_and_only_deepens():
    """MAE is stored as the worst (most negative) pnl seen — monotonic."""
    calc, state = _make_state()
    # Go to -0.8, then bounce to -0.3, then plunge to -1.4.
    # MAE should end at -1.4.
    # Past Phase 1 min-age guardrail (300 s default) so the calculator
    # actually runs and updates state.mae_pct on each tick.
    calc.calculate(
        state, current_pnl_pct=-0.8, position_age_seconds=350,
        regime_still_supports=True,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert state.mae_pct == -0.8
    calc.calculate(
        state, current_pnl_pct=-0.3, position_age_seconds=355,
        regime_still_supports=True,
        velocity_pct_per_s=+0.05, acceleration_pct_per_s2=+0.01,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert state.mae_pct == -0.8  # unchanged (not deeper)
    calc.calculate(
        state, current_pnl_pct=-1.4, position_age_seconds=360,
        regime_still_supports=False,
        velocity_pct_per_s=-0.10, acceleration_pct_per_s2=-0.02,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert state.mae_pct == -1.4


# --- T1-2: MAE non-monotonic regression fix (2026-05-12) -------------------
#
# Production root cause: positions in the loser lane briefly turn
# profitable, fire TIME_DECAY_HANDOFF (state destroyed), then swing back
# into negative territory and re-INIT a fresh state at mae_pct=0.0.
# 173 HANDOFF events in past 6 h, 159 followed by re-INIT, 56 lost MAE
# history (top: INJUSDT lost -0.68 % across the round-trip).
#
# Fix: (a) all mae_pct writes route through _assign_mae_monotonic to
# enforce strict-min and emit TIME_DECAY_MAE_MONOTONIC_HOLD on any
# regression attempt; (b) create_state accepts prior_mae_pct kwarg so a
# recreated state can inherit the high-water mark; (c) the watchdog's
# _td_mae_high_water dict snapshots MAE before each _td_states deletion
# and clears only on confirmed close.


def test_t1_2_assign_mae_monotonic_holds_regression():
    """T1-2 defense-in-depth: a less-negative candidate must NOT regress
    MAE. The helper holds the prior value and emits HOLD log."""
    calc, state = _make_state()
    state.mae_pct = -2.0
    deepened = calc._assign_mae_monotonic(state, candidate=-0.5, source="test")
    assert deepened is False
    assert state.mae_pct == -2.0, "MAE must be held at the deeper value"


def test_t1_2_assign_mae_monotonic_accepts_deepening():
    """T1-2: a more-negative candidate must be accepted as the new MAE."""
    calc, state = _make_state()
    state.mae_pct = -1.0
    deepened = calc._assign_mae_monotonic(state, candidate=-2.5, source="test")
    assert deepened is True
    assert state.mae_pct == -2.5


def test_t1_2_assign_mae_monotonic_equal_is_noop():
    """T1-2: equal candidate is a no-op (returns False, no log)."""
    calc, state = _make_state()
    state.mae_pct = -1.0
    deepened = calc._assign_mae_monotonic(state, candidate=-1.0, source="test")
    assert deepened is False
    assert state.mae_pct == -1.0


def test_t1_2_create_state_preserves_prior_mae():
    """T1-2: create_state(prior_mae_pct=-1.85) seeds new state at -1.85
    via the monotonic helper."""
    from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator
    calc = TimeDecaySLCalculator(TimeDecayConfig())
    state = calc.create_state(
        symbol="ARB", direction="Buy", entry_price=1.0,
        original_sl_pct=2.0, max_hold_seconds=2700,
        atr_5m_pct=0.5, regime_confidence=0.6,
        prior_mae_pct=-1.85,
    )
    assert state.mae_pct == -1.85


def test_t1_2_create_state_ignores_zero_prior_mae():
    """T1-2: prior_mae_pct=0.0 (the default for first-creation with no
    history) bypasses the seeding branch and leaves state default."""
    from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator
    calc = TimeDecaySLCalculator(TimeDecayConfig())
    state = calc.create_state(
        symbol="NEW", direction="Buy", entry_price=1.0,
        original_sl_pct=2.0, max_hold_seconds=2700,
        atr_5m_pct=0.5, regime_confidence=0.6,
        prior_mae_pct=0.0,
    )
    assert state.mae_pct == 0.0


def test_t1_2_mae_preserved_across_recreation_round_trip():
    """T1-2 end-to-end: simulate the production HANDOFF→re-INIT trigger.
    Build deep MAE in state-A; snapshot it; recreate as state-B with
    prior_mae_pct=snapshot; assert MAE preserved; then a shallower live
    tick must NOT regress."""
    from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator
    calc = TimeDecaySLCalculator(TimeDecayConfig())
    # State A: build MAE to -2.0 % via calculate() ticks past grace
    state_a = calc.create_state(
        symbol="INJ", direction="Buy", entry_price=10.0,
        original_sl_pct=2.0, max_hold_seconds=2700,
        atr_5m_pct=0.5, regime_confidence=0.6,
    )
    calc.calculate(
        state_a, current_pnl_pct=-2.0, position_age_seconds=400,
        regime_still_supports=False,
        velocity_pct_per_s=-0.05, acceleration_pct_per_s2=-0.01,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert state_a.mae_pct == -2.0
    snapshot = state_a.mae_pct
    # State B: simulate re-INIT after profit-handoff
    state_b = calc.create_state(
        symbol=state_a.symbol, direction=state_a.direction,
        entry_price=state_a.entry_price,
        original_sl_pct=state_a.original_sl_pct,
        max_hold_seconds=state_a.max_hold_seconds,
        atr_5m_pct=state_a.atr_5m_pct,
        regime_confidence=state_a.regime_confidence,
        prior_mae_pct=snapshot,
    )
    assert state_b.mae_pct == -2.0
    # First post-recreation tick at SHALLOWER pnl must not regress
    calc.calculate(
        state_b, current_pnl_pct=-0.3, position_age_seconds=405,
        regime_still_supports=True,
        velocity_pct_per_s=+0.05, acceleration_pct_per_s2=+0.01,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert state_b.mae_pct == -2.0, (
        "T1-2: MAE must not regress when current pnl is shallower than preserved high-water mark"
    )


def test_t1_2_existing_mae_guard_threshold_still_works():
    """T1-2 hard constraint: Phase 2 MAE_GUARD threshold logic
    unchanged. A state with mae_pct=-0.4 (ratio 0.20 with sl=2.0)
    must still block; -1.5 (ratio 0.75) must still release."""
    calc, state = _make_state(original_sl_pct=2.0)
    state.p_win = max(calc.cfg.p_win_min, calc.cfg.p_win_force_close - 0.01)
    state.mae_pct = -0.4
    state.last_pnl_pct = -0.4
    out = calc.calculate(
        state, current_pnl_pct=-0.4, position_age_seconds=600,
        regime_still_supports=False, velocity_pct_per_s=-0.01,
        acceleration_pct_per_s2=0.0,
        structural_invalidation=True, invalidation_reason="test_bypass",
    )
    assert out is None, (
        f"T1-2: Phase 2 MAE_GUARD must still block at ratio 0.20; got {out}"
    )


# ═════════════════════════════════════════════════════════════════════════
# Runner (so the file works without pytest)
# ═════════════════════════════════════════════════════════════════════════


def _run_all():
    tests = [
        fn for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = []
    for fn in tests:
        try:
            fn()
            print(f"  [PASS] {fn.__name__}")
        except AssertionError as e:
            failed.append((fn.__name__, str(e)))
            print(f"  [FAIL] {fn.__name__}: {e}")
        except Exception as e:
            failed.append((fn.__name__, f"{type(e).__name__}: {e}"))
            print(f"  [ERROR] {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    if failed:
        print("\nFailures:")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
