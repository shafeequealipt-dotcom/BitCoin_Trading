"""Phase 4 — counter-setup classifier branches + trade_direction.

Verifies the philosophical fix: when the in-direction zones are missing
but counter-direction zones exist near price, classify_setup emits
``BULLISH_FVG_OB_COUNTER`` / ``BEARISH_FVG_OB_COUNTER`` with reduced
confidence (× counter_confidence_multiplier) and sets
``analysis.trade_direction`` to the OPPOSITE of suggested_direction.

The decision tree priority is preserved:
    1. In-direction FVG_OB (full confidence)
    2. *_FVG_OB_COUNTER (reduced confidence) ← Phase 4 inserts here
    3. *_STRUCTURAL_BREAK
    4. *_LIQUIDITY_SWEEP
    5. *_RANGE_BREAKOUT/BREAKDOWN
    6. NONE
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.analysis.structure.models.structure_types import (
    FairValueGap,
    LiquiditySweep,
    MarketStructureResult,
    OrderBlock,
    SetupType,
    StructuralAnalysis,
    StructureEvent,
)
from src.analysis.structure.structure_engine import StructureEngine
from src.config.settings import SetupTypesSettings, StructureSettings


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────


def _make_engine(**setup_overrides) -> StructureEngine:
    settings = StructureSettings(
        setup_types=SetupTypesSettings(**setup_overrides),
    )
    return StructureEngine(settings)


def _bullish_in_direction_analysis() -> StructuralAnalysis:
    """In-direction long: should classify BULLISH_FVG_OB."""
    a = StructuralAnalysis(
        symbol="ADAUSDT",
        suggested_direction="long",
        smc_confluence=80,
        position_in_range=0.2,
        total_confluence_factors=4,
    )
    a.market_structure = MarketStructureResult(structure="uptrend", strength="strong")
    a.nearest_fvg = FairValueGap(direction="bullish", filled=False)
    a.nearest_ob = OrderBlock(direction="bullish", fresh=True)
    a.mtf_confluence = MagicMock(score=8)
    return a


def _counter_only_long_analysis() -> StructuralAnalysis:
    """Suggested short, but only bullish counter zones present.

    Mirrors the live BTCUSDT/ETHUSDT/SOLUSDT pattern: structure has been
    re-classified to favor a short, in-direction bear zones don't exist
    near price (or filled), but bullish demand zones from earlier
    accumulation still sit there as counter zones. Result: long counter
    trade.
    """
    a = StructuralAnalysis(
        symbol="BTCUSDT",
        suggested_direction="short",
        smc_confluence=50,
        position_in_range=0.5,
        total_confluence_factors=3,
    )
    a.market_structure = MarketStructureResult(structure="ranging", strength="moderate")
    # In-direction (bear) zones missing
    a.nearest_fvg = None
    a.nearest_ob = None
    # Counter (bull) zones present
    a.nearest_fvg_counter = FairValueGap(direction="bullish", filled=False)
    a.nearest_ob_counter = OrderBlock(direction="bullish", fresh=True)
    a.mtf_confluence = MagicMock(score=6)  # 0.6 ≥ counter_mtf_threshold 0.40
    return a


def _counter_only_short_analysis() -> StructuralAnalysis:
    """Mirror — suggested long, only bearish counter zones present."""
    a = StructuralAnalysis(
        symbol="ETHUSDT",
        suggested_direction="long",
        smc_confluence=50,
        position_in_range=0.5,
        total_confluence_factors=3,
    )
    a.market_structure = MarketStructureResult(structure="ranging", strength="moderate")
    a.nearest_fvg = None
    a.nearest_ob = None
    a.nearest_fvg_counter = FairValueGap(direction="bearish", filled=False)
    a.nearest_ob_counter = OrderBlock(direction="bearish", fresh=True)
    a.mtf_confluence = MagicMock(score=6)
    return a


# ──────────────────────────────────────────────────────────────────────────
# Counter branch firing
# ──────────────────────────────────────────────────────────────────────────


class TestCounterBranchFires:
    def test_bullish_counter_when_only_counter_zones_present(self) -> None:
        eng = _make_engine()
        a = _counter_only_long_analysis()
        stype, conf = eng.classify_setup(a)
        assert stype == SetupType.BULLISH_FVG_OB_COUNTER
        assert a.trade_direction == "long"
        # Confidence reduced: base ~ min(0.6, max(0.5, 0.5)) = 0.5; ×0.7 = 0.35
        assert conf == pytest.approx(0.35, abs=0.001)

    def test_bearish_counter_when_only_counter_zones_present(self) -> None:
        eng = _make_engine()
        a = _counter_only_short_analysis()
        stype, conf = eng.classify_setup(a)
        assert stype == SetupType.BEARISH_FVG_OB_COUNTER
        assert a.trade_direction == "short"

    def test_in_direction_priority_over_counter(self) -> None:
        """In-direction setup must override counter even when both available."""
        eng = _make_engine(fvg_ob_min_confluence=0.5)
        a = _bullish_in_direction_analysis()
        # Add bogus counter zones — should be IGNORED because in-direction fires first.
        a.nearest_fvg_counter = FairValueGap(direction="bearish", filled=False)
        a.nearest_ob_counter = OrderBlock(direction="bearish", fresh=True)
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BULLISH_FVG_OB
        assert a.trade_direction == "long"  # matches suggested_direction


class TestCounterBranchFailureModes:
    def test_no_counter_when_disabled(self) -> None:
        eng = _make_engine(counter_setup_enabled=False)
        a = _counter_only_long_analysis()
        stype, _ = eng.classify_setup(a)
        # Counter disabled → falls through to NONE (no in-direction, no BoS).
        assert stype == SetupType.NONE
        assert a.trade_direction == ""

    def test_no_counter_when_strict_alignment_blocks_volatile(self) -> None:
        eng = _make_engine(counter_alignment_strict=True)
        a = _counter_only_long_analysis()
        a.market_structure = MarketStructureResult(structure="volatile")
        stype, _ = eng.classify_setup(a)
        # strict=True rejects volatile → NONE
        assert stype == SetupType.NONE

    def test_volatile_accepted_when_strict_off(self) -> None:
        eng = _make_engine(counter_alignment_strict=False)
        a = _counter_only_long_analysis()
        a.market_structure = MarketStructureResult(structure="volatile")
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.BULLISH_FVG_OB_COUNTER

    def test_no_counter_when_mtf_below_threshold(self) -> None:
        eng = _make_engine(counter_mtf_threshold=0.50)
        a = _counter_only_long_analysis()
        a.mtf_confluence = MagicMock(score=4)  # 0.4 < 0.5 threshold
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.NONE

    def test_no_counter_when_counter_fvg_filled(self) -> None:
        eng = _make_engine()
        a = _counter_only_long_analysis()
        a.nearest_fvg_counter = FairValueGap(direction="bullish", filled=True)
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.NONE

    def test_no_counter_when_counter_ob_stale(self) -> None:
        eng = _make_engine()
        a = _counter_only_long_analysis()
        a.nearest_ob_counter = OrderBlock(direction="bullish", fresh=False)
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.NONE

    def test_long_counter_blocked_in_uptrend(self) -> None:
        # Suggested short but structure=uptrend (a contradictory edge case).
        # Long counter trade in an uptrending market doesn't add information,
        # so _counter_alignment rejects.
        eng = _make_engine()
        a = _counter_only_long_analysis()
        a.market_structure = MarketStructureResult(structure="uptrend")
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.NONE

    def test_short_counter_blocked_in_downtrend(self) -> None:
        eng = _make_engine()
        a = _counter_only_short_analysis()
        a.market_structure = MarketStructureResult(structure="downtrend")
        stype, _ = eng.classify_setup(a)
        assert stype == SetupType.NONE


class TestTradeDirectionField:
    """trade_direction must always reflect the chosen branch."""

    def test_in_direction_long_sets_long(self) -> None:
        eng = _make_engine(fvg_ob_min_confluence=0.5)
        a = _bullish_in_direction_analysis()
        eng.classify_setup(a)
        assert a.trade_direction == "long"

    def test_counter_overrides_to_long(self) -> None:
        eng = _make_engine()
        a = _counter_only_long_analysis()
        eng.classify_setup(a)
        # Suggested was short, but counter trade goes long.
        assert a.suggested_direction == "short"
        assert a.trade_direction == "long"

    def test_counter_overrides_to_short(self) -> None:
        eng = _make_engine()
        a = _counter_only_short_analysis()
        eng.classify_setup(a)
        assert a.suggested_direction == "long"
        assert a.trade_direction == "short"

    def test_none_clears_trade_direction(self) -> None:
        eng = _make_engine()
        a = StructuralAnalysis(symbol="X", suggested_direction="long")
        a.market_structure = MarketStructureResult()
        eng.classify_setup(a)
        assert a.trade_direction == ""


class TestConfidenceReduction:
    """Counter setups must get confidence × counter_confidence_multiplier."""

    def test_phase4_default_multiplier_07(self) -> None:
        eng = _make_engine()
        # Equivalent in-direction would be conf = min(0.6, max(0.5, 0.5)) = 0.5
        # Counter applies × 0.7 → 0.35
        a = _counter_only_long_analysis()
        _, conf = eng.classify_setup(a)
        assert conf == pytest.approx(0.35, abs=0.001)

    def test_custom_multiplier_05(self) -> None:
        eng = _make_engine(counter_confidence_multiplier=0.5)
        a = _counter_only_long_analysis()
        _, conf = eng.classify_setup(a)
        assert conf == pytest.approx(0.25, abs=0.001)

    def test_custom_multiplier_09(self) -> None:
        eng = _make_engine(counter_confidence_multiplier=0.9)
        a = _counter_only_long_analysis()
        _, conf = eng.classify_setup(a)
        assert conf == pytest.approx(0.45, abs=0.001)

    def test_validation_rejects_zero_multiplier(self) -> None:
        with pytest.raises(ValueError, match="counter_confidence_multiplier"):
            SetupTypesSettings(counter_confidence_multiplier=0.0)

    def test_validation_rejects_above_one_multiplier(self) -> None:
        with pytest.raises(ValueError, match="counter_confidence_multiplier"):
            SetupTypesSettings(counter_confidence_multiplier=1.1)


class TestCounterAlignmentHelper:
    def test_long_counter_accepts_downtrend(self) -> None:
        cfg = SetupTypesSettings()
        assert StructureEngine._counter_alignment("long", "downtrend", cfg) is True

    def test_long_counter_accepts_ranging(self) -> None:
        cfg = SetupTypesSettings()
        assert StructureEngine._counter_alignment("long", "ranging", cfg) is True

    def test_long_counter_accepts_volatile_when_not_strict(self) -> None:
        cfg = SetupTypesSettings(counter_alignment_strict=False)
        assert StructureEngine._counter_alignment("long", "volatile", cfg) is True

    def test_long_counter_rejects_volatile_when_strict(self) -> None:
        cfg = SetupTypesSettings(counter_alignment_strict=True)
        assert StructureEngine._counter_alignment("long", "volatile", cfg) is False

    def test_long_counter_rejects_uptrend(self) -> None:
        cfg = SetupTypesSettings()
        assert StructureEngine._counter_alignment("long", "uptrend", cfg) is False

    def test_short_counter_mirror(self) -> None:
        cfg = SetupTypesSettings()
        assert StructureEngine._counter_alignment("short", "uptrend", cfg) is True
        assert StructureEngine._counter_alignment("short", "ranging", cfg) is True
        assert StructureEngine._counter_alignment("short", "downtrend", cfg) is False


# ─── Issue 6 (2026-06-08): FVG-OB-in-ranging confidence discount ───────


def _ranging_bullish_fvg_ob() -> StructuralAnalysis:
    """In-direction long FVG-OB in a RANGING regime (the loss archetype)."""
    a = StructuralAnalysis(
        symbol="ADAUSDT", suggested_direction="long", smc_confluence=90,
        position_in_range=0.2, total_confluence_factors=4,
    )
    a.market_structure = MarketStructureResult(structure="ranging", strength="moderate")
    a.nearest_fvg = FairValueGap(direction="bullish", filled=False)
    a.nearest_ob = OrderBlock(direction="bullish", fresh=True)
    a.mtf_confluence = MagicMock(score=8)  # 0.8 ≥ fvg_ob_min 0.7 AND ≥ ranging thr 0.55
    return a


def test_issue6_fvg_ob_ranging_discounted():
    """FVG-OB in ranging: confidence is multiplied by the discount (0.75)."""
    eng = _make_engine(fvg_ob_ranging_confidence_discount=0.75)
    stype, conf = eng.classify_setup(_ranging_bullish_fvg_ob())
    assert stype == SetupType.BULLISH_FVG_OB
    # conf = min(0.8, 0.9) = 0.8 → discounted 0.8 * 0.75 = 0.6
    assert abs(conf - 0.6) < 0.01, conf


def test_issue6_fvg_ob_ranging_off_switch():
    """discount=1.0 disables the down-weight (back-compat)."""
    eng = _make_engine(fvg_ob_ranging_confidence_discount=1.0)
    stype, conf = eng.classify_setup(_ranging_bullish_fvg_ob())
    assert stype == SetupType.BULLISH_FVG_OB
    assert abs(conf - 0.8) < 0.01, conf  # undiscounted


def test_issue6_trending_fvg_ob_not_discounted():
    """An FVG-OB in a TRENDING regime is NOT discounted (only ranging is the
    losing archetype)."""
    eng = _make_engine(fvg_ob_ranging_confidence_discount=0.75)
    a = _bullish_in_direction_analysis()        # struct = uptrend
    a.smc_confluence = 90
    a.mtf_confluence = MagicMock(score=8)
    stype, conf = eng.classify_setup(a)
    assert stype == SetupType.BULLISH_FVG_OB
    assert abs(conf - 0.8) < 0.01, conf         # uptrend → no discount
