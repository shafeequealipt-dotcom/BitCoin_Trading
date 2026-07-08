"""Element 3 (2026-06-11) — brain/APEX rendering of the range truth and
its propagation, plus the scanner plumbing contracts.

Covers:
- the pure marker renderer ``_range_breakout_marker`` in both forms,
  including old-object safety (a cached pre-fields StructuralAnalysis
  renders nothing);
- ``StructuralData.format()`` appends the truth only when present, and
  the legacy line is byte-identical for in-range coins;
- the assembler propagates both fields (source contract);
- scanner_worker passes ONLY range_breakout to the labeler (and not
  position_in_range, which must stay unplumbed), gated by the
  [scanner.labeller] flag;
- the strategist render sites are flag-gated by range_truth_enabled.
"""

import inspect
from types import SimpleNamespace

from src.apex.models import StructuralData
from src.brain.strategist import _range_breakout_marker


class TestMarkerRenderer:
    def test_in_range_renders_nothing(self):
        a = SimpleNamespace(range_breakout="", range_overshoot_pct=0.0)
        assert _range_breakout_marker(a, compact=True) == ""
        assert _range_breakout_marker(a, compact=False) == ""

    def test_old_cached_object_without_fields_renders_nothing(self):
        a = SimpleNamespace()  # pre-Element-3 StructuralAnalysis shape
        assert _range_breakout_marker(a, compact=True) == ""
        assert _range_breakout_marker(a, compact=False) == ""

    def test_below_full_form(self):
        a = SimpleNamespace(range_breakout="below", range_overshoot_pct=2.34)
        out = _range_breakout_marker(a, compact=False)
        assert out == " (BELOW RANGE by 2.3% — breakdown, not a floor)"

    def test_above_full_form(self):
        a = SimpleNamespace(range_breakout="above", range_overshoot_pct=1.0)
        out = _range_breakout_marker(a, compact=False)
        assert out == " (ABOVE RANGE by 1.0% — breakout, not a ceiling)"

    def test_compact_forms(self):
        below = SimpleNamespace(range_breakout="below", range_overshoot_pct=2.3)
        above = SimpleNamespace(range_breakout="above", range_overshoot_pct=0.7)
        assert _range_breakout_marker(below, compact=True) == "BELOW-RANGE(2.3%) "
        assert _range_breakout_marker(above, compact=True) == "ABOVE-RANGE(0.7%) "

    def test_garbage_value_renders_nothing(self):
        a = SimpleNamespace(range_breakout="sideways", range_overshoot_pct=9.0)
        assert _range_breakout_marker(a, compact=True) == ""


class TestApexStructuralData:
    def test_format_in_range_is_legacy_byte_identical(self):
        sd = StructuralData(symbol="X", position_in_range=0.42)
        out = sd.format()
        assert "Position in range: 42%" in out
        assert "BELOW" not in out and "ABOVE" not in out

    def test_format_below_appends_truth(self):
        sd = StructuralData(
            symbol="X", position_in_range=0.0,
            range_breakout="below", range_overshoot_pct=2.3,
        )
        assert (
            "Position in range: 0% (price is BELOW the range low by 2.3%)"
            in sd.format()
        )

    def test_format_above_appends_truth(self):
        sd = StructuralData(
            symbol="X", position_in_range=1.0,
            range_breakout="above", range_overshoot_pct=0.8,
        )
        assert (
            "Position in range: 100% (price is ABOVE the range high by 0.8%)"
            in sd.format()
        )


class TestWiringContracts:
    def test_assembler_propagates_both_fields(self):
        from src.apex import assembler
        src = inspect.getsource(
            assembler._gather_structural_data_from_cache
        )
        assert 'getattr(analysis, "range_breakout", "")' in src
        assert 'getattr(analysis, "range_overshoot_pct", 0.0)' in src

    def test_scanner_passes_only_breakout_not_position(self):
        import src.workers.scanner_worker as sw
        src = inspect.getsource(sw.ScannerWorker)
        assert 'getattr(structure, "range_breakout", "")' in src
        assert "range_fade_breakout_guard_enabled" in src
        # position_in_range stays UNPLUMBED into the label_state CALL
        # (the dormant in-range gates keep legacy behaviour). Scope the
        # assertion to the call segment — position_in_range= is
        # legitimately passed elsewhere (compute_interestingness).
        start = src.index("label_state(")
        end = src.index("StateLabelBlock", start)
        call_segment = src[start:end]
        assert "position_in_range=" not in call_segment
        assert "range_breakout=" in call_segment

    def test_strategist_render_sites_are_flag_gated(self):
        from src.brain.strategist import ClaudeStrategist
        full_src = inspect.getsource(
            ClaudeStrategist._format_packages_for_prompt_full
        )
        assert "range_truth_enabled" in full_src
        assert "_range_breakout_marker" in full_src

    def test_engine_constructor_receives_truth_fields(self):
        from src.analysis.structure.structure_engine import StructureEngine
        src = inspect.getsource(StructureEngine.analyze)
        assert "range_breakout=range_breakout" in src
        assert "range_overshoot_pct=range_overshoot_pct" in src
        assert "_compute_range_position" in src
