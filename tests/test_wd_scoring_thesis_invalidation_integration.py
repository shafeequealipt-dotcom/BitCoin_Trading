"""Integration-flavored tests for the mid-hold thesis_invalidation
interaction with the Issue 1 brain-close scoring.

IMPLEMENT_FIVE_ISSUES_FIX.md (2026-05-20) requires that
``thesis_invalidation`` close reasons — which mid-hold's commit 547dc88
added to the strategic-action min-hold allowed-reasons list — STILL
route through the scoring intercept rather than bypass it. Scoring is
the safety net.

These tests document the design tradeoff that came out of writing
them: under default weights, the ``shallow_loser`` PnL bucket
contributes -3.0 (the scoring system penalises closing while losing),
which means a thesis_invalidation on a SHALLOW loss does NOT clear the
+6.0 threshold even with structural reasoning + broken XRAY +
imminent-deadline signals. To clear threshold the position needs to
also show strong-negative velocity, high SL consumption, or
aged-losing status (>30 min) to overcome the PnL drag.

This means: in enforce mode, some thesis_invalidations the brain
issues on shallow losses will be REJECTED and the position SL-tightened
30% toward breakeven instead. That is consistent with the scoring's
philosophy ("brain wanted out; math says hold and tighten") but the
operator should validate against log-only data before enabling enforce.

These are pure-function tests (no watchdog wiring), aligned with the
existing tests/test_wd_brain_scoring.py style. Watchdog-side wiring is
covered by the existing watchdog suites + the cherry-picked scoring
commits + the WD_SCORING_PATH_REACHED diagnostic added in C1.
"""

from __future__ import annotations

from src.risk.wd_brain_scoring import (
    DEFAULT_THRESHOLD,
    compute_brain_close_score,
)


def test_aged_losing_thesis_invalidation_with_full_signal_stack_clears_threshold() -> None:
    """Aged-losing position (>30 min held, in loss), brain cites
    structural invalidation, XRAY direction has flipped, SL nearly
    consumed, velocity strongly negative, deadline imminent. Every
    factor pushes toward execute. Composite should clear +6.0 and the
    recommendation is "execute" — both brain AND math agree this is
    a real invalidation worth honouring."""
    score = compute_brain_close_score(
        pnl_pct=-1.20,
        time_remaining_s=3 * 60,
        age_s=2400,
        velocity_pct_per_s=-0.005,
        sl_consumption_pct=85,
        xray_match="broken",
        reasoning_text=(
            "thesis invalidated: structure broke down through swing low, "
            "trendline lost, regime flipped — exit immediately"
        ),
    )
    assert score.composite >= DEFAULT_THRESHOLD, (
        f"Aged-losing thesis_invalidation with full signal stack should "
        f"clear threshold {DEFAULT_THRESHOLD}, got composite={score.composite:+.2f}"
    )
    assert score.recommendation == "execute"


def test_shallow_loser_thesis_invalidation_falls_below_threshold_under_defaults() -> None:
    """Shallow-loss thesis_invalidation: brain cites structural break
    and XRAY has flipped, but the position has barely budged from
    entry. Under default weights the shallow_loser PnL bucket (-3.0)
    plus young age (-1.0) plus comfortable SL (-1.0) outweigh the
    structural (+2.0) + XRAY-broken (+2.0) + imminent (+1.0)
    positives, so composite lands around 0 and the recommendation
    is reject (or reject_and_tighten if composite < 0).

    This is the design tradeoff documented at module top: the
    scoring system errs toward holding shallow losses, even when
    the brain claims invalidation. The operator should validate
    against log-only data before flipping enforce — they may want
    to retune the shallow_loser weight if too many genuine
    invalidations get rejected."""
    score = compute_brain_close_score(
        pnl_pct=-0.20,
        time_remaining_s=3 * 60,
        age_s=420,
        velocity_pct_per_s=-0.0012,
        sl_consumption_pct=55,
        xray_match="broken",
        reasoning_text=(
            "thesis invalidated: structure broke down through swing low"
        ),
    )
    assert score.composite < DEFAULT_THRESHOLD
    assert score.recommendation in ("reject", "reject_and_tighten")


def test_weak_thesis_invalidation_claim_strongly_rejected() -> None:
    """A weak thesis_invalidation: brain says "thesis invalidated" but
    XRAY still supports the position, PnL is shallow, plenty of time
    left, velocity is benign. Scoring strongly rejects — this looks
    more like a panic close mislabeled as invalidation than a real
    structural break. Composite well below threshold; recommendation
    is reject_and_tighten."""
    score = compute_brain_close_score(
        pnl_pct=-0.30,
        time_remaining_s=18 * 60,
        age_s=240,
        velocity_pct_per_s=0.0001,
        sl_consumption_pct=20,
        xray_match="supports",
        reasoning_text="thesis invalidated",
    )
    assert score.composite < DEFAULT_THRESHOLD
    assert score.recommendation == "reject_and_tighten"


def test_thesis_invalidation_on_young_position_still_scores_not_silently_bypassed() -> None:
    """A thesis_invalidation on a position younger than the 300s
    min-hold gate: mid-hold's commit 547dc88 lets the close past the
    min-hold gate via the allowed-reasons list. Our integration of
    the cherry-picked scoring means the scoring still runs (it does
    NOT honour the allowed-reasons bypass). This test asserts the
    score math yields a sensible answer for a young invalidation
    (not a NaN/zero/error short-circuit) — whichever direction the
    recommendation lands, it must be one of the three valid values.
    The infant age bucket (-2) is part of the calculation."""
    score = compute_brain_close_score(
        pnl_pct=-0.40,
        time_remaining_s=14 * 60,
        age_s=120,  # under min-hold; mid-hold lets this past
        velocity_pct_per_s=-0.0008,
        sl_consumption_pct=35,
        xray_match="broken",
        reasoning_text=(
            "thesis invalidated: setup broke down, structure invalidated"
        ),
    )
    assert score.recommendation in ("execute", "reject", "reject_and_tighten")
    assert -10.0 < score.composite < 10.0
    # The infant age bucket should be flagged.
    assert score.factors.age_bucket == "infant"
