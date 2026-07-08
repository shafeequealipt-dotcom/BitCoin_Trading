"""Phase 3 — `_find_nearest_*` contract returns both directions.

Verifies the result-dataclass contract change. The finders now scan
the full FVG/OB list and surface the nearest in-direction zone AND
the nearest counter-direction zone as a structured result, rather
than returning ``Optional[FairValueGap]`` for the in-direction slot
only.

Phase 4's classifier consumes ``counter_direction`` to emit
``BULLISH_FVG_OB_COUNTER`` / ``BEARISH_FVG_OB_COUNTER`` — that's
the philosophical "characterize, don't reject" payoff. These tests
verify the Phase 3 plumbing without exercising classifier branches.
"""

from __future__ import annotations

import pytest

from src.analysis.structure.models.structure_types import (
    FairValueGap,
    NearestFVGResult,
    NearestOBResult,
    OrderBlock,
)
from src.analysis.structure.structure_engine import StructureEngine
from src.config.settings import SetupTypesSettings


def _cfg(**overrides) -> SetupTypesSettings:
    base = dict(
        fvg_atr_multiplier=3.0,
        ob_atr_multiplier=4.0,
        fvg_min_distance_pct=2.0,
        ob_min_distance_pct=3.0,
    )
    base.update(overrides)
    return SetupTypesSettings(**base)


def _fvg(direction: str, dist_pct: float, *, price: float = 100.0,
         filled: bool = False, created_index: int = 0) -> FairValueGap:
    sign = 1 if direction == "bullish" else -1
    midpoint = price * (1.0 + sign * dist_pct / 100.0)
    return FairValueGap(
        direction=direction, filled=filled, midpoint=midpoint,
        created_index=created_index,
    )


def _ob(direction: str, dist_pct: float, *, price: float = 100.0,
        fresh: bool = True) -> OrderBlock:
    sign = 1 if direction == "bullish" else -1
    midpoint = price * (1.0 + sign * dist_pct / 100.0)
    return OrderBlock(direction=direction, fresh=fresh, midpoint=midpoint)


class TestFVGContractFourCases:
    """Four cases the contract must distinguish: (in only, counter only,
    both, neither). Each case verifies the result dataclass populates
    the correct slots."""

    def test_in_only(self) -> None:
        # Suggested long; only bullish unfilled FVG within window.
        cfg = _cfg()
        fvgs = [_fvg("bullish", 1.5)]
        out = StructureEngine._find_nearest_fvg(fvgs, 100.0, "long", 0.5, cfg)
        assert isinstance(out, NearestFVGResult)
        assert out.in_direction is not None
        assert out.counter_direction is None
        assert out.in_distance_pct == pytest.approx(1.5, abs=1e-6)
        assert out.counter_distance_pct is None
        assert out.suggested_direction == "long"

    def test_counter_only(self) -> None:
        # Suggested long; only bearish unfilled FVG within window.
        cfg = _cfg()
        fvgs = [_fvg("bearish", 1.5)]
        out = StructureEngine._find_nearest_fvg(fvgs, 100.0, "long", 0.5, cfg)
        assert out.in_direction is None
        assert out.counter_direction is not None
        assert out.counter_distance_pct == pytest.approx(1.5, abs=1e-6)

    def test_both_directions(self) -> None:
        # Both bullish and bearish unfilled FVGs within window.
        cfg = _cfg()
        fvgs = [_fvg("bullish", 1.0), _fvg("bearish", 1.5)]
        out = StructureEngine._find_nearest_fvg(fvgs, 100.0, "long", 0.5, cfg)
        assert out.in_direction is not None
        assert out.in_direction.direction == "bullish"
        assert out.counter_direction is not None
        assert out.counter_direction.direction == "bearish"
        assert out.in_distance_pct == pytest.approx(1.0, abs=1e-6)
        assert out.counter_distance_pct == pytest.approx(1.5, abs=1e-6)

    def test_neither(self) -> None:
        cfg = _cfg()
        out = StructureEngine._find_nearest_fvg([], 100.0, "long", 0.5, cfg)
        assert out.in_direction is None
        assert out.counter_direction is None


class TestOBContractFourCases:
    """OB contract mirror — same four cases."""

    def test_in_only(self) -> None:
        cfg = _cfg()
        obs = [_ob("bullish", 2.5)]
        out = StructureEngine._find_nearest_ob(obs, 100.0, "long", 0.5, cfg)
        assert isinstance(out, NearestOBResult)
        assert out.in_direction is not None
        assert out.counter_direction is None

    def test_counter_only(self) -> None:
        cfg = _cfg()
        obs = [_ob("bearish", 2.5)]
        out = StructureEngine._find_nearest_ob(obs, 100.0, "long", 0.5, cfg)
        assert out.in_direction is None
        assert out.counter_direction is not None

    def test_both_directions(self) -> None:
        cfg = _cfg()
        obs = [_ob("bullish", 2.0), _ob("bearish", 2.8)]
        out = StructureEngine._find_nearest_ob(obs, 100.0, "long", 0.5, cfg)
        assert out.in_direction is not None
        assert out.counter_direction is not None
        assert out.in_distance_pct == pytest.approx(2.0, abs=1e-6)
        assert out.counter_distance_pct == pytest.approx(2.8, abs=1e-6)

    def test_neither(self) -> None:
        cfg = _cfg()
        out = StructureEngine._find_nearest_ob([], 100.0, "long", 0.5, cfg)
        assert out.in_direction is None
        assert out.counter_direction is None


class TestClosestWithinWindowSemantics:
    """Phase 3 changes the selection rule from 'first iterated' to
    'closest within window' so the result no longer depends on the
    creation-index ordering of the input list."""

    def test_fvg_picks_closest_in_direction(self) -> None:
        # Three bullish unfilled FVGs at 1.8%, 1.2%, 1.6% — closest is 1.2%.
        cfg = _cfg()
        fvgs = [
            _fvg("bullish", 1.8, created_index=10),
            _fvg("bullish", 1.2, created_index=5),
            _fvg("bullish", 1.6, created_index=8),
        ]
        out = StructureEngine._find_nearest_fvg(fvgs, 100.0, "long", 0.0, cfg)
        assert out.in_distance_pct == pytest.approx(1.2, abs=1e-6)

    def test_fvg_picks_closest_counter_direction(self) -> None:
        cfg = _cfg()
        fvgs = [
            _fvg("bearish", 1.7),
            _fvg("bearish", 1.0),
            _fvg("bearish", 1.4),
        ]
        out = StructureEngine._find_nearest_fvg(fvgs, 100.0, "long", 0.0, cfg)
        assert out.in_direction is None
        assert out.counter_distance_pct == pytest.approx(1.0, abs=1e-6)

    def test_ob_picks_closest_per_slot(self) -> None:
        cfg = _cfg()
        obs = [
            _ob("bullish", 2.7),
            _ob("bullish", 2.1),
            _ob("bearish", 2.9),
            _ob("bearish", 1.5),
        ]
        out = StructureEngine._find_nearest_ob(obs, 100.0, "long", 0.0, cfg)
        assert out.in_distance_pct == pytest.approx(2.1, abs=1e-6)
        assert out.counter_distance_pct == pytest.approx(1.5, abs=1e-6)


class TestEdgeCases:
    def test_empty_direction_returns_empty_result(self) -> None:
        # When suggested_direction is "" we can't classify in vs counter.
        cfg = _cfg()
        fvgs = [_fvg("bullish", 1.5), _fvg("bearish", 1.5)]
        out = StructureEngine._find_nearest_fvg(fvgs, 100.0, "", 0.5, cfg)
        assert isinstance(out, NearestFVGResult)
        assert out.in_direction is None
        assert out.counter_direction is None
        assert out.suggested_direction == ""

    def test_filled_fvg_excluded_from_both_slots(self) -> None:
        cfg = _cfg()
        bull_filled = _fvg("bullish", 1.0, filled=True)
        bear_filled = _fvg("bearish", 1.0, filled=True)
        bull_open = _fvg("bullish", 1.5)
        bear_open = _fvg("bearish", 1.5)
        out = StructureEngine._find_nearest_fvg(
            [bull_filled, bear_filled, bull_open, bear_open],
            100.0, "long", 0.0, cfg,
        )
        assert out.in_direction is bull_open
        assert out.counter_direction is bear_open

    def test_stale_ob_excluded_from_both_slots(self) -> None:
        cfg = _cfg()
        stale_bull = _ob("bullish", 2.0, fresh=False)
        stale_bear = _ob("bearish", 2.0, fresh=False)
        fresh_bull = _ob("bullish", 2.5)
        fresh_bear = _ob("bearish", 2.5)
        out = StructureEngine._find_nearest_ob(
            [stale_bull, stale_bear, fresh_bull, fresh_bear],
            100.0, "long", 0.0, cfg,
        )
        assert out.in_direction is fresh_bull
        assert out.counter_direction is fresh_bear

    def test_zero_current_price_safe(self) -> None:
        cfg = _cfg()
        fvgs = [_fvg("bullish", 1.5)]
        out = StructureEngine._find_nearest_fvg(fvgs, 0.0, "long", 0.5, cfg)
        # Division-by-zero guard returns empty result rather than raising.
        assert out.in_direction is None
        assert out.counter_direction is None

    def test_short_direction_swaps_in_and_counter(self) -> None:
        cfg = _cfg()
        # Suggested short — bearish is in_direction, bullish is counter.
        fvgs = [_fvg("bullish", 1.5), _fvg("bearish", 1.0)]
        out = StructureEngine._find_nearest_fvg(fvgs, 100.0, "short", 0.0, cfg)
        assert out.in_direction is not None
        assert out.in_direction.direction == "bearish"
        assert out.counter_direction is not None
        assert out.counter_direction.direction == "bullish"


class TestStructuralAnalysisCounterFields:
    """Phase 3 added two fields to StructuralAnalysis — verify defaults."""

    def test_default_counter_fields_none(self) -> None:
        from src.analysis.structure.models.structure_types import StructuralAnalysis

        a = StructuralAnalysis(symbol="X")
        assert a.nearest_fvg_counter is None
        assert a.nearest_ob_counter is None

    def test_counter_fields_assignable(self) -> None:
        from src.analysis.structure.models.structure_types import StructuralAnalysis

        a = StructuralAnalysis(symbol="X")
        a.nearest_fvg_counter = _fvg("bearish", 1.5)
        a.nearest_ob_counter = _ob("bearish", 2.5)
        assert a.nearest_fvg_counter is not None
        assert a.nearest_ob_counter is not None
