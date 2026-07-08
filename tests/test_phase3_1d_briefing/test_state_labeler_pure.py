"""Phase 3 of the 1D briefing rewrite — state labeller pure-function tests.

Single question per test: "given an unambiguous state, does the right
label fire?" Plus boundary cases that must NOT fire.

Per the test-velocity rule (≤10 min on tests / phase), these tests do
not exercise every label exhaustively — they cover the labels with
the trickiest semantics (counter-trade direction inversion, funding
extreme regime gate, advisory fall-through) and confirm the
no-fire fall-through goes to NO_TRADEABLE_STATE.
"""

from src.workers.scanner.state_labeler import (
    _FUNDING_EXTREME_DECIMAL,
    LABEL_BASE_WEIGHTS,
    LABEL_BREAKOUT_PENDING,
    LABEL_COUNTER_TRADE_LONG,
    LABEL_FUNDING_EXTREME_FADE_LONG,
    LABEL_FUNDING_EXTREME_FADE_SHORT,
    LABEL_LIQUIDITY_SWEEP_REVERSAL_SHORT,
    LABEL_NO_TRADEABLE_STATE,
    LABEL_OPEN_POSITION_HOLD_REVIEW,
    LABEL_RECENT_LOSER_COOLDOWN,
    LABEL_TREND_PULLBACK_LONG,
    label_state,
)
from src.config.settings import Settings


def test_trend_pullback_long_fires_in_uptrend_with_bullish_setup() -> None:
    res = label_state(
        setup_type="bullish_fvg_ob",
        setup_type_confidence=0.7,
        trade_direction="long",
        suggested_direction="long",
        regime="trending_up",
        consensus="GOOD",
        consensus_direction="long",
    )
    assert res.primary == LABEL_TREND_PULLBACK_LONG
    assert res.confidence > 0.5


def test_trend_pullback_does_not_fire_in_ranging() -> None:
    res = label_state(
        setup_type="bullish_fvg_ob",
        setup_type_confidence=0.7,
        trade_direction="long",
        regime="ranging",
        consensus="GOOD",
        consensus_direction="long",
    )
    assert res.primary != LABEL_TREND_PULLBACK_LONG


def test_counter_trade_long_inverts_direction() -> None:
    """COUNTER setups carry trade_direction OPPOSITE to suggested_direction."""
    res = label_state(
        setup_type="bullish_fvg_ob_counter",
        setup_type_confidence=0.45,
        trade_direction="long",        # going long
        suggested_direction="short",   # against the bias
        regime="ranging",
        consensus_direction="long",
    )
    assert LABEL_COUNTER_TRADE_LONG in res.all_labels


def test_funding_extreme_fade_short_fires_when_longs_pay() -> None:
    """Positive funding > +0.0015 = longs pay = crowd long → fade short."""
    res = label_state(
        funding_rate=0.0030,           # 0.30% — longs paying
        regime="ranging",
        consensus_direction="short",
    )
    assert LABEL_FUNDING_EXTREME_FADE_SHORT in res.all_labels
    # Negative-funding-fade-LONG should NOT also fire.
    assert LABEL_FUNDING_EXTREME_FADE_LONG not in res.all_labels


def test_funding_extreme_fade_long_blocked_in_downtrend() -> None:
    """Even with negative-funding extreme, no fade-long if regime is trending_down."""
    res = label_state(
        funding_rate=-0.0050,          # -0.50% — shorts paying heavily
        regime="trending_down",        # but trend is down — don't fight it
        consensus_direction="long",
    )
    assert LABEL_FUNDING_EXTREME_FADE_LONG not in res.all_labels


def test_liquidity_sweep_reversal_short_fires_for_bearish_sweep() -> None:
    res = label_state(
        setup_type="bearish_liquidity_sweep",
        setup_type_confidence=0.65,
        trade_direction="short",
        regime="trending_down",
    )
    assert LABEL_LIQUIDITY_SWEEP_REVERSAL_SHORT in res.all_labels


def test_no_tradeable_state_when_nothing_fires() -> None:
    """All-defaults call: no setup, no funding extreme, no position, no recent loss."""
    res = label_state()
    assert res.primary == LABEL_NO_TRADEABLE_STATE
    assert res.secondary == []
    assert res.confidence == 0.0


def test_open_position_advisory_always_fires() -> None:
    """Even with no other state, an open position surfaces hold-review."""
    res = label_state(has_open_position=True)
    assert LABEL_OPEN_POSITION_HOLD_REVIEW in res.all_labels


def test_recent_loser_advisory_fires() -> None:
    res = label_state(is_recent_loser=True)
    assert LABEL_RECENT_LOSER_COOLDOWN in res.all_labels


def test_primary_picked_by_base_weight_times_confidence() -> None:
    """When multiple labels fire, primary = highest base_weight × confidence."""
    res = label_state(
        # Trend-pullback long fires (base_weight 0.85)
        setup_type="bullish_fvg_ob",
        setup_type_confidence=0.7,
        trade_direction="long",
        regime="trending_up",
        # Funding-extreme-fade long ALSO fires (base_weight 0.60)
        funding_rate=-0.0030,
    )
    # Trend-pullback should win as primary (0.85 × 0.7 = 0.595 vs ~0.60 × ~0.4 = 0.24).
    assert res.primary == LABEL_TREND_PULLBACK_LONG
    assert LABEL_FUNDING_EXTREME_FADE_LONG in res.secondary


def test_funding_extreme_boundary_matches_qualitative_gate() -> None:
    """Layer 1 Defect 8 regression guard: the labeller's funding-extreme
    threshold must equal the qualitative gate's funding_blocker_threshold_pct
    so the FUNDING_EXTREME_FADE labels fire at the same boundary the
    with-crowd direction is blocked. Historical drift left these at
    0.0015 vs 0.001 — a dead band of 0.001-0.0015 where neither side
    fired.
    """
    gate_threshold = (
        Settings().scanner.qualitative.funding_blocker_threshold_pct
    )
    assert _FUNDING_EXTREME_DECIMAL == gate_threshold, (
        f"Labeller funding-extreme constant ({_FUNDING_EXTREME_DECIMAL}) "
        f"diverged from qualitative gate ({gate_threshold}). They must "
        f"mirror so labels and gates fire at the same boundary."
    )


def test_funding_extreme_fade_short_fires_at_aligned_boundary() -> None:
    """At funding=0.0012 (above new 0.001 boundary, below old 0.0015),
    FUNDING_EXTREME_FADE_SHORT must fire. Historically it did not because
    the labeller threshold was 0.0015."""
    res = label_state(
        funding_rate=0.0012,
        regime="ranging",
        consensus_direction="short",
    )
    assert LABEL_FUNDING_EXTREME_FADE_SHORT in res.all_labels


def test_breakout_pending_fires_for_bullish_range_breakout() -> None:
    res = label_state(
        setup_type="bullish_range_breakout",
        setup_type_confidence=0.7,
        trade_direction="long",
        regime="ranging",
        consensus_direction="long",
    )
    assert LABEL_BREAKOUT_PENDING in res.all_labels


def test_breakout_pending_fires_for_bearish_range_breakdown() -> None:
    """Layer 1 Defect 2 regression guard: the labeller's BREAKOUT_PENDING
    trigger must match the producer's BEARISH_RANGE_BREAKDOWN string
    (structure_types.py:48). The historical bug expected the wrong
    spelling ``bearish_range_breakout`` so this arm never fired."""
    res = label_state(
        setup_type="bearish_range_breakdown",
        setup_type_confidence=0.7,
        trade_direction="short",
        regime="ranging",
        consensus_direction="short",
    )
    assert LABEL_BREAKOUT_PENDING in res.all_labels


def test_label_base_weights_table_is_complete() -> None:
    """Every fired label name has an entry in LABEL_BASE_WEIGHTS so the
    Phase 4 ranker doesn't trip on a missing key."""
    # Sample one label per category to keep the test fast; the
    # ranker's KeyError protection is via dict.get so this is sanity.
    for name in (
        LABEL_TREND_PULLBACK_LONG, LABEL_COUNTER_TRADE_LONG,
        LABEL_FUNDING_EXTREME_FADE_SHORT, LABEL_NO_TRADEABLE_STATE,
        LABEL_OPEN_POSITION_HOLD_REVIEW, LABEL_RECENT_LOSER_COOLDOWN,
    ):
        assert name in LABEL_BASE_WEIGHTS
        assert 0.0 <= LABEL_BASE_WEIGHTS[name] <= 1.0


def test_labeler_never_raises_on_garbage_input() -> None:
    """Defensive contract: any combination of inputs returns a result."""
    # Mixed-case strings, NaN-ish floats simulated via extreme values.
    res = label_state(
        setup_type="UNKNOWN_TYPE",
        regime="UNKNOWN_REGIME",
        funding_rate=99.0,           # absurd
        fear_greed=200,              # out of range
        change_24h_pct=-9999.0,
        consensus="weird",
        consensus_direction="???",
        trade_direction="upward",
    )
    # No crash, returns a valid result. Primary may or may not be
    # NO_TRADEABLE_STATE depending on which legit triggers fire on
    # numeric extremes; the contract is just "no exception".
    assert res.primary in LABEL_BASE_WEIGHTS


# ── Issue 3 of 2026-05-19 direction-bias fix Phase B — regime haircut ──
#
# The 8 per-trigger regime hard-kill predicates were converted to soft
# confidence-haircut multipliers. ``regime_haircut`` parameter on
# ``label_state``:
#
#   0.0 (default) → legacy hard-kill (label suppressed in mismatched regime).
#   0.5           → labels fire at half confidence in mismatched regime.
#   1.0           → regime gate fully removed (full confidence regardless).
#
# Existing tests above all rely on the 0.0 default and continue to pass
# verbatim. The tests below cover the new haircut semantics.


def test_funding_extreme_fade_long_fires_in_downtrend_with_haircut() -> None:
    """With haircut > 0.0, negative-funding extreme fade-long fires
    even in trending_down regime (at reduced confidence)."""
    res = label_state(
        funding_rate=-0.0050,
        regime="trending_down",
        consensus_direction="long",
        regime_haircut=0.5,
    )
    assert LABEL_FUNDING_EXTREME_FADE_LONG in res.all_labels


def test_funding_extreme_fade_long_suppressed_with_zero_haircut() -> None:
    """With haircut == 0.0 (legacy default), regime mismatch suppresses
    the label — verifies backwards compatibility."""
    res = label_state(
        funding_rate=-0.0050,
        regime="trending_down",
        consensus_direction="long",
        regime_haircut=0.0,
    )
    assert LABEL_FUNDING_EXTREME_FADE_LONG not in res.all_labels


def test_trend_pullback_long_fires_in_ranging_with_haircut() -> None:
    """Trend-pullback in mismatched regime fires at reduced confidence
    when haircut > 0.0."""
    res_with_match = label_state(
        setup_type="bullish_fvg_ob",
        setup_type_confidence=0.7,
        trade_direction="long",
        regime="trending_up",          # matches — full confidence
        consensus_direction="long",
        regime_haircut=0.5,
    )
    res_with_mismatch = label_state(
        setup_type="bullish_fvg_ob",
        setup_type_confidence=0.7,
        trade_direction="long",
        regime="trending_down",        # mismatch — haircut applies
        consensus_direction="long",
        regime_haircut=0.5,
    )
    # In matched regime, trend_pullback is primary at full confidence.
    assert res_with_match.primary == LABEL_TREND_PULLBACK_LONG
    # In mismatched regime with haircut=0.5, the label still appears
    # in all_labels but at half confidence.
    assert LABEL_TREND_PULLBACK_LONG in res_with_mismatch.all_labels


def test_haircut_one_removes_regime_gate_entirely() -> None:
    """With haircut == 1.0, the regime gate has no effect — labels
    fire at full confidence in any regime."""
    res_uptrend = label_state(
        setup_type="bullish_fvg_ob",
        setup_type_confidence=0.7,
        trade_direction="long",
        regime="trending_up",
        consensus_direction="long",
        regime_haircut=1.0,
    )
    res_ranging = label_state(
        setup_type="bullish_fvg_ob",
        setup_type_confidence=0.7,
        trade_direction="long",
        regime="ranging",
        consensus_direction="long",
        regime_haircut=1.0,
    )
    assert res_uptrend.primary == LABEL_TREND_PULLBACK_LONG
    assert LABEL_TREND_PULLBACK_LONG in res_ranging.all_labels


def test_extreme_fear_long_fires_in_downtrend_with_haircut() -> None:
    """Extreme-fear contrarian bias fires in trending_down with
    haircut > 0.0 (at reduced confidence)."""
    res = label_state(
        fear_greed=15,
        regime="trending_down",
        consensus_direction="long",
        trade_direction="long",
        regime_haircut=0.5,
    )
    from src.workers.scanner.state_labeler import LABEL_EXTREME_FEAR_LONG_BIAS
    assert LABEL_EXTREME_FEAR_LONG_BIAS in res.all_labels


def test_extreme_fear_long_suppressed_with_zero_haircut() -> None:
    """Default (haircut=0.0) preserves legacy hard-kill in trending_down."""
    res = label_state(
        fear_greed=15,
        regime="trending_down",
        consensus_direction="long",
        trade_direction="long",
    )  # no regime_haircut arg → default 0.0
    from src.workers.scanner.state_labeler import LABEL_EXTREME_FEAR_LONG_BIAS
    assert LABEL_EXTREME_FEAR_LONG_BIAS not in res.all_labels


def test_labeller_regime_haircut_version_constant_present() -> None:
    """Issue 3 fix bumped LABELLER_REGIME_HAIRCUT_VERSION to 2 so the
    boot sentinel STATE_LABELLER_REGIME_HAIRCUT_INIT confirms the soft-
    haircut implementation is in memory at process start."""
    from src.workers.scanner.state_labeler import (
        LABELLER_REGIME_HAIRCUT_VERSION,
    )
    assert LABELLER_REGIME_HAIRCUT_VERSION == 2
