"""Element 3 (2026-06-11) — the range-fade breakout guard.

Rule 5 trial condition: the range-fade labels fire only on genuine
in-range extremes, not on breakdowns. The June-11 DYDX construction was:
``position_in_range=None`` (the labeler never receives it),
direction long, price BELOW the range — and RANGE_FADE_LONG fired on
the directional anchor alone, all 24 submissions.

Also pins: legacy behaviour is byte-identical when ``range_breakout``
is "" (the default), the opposite-side break does NOT suppress, the
setup-driven labels (TREND_PULLBACK_*) are unaffected, and the funding
fades carry the same guard.
"""

from src.workers.scanner.state_labeler import (
    LABEL_RANGE_FADE_LONG,
    LABEL_RANGE_FADE_SHORT,
    LABEL_TREND_PULLBACK_LONG,
    _trigger_funding_extreme_fade_long,
    _trigger_funding_extreme_fade_short,
    _trigger_range_fade_long,
    _trigger_range_fade_short,
    label_state,
)


class TestRangeFadeLongGuard:
    BASE = dict(
        regime="ranging", trade_direction="long",
        position_in_range=None, consensus_direction="long",
        setup_type_confidence=0.5, regime_haircut=0.5,
    )

    def test_june11_dydx_construction_suppressed(self):
        # position unknown to the labeler, direction long, price below
        # the range: the fade premise is false — must not fire.
        assert _trigger_range_fade_long(
            **self.BASE, range_breakout="below",
        ) is None

    def test_legacy_behaviour_when_no_breakout(self):
        legacy = _trigger_range_fade_long(**self.BASE)
        explicit = _trigger_range_fade_long(**self.BASE, range_breakout="")
        assert legacy is not None
        assert explicit == legacy

    def test_above_break_also_suppresses_the_long_fade(self):
        # Cross-check fix (2026-06-11): ANY genuine break falsifies the
        # fade premise — above the range, "buy the range low" is as
        # meaningless as below it (and the in-range gate is dormant in
        # production, so this guard is the only truth check).
        assert _trigger_range_fade_long(
            **self.BASE, range_breakout="above",
        ) is None


class TestRangeFadeShortGuard:
    BASE = dict(
        regime="ranging", trade_direction="short",
        position_in_range=None, consensus_direction="short",
        setup_type_confidence=0.5, regime_haircut=0.5,
    )

    def test_above_break_suppresses_short_fade(self):
        assert _trigger_range_fade_short(
            **self.BASE, range_breakout="above",
        ) is None

    def test_below_break_also_suppresses_short_fade(self):
        # Cross-check fix (2026-06-11): mirror of the long side — a
        # short fade premise ("sell the range high") is false anywhere
        # outside the range.
        assert _trigger_range_fade_short(
            **self.BASE, range_breakout="below",
        ) is None


class TestFundingFadeGuards:
    def test_funding_fade_long_suppressed_below_range(self):
        base = dict(
            funding_rate=-0.002, regime="ranging",
            position_in_range=None, regime_haircut=0.5,
        )
        assert _trigger_funding_extreme_fade_long(**base) is not None
        assert _trigger_funding_extreme_fade_long(
            **base, range_breakout="below",
        ) is None

    def test_funding_fade_short_suppressed_above_range(self):
        base = dict(
            funding_rate=0.002, regime="ranging",
            position_in_range=None, regime_haircut=0.5,
        )
        assert _trigger_funding_extreme_fade_short(**base) is not None
        assert _trigger_funding_extreme_fade_short(
            **base, range_breakout="above",
        ) is None


class TestLabelStateEndToEnd:
    def _kwargs(self, **over):
        kw = dict(
            setup_type="bullish_fvg_ob",
            setup_type_confidence=0.5,
            trade_direction="long",
            suggested_direction="long",
            regime="trending_up",
            consensus_direction="long",
            regime_haircut=0.5,
        )
        kw.update(over)
        return kw

    def test_breakdown_kills_fade_but_not_pullback(self):
        # With a below-range break: the trend pullback (setup-driven)
        # still labels the coin — every coin still ranks — but the
        # false fade label is gone.
        result = label_state(**self._kwargs(range_breakout="below"))
        labels = [result.primary] + list(result.secondary)
        assert LABEL_TREND_PULLBACK_LONG in labels
        assert LABEL_RANGE_FADE_LONG not in labels

    def test_no_breakout_keeps_legacy_labels(self):
        legacy = label_state(**self._kwargs())
        explicit = label_state(**self._kwargs(range_breakout=""))
        assert legacy.primary == explicit.primary
        assert list(legacy.secondary) == list(explicit.secondary)
        labels = [legacy.primary] + list(legacy.secondary)
        assert LABEL_RANGE_FADE_LONG in labels

    def test_short_side_mirror(self):
        result = label_state(**self._kwargs(
            setup_type="bearish_fvg_ob",
            trade_direction="short",
            suggested_direction="short",
            regime="trending_down",
            consensus_direction="short",
            range_breakout="above",
        ))
        labels = [result.primary] + list(result.secondary)
        assert LABEL_RANGE_FADE_SHORT not in labels
