"""Surgical tests for the Issue 1 (2026-05-18) brain-close scoring module.

Covers:
- The two historical worked examples from
  ``IMPLEMENT_THREE_ISSUES_FIX.md`` Issue 1 §B (BSBUSDT, HYPEUSDT) —
  both should land on "reject_and_tighten" under default weights.
- A high-conviction execute scenario.
- A regression-sanity winning close that intentionally falls below
  threshold under defaults (the system errs on the side of holding;
  operator can tune PnL weights to lower the bar).
- Edge cases: NaN PnL, missing velocity, missing SL consumption,
  empty / vague / structural reasoning, stale / unavailable XRAY.
- Operator-supplied weight overrides merge with defaults.

Pure-function tests — no I/O, no monkey-patching, no fixtures.
"""

from __future__ import annotations

import math

from src.risk.wd_brain_scoring import (
    DEFAULT_THRESHOLD,
    DEFAULT_WEIGHTS,
    compute_brain_close_score,
    compute_sl_consumption_pct,
)


def test_bsbusdt_historical_should_reject_and_tighten() -> None:
    """Operator worked example: PnL -1.25%, age 498s, SL ~50% consumed,
    XRAY still supports the position, brain cites structural reasoning.
    Expected: composite around 0 with reject_and_tighten outcome (the
    -3 PnL bucket dominates and pushes the result negative)."""
    score = compute_brain_close_score(
        pnl_pct=-1.25,
        time_remaining_s=4 * 60,
        age_s=498,
        velocity_pct_per_s=None,
        sl_consumption_pct=50,
        xray_match="supports",
        reasoning_text="structural setup invalidation per breakdown",
    )
    assert score.recommendation in ("reject", "reject_and_tighten")
    assert score.composite < DEFAULT_THRESHOLD


def test_hypeusdt_historical_should_strongly_reject() -> None:
    """Operator worked example: PnL -0.20%, age 1899s, plenty of SL
    runway, vague brain reasoning. Expected: strongly negative
    composite, recommendation reject_and_tighten."""
    score = compute_brain_close_score(
        pnl_pct=-0.20,
        time_remaining_s=15 * 60,
        age_s=1899,
        velocity_pct_per_s=None,
        sl_consumption_pct=10,
        xray_match="neutral",
        reasoning_text="loss accelerating",
    )
    assert score.recommendation == "reject_and_tighten"
    assert score.composite < 0


def test_high_conviction_winning_close_executes() -> None:
    """Winning close at +1.5% with broken XRAY structure (true
    invalidation) + structural reasoning + tight deadline + low SL
    consumption -> composite above threshold -> execute."""
    score = compute_brain_close_score(
        pnl_pct=1.5,
        time_remaining_s=2 * 60,
        age_s=900,
        velocity_pct_per_s=-0.008,
        sl_consumption_pct=10,
        xray_match="broken",
        reasoning_text="structure broken; trend reversal at resistance",
    )
    assert score.recommendation == "execute"
    assert score.composite >= DEFAULT_THRESHOLD


def test_winning_close_with_vague_reasoning_below_threshold() -> None:
    """+1.2% PnL + vague reasoning + neutral XRAY -> composite below
    threshold under defaults. Intentional regression-safety: the
    system keeps holding low-conviction winners; operator tunes via
    weights if more eager firing is desired."""
    score = compute_brain_close_score(
        pnl_pct=1.2,
        time_remaining_s=3 * 60,
        age_s=900,
        velocity_pct_per_s=0.001,
        sl_consumption_pct=10,
        xray_match="neutral",
        reasoning_text="locking the gain",
    )
    assert score.recommendation == "reject"
    assert 0 <= score.composite < DEFAULT_THRESHOLD


def test_nan_inputs_fail_soft_with_notes() -> None:
    """All-NaN / missing-input edge case -> conservative reject_and_tighten
    with a populated notes list flagging the substitutions."""
    score = compute_brain_close_score(
        pnl_pct=float("nan"),
        time_remaining_s=float("nan"),
        age_s=-10,
        velocity_pct_per_s=None,
        sl_consumption_pct=None,
        xray_match="",
        reasoning_text="",
    )
    assert isinstance(score.composite, float) and not math.isnan(score.composite)
    assert score.recommendation in ("reject", "reject_and_tighten")
    notes = set(score.notes)
    assert "pnl_nan_replaced_with_0" in notes
    assert "time_remaining_nan_replaced_with_0" in notes
    assert "age_invalid_replaced_with_0" in notes
    assert "velocity_unavailable_used_0" in notes
    assert "sl_unavailable_used_50pct_midpoint" in notes


def test_stale_xray_contributes_zero() -> None:
    """A stale XRAY verdict should drop to neutral (zero contribution)
    rather than fall back to the cached direction."""
    score = compute_brain_close_score(
        pnl_pct=-0.6,
        time_remaining_s=10 * 60,
        age_s=600,
        velocity_pct_per_s=0.0,
        sl_consumption_pct=40,
        xray_match="stale",
        reasoning_text="",
    )
    assert score.factors.xray_bucket == "stale"
    assert score.factors.xray_factor == 0.0


def test_structural_reasoning_keywords_bump_factor() -> None:
    """Reasoning containing one of the structural keywords (e.g.
    'invalidate') lands in the structural bucket (+2.0)."""
    vague = compute_brain_close_score(
        pnl_pct=0.5, time_remaining_s=300, age_s=600,
        velocity_pct_per_s=None, sl_consumption_pct=40,
        xray_match="neutral", reasoning_text="time to close",
    )
    structural = compute_brain_close_score(
        pnl_pct=0.5, time_remaining_s=300, age_s=600,
        velocity_pct_per_s=None, sl_consumption_pct=40,
        xray_match="neutral",
        reasoning_text="structure invalidated; setup broken",
    )
    assert vague.factors.reasoning_bucket == "vague"
    assert structural.factors.reasoning_bucket == "structural"
    assert structural.composite > vague.composite


def test_operator_weight_overrides_merge_with_defaults() -> None:
    """A partial override (just the PnL strong_winner bucket) merges
    on top of DEFAULT_WEIGHTS. Other factors keep their defaults."""
    override = {
        "pnl": {"strong_winner": 5.0},  # raise from default 3.0
    }
    score = compute_brain_close_score(
        pnl_pct=1.5,
        time_remaining_s=2 * 60,
        age_s=900,
        velocity_pct_per_s=-0.008,
        sl_consumption_pct=10,
        xray_match="broken",
        reasoning_text="structure broken",
        weights=override,
    )
    # PnL factor is the overridden value
    assert score.factors.pnl_factor == 5.0
    # Other factors still come from defaults
    assert score.factors.xray_factor == DEFAULT_WEIGHTS["xray"]["broken"]


def test_log_dict_is_flat_and_loggable() -> None:
    """as_log_dict() must return scalars suitable for a single-line
    structured log emission."""
    score = compute_brain_close_score(
        pnl_pct=-0.5,
        time_remaining_s=300,
        age_s=600,
        velocity_pct_per_s=0.0,
        sl_consumption_pct=40,
        xray_match="neutral",
        reasoning_text="",
    )
    d = score.as_log_dict()
    for key, value in d.items():
        assert isinstance(key, str)
        assert isinstance(value, (int, float, str)), (
            f"{key}={value!r} is not a scalar"
        )


# ────────────────────────────────────────────────────────────────────
# C1 Phase 1.4b — shared SL-consumption helper tests.
#
# Confirms the helper is direction-aware, clamps to [0, 100], returns
# None on malformed inputs, and produces the same number for the brain
# prompt's "entry SL" call as for the watchdog scorer's "current SL"
# call when the same SL value is passed. The C1 diagnostic relies on
# this equivalence — once both call sites use the helper, any non-zero
# delta in WD_SL_PCT_DIVERGENCE must come from the SL value (trailing)
# and not from formula drift.
# ────────────────────────────────────────────────────────────────────


def test_helper_long_at_entry() -> None:
    """Long position, price exactly at entry: 0% consumed."""
    pct = compute_sl_consumption_pct(
        side="Buy", entry_price=100.0, stop_loss=95.0, current_price=100.0,
    )
    assert pct == 0.0


def test_helper_long_at_stop() -> None:
    """Long position, price exactly at the stop: 100% consumed."""
    pct = compute_sl_consumption_pct(
        side="Long", entry_price=100.0, stop_loss=95.0, current_price=95.0,
    )
    assert pct == 100.0


def test_helper_long_halfway() -> None:
    """Long position, price halfway between entry and stop: 50% consumed."""
    pct = compute_sl_consumption_pct(
        side="Buy", entry_price=100.0, stop_loss=90.0, current_price=95.0,
    )
    assert pct == 50.0


def test_helper_long_in_profit() -> None:
    """Long position, price above entry (in profit): 0% consumed."""
    pct = compute_sl_consumption_pct(
        side="Buy", entry_price=100.0, stop_loss=95.0, current_price=102.0,
    )
    assert pct == 0.0


def test_helper_long_overshoots_stop() -> None:
    """Long position, price below the stop (wick past): clamped to 100%."""
    pct = compute_sl_consumption_pct(
        side="Buy", entry_price=100.0, stop_loss=95.0, current_price=92.0,
    )
    assert pct == 100.0


def test_helper_short_at_entry() -> None:
    """Short position, price exactly at entry: 0% consumed."""
    pct = compute_sl_consumption_pct(
        side="Sell", entry_price=100.0, stop_loss=105.0, current_price=100.0,
    )
    assert pct == 0.0


def test_helper_short_at_stop() -> None:
    """Short position, price exactly at the stop: 100% consumed."""
    pct = compute_sl_consumption_pct(
        side="Short", entry_price=100.0, stop_loss=105.0, current_price=105.0,
    )
    assert pct == 100.0


def test_helper_short_halfway() -> None:
    """Short position, price halfway between entry and stop: 50% consumed."""
    pct = compute_sl_consumption_pct(
        side="Sell", entry_price=100.0, stop_loss=110.0, current_price=105.0,
    )
    assert pct == 50.0


def test_helper_short_in_profit() -> None:
    """Short position, price below entry (in profit): 0% consumed."""
    pct = compute_sl_consumption_pct(
        side="Sell", entry_price=100.0, stop_loss=105.0, current_price=98.0,
    )
    assert pct == 0.0


def test_helper_short_overshoots_stop() -> None:
    """Short position, price above the stop (wick past): clamped to 100%."""
    pct = compute_sl_consumption_pct(
        side="Sell", entry_price=100.0, stop_loss=105.0, current_price=108.0,
    )
    assert pct == 100.0


def test_helper_returns_none_on_zero_entry() -> None:
    assert compute_sl_consumption_pct(
        side="Buy", entry_price=0.0, stop_loss=95.0, current_price=100.0,
    ) is None


def test_helper_returns_none_on_zero_sl() -> None:
    assert compute_sl_consumption_pct(
        side="Buy", entry_price=100.0, stop_loss=0.0, current_price=100.0,
    ) is None


def test_helper_returns_none_on_zero_current_price() -> None:
    assert compute_sl_consumption_pct(
        side="Buy", entry_price=100.0, stop_loss=95.0, current_price=0.0,
    ) is None


def test_helper_returns_none_when_sl_equals_entry() -> None:
    """SL at break-even is degenerate — no risk envelope to measure against."""
    assert compute_sl_consumption_pct(
        side="Buy", entry_price=100.0, stop_loss=100.0, current_price=99.0,
    ) is None


def test_helper_returns_none_on_misordered_long() -> None:
    """Long with SL above entry is malformed; helper rejects."""
    assert compute_sl_consumption_pct(
        side="Buy", entry_price=100.0, stop_loss=110.0, current_price=99.0,
    ) is None


def test_helper_returns_none_on_misordered_short() -> None:
    """Short with SL below entry is malformed; helper rejects."""
    assert compute_sl_consumption_pct(
        side="Sell", entry_price=100.0, stop_loss=90.0, current_price=101.0,
    ) is None


def test_helper_brain_vs_scorer_byte_identical_when_sl_matches() -> None:
    """The key alignment guarantee: when the brain prompt and the
    watchdog scorer pass the same SL value, the helper returns the
    same number. Any non-zero delta in WD_SL_PCT_DIVERGENCE must come
    from the SL value (trailing) and not formula drift."""
    # Long: SL has been trailed from 90 to 92; price at 95.
    same_sl = 92.0
    pct_brain = compute_sl_consumption_pct(
        side="Buy", entry_price=100.0, stop_loss=same_sl, current_price=95.0,
    )
    pct_scorer = compute_sl_consumption_pct(
        side="Buy", entry_price=100.0, stop_loss=same_sl, current_price=95.0,
    )
    assert pct_brain == pct_scorer
    assert pct_brain == 62.5  # (100-95) / (100-92) * 100 = 62.5%


def test_helper_brain_vs_scorer_differ_when_sl_differs() -> None:
    """When brain reads thesis entry SL and scorer reads current
    trailed SL, the percentages differ. This is the divergence the
    diagnostic surfaces."""
    sl_entry = 90.0
    sl_current = 95.0  # trailed 50% toward entry
    price = 97.0
    pct_brain = compute_sl_consumption_pct(
        side="Buy", entry_price=100.0, stop_loss=sl_entry, current_price=price,
    )
    pct_scorer = compute_sl_consumption_pct(
        side="Buy", entry_price=100.0, stop_loss=sl_current, current_price=price,
    )
    # Brain: (100-97)/(100-90)*100 = 30%
    # Scorer: (100-97)/(100-95)*100 = 60%
    assert pct_brain == 30.0
    assert pct_scorer == 60.0
    assert pct_scorer - pct_brain == 30.0


# ────────────────────────────────────────────────────────────────────
# P0-3 fix (2026-05-22) — brain_vote_factor tests.
# Validates that explicit brain votes contribute a bounded positive
# weight to the composite, gated on reasoning quality. Automated
# close paths (brain_vote_present=False) preserve pre-fix composite.
# ────────────────────────────────────────────────────────────────────


def test_brain_vote_absent_preserves_pre_fix_composite() -> None:
    """When brain_vote_present is False (automated close path), the
    composite must equal the pre-fix value (no contribution from
    brain_vote_factor)."""
    s = compute_brain_close_score(
        pnl_pct=-1.5, time_remaining_s=900, age_s=900,
        velocity_pct_per_s=-0.01, sl_consumption_pct=70.0,
        xray_match="broken", reasoning_text="structural breakdown",
        brain_vote_present=False,
    )
    assert s.factors.brain_vote_bucket == "absent"
    assert s.factors.brain_vote_factor == 0.0


def test_brain_vote_structural_adds_2_0() -> None:
    """Explicit brain vote with structural reasoning adds +2.0."""
    s = compute_brain_close_score(
        pnl_pct=-1.5, time_remaining_s=900, age_s=900,
        velocity_pct_per_s=-0.01, sl_consumption_pct=70.0,
        xray_match="broken", reasoning_text="structural breakdown",
        brain_vote_present=True,
    )
    assert s.factors.brain_vote_bucket == "structural"
    assert s.factors.brain_vote_factor == 2.0


def test_brain_vote_vague_adds_1_0() -> None:
    """Explicit brain vote with vague (non-keyword) reasoning adds +1.0."""
    s = compute_brain_close_score(
        pnl_pct=-1.5, time_remaining_s=900, age_s=900,
        velocity_pct_per_s=-0.01, sl_consumption_pct=70.0,
        xray_match="broken", reasoning_text="this is going badly",
        brain_vote_present=True,
    )
    assert s.factors.brain_vote_bucket == "vague"
    assert s.factors.brain_vote_factor == 1.0


def test_brain_vote_empty_adds_0_5() -> None:
    """Explicit brain vote with empty reasoning still adds +0.5."""
    s = compute_brain_close_score(
        pnl_pct=-1.5, time_remaining_s=900, age_s=900,
        velocity_pct_per_s=-0.01, sl_consumption_pct=70.0,
        xray_match="broken", reasoning_text="",
        brain_vote_present=True,
    )
    assert s.factors.brain_vote_bucket == "empty"
    assert s.factors.brain_vote_factor == 0.5


def test_icp_16_50_regression_executes_with_brain_vote() -> None:
    """The 2026-05-22 ICP 16:50:40 case: deep_loser, strong_negative
    velocity, broken XRAY, structural reasoning, 74.6% SL. Pre-fix
    composite was 4.5 (reject). Post-fix with brain_vote_present=True
    should be 6.5 (execute)."""
    s = compute_brain_close_score(
        pnl_pct=-1.8615,
        time_remaining_s=1368.0,
        age_s=1332.0,
        velocity_pct_per_s=-0.014892,
        sl_consumption_pct=74.6,
        xray_match="broken",
        reasoning_text="URGENT structural invalidation at this level",
        brain_vote_present=True,
    )
    assert s.composite == 6.5
    assert s.recommendation == "execute"
    assert s.factors.brain_vote_factor == 2.0


def test_c1_regression_vague_panic_on_sound_still_rejects() -> None:
    """The C1-target scenario: vague-reasoning panic-close on a
    structurally-supportive position. brain_vote_factor adds +1.0
    but the structural negatives keep the composite below threshold,
    preserving C1 anti-churn."""
    s = compute_brain_close_score(
        pnl_pct=-0.3,
        time_remaining_s=1500.0,
        age_s=900.0,
        velocity_pct_per_s=-0.003,
        sl_consumption_pct=35.0,
        xray_match="supports",
        reasoning_text="this looks bad, closing",
        brain_vote_present=True,
    )
    # pnl=-3 + time=-2 + age=0 + vel=+1 + sl=-1 + xray=-2 + reasoning=+0.5
    # + brain_vote=+1 = -5.5 → reject_and_tighten
    assert s.composite == -5.5
    assert s.recommendation == "reject_and_tighten"
