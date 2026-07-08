"""Phase 4 of the 1D briefing rewrite — interestingness ranker.

Two questions per test:
1. Pure-function determinism + monotonicity (better state → higher score).
2. Component breakdown sums to score; weights validated to 1.0.
"""

import pytest

from src.config.settings import (
    ScannerBriefingInterestingnessWeights,
    ScannerBriefingSettings,
)
from src.workers.scanner.interestingness import (
    InterestingnessWeights,
    compute_interestingness,
)
from src.workers.scanner.state_labeler import (
    LABEL_FUNDING_EXTREME_FADE_LONG,
    LABEL_NO_TRADEABLE_STATE,
    LABEL_TREND_PULLBACK_LONG,
)


def test_default_weights_sum_to_one() -> None:
    w = InterestingnessWeights()
    assert abs(w.total - 1.0) < 1e-6


def test_weights_validation_rejects_non_unit_sum() -> None:
    with pytest.raises(ValueError, match="must sum to 1.0"):
        ScannerBriefingInterestingnessWeights(cleanness=0.5)  # rest at defaults


def test_briefing_settings_validates_top_n_geq_min() -> None:
    with pytest.raises(ValueError, match="must be <= top_n_packages"):
        ScannerBriefingSettings(top_n_packages=10, min_briefing_packages=12)


def test_pure_determinism_same_input_same_output() -> None:
    """Same inputs → same score, byte-identical breakdown."""
    kwargs = dict(
        setup_type="bullish_fvg_ob",
        setup_type_confidence=0.7,
        setup_score=72.0,
        trade_direction="long",
        rr_ratio=2.1,
        regime="trending_up",
        regime_confidence=0.82,
        consensus="GOOD",
        consensus_direction="long",
        signal_direction="long",
        funding_rate=-0.0018,
        fear_greed=18,
        primary_label=LABEL_TREND_PULLBACK_LONG,
        secondary_labels=[LABEL_FUNDING_EXTREME_FADE_LONG],
    )
    a = compute_interestingness(**kwargs)
    b = compute_interestingness(**kwargs)
    assert a.score == b.score
    assert a.breakdown == b.breakdown
    assert a.state_cleanness == b.state_cleanness
    assert a.confluence_count == b.confluence_count


def test_breakdown_sums_to_score() -> None:
    """Per-component contributions are already-weighted; they sum to score."""
    res = compute_interestingness(
        setup_type="bullish_fvg_ob",
        setup_type_confidence=0.65,
        setup_score=60.0,
        trade_direction="long",
        rr_ratio=2.0,
        regime="trending_up",
        regime_confidence=0.7,
        consensus="GOOD",
        consensus_direction="long",
        primary_label=LABEL_TREND_PULLBACK_LONG,
    )
    summed = round(sum(res.breakdown.values()), 4)
    # Round-trip through clamp may shave at most 1e-4 off.
    assert abs(summed - res.score) < 1e-3


def test_clean_state_outranks_weak_state() -> None:
    """Clean trend with sweep-equivalent label scores higher than weak unlabeled."""
    clean = compute_interestingness(
        setup_type="bullish_fvg_ob",
        setup_type_confidence=0.85,
        setup_score=85.0,
        trade_direction="long",
        rr_ratio=2.5,
        regime="trending_up",
        regime_confidence=0.9,
        adx=32.0,
        consensus="STRONG",
        consensus_direction="long",
        signal_direction="long",
        funding_rate=-0.0020,
        fear_greed=18,
        mtf_quality="strong",
        mtf_aligned_count=3,
        primary_label=LABEL_TREND_PULLBACK_LONG,
    )
    weak = compute_interestingness(
        regime="dead",
        regime_confidence=0.30,
        primary_label=LABEL_NO_TRADEABLE_STATE,
    )
    assert clean.score > weak.score
    # Sanity: clean is significantly above the weak floor.
    assert clean.score >= 0.55
    assert weak.score <= 0.20


def test_open_position_floor_bumps_score() -> None:
    """Holding a position adds a small absolute bump (~+0.03 default)."""
    base = compute_interestingness(
        regime="ranging",
        regime_confidence=0.40,
        primary_label=LABEL_NO_TRADEABLE_STATE,
    )
    held = compute_interestingness(
        regime="ranging",
        regime_confidence=0.40,
        has_open_position=True,
        primary_label=LABEL_NO_TRADEABLE_STATE,
    )
    assert held.score > base.score
    # Default weight 0.03 — bump must be at least ~0.025 (clamp leeway).
    assert held.score - base.score >= 0.025


def test_confluence_count_returned_as_integer() -> None:
    res = compute_interestingness(
        consensus_direction="long",
        trade_direction="long",
        signal_direction="long",
        regime="trending_up",
        funding_rate=-0.0010,    # implies long anchor
    )
    assert isinstance(res.confluence_count, int)
    # 3 explicit anchors + funding-implied + regime-trend = 5 long anchors.
    assert res.confluence_count >= 4


def test_score_clamped_to_unit_interval() -> None:
    """Even with maxed-out inputs, score is in [0, 1]."""
    res = compute_interestingness(
        setup_type="bullish_fvg_ob",
        setup_type_confidence=1.0,
        setup_score=100.0,
        trade_direction="long",
        rr_ratio=10.0,                 # saturates internally at 3.0
        regime="trending_up",
        regime_confidence=1.0,
        adx=80.0,
        consensus="STRONG",
        consensus_direction="long",
        signal_direction="long",
        funding_rate=-0.10,            # absurd extreme — saturates
        fear_greed=5,
        mtf_quality="strong",
        mtf_aligned_count=4,
        has_open_position=True,
        primary_label=LABEL_TREND_PULLBACK_LONG,
        secondary_labels=[LABEL_FUNDING_EXTREME_FADE_LONG],
    )
    assert 0.0 <= res.score <= 1.0
