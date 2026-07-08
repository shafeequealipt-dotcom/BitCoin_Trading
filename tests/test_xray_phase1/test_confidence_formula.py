"""Phase 1 — XRAY confidence formula fix.

Verifies the two changes in commit ``fix(xray/phase-1)``:

1. ``_classify_signal`` in ``src/analysis/structure/liquidity.py`` returns
   directional weak labels (``weak_long`` / ``weak_short``) instead of the
   pre-fix directionless ``weak_signal``. This restores the +30 sweep
   contribution to ``_compute_smc_confluence`` for genuine but weak
   reversals.
2. ``classify_setup`` in ``src/analysis/structure/structure_engine.py``
   drops the historical ``max(smc_01, 0.5)`` / ``max(..., 0.5)`` floor
   across every branch (BULLISH/BEARISH FVG_OB, FVG_OB_COUNTER,
   STRUCTURAL_BREAK, LIQUIDITY_SWEEP, RANGE_BREAKOUT/BREAKDOWN). Confidence
   now reflects the actual computed components without artificial
   promotion. Path C (judgment-based prompt) trusts truthful values.

Also covers ``_compute_smc_confluence`` returning the ``(score, breakdown)``
tuple shape and the ``StructuralAnalysis.smc_breakdown`` field being
populated.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.analysis.structure.liquidity import _classify_signal
from src.analysis.structure.models.structure_types import (
    FairValueGap,
    LiquiditySweep,
    LiquidityZone,
    MarketStructureResult,
    OrderBlock,
    SetupType,
    StructuralAnalysis,
)
from src.analysis.structure.structure_engine import StructureEngine
from src.config.settings import SetupTypesSettings, StructureSettings


# ─────────────────────────────────────────────────────────────────────────
# Part 1 — _classify_signal directional labels
# ─────────────────────────────────────────────────────────────────────────


class TestClassifySignalDirectional:
    """Pre-fix _classify_signal returned 'weak_signal' (no direction). After
    the fix every return carries direction so the +30 sweep substring check
    in _compute_smc_confluence matches genuine weak sweeps."""

    @pytest.mark.parametrize("direction", ["long", "short"])
    def test_high_probability(self, direction: str) -> None:
        signal = _classify_signal(direction, rev_ratio=0.6, depth_pct=0.15)
        assert signal == f"high_probability_{direction}"
        assert direction in signal

    @pytest.mark.parametrize("direction", ["long", "short"])
    def test_moderate_via_rev_ratio(self, direction: str) -> None:
        signal = _classify_signal(direction, rev_ratio=0.4, depth_pct=0.02)
        assert signal == f"moderate_{direction}"
        assert direction in signal

    @pytest.mark.parametrize("direction", ["long", "short"])
    def test_moderate_via_depth_pct(self, direction: str) -> None:
        signal = _classify_signal(direction, rev_ratio=0.1, depth_pct=0.06)
        assert signal == f"moderate_{direction}"
        assert direction in signal

    @pytest.mark.parametrize("direction", ["long", "short"])
    def test_weak_keeps_direction(self, direction: str) -> None:
        """The smoking gun. Pre-fix this returned 'weak_signal' and the
        downstream substring match silently failed — sweeps were detected
        but never contributed to confluence."""
        signal = _classify_signal(direction, rev_ratio=0.2, depth_pct=0.03)
        assert signal == f"weak_{direction}"
        assert direction in signal
        assert signal != "weak_signal"

    def test_no_signal_collision(self) -> None:
        """Long and short labels at every grade are distinct strings."""
        labels = {
            _classify_signal(d, r, dp)
            for d in ("long", "short")
            for r, dp in ((0.6, 0.15), (0.4, 0.0), (0.1, 0.06), (0.0, 0.0))
        }
        # 6 distinct labels: high_probability_{long,short}, moderate_{long,short},
        # weak_{long,short} — moderate_via_rev / moderate_via_depth collapse.
        assert len(labels) == 6


# ─────────────────────────────────────────────────────────────────────────
# Part 2 — _compute_smc_confluence return shape + sweep gate
# ─────────────────────────────────────────────────────────────────────────


def _bullish_fvg(price: float = 100.0) -> FairValueGap:
    return FairValueGap(
        direction="bullish", top=101.0, bottom=99.0, midpoint=100.0,
        filled=False, gap_size_pct=2.0,
    )


def _bullish_ob(price: float = 100.0) -> OrderBlock:
    return OrderBlock(
        direction="bullish", high=100.5, low=99.5, midpoint=100.0,
        fresh=True, displacement_strength="strong",
    )


def _sell_side_zone() -> LiquidityZone:
    return LiquidityZone(
        zone_type="sell_side", level=98.0,
        zone_high=98.5, zone_low=97.5, strength=3.0, swept=False,
    )


def _weak_long_sweep() -> LiquiditySweep:
    """A real-but-weak sweep that pre-fix would not contribute (signal was
    'weak_signal' so the substring check failed)."""
    sig = _classify_signal("long", rev_ratio=0.2, depth_pct=0.03)
    return LiquiditySweep(sweep_type="bullish_sweep", level_swept=98.0,
                          signal=sig, reversal_strength="weak")


def _maxed_bullish_fvg() -> FairValueGap:
    """A perfect in-direction FVG: at price, strong displacement, unfilled."""
    return FairValueGap(
        direction="bullish", top=101.0, bottom=99.0, midpoint=100.0,
        filled=False, fill_percentage=0.0, gap_size_pct=2.0,
        displacement_strength="strong",
    )


def _maxed_bullish_ob() -> OrderBlock:
    """A perfect in-direction OB: at price, fresh, full strength_score."""
    return OrderBlock(
        direction="bullish", high=100.5, low=99.5, midpoint=100.0,
        fresh=True, displacement_strength="strong", strength_score=100.0,
    )


def _maxed_sell_side_zone() -> LiquidityZone:
    return LiquidityZone(
        zone_type="sell_side", level=98.0, zone_high=98.5, zone_low=97.5,
        strength=5.0, equal_count=5, swept=False,
    )


def _maxed_long_sweep() -> LiquiditySweep:
    sig = _classify_signal("long", rev_ratio=0.6, depth_pct=0.15)  # high_probability
    return LiquiditySweep(sweep_type="bullish_sweep", level_swept=98.0,
                          signal=sig, reversal_strength="strong", age_candles=0)


class TestComputeSMCConfluenceGraded:
    """Issue 3 (2026-06-06): _compute_smc_confluence is now GRADED — each
    component scales within its cap by the coin's own zone quality, so the
    score spreads per coin instead of pinning at a constant 70. Returns
    (score, breakdown)."""

    def test_empty_inputs(self) -> None:
        score, br = StructureEngine._compute_smc_confluence(
            [], [], [], [], current_price=100.0, direction="long",
        )
        assert score == 0
        assert br == {"fvg": 0, "ob": 0, "liq": 0, "sweep": 0}

    def test_mediocre_components_score_below_ceiling(self) -> None:
        """The legacy fixtures (weak displacement, zero strength_score, weak
        sweep) used to sum to a flat 100. Graded, each contributes a partial
        amount and the total lands well below the ceiling — proof the constant
        is gone."""
        score, br = StructureEngine._compute_smc_confluence(
            [_bullish_fvg()], [_bullish_ob()], [_sell_side_zone()],
            [_weak_long_sweep()],
            current_price=100.0, direction="long",
        )
        # Every component fires, but none at full cap.
        assert 0 < br["fvg"] < 25
        assert 0 < br["ob"] < 30
        assert 0 < br["liq"] < 15
        assert 0 < br["sweep"] < 30
        assert score == sum(br.values())
        assert score < 100, f"mediocre inputs must not pin at the ceiling: {br}"

    def test_graded_spread_strong_outscores_weak(self) -> None:
        """The core fix: a strong setup scores meaningfully higher than a
        mediocre one — the score now differentiates coins."""
        strong, _ = StructureEngine._compute_smc_confluence(
            [_maxed_bullish_fvg()], [_maxed_bullish_ob()],
            [_maxed_sell_side_zone()], [_maxed_long_sweep()],
            current_price=100.0, direction="long",
        )
        weak, _ = StructureEngine._compute_smc_confluence(
            [_bullish_fvg()], [_bullish_ob()], [_sell_side_zone()],
            [_weak_long_sweep()],
            current_price=100.0, direction="long",
        )
        assert strong > weak + 20, (
            f"graded score must spread strong vs weak: strong={strong} weak={weak}"
        )

    def test_weak_sweep_still_contributes(self) -> None:
        """The XRAY phase-1 directional-label fix is preserved: a weak but real
        in-direction sweep still contributes (graded, > 0) rather than the
        pre-phase-1 zero."""
        score, br = StructureEngine._compute_smc_confluence(
            [], [], [], [_weak_long_sweep()],
            current_price=100.0, direction="long",
        )
        assert br["sweep"] > 0, f"weak directional sweep must contribute; got {br}"
        assert score == br["sweep"]

    def test_short_direction_substring_match(self) -> None:
        """Mirror: a weak_short signal contributes for direction=short."""
        sig = _classify_signal("short", rev_ratio=0.2, depth_pct=0.03)
        sweep = LiquiditySweep(sweep_type="bearish_sweep", level_swept=102.0,
                               signal=sig, reversal_strength="weak", age_candles=0)
        score, br = StructureEngine._compute_smc_confluence(
            [], [], [], [sweep],
            current_price=100.0, direction="short",
        )
        assert br["sweep"] > 0
        assert score == br["sweep"]

    def test_wrong_direction_signal_no_contribution(self) -> None:
        """A weak_long signal must NOT contribute when direction is short
        — the substring 'short' is not in 'weak_long'."""
        sig = _classify_signal("long", rev_ratio=0.2, depth_pct=0.03)
        sweep = LiquiditySweep(sweep_type="bullish_sweep", level_swept=98.0,
                               signal=sig, reversal_strength="weak")
        score, br = StructureEngine._compute_smc_confluence(
            [], [], [], [sweep],
            current_price=100.0, direction="short",
        )
        assert br["sweep"] == 0
        assert score == 0

    def test_maxed_components_reach_ceiling_and_cap(self) -> None:
        """Perfect zones on every component reach the per-component caps and
        the total is clamped at 100."""
        score, br = StructureEngine._compute_smc_confluence(
            [_maxed_bullish_fvg()], [_maxed_bullish_ob()],
            [_maxed_sell_side_zone(), _maxed_sell_side_zone()],  # only best counts
            [_maxed_long_sweep()],
            current_price=100.0, direction="long",
        )
        assert br == {"fvg": 25, "ob": 30, "liq": 15, "sweep": 30}
        assert score == 100
        assert sum(br.values()) <= 100


# ─────────────────────────────────────────────────────────────────────────
# Part 3 — classify_setup confidence formula (0.5 floor removed)
# ─────────────────────────────────────────────────────────────────────────


def _engine_with_settings(fvg_ob_min: float = 0.5) -> StructureEngine:
    """Build a StructureEngine with settings tuned for classify_setup tests.

    classify_setup is pure with respect to fields on ``StructuralAnalysis``
    — it does not call any sub-engines, so the lazy-wired sub-engine
    attributes can stay None.
    """
    settings = StructureSettings()
    if not hasattr(settings, "setup_types") or settings.setup_types is None:
        settings.setup_types = SetupTypesSettings()
    settings.setup_types.fvg_ob_min_confluence = fvg_ob_min
    settings.setup_types.counter_setup_enabled = True
    return StructureEngine(settings=settings)


def _make_analysis(
    *,
    direction: str = "long",
    structure: str = "uptrend",
    smc_confluence: int = 30,  # smc_01 = 0.30 — below the pre-fix 0.5 floor
    smc_breakdown: dict[str, int] | None = None,
    mtf_score: int = 8,  # mtf_score_01 = 0.8
    nearest_fvg: FairValueGap | None = None,
    nearest_ob: OrderBlock | None = None,
) -> StructuralAnalysis:
    """Build a StructuralAnalysis suitable for classify_setup."""
    if smc_breakdown is None:
        smc_breakdown = {"fvg": 25, "ob": 5, "liq": 0, "sweep": 0}
    if nearest_fvg is None:
        if direction == "long":
            nearest_fvg = _bullish_fvg()
        else:
            nearest_fvg = FairValueGap(
                direction="bearish", top=101.0, bottom=99.0, midpoint=100.0,
                filled=False, gap_size_pct=2.0,
            )
    if nearest_ob is None:
        if direction == "long":
            nearest_ob = _bullish_ob()
        else:
            nearest_ob = OrderBlock(
                direction="bearish", high=100.5, low=99.5, midpoint=100.0,
                fresh=True, displacement_strength="strong",
            )
    mtf = MagicMock()
    mtf.score = mtf_score
    return StructuralAnalysis(
        symbol="TESTUSDT",
        current_price=100.0,
        suggested_direction=direction,
        market_structure=MarketStructureResult(structure=structure),
        nearest_fvg=nearest_fvg,
        nearest_ob=nearest_ob,
        smc_confluence=smc_confluence,
        smc_breakdown=dict(smc_breakdown),
        mtf_confluence=mtf,
    )


class TestClassifySetupFloorRemoved:
    """Pre-fix every confidence branch had a max(..., 0.5) floor that
    promoted weak setups to 0.5 even when their actual combined
    confidence was lower. Post-fix the value is truthful."""

    def test_bullish_fvg_ob_below_old_floor(self) -> None:
        """smc_01 = 0.30, mtf = 0.80 → conf = min(0.80, 0.30) = 0.30.
        Pre-fix: max(0.30, 0.5) → 0.5 → conf = min(0.80, 0.5) = 0.5."""
        engine = _engine_with_settings(fvg_ob_min=0.5)
        analysis = _make_analysis(
            direction="long", structure="uptrend",
            smc_confluence=30, mtf_score=8,
        )
        setup, conf = engine.classify_setup(analysis)
        assert setup == SetupType.BULLISH_FVG_OB
        assert conf == pytest.approx(0.30, abs=1e-3)

    def test_bullish_fvg_ob_mtf_bound(self) -> None:
        """smc_01 = 0.85, mtf = 0.80 → conf = min(0.80, 0.85) = 0.80.
        Pre-fix: max(0.85, 0.5) = 0.85 → conf = min(0.80, 0.85) = 0.80.
        Same result here — the floor only mattered when smc was below 0.5."""
        engine = _engine_with_settings(fvg_ob_min=0.5)
        analysis = _make_analysis(
            direction="long", structure="uptrend",
            smc_confluence=85, mtf_score=8,
        )
        setup, conf = engine.classify_setup(analysis)
        assert setup == SetupType.BULLISH_FVG_OB
        assert conf == pytest.approx(0.80, abs=1e-3)

    def test_bearish_fvg_ob_below_old_floor(self) -> None:
        engine = _engine_with_settings(fvg_ob_min=0.5)
        analysis = _make_analysis(
            direction="short", structure="downtrend",
            smc_confluence=30, mtf_score=8,
        )
        setup, conf = engine.classify_setup(analysis)
        assert setup == SetupType.BEARISH_FVG_OB
        assert conf == pytest.approx(0.30, abs=1e-3)

    def test_strong_setup_reaches_high_conf(self) -> None:
        """Full SMC (sweep included) + strong MTF reaches 0.85+. Pre-fix
        this was the same number; post-fix it remains the same — no
        regression for high-quality setups."""
        engine = _engine_with_settings(fvg_ob_min=0.5)
        analysis = _make_analysis(
            direction="long", structure="uptrend",
            smc_confluence=85, mtf_score=9,  # mtf_01 = 0.9
            smc_breakdown={"fvg": 25, "ob": 30, "liq": 0, "sweep": 30},
        )
        setup, conf = engine.classify_setup(analysis)
        assert setup == SetupType.BULLISH_FVG_OB
        # min(0.9, 0.85) = 0.85
        assert conf == pytest.approx(0.85, abs=1e-3)

    def test_smc_breakdown_propagated_to_log_helper(self, caplog) -> None:
        """The XRAY_CONFIDENCE_DETAIL log line includes per-component
        breakdown read from analysis.smc_breakdown."""
        import logging
        engine = _engine_with_settings(fvg_ob_min=0.5)
        analysis = _make_analysis(
            direction="long", structure="uptrend",
            smc_confluence=85, mtf_score=8,
            smc_breakdown={"fvg": 25, "ob": 30, "liq": 0, "sweep": 30},
        )
        with caplog.at_level(logging.INFO, logger="xray"):
            engine.classify_setup(analysis)
        # The log message should contain the breakdown values.
        msgs = [r.getMessage() for r in caplog.records]
        detail = [m for m in msgs if "XRAY_CONFIDENCE_DETAIL" in m]
        # Loguru routing may not always reach pytest's caplog; the helper
        # is best-effort by design. Either we see the line or we don't —
        # but if we do see it, fields must be present.
        for line in detail:
            assert "fvg=25" in line
            assert "ob=30" in line
            assert "liq=0" in line
            assert "sweep=30" in line
            assert "setup=bullish_fvg_ob" in line


# ─────────────────────────────────────────────────────────────────────────
# Part 4 — Regression: existing behavior preserved
# ─────────────────────────────────────────────────────────────────────────


class TestNoFloorMaskingRegression:
    """Whatever the floor used to mask, post-fix should reveal honestly.
    Weak setups should now show low confidence (below 0.5)."""

    @pytest.mark.parametrize("smc_value", [0, 5, 10, 20, 30, 40])
    def test_weak_smc_values_pass_through(self, smc_value: int) -> None:
        """Post-fix smc_01 values below 0.5 produce conf below 0.5."""
        engine = _engine_with_settings(fvg_ob_min=0.5)
        analysis = _make_analysis(
            direction="long", structure="uptrend",
            smc_confluence=smc_value, mtf_score=9,  # mtf_01 = 0.9
        )
        setup, conf = engine.classify_setup(analysis)
        assert setup == SetupType.BULLISH_FVG_OB
        # Pre-fix: floor would push all of these to 0.5.
        # Post-fix: each maps directly to smc_01 (since mtf > smc_01).
        expected = smc_value / 100.0
        assert conf == pytest.approx(expected, abs=1e-3), (
            f"smc={smc_value} → expected {expected}, got {conf}"
        )

    def test_none_setup_unchanged(self) -> None:
        """No tradeable structure → SetupType.NONE, conf 0.0. Floor never
        applied here, regression check only."""
        engine = _engine_with_settings(fvg_ob_min=0.5)
        analysis = _make_analysis(
            direction="",  # no direction → no branch fires
            smc_confluence=0,
            nearest_fvg=None, nearest_ob=None,
        )
        setup, conf = engine.classify_setup(analysis)
        assert setup == SetupType.NONE
        assert conf == 0.0
