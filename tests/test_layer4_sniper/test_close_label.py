"""Issue C Phase 3a — disambiguate ``closed_by`` labels by trigger path.

Verifies that ``_resolve_full_close_label`` returns a path-specific
label for every known trigger source, and falls back to the legacy
``mode4_p9`` for unknown sources. Pre-fix every full closure carried
the same hardcoded ``"mode4_p9"`` string regardless of which code
path triggered it; this produced the audit's "32 mode4_p9 events"
misclassification (the substring appears in tick-evaluation logs
without being a close event). Distinct labels mean
COORD_CLOSE_END / M4_ACT_CLOSE / Mode4 CLOSED log lines now reveal
the actual trigger path without reading source.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.workers.profit_sniper import _resolve_full_close_label


class TestResolveFullCloseLabel:
    def test_score_source_maps_to_score_full(self) -> None:
        action = SimpleNamespace(source="score")
        assert _resolve_full_close_label(action) == "mode4_score_full"

    def test_both_source_maps_to_score_full(self) -> None:
        """``source='both'`` means score reached threshold AND the
        anti-greed backstop agreed. The score condition was the
        sufficient one — label as score."""
        action = SimpleNamespace(source="both")
        assert _resolve_full_close_label(action) == "mode4_score_full"

    def test_anti_greed_source_maps_to_anti_greed_full(self) -> None:
        action = SimpleNamespace(source="anti_greed")
        assert _resolve_full_close_label(action) == "mode4_anti_greed_full"

    def test_stall_escape_source_maps_to_stall_valve(self) -> None:
        """The mature-stall valve at ``_stall_escape_action:2481`` sets
        ``action.source = 'stall_escape'`` (line 529 of tick) and is
        the path that fired all four 2026-05-08 audit-window full
        closures."""
        action = SimpleNamespace(source="stall_escape")
        assert _resolve_full_close_label(action) == "mode4_stall_valve"

    def test_unknown_source_falls_back_to_legacy_label(self) -> None:
        """Unknown source must produce a non-None label so the close
        path never propagates ``None`` into ``trade_coordinator``. The
        legacy ``mode4_p9`` is the safe fallback — operators who see
        it know they are looking at a path the ``_FULL_CLOSE_LABEL_BY_SOURCE``
        table does not yet cover."""
        action = SimpleNamespace(source="some_future_path_not_yet_registered")
        assert _resolve_full_close_label(action) == "mode4_p9"

    def test_empty_source_falls_back(self) -> None:
        action = SimpleNamespace(source="")
        assert _resolve_full_close_label(action) == "mode4_p9"

    def test_missing_source_attribute_falls_back(self) -> None:
        """If ``ActionResult.source`` is ever omitted (legacy unit
        tests or future refactor), ``getattr`` returns the empty
        string and we fall through to the legacy label."""
        action = SimpleNamespace()  # no `source` at all
        assert _resolve_full_close_label(action) == "mode4_p9"
