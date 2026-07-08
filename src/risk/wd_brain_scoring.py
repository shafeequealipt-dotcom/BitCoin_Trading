"""Multi-factor scoring for brain-driven close-vote arbitration.

Issue 1 of ``IMPLEMENT_THREE_ISSUES_FIX.md`` (2026-05-18). The brain's
discretionary close path (``wd_claude_action``) has a near-zero
historical win rate (56 closes, -$463.41 cumulative, 4 wins) because
the brain cannot see the same per-position state the watchdog does.
This module turns brain close decisions into VOTES that combine with
watchdog-side signals (PnL, age, time-to-deadline, velocity, SL
geometry, XRAY structural verdict, brain reasoning quality) into a
composite score. The watchdog uses the score to decide whether to
fire the close, hold, or hold + tighten SL toward break-even.

The scoring function is pure — no I/O, no side effects, no datetime
calls — so it can be unit-tested deterministically. The caller
provides the factor inputs; the watchdog wiring lives in
``src/workers/position_watchdog.py``.

Phase 1 (log-only) runs the scoring on every brain close vote but
the brain's close still fires; the operator validates the predictions
against real post-close PnL trajectories before flipping
``wd_brain_scoring_enforce`` to True (Phase 2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Mapping

# ────────────────────────────────────────────────────────────────────
# Default factor weights from IMPLEMENT_THREE_ISSUES_FIX.md Issue 1 §B.
# Each factor maps observation-bucket strings to a signed contribution.
# Positive contributions push toward "execute the close"; negative
# contributions push toward "hold the position".
# ────────────────────────────────────────────────────────────────────
DEFAULT_THRESHOLD: float = 6.0

DEFAULT_WEIGHTS: Mapping[str, Mapping[str, float]] = {
    "pnl": {
        "strong_winner": 3.0,        # PnL > +1.0%
        "mild_winner": 1.5,          # +0.3% < PnL <= +1.0%
        "weak_winner": 0.5,          # 0% < PnL <= +0.3%
        "shallow_loser": -3.0,       # -0.5% <= PnL <= 0%
        "moderate_loser": -1.0,      # -1.5% <= PnL < -0.5%
        "deep_loser": 0.5,           # PnL < -1.5%  (let SL handle)
    },
    "time_remaining": {
        "deep": -2.0,                # >20 min remaining
        "moderate": -1.0,            # 10-20 min
        "shallow": 0.0,              # 5-10 min
        "imminent": 1.0,             # <5 min
    },
    "age": {
        "infant": -2.0,              # <3 min
        "young": -1.0,               # 3-10 min
        "mature": 0.0,               # 10-30 min
        "aged_losing": 1.0,          # >30 min AND PnL < 0
    },
    "velocity": {
        "strong_positive": -2.0,     # moving toward TP fast
        "mild_positive": -1.0,
        "stationary": 0.0,
        "mild_negative": 1.0,
        "strong_negative": 2.0,      # accelerating into SL
    },
    "sl_consumption": {
        "spacious": -2.0,            # 0-30% consumed
        "comfortable": -1.0,         # 30-60%
        "tight": 0.0,                # 60-80%
        "imminent": 1.0,             # >80%
    },
    "xray": {
        "supports": -2.0,            # XRAY direction matches position
        "neutral": 0.0,              # neutral / unavailable / stale
        "stale": 0.0,
        "unavailable": 0.0,
        "broken": 2.0,               # XRAY direction opposes position
    },
    "reasoning": {
        "structural": 2.0,           # cited structural evidence
        "vague": 0.5,                # non-empty without keywords
        "empty": 0.0,
    },
    # P0-3 fix (2026-05-22) — brain_vote factor. When the brain
    # explicitly votes close (as distinct from the close path firing
    # for automated reasons), add a bounded positive contribution
    # gated on reasoning quality. The factor combines with the existing
    # reasoning factor: a brain-with-structural-reasoning close gets
    # +2.0 (brain_vote) + +2.0 (reasoning) on the closing side,
    # bridging the typical loser-state gap to the 6.0 threshold while
    # still letting the structural factors (xray, velocity, sl) gate
    # whether the composite passes. A brain-silent automated close
    # leaves this factor at 0 so the C1 anti-churn semantics for
    # silent paths are preserved verbatim.
    "brain_vote": {
        "structural": 2.0,           # brain voted + cited structural evidence
        "vague": 1.0,                # brain voted + non-empty without keywords
        "empty": 0.5,                # brain voted + empty reasoning
        "absent": 0.0,               # path fired automatically (no brain vote)
    },
}

STRUCTURAL_KEYWORDS: frozenset[str] = frozenset({
    "structure",
    "invalidate",
    "invalidation",
    "invalidated",
    "broken",
    "breakdown",
    "breakout",
    "setup",
    "regime",
    "reversal",
    "fvg",
    "ob",
    "order block",
    "support",
    "resistance",
    "trendline",
    "trend reversal",
})


# ────────────────────────────────────────────────────────────────────
# Output dataclasses
# ────────────────────────────────────────────────────────────────────

Recommendation = Literal["execute", "reject", "reject_and_tighten"]


@dataclass
class BrainCloseScoreFactors:
    """Per-factor breakdown so logs and tests can inspect each input."""
    pnl_pct: float
    pnl_bucket: str
    pnl_factor: float

    time_remaining_s: float
    time_bucket: str
    time_factor: float

    age_s: float
    age_bucket: str
    age_factor: float

    velocity_pct_per_s: float
    velocity_bucket: str
    velocity_factor: float

    sl_consumption_pct: float
    sl_bucket: str
    sl_factor: float

    xray_bucket: str
    xray_factor: float

    reasoning_bucket: str
    reasoning_factor: float

    # P0-3 fix (2026-05-22) — explicit-brain-vote authority weight.
    # `brain_vote_bucket` mirrors reasoning_bucket when the call site
    # passed brain_vote_present=True; "absent" otherwise. The factor
    # is the looked-up weight (0.0 in the absent case).
    brain_vote_bucket: str = "absent"
    brain_vote_factor: float = 0.0


@dataclass
class BrainCloseScore:
    """Final score with recommendation and a structured factor breakdown."""
    factors: BrainCloseScoreFactors
    composite: float
    threshold: float
    recommendation: Recommendation
    notes: list[str] = field(default_factory=list)

    def as_log_dict(self) -> dict[str, float | str]:
        """Flatten to scalar fields suitable for a single-line log emission."""
        f = self.factors
        return {
            "composite": round(self.composite, 2),
            "threshold": self.threshold,
            "recommendation": self.recommendation,
            "pnl_pct": round(f.pnl_pct, 4),
            "pnl_bucket": f.pnl_bucket,
            "pnl_factor": f.pnl_factor,
            "time_remaining_s": round(f.time_remaining_s, 0),
            "time_bucket": f.time_bucket,
            "time_factor": f.time_factor,
            "age_s": round(f.age_s, 0),
            "age_bucket": f.age_bucket,
            "age_factor": f.age_factor,
            "velocity": round(f.velocity_pct_per_s, 6),
            "velocity_bucket": f.velocity_bucket,
            "velocity_factor": f.velocity_factor,
            "sl_pct": round(f.sl_consumption_pct, 1),
            "sl_bucket": f.sl_bucket,
            "sl_factor": f.sl_factor,
            "xray_bucket": f.xray_bucket,
            "xray_factor": f.xray_factor,
            "reasoning_bucket": f.reasoning_bucket,
            "reasoning_factor": f.reasoning_factor,
            "brain_vote_bucket": f.brain_vote_bucket,
            "brain_vote_factor": f.brain_vote_factor,
        }


# ────────────────────────────────────────────────────────────────────
# Bucket classifiers (pure)
# ────────────────────────────────────────────────────────────────────

def _classify_pnl(pnl_pct: float) -> str:
    if math.isnan(pnl_pct):
        return "weak_winner"  # neutral-ish; should not happen post-NaN-guard
    if pnl_pct > 1.0:
        return "strong_winner"
    if pnl_pct > 0.3:
        return "mild_winner"
    if pnl_pct > 0.0:
        return "weak_winner"
    if pnl_pct >= -0.5:
        return "shallow_loser"
    if pnl_pct >= -1.5:
        return "moderate_loser"
    return "deep_loser"


def _classify_time_remaining(seconds: float) -> str:
    s = max(0.0, seconds)
    if s > 20 * 60:
        return "deep"
    if s >= 10 * 60:
        return "moderate"
    if s >= 5 * 60:
        return "shallow"
    return "imminent"


def _classify_age(age_s: float, pnl_pct: float) -> str:
    if age_s < 3 * 60:
        return "infant"
    if age_s < 10 * 60:
        return "young"
    if age_s < 30 * 60:
        return "mature"
    if pnl_pct < 0:
        return "aged_losing"
    return "mature"


def _classify_velocity(v: float) -> str:
    if math.isnan(v):
        return "stationary"
    # Velocity expressed as pnl_pct per second (so 0.01 = 0.01% / s = 0.6% / min).
    if v >= 0.01:
        return "strong_positive"
    if v >= 0.002:
        return "mild_positive"
    if v <= -0.01:
        return "strong_negative"
    if v <= -0.002:
        return "mild_negative"
    return "stationary"


def _classify_sl(sl_consumption_pct: float) -> str:
    if math.isnan(sl_consumption_pct):
        return "tight"  # midpoint; conservative
    if sl_consumption_pct < 30:
        return "spacious"
    if sl_consumption_pct < 60:
        return "comfortable"
    if sl_consumption_pct < 80:
        return "tight"
    return "imminent"


def _classify_xray(xray_match: str) -> str:
    normalized = (xray_match or "").strip().lower()
    if normalized in ("supports", "neutral", "stale", "unavailable", "broken"):
        return normalized
    return "neutral"


def _classify_reasoning(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return "empty"
    lower = stripped.lower()
    if any(keyword in lower for keyword in STRUCTURAL_KEYWORDS):
        return "structural"
    return "vague"


# ────────────────────────────────────────────────────────────────────
# Composite calculation
# ────────────────────────────────────────────────────────────────────

def _weight(
    weights: Mapping[str, Mapping[str, float]],
    factor: str,
    bucket: str,
    fallback: float = 0.0,
) -> float:
    """Look up a weight with safe fallback when the operator override
    omits a bucket. ``weights`` may merge partial overrides on top of
    DEFAULT_WEIGHTS."""
    layer = weights.get(factor) or DEFAULT_WEIGHTS.get(factor) or {}
    if bucket in layer:
        return float(layer[bucket])
    return float(DEFAULT_WEIGHTS.get(factor, {}).get(bucket, fallback))


def compute_brain_close_score(
    *,
    pnl_pct: float,
    time_remaining_s: float,
    age_s: float,
    velocity_pct_per_s: float | None,
    sl_consumption_pct: float | None,
    xray_match: str,
    reasoning_text: str,
    threshold: float = DEFAULT_THRESHOLD,
    weights: Mapping[str, Mapping[str, float]] | None = None,
    brain_vote_present: bool = False,
) -> BrainCloseScore:
    """Compute the composite close-vote score for a brain-driven close.

    Args:
        pnl_pct: Position PnL in percent (signed; winners > 0, losers < 0).
        time_remaining_s: Seconds remaining until the strategic plan
            deadline expires. Clamp negatives to 0 internally.
        age_s: Seconds since position open.
        velocity_pct_per_s: Recent PnL velocity expressed in pnl_pct per
            second. Pass ``None`` when unknown (the function substitutes 0).
        sl_consumption_pct: Percent of the SL distance consumed (0 = at
            entry, 100 = SL hit). Pass ``None`` when price feed is
            unavailable (the function substitutes 50 → midpoint, neutral).
        xray_match: One of ``"supports"`` / ``"neutral"`` / ``"broken"`` /
            ``"stale"`` / ``"unavailable"``. The watchdog computes this
            by comparing XRAY ``trade_direction`` to the position's side
            with staleness guard.
        reasoning_text: Free-form brain reasoning string from
            ``PositionAction.reason``.
        threshold: Minimum composite for the close to fire under enforce
            mode. Default ``DEFAULT_THRESHOLD`` (6.0).
        weights: Optional override of the per-factor weight table.
            Partial overrides merge on top of ``DEFAULT_WEIGHTS``; any
            missing factor or bucket falls back to the default value.
        brain_vote_present: P0-3 fix (2026-05-22). When True, the
            caller asserts that an explicit brain close vote drove
            this scoring call (as opposed to an automated close path).
            Adds a bounded positive contribution to the composite
            gated on reasoning quality:
            ``brain_vote['structural'|'vague'|'empty'] = +2.0|+1.0|+0.5``.
            When False (default), the brain_vote factor is the
            ``"absent"`` bucket weight (0.0) — preserving the pre-P0-3
            composite verbatim for callers that have not been
            re-wired. The watchdog scoring intercept always passes
            True since that path is reached only via an explicit
            brain vote.

    Returns:
        ``BrainCloseScore`` with the per-factor breakdown, composite,
        and recommendation (``"execute"`` / ``"reject"`` /
        ``"reject_and_tighten"``). The per-factor breakdown carries
        ``brain_vote_bucket`` and ``brain_vote_factor`` so logs can
        render the bucket name and weight that contributed.
    """
    effective_weights: Mapping[str, Mapping[str, float]] = (
        weights if weights is not None else DEFAULT_WEIGHTS
    )

    notes: list[str] = []

    # Sanitize inputs — fail-soft so a glitchy upstream feed cannot raise.
    if math.isnan(pnl_pct):
        notes.append("pnl_nan_replaced_with_0")
        pnl_pct = 0.0
    if math.isnan(time_remaining_s):
        notes.append("time_remaining_nan_replaced_with_0")
        time_remaining_s = 0.0
    if time_remaining_s < 0:
        time_remaining_s = 0.0
    if math.isnan(age_s) or age_s < 0:
        notes.append("age_invalid_replaced_with_0")
        age_s = 0.0
    if velocity_pct_per_s is None or math.isnan(velocity_pct_per_s):
        velocity_pct_per_s = 0.0
        notes.append("velocity_unavailable_used_0")
    if sl_consumption_pct is None or math.isnan(sl_consumption_pct):
        sl_consumption_pct = 50.0
        notes.append("sl_unavailable_used_50pct_midpoint")

    pnl_bucket = _classify_pnl(pnl_pct)
    time_bucket = _classify_time_remaining(time_remaining_s)
    age_bucket = _classify_age(age_s, pnl_pct)
    velocity_bucket = _classify_velocity(velocity_pct_per_s)
    sl_bucket = _classify_sl(sl_consumption_pct)
    xray_bucket = _classify_xray(xray_match)
    reasoning_bucket = _classify_reasoning(reasoning_text)

    pnl_factor = _weight(effective_weights, "pnl", pnl_bucket)
    time_factor = _weight(effective_weights, "time_remaining", time_bucket)
    age_factor = _weight(effective_weights, "age", age_bucket)
    velocity_factor = _weight(effective_weights, "velocity", velocity_bucket)
    sl_factor = _weight(effective_weights, "sl_consumption", sl_bucket)
    xray_factor = _weight(effective_weights, "xray", xray_bucket)
    reasoning_factor = _weight(effective_weights, "reasoning", reasoning_bucket)

    # P0-3 fix (2026-05-22) — brain_vote authority factor. Mirrors
    # reasoning bucket when the call site passed brain_vote_present=True;
    # "absent" otherwise. Absent → 0.0 by the DEFAULT_WEIGHTS entry, so
    # automated close paths (no explicit brain vote) score exactly as
    # the pre-fix composite. Explicit brain vote contributes a bounded
    # positive contribution gated on reasoning quality.
    brain_vote_bucket = reasoning_bucket if brain_vote_present else "absent"
    brain_vote_factor = _weight(
        effective_weights, "brain_vote", brain_vote_bucket,
    )

    composite = (
        pnl_factor
        + time_factor
        + age_factor
        + velocity_factor
        + sl_factor
        + xray_factor
        + reasoning_factor
        + brain_vote_factor
    )

    if composite >= threshold:
        recommendation: Recommendation = "execute"
    elif composite >= 0:
        recommendation = "reject"
    else:
        recommendation = "reject_and_tighten"

    factors = BrainCloseScoreFactors(
        pnl_pct=pnl_pct,
        pnl_bucket=pnl_bucket,
        pnl_factor=pnl_factor,
        time_remaining_s=time_remaining_s,
        time_bucket=time_bucket,
        time_factor=time_factor,
        age_s=age_s,
        age_bucket=age_bucket,
        age_factor=age_factor,
        velocity_pct_per_s=velocity_pct_per_s,
        velocity_bucket=velocity_bucket,
        velocity_factor=velocity_factor,
        sl_consumption_pct=sl_consumption_pct,
        sl_bucket=sl_bucket,
        sl_factor=sl_factor,
        xray_bucket=xray_bucket,
        xray_factor=xray_factor,
        reasoning_bucket=reasoning_bucket,
        reasoning_factor=reasoning_factor,
        brain_vote_bucket=brain_vote_bucket,
        brain_vote_factor=brain_vote_factor,
    )

    return BrainCloseScore(
        factors=factors,
        composite=composite,
        threshold=threshold,
        recommendation=recommendation,
        notes=notes,
    )


# ────────────────────────────────────────────────────────────────────
# Shared SL-consumption helper — C1 Phase 1.4b (2026-05-21).
#
# Before this helper landed, the brain CALL_B prompt and the watchdog
# scoring intercept each computed "SL % consumed" with their own inline
# formula. The formulas were mathematically equivalent, but they read
# different stop-loss values: the brain prompt used
# ``thesis_data["stop_loss_price"]`` (the entry-time SL stored in the
# thesis snapshot), while the watchdog scorer used ``pos.stop_loss``
# (the current, possibly-trailed SL). When SL had been trailed the two
# numbers diverged, leaving the brain reading "X% consumed" while the
# scorer fed "Y% consumed" into the composite. This helper unifies the
# arithmetic so both call sites compute the same number when given the
# same SL value; the *choice of which SL to pass* is the only remaining
# divergence axis and is now an explicit caller decision.
# ────────────────────────────────────────────────────────────────────


_BUY_SIDES: frozenset[str] = frozenset({"buy", "long"})


def compute_sl_consumption_pct(
    *,
    side: str,
    entry_price: float,
    stop_loss: float,
    current_price: float,
) -> float | None:
    """Return the percent of SL distance consumed by the current price.

    Returns a float in ``[0.0, 100.0]`` or ``None`` when the inputs are
    not well-formed (any non-positive price, ``sl == entry``, or
    unrecognised side). The percent is clamped to ``[0, 100]`` so the
    brain prompt and the watchdog scorer cannot disagree on whether
    price "overshot" the stop — both treat a wick past the stop as
    100% consumed for display and bucketing purposes.

    Args:
        side: ``"Buy"`` / ``"Long"`` / ``"buy"`` / ``"long"`` for long
            positions; anything else is treated as a short.
        entry_price: Position entry price.
        stop_loss: Stop-loss price to measure against. The caller chooses
            which SL is the canonical reference for its question —
            entry-time SL for "what fraction of my original risk budget
            have I consumed?" or current trailed SL for "how close is
            price to my current stop?". This function does not impose
            a choice.
        current_price: The price to evaluate against (typically the
            position's mark price).

    Returns:
        Percent of SL distance consumed in ``[0.0, 100.0]``, or
        ``None`` when inputs are not well-formed.
    """
    try:
        e = float(entry_price)
        sl = float(stop_loss)
        p = float(current_price)
    except (TypeError, ValueError):
        return None
    if e <= 0 or sl <= 0 or p <= 0:
        return None
    if e == sl:
        return None
    side_lc = (side or "").strip().lower()
    is_long = side_lc in _BUY_SIDES
    if is_long:
        total_risk = e - sl
        moved = max(0.0, e - p)
    else:
        total_risk = sl - e
        moved = max(0.0, p - e)
    if total_risk <= 0:
        # Misordered SL relative to side (e.g. SL above entry on a long).
        # Treat as malformed rather than returning a negative or >100
        # percent that the scorer would mis-bucket.
        return None
    moved = min(moved, total_risk)
    return min(moved / total_risk * 100.0, 100.0)


__all__ = [
    "BrainCloseScore",
    "BrainCloseScoreFactors",
    "DEFAULT_THRESHOLD",
    "DEFAULT_WEIGHTS",
    "STRUCTURAL_KEYWORDS",
    "compute_brain_close_score",
    "compute_sl_consumption_pct",
]
