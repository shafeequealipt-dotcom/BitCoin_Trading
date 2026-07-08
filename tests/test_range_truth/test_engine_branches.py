"""Four-Element Prompt Recalibration, Element 3 (2026-06-11) — the
pre-clamp range truth computed by ``_compute_range_position``, with the
break detection corrected by the same-day real-pipeline cross-check.

The load-bearing fact these tests encode: the real
SupportResistanceEngine FILTERS supports to strictly below the current
price and resistances to strictly above it, so on live data a genuine
breakdown arrives with an EMPTY supports list (and a breakout with an
empty resistances list). The break must therefore be detected from the
UNFILTERED swing structure — the originally shipped raw-value detection
could never fire on engine output, which only the end-to-end harness
caught (verify_recalibration_pipeline_e2e.py). TestRealEngineBreakdown
pins that exact scenario through the REAL engine so the regression can
never silently return.

Also covered: the clamped ``position_in_range`` is byte-identical to
the legacy formula in every branch (the bounds-assuming consumers are
frozen — Rule 5), exactly-at-boundary is not a break, and ``to_dict``
carries the new keys.
"""

from types import SimpleNamespace

import pytest

from src.analysis.structure.structure_engine import _compute_range_position
from src.analysis.structure.models.structure_types import StructuralAnalysis


def _lvl(price: float):
    return SimpleNamespace(price=price)


class TestTwoSidedInRange:
    """With both levels present the SR filter guarantees an in-range
    price on live data; the clamp behaves exactly as legacy."""

    def test_middle(self):
        pos, brk, ov = _compute_range_position(105.0, _lvl(100.0), _lvl(110.0))
        assert pos == pytest.approx(0.5)
        assert brk == ""
        assert ov == 0.0

    def test_exactly_at_the_low_is_not_a_break(self):
        pos, brk, ov = _compute_range_position(100.0, _lvl(100.0), _lvl(110.0))
        assert (pos, brk, ov) == (0.0, "", 0.0)

    def test_exactly_at_the_high_is_not_a_break(self):
        pos, brk, ov = _compute_range_position(110.0, _lvl(100.0), _lvl(110.0))
        assert (pos, brk, ov) == (1.0, "", 0.0)

    def test_zero_width_range_falls_back_to_neutral(self):
        pos, brk, ov = _compute_range_position(100.0, _lvl(100.0), _lvl(100.0))
        assert (pos, brk, ov) == (0.5, "", 0.0)

    def test_defensive_raw_breakdown_for_non_engine_callers(self):
        # Unreachable through the real engine (the SR filter), kept for
        # callers that pass unfiltered levels.
        pos, brk, ov = _compute_range_position(97.0, _lvl(100.0), _lvl(110.0))
        assert pos == 0.0
        assert brk == "below"
        assert ov == pytest.approx(3.0)


class TestSwingBreakDetection:
    """The real path: a genuine break empties one level list and the
    boundary survives only in the unfiltered swing structure."""

    LOWS = [(10, 100.0), (20, 104.0), (30, 101.0)]
    HIGHS = [(5, 110.0), (15, 108.0)]

    def test_breakdown_below_all_swing_lows(self):
        # The June-11 DYDX construction: supports filtered empty, price
        # under the lowest detected swing low (100), only a resistance
        # above. Overshoot is a percent of the broken low.
        pos, brk, ov = _compute_range_position(
            97.0, None, _lvl(110.0),
            swing_lows=self.LOWS, swing_highs=self.HIGHS,
        )
        assert pos == 0.0  # clamped value unchanged for consumers
        assert brk == "below"
        assert ov == pytest.approx(3.0)

    def test_exactly_at_the_lowest_low_is_not_a_break(self):
        pos, brk, ov = _compute_range_position(
            100.0, None, _lvl(110.0),
            swing_lows=self.LOWS, swing_highs=self.HIGHS,
        )
        assert brk == ""
        assert ov == 0.0

    def test_breakout_above_all_swing_highs(self):
        pos, brk, ov = _compute_range_position(
            113.0, _lvl(100.0), None,
            swing_lows=self.LOWS, swing_highs=self.HIGHS,
        )
        assert pos == 1.0
        assert brk == "above"
        assert ov == pytest.approx(3.0 / 110.0 * 100.0)

    def test_exactly_at_the_highest_high_is_not_a_break(self):
        pos, brk, ov = _compute_range_position(
            110.0, _lvl(100.0), None,
            swing_lows=self.LOWS, swing_highs=self.HIGHS,
        )
        assert brk == ""

    def test_both_levels_present_swing_data_ignored(self):
        # In range by construction — swing data must not manufacture a
        # break when both levels exist.
        pos, brk, ov = _compute_range_position(
            105.0, _lvl(100.0), _lvl(110.0),
            swing_lows=self.LOWS, swing_highs=self.HIGHS,
        )
        assert brk == ""

    def test_missing_swing_data_degrades_to_no_marker(self):
        pos, brk, ov = _compute_range_position(97.0, None, _lvl(110.0))
        assert (brk, ov) == ("", 0.0)
        pos, brk, ov = _compute_range_position(
            97.0, None, _lvl(110.0), swing_lows=[], swing_highs=None,
        )
        assert (brk, ov) == ("", 0.0)

    def test_no_levels_at_all_neutral(self):
        pos, brk, ov = _compute_range_position(100.0, None, None)
        assert (pos, brk, ov) == (0.5, "", 0.0)


class TestLegacyClampParity:
    """The clamped value must be byte-identical to the legacy formula —
    the bounds-assuming consumers (setup score, interestingness, fade
    gates, breakout classifier, SL/TP) are behavior-frozen (Rule 5)."""

    @pytest.mark.parametrize("price", [95.0, 100.0, 101.5, 107.3, 110.0, 115.0])
    def test_two_sided_parity(self, price):
        s, r = 100.0, 110.0
        legacy = max(0.0, min(1.0, (price - s) / (r - s)))
        pos, _, _ = _compute_range_position(price, _lvl(s), _lvl(r))
        assert pos == pytest.approx(legacy)

    @pytest.mark.parametrize("price", [94.0, 100.0, 102.0, 105.0, 109.0])
    def test_support_only_parity(self, price):
        s = 100.0
        legacy = max(0.0, min(1.0, (price - s) / (s * 0.05)))
        pos, _, _ = _compute_range_position(price, _lvl(s), None)
        assert pos == pytest.approx(legacy)

    @pytest.mark.parametrize("price", [90.0, 96.0, 100.0, 104.0])
    def test_resistance_only_parity(self, price):
        r = 100.0
        legacy = max(0.0, min(1.0, 1.0 - (r - price) / (r * 0.05)))
        pos, _, _ = _compute_range_position(price, None, _lvl(r))
        assert pos == pytest.approx(legacy)


class TestRealEngineBreakdown:
    """End-to-end through the REAL StructureEngine: a clear range that
    breaks down must produce the 'below' marker. This is the exact
    scenario whose absence the e2e harness caught — keep it green."""

    def _candles(self):
        candles = []
        for i in range(120):
            cyc = i % 10
            base = 100.0 + (cyc if cyc <= 5 else 10 - cyc) * 2.0
            if i >= 112:
                base = 100.0 - (i - 111) * 0.4
            candles.append(SimpleNamespace(
                open=base + 0.2, high=base + 0.6, low=base - 0.6,
                close=base, volume=1000.0,
            ))
        return candles

    def test_real_engine_marks_the_breakdown(self):
        from src.config.settings import StructureSettings
        from src.analysis.structure.structure_engine import StructureEngine

        engine = StructureEngine(StructureSettings())
        analysis = engine.analyze("RANGETESTUSDT", 97.0, self._candles())
        assert analysis is not None
        assert 0.0 <= analysis.position_in_range <= 1.0
        assert analysis.range_breakout == "below"
        assert analysis.range_overshoot_pct > 0.0


class TestDataclassAndDict:
    def test_defaults_are_in_range(self):
        a = StructuralAnalysis(symbol="X")
        assert a.range_breakout == ""
        assert a.range_overshoot_pct == 0.0

    def test_to_dict_carries_truth_fields_rounded(self):
        a = StructuralAnalysis(
            symbol="X", range_breakout="below", range_overshoot_pct=2.345,
        )
        d = a.to_dict()
        assert d["range_breakout"] == "below"
        assert d["range_overshoot_pct"] == 2.35
