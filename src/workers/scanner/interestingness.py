"""Phase 4 of the 1D briefing rewrite — interestingness ranker.

Continuous score in [0, 1] that turns a coin's full per-cycle state into
a single number the briefing-mode scanner sorts by. Replaces the
all-or-nothing exclusion gate with a graded preference: every coin gets
a score; the brain receives the top-N briefings.

Design properties (from the architecture document):

    1. **No hard cuts.** Every coin gets a score, however low.
    2. **State-cleanness reward.** A coin in one unambiguous regime
       with a clear structural picture beats a mixed one.
    3. **Confluence reward.** When regime, structure, signal, funding,
       and MTF bias all point the same way, score climbs.
    4. **Extremity reward.** Extreme conditions (extreme funding, F&G,
       range position) earn a bonus — those are the cleanest edges.
    5. **Label strength.** A coin with a high-base-weight label
       (TREND_PULLBACK, LIQUIDITY_SWEEP) ranks above one with only
       a low-weight label (NO_TRADEABLE_STATE).
    6. **Calibrated.** Default weights produce a bell-shaped distribution
       across a 50-coin watch list with the top-15 cut comfortably
       seating ≥12 actionable coins per cycle in current market
       conditions.
    7. **Pure & deterministic.** No IO, no randomness; same inputs
       always produce the same score. Never raises.

Formula:

    I(coin) = w_S * cleanness(state)
            + w_C * confluence(state)
            + w_X * extremity(state)
            + w_L * label_strength(labels)
            + w_R * structural_quality(state)
            + w_M * mtf_alignment(state)
            + w_O * open_position_floor(state)

with default weights summing to 1.0. The breakdown is returned alongside
the score so the per-coin ``BRIEFING_INTERESTINGNESS`` log line and
the brain prompt can render the component contributions.

Phase 5 plumbs richer state (per-coin regime confidence, ADX, choppiness,
volume_ratio, position_in_range, MTF bias) — components fall back to
safe defaults when those inputs aren't yet available.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.workers.scanner.state_labeler import (
    LABEL_BASE_WEIGHTS,
    LABEL_NO_TRADEABLE_STATE,
)


@dataclass(frozen=True)
class InterestingnessWeights:
    """Component weights for the interestingness formula. Must sum to 1.0."""

    cleanness: float = 0.20
    confluence: float = 0.20
    extremity: float = 0.15
    label_strength: float = 0.20
    structural_quality: float = 0.15
    mtf_alignment: float = 0.07
    open_position_floor: float = 0.03

    @property
    def total(self) -> float:
        return (
            self.cleanness + self.confluence + self.extremity
            + self.label_strength + self.structural_quality
            + self.mtf_alignment + self.open_position_floor
        )


@dataclass(frozen=True)
class InterestingnessResult:
    """Output of :func:`compute_interestingness`.

    Attributes:
        score: 0..1 — final weighted-sum interestingness.
        breakdown: per-component contribution dict for logs and prompt.
            Keys: ``"cleanness"``, ``"confluence"``, ``"extremity"``,
            ``"label_strength"``, ``"structural_quality"``,
            ``"mtf_alignment"``, ``"open_position_floor"``. Each value
            is the **already-weighted** contribution (component × weight),
            so summing them recovers ``score``.
        state_cleanness: Raw cleanness component value (0..1, unweighted).
            Surfaced separately for the CoinPackage field of the same
            name so callers can read it without unpacking ``breakdown``.
        confluence_count: Integer count of directional anchors that
            aligned (out of up-to-6 anchors). Surfaced for transparency.
    """

    score: float
    breakdown: dict[str, float] = field(default_factory=dict)
    state_cleanness: float = 0.0
    confluence_count: int = 0


# ── Component computations ───────────────────────────────────────────


def _safe_clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp x to [lo, hi]; coerce non-finite to lo."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return lo
    if not math.isfinite(v):
        return lo
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _is_trending_up(regime: str) -> bool:
    return "trending_up" in regime or "trend_up" in regime


def _is_trending_down(regime: str) -> bool:
    return "trending_down" in regime or "trend_down" in regime


def _is_ranging(regime: str) -> bool:
    return "rang" in regime


def _cleanness(
    *,
    regime: str,
    regime_confidence: float,
    setup_type: str,
    setup_type_confidence: float,
    trade_direction: str,
    consensus_direction: str,
    adx: float | None,
    choppiness: float | None,
) -> float:
    """How unambiguously the coin sits in one regime + structural bias.

    Combines:
        * regime_confidence (0..1)
        * setup_type_confidence (0..1, zero when setup_type=="none")
        * direction-agreement penalty (1.0 - direction_conflict)
        * a small ADX/choppiness sanity bonus
    """
    rc = _safe_clamp(regime_confidence)
    sc = 0.0 if (setup_type or "none") == "none" else _safe_clamp(setup_type_confidence)
    # Direction conflict: trade_direction vs consensus_direction.
    td = (trade_direction or "").lower()
    cd = (consensus_direction or "").lower()
    if td and cd and td not in {"", "neutral"} and cd not in {"", "neutral"}:
        conflict = 1.0 if td != cd else 0.0
    else:
        conflict = 0.0
    direction_score = 1.0 - conflict
    # ADX/choppiness sanity: prefer high ADX or low choppiness.
    if adx is not None and adx > 20:
        sanity = 1.0
    elif choppiness is not None and choppiness < 60:
        sanity = 1.0
    elif adx is None and choppiness is None:
        sanity = 0.5  # unknown — neutral
    else:
        sanity = 0.5
    return _safe_clamp(
        0.40 * rc + 0.30 * sc + 0.20 * direction_score + 0.10 * sanity
    )


def _confluence(
    *,
    consensus_direction: str,
    trade_direction: str,
    signal_direction: str,
    funding_rate: float,
    mtf_h1_bias: str,
    mtf_h4_bias: str,
    mtf_d1_bias: str,
    regime: str,
) -> tuple[float, int]:
    """Count how many directional anchors agree.

    Returns ``(score, n_aligned)``. ``score = n_aligned / max(n_directional, 1)``.
    """
    anchors: list[str] = []

    def _push(label: str | None) -> None:
        if not label:
            return
        s = label.lower()
        if s in {"long", "buy"}:
            anchors.append("long")
        elif s in {"short", "sell"}:
            anchors.append("short")
        # neutral / unknown ignored

    _push(consensus_direction)
    _push(trade_direction)
    _push(signal_direction)
    _push(mtf_h1_bias)
    _push(mtf_h4_bias)
    _push(mtf_d1_bias)
    # Funding-implied: longs paying (positive) → crowd long → "short"
    # is the contrarian implication; symmetric for negative.
    if funding_rate >= 0.0005:
        anchors.append("short")
    elif funding_rate <= -0.0005:
        anchors.append("long")
    # Regime trend direction.
    if _is_trending_up(regime):
        anchors.append("long")
    elif _is_trending_down(regime):
        anchors.append("short")

    if not anchors:
        return 0.0, 0
    n_long = anchors.count("long")
    n_short = anchors.count("short")
    n_aligned = max(n_long, n_short)
    return _safe_clamp(n_aligned / len(anchors)), n_aligned


_FUNDING_EXTREME = 0.002          # 0.2% — saturation point
_OI_EXTREME_PCT = 8.0             # |OI 24h delta| at which extremity = 1.0


def _extremity(
    *,
    funding_rate: float,
    fear_greed: int,
    position_in_range: float | None,
    oi_change_24h_pct: float,
    volume_ratio: float | None,
) -> float:
    """Reward extremity. Max (not sum) — any one extreme is a clean edge."""
    funding_ex = _safe_clamp(abs(funding_rate) / _FUNDING_EXTREME)
    if 0 < fear_greed < 15 or fear_greed > 85:
        fg_ex = 1.0
    elif 0 < fear_greed < 25 or fear_greed > 75:
        fg_ex = 0.5
    else:
        fg_ex = 0.0
    if position_in_range is None:
        range_ex = 0.0
    elif position_in_range < 0.10 or position_in_range > 0.90:
        range_ex = 1.0
    elif position_in_range < 0.20 or position_in_range > 0.80:
        range_ex = 0.5
    else:
        range_ex = 0.0
    oi_ex = _safe_clamp(abs(oi_change_24h_pct) / _OI_EXTREME_PCT)
    if volume_ratio is None:
        vol_ex = 0.0
    else:
        vol_ex = _safe_clamp(max(0.0, volume_ratio - 1.0) / 1.5)
    return max(funding_ex, fg_ex, range_ex, oi_ex, vol_ex)


def _label_strength(
    *,
    primary_label: str,
    secondary_labels: list[str],
) -> float:
    """Primary label base weight + decayed secondaries.

    A coin with no labels (impossible — labeler always returns at least
    NO_TRADEABLE_STATE) gets a 0.05 floor so unlabeled states still score.
    """
    if not primary_label:
        return 0.05
    primary = LABEL_BASE_WEIGHTS.get(primary_label, 0.05)
    if primary_label == LABEL_NO_TRADEABLE_STATE and not secondary_labels:
        return 0.05
    secondary = sum(
        0.4 * LABEL_BASE_WEIGHTS.get(name, 0.05)
        for name in secondary_labels
    )
    return _safe_clamp(primary + 0.3 * secondary)


def _structural_quality(
    *,
    setup_score: float,
    rr_ratio: float,
    mtf_quality: str,
) -> float:
    """Wrap legacy setup_score (0-100) and direction-aware RR continuously.

    RR saturates at 3.0 (mirrors ``_compute_opportunity_score`` line 299).
    """
    setup_norm = _safe_clamp(setup_score / 100.0)
    rr_norm = _safe_clamp(rr_ratio / 3.0)
    q = (mtf_quality or "").lower()
    if q in {"strong", "maximum"}:
        mtf_score = 1.0
    elif q == "good":
        mtf_score = 0.7
    elif q == "moderate":
        mtf_score = 0.4
    else:
        mtf_score = 0.0
    return _safe_clamp(0.45 * setup_norm + 0.35 * rr_norm + 0.20 * mtf_score)


def _mtf_alignment(*, aligned_count: int) -> float:
    """Per-coin MTF bias alignment count (0..4) → 0..1."""
    return _safe_clamp(aligned_count / 4.0)


def _open_position_floor(*, has_open_position: bool) -> float:
    """1.0 when a position is held, else 0.0."""
    return 1.0 if has_open_position else 0.0


# ── Public API ────────────────────────────────────────────────────────


def compute_interestingness(
    *,
    weights: InterestingnessWeights | None = None,
    # State inputs (Phase 5's CoinState dataclass projects onto these
    # exact kwargs — both phases use the same function signature).
    setup_type: str = "none",
    setup_type_confidence: float = 0.0,
    setup_score: float = 0.0,
    trade_direction: str = "",
    suggested_direction: str = "",
    rr_ratio: float = 0.0,
    mtf_quality: str = "",
    mtf_h1_bias: str = "",
    mtf_h4_bias: str = "",
    mtf_d1_bias: str = "",
    mtf_aligned_count: int = 0,
    regime: str = "",
    regime_confidence: float = 0.0,
    adx: float | None = None,
    choppiness: float | None = None,
    consensus: str = "",
    consensus_direction: str = "",
    signal_direction: str = "",
    funding_rate: float = 0.0,
    fear_greed: int = 0,
    position_in_range: float | None = None,
    oi_change_24h_pct: float = 0.0,
    volume_ratio: float | None = None,
    has_open_position: bool = False,
    primary_label: str = LABEL_NO_TRADEABLE_STATE,
    secondary_labels: list[str] | None = None,
) -> InterestingnessResult:
    """Compute interestingness score + per-component breakdown.

    Args:
        weights: Component weights. Defaults to
            :class:`InterestingnessWeights` (must sum to 1.0). Custom
            weights are validated upstream by ``ScannerBriefingSettings``;
            this function trusts whatever it receives.
        ...: state inputs — see module docstring for rationale.

    Returns:
        :class:`InterestingnessResult` with ``score`` clamped to [0, 1]
        and ``breakdown`` carrying per-component **already-weighted**
        contributions (so they sum to ``score``).
    """
    w = weights or InterestingnessWeights()
    secondaries = list(secondary_labels or [])

    cleanness = _cleanness(
        regime=regime,
        regime_confidence=regime_confidence,
        setup_type=setup_type,
        setup_type_confidence=setup_type_confidence,
        trade_direction=trade_direction or suggested_direction,
        consensus_direction=consensus_direction,
        adx=adx,
        choppiness=choppiness,
    )
    confluence_score, n_aligned = _confluence(
        consensus_direction=consensus_direction,
        trade_direction=trade_direction,
        signal_direction=signal_direction or consensus_direction,
        funding_rate=funding_rate,
        mtf_h1_bias=mtf_h1_bias,
        mtf_h4_bias=mtf_h4_bias,
        mtf_d1_bias=mtf_d1_bias,
        regime=regime,
    )
    extremity = _extremity(
        funding_rate=funding_rate,
        fear_greed=fear_greed,
        position_in_range=position_in_range,
        oi_change_24h_pct=oi_change_24h_pct,
        volume_ratio=volume_ratio,
    )
    label_strength = _label_strength(
        primary_label=primary_label or LABEL_NO_TRADEABLE_STATE,
        secondary_labels=secondaries,
    )
    structural_quality = _structural_quality(
        setup_score=setup_score,
        rr_ratio=rr_ratio,
        mtf_quality=mtf_quality,
    )
    mtf_align = _mtf_alignment(aligned_count=mtf_aligned_count)
    pos_floor = _open_position_floor(has_open_position=has_open_position)

    weighted = {
        "cleanness": w.cleanness * cleanness,
        "confluence": w.confluence * confluence_score,
        "extremity": w.extremity * extremity,
        "label_strength": w.label_strength * label_strength,
        "structural_quality": w.structural_quality * structural_quality,
        "mtf_alignment": w.mtf_alignment * mtf_align,
        "open_position_floor": w.open_position_floor * pos_floor,
    }
    raw_score = sum(weighted.values())
    score = _safe_clamp(raw_score)
    return InterestingnessResult(
        score=round(score, 4),
        breakdown={k: round(v, 4) for k, v in weighted.items()},
        state_cleanness=round(cleanness, 4),
        confluence_count=int(n_aligned),
    )
