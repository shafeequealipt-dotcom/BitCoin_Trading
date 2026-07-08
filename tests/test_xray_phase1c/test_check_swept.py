"""Phase 1c — `_check_swept` canonical sweep+reclaim semantic.

Verifies that ``LiquidityMapper._check_swept`` no longer marks zones swept
on stale historical wicks. The legacy implementation iterated the full
candle window (``range(n)``) and marked any zone with ANY wick beyond
its level as swept. Over a typical 200-candle window virtually every
level had been wicked at some point, leaving almost every zone pre-marked
swept. This collapsed both the +15 unswept-liquidity component and the
+30 active-sweep component of the SMC confluence formula to 0
universe-wide, capping setup_type_confidence at 0.55.

The Phase 1c fix bounds the scan to ``sweep_recency_bars`` (default 30)
and requires the canonical SMC sweep+reclaim pattern: a violation
(wick beyond level) followed by a later candle that closes back through
the level. Same-candle violation+reclaim is intentionally not caught
here so ``LiquidityMapper.detect_sweeps`` retains its single-candle
sweep detection responsibility (and the resulting ``LiquiditySweep``
record powers the +30 sweep component).

Test data uses baseline values that do NOT satisfy the reclaim
condition by accident:
  - Buy-side zone (level=100): baseline closes=100.5 (above level, not
    a reclaim candidate). Violations push highs above; reclaims push
    a specific bar below.
  - Sell-side zone (level=100): baseline closes=99.5 (below level, not
    a reclaim candidate). Violations push lows below; reclaims push a
    specific bar above.
"""

from __future__ import annotations

import numpy as np

from src.analysis.structure.liquidity import LiquidityMapper
from src.analysis.structure.models.structure_types import LiquidityZone
from src.config.settings import StructureSettings


def _mapper(
    sweep_recency_bars: int = 30,
    sweep_require_reclaim: bool = True,
) -> LiquidityMapper:
    settings = StructureSettings(
        sweep_recency_bars=sweep_recency_bars,
        sweep_require_reclaim=sweep_require_reclaim,
    )
    return LiquidityMapper(settings)


def _arrays(
    highs: list[float], lows: list[float], closes: list[float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.array(highs, dtype=np.float64),
        np.array(lows, dtype=np.float64),
        np.array(closes, dtype=np.float64),
    )


def _buy_side_baseline(n: int) -> tuple[list[float], list[float], list[float]]:
    """Buy-side baseline arrays: closes ABOVE level=100 so reclaim
    condition (closes < level) fires only when a test sets it
    explicitly."""
    return [99.0] * n, [98.0] * n, [100.5] * n


def _sell_side_baseline(n: int) -> tuple[list[float], list[float], list[float]]:
    """Sell-side baseline arrays: closes BELOW level=100 so reclaim
    condition (closes > level) fires only when a test sets it
    explicitly."""
    return [101.5] * n, [100.5] * n, [99.5] * n


# ─────────────────────────────────────────────────────────────────────────
# Recency window — stale violations no longer mark zones swept
# ─────────────────────────────────────────────────────────────────────────


class TestRecencyWindow:
    """Violations older than ``sweep_recency_bars`` are ignored."""

    def test_buy_side_stale_violation_unswept(self) -> None:
        """Wick above level 200 bars ago, no recent activity → unswept."""
        n = 200
        highs, lows, closes = _buy_side_baseline(n)
        # Violation 200 bars ago (index 0); reclaim bar 1 — both stale
        highs[0] = 102.0
        closes[1] = 98.0
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="buy_side", level=100.0)

        _mapper(sweep_recency_bars=30)._check_swept(zone, h, lo, c, n)

        assert zone.swept is False, (
            "Stale violation (200 bars ago) outside recency window must "
            "leave the zone unswept so the +15 liquidity component fires."
        )
        assert zone.reclaimed_at is None

    def test_sell_side_stale_violation_unswept(self) -> None:
        n = 200
        highs, lows, closes = _sell_side_baseline(n)
        lows[0] = 98.0
        closes[1] = 101.0
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="sell_side", level=100.0)

        _mapper(sweep_recency_bars=30)._check_swept(zone, h, lo, c, n)

        assert zone.swept is False
        assert zone.reclaimed_at is None

    def test_recent_violation_within_window_processed(self) -> None:
        """Violation 5 bars ago with reclaim 2 bars ago → swept."""
        n = 50
        highs, lows, closes = _buy_side_baseline(n)
        # Violation at index 45 (5 bars before end); close stays above
        highs[45] = 102.0
        closes[45] = 102.0
        # Reclaim at index 48 — only this bar drops below
        closes[48] = 98.5
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="buy_side", level=100.0)

        _mapper(sweep_recency_bars=30)._check_swept(zone, h, lo, c, n)

        assert zone.swept is True
        assert zone.swept_at == 45.0
        assert zone.reclaimed_at == 48.0


# ─────────────────────────────────────────────────────────────────────────
# Reclaim required — violation alone is not enough
# ─────────────────────────────────────────────────────────────────────────


class TestRequireReclaim:
    """When ``sweep_require_reclaim=True``, violation needs a later reclaim."""

    def test_buy_side_violation_no_reclaim_unswept(self) -> None:
        n = 30
        highs, lows, closes = _buy_side_baseline(n)
        # Violation at 5; subsequent bars still close above level
        highs[5] = 105.0
        closes[5] = 102.0  # not same-candle reclaim
        # Bars 6..n already at baseline 100.5 (above level) — no reclaim
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="buy_side", level=100.0)

        _mapper(sweep_recency_bars=30, sweep_require_reclaim=True)._check_swept(
            zone, h, lo, c, n,
        )

        assert zone.swept is False, (
            "Violation without later reclaim is an in-progress sweep and "
            "should leave the zone unswept under the canonical semantic."
        )
        assert zone.reclaimed_at is None

    def test_buy_side_violation_with_reclaim_swept(self) -> None:
        n = 30
        highs, lows, closes = _buy_side_baseline(n)
        highs[5] = 105.0
        closes[5] = 102.0  # not same-candle reclaim
        closes[10] = 98.5  # reclaim 5 bars later
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="buy_side", level=100.0)

        _mapper(sweep_recency_bars=30, sweep_require_reclaim=True)._check_swept(
            zone, h, lo, c, n,
        )

        assert zone.swept is True
        assert zone.swept_at == 5.0
        assert zone.reclaimed_at == 10.0

    def test_sell_side_mirror(self) -> None:
        n = 30
        highs, lows, closes = _sell_side_baseline(n)
        lows[8] = 95.0
        closes[8] = 98.0  # not same-candle reclaim
        closes[15] = 100.5  # reclaim
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="sell_side", level=100.0)

        _mapper(sweep_recency_bars=30, sweep_require_reclaim=True)._check_swept(
            zone, h, lo, c, n,
        )

        assert zone.swept is True
        assert zone.swept_at == 8.0
        assert zone.reclaimed_at == 15.0


# ─────────────────────────────────────────────────────────────────────────
# Same-candle reclaim — left for detect_sweeps
# ─────────────────────────────────────────────────────────────────────────


class TestSameCandlePattern:
    """Single-candle violation+reclaim must NOT be marked swept here.

    ``LiquidityMapper.detect_sweeps`` (line 222 onward) finds the
    single-candle pattern (wick + close back inside the same bar) and
    produces the ``LiquiditySweep`` record that powers the +30 active-
    sweep component. If ``_check_swept`` short-circuited this pattern
    by marking the zone swept, ``detect_sweeps`` would skip it at line
    219 and the sweep event would be lost. The two functions handle
    disjoint patterns.
    """

    def test_single_candle_buy_side_not_swept(self) -> None:
        n = 30
        highs, lows, closes = _buy_side_baseline(n)
        # Single-candle sweep at index 25 — wick above + close below in
        # the same bar. No multi-bar reclaim follows (baseline above).
        highs[25] = 105.0
        closes[25] = 98.5  # same-candle reclaim
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="buy_side", level=100.0)

        _mapper(sweep_recency_bars=30)._check_swept(zone, h, lo, c, n)

        assert zone.swept is False, (
            "Same-candle sweep+reclaim is detect_sweeps' responsibility; "
            "_check_swept must not pre-empt it."
        )

    def test_single_candle_sell_side_not_swept(self) -> None:
        n = 30
        highs, lows, closes = _sell_side_baseline(n)
        lows[25] = 95.0
        closes[25] = 101.0  # same-candle reclaim
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="sell_side", level=100.0)

        _mapper(sweep_recency_bars=30)._check_swept(zone, h, lo, c, n)

        assert zone.swept is False


# ─────────────────────────────────────────────────────────────────────────
# require_reclaim=False — wick-only fallback (still bounded)
# ─────────────────────────────────────────────────────────────────────────


class TestWickOnlyFallback:
    """When ``sweep_require_reclaim=False`` falls back to wick-only detection.

    Still an improvement over the legacy unbounded scan because the
    recency window applies — stale historical wicks are still ignored.
    """

    def test_recent_wick_marks_swept(self) -> None:
        n = 30
        highs, lows, closes = _buy_side_baseline(n)
        highs[20] = 105.0  # wick within window, no reclaim required
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="buy_side", level=100.0)

        _mapper(sweep_recency_bars=30, sweep_require_reclaim=False)._check_swept(
            zone, h, lo, c, n,
        )

        assert zone.swept is True
        assert zone.swept_at == 20.0
        assert zone.reclaimed_at is None

    def test_stale_wick_still_unswept_in_fallback(self) -> None:
        """Even with reclaim disabled, the recency window bounds the scan."""
        n = 200
        highs, lows, closes = _buy_side_baseline(n)
        highs[0] = 105.0  # stale wick outside window
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="buy_side", level=100.0)

        _mapper(sweep_recency_bars=30, sweep_require_reclaim=False)._check_swept(
            zone, h, lo, c, n,
        )

        assert zone.swept is False, (
            "Recency window must apply even in wick-only fallback mode."
        )


# ─────────────────────────────────────────────────────────────────────────
# Quiet zones — no violations at all → unswept
# ─────────────────────────────────────────────────────────────────────────


class TestQuietZone:
    """Zones with no violation activity stay unswept."""

    def test_buy_side_no_activity(self) -> None:
        n = 50
        highs, lows, closes = _buy_side_baseline(n)
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="buy_side", level=100.0)

        _mapper()._check_swept(zone, h, lo, c, n)

        assert zone.swept is False
        assert zone.reclaimed_at is None

    def test_sell_side_no_activity(self) -> None:
        n = 50
        highs, lows, closes = _sell_side_baseline(n)
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="sell_side", level=100.0)

        _mapper()._check_swept(zone, h, lo, c, n)

        assert zone.swept is False


# ─────────────────────────────────────────────────────────────────────────
# Multiple violations in window — first paired reclaim wins
# ─────────────────────────────────────────────────────────────────────────


class TestMultipleViolations:
    """When several violations exist, the earliest paired reclaim wins."""

    def test_first_violation_paired_with_first_later_reclaim(self) -> None:
        n = 30
        highs, lows, closes = _buy_side_baseline(n)
        # Two violations
        highs[10] = 105.0
        closes[10] = 102.0
        highs[20] = 106.0
        closes[20] = 103.0
        # Reclaim at 15 (between the two violations)
        closes[15] = 98.0
        h, lo, c = _arrays(highs, lows, closes)
        zone = LiquidityZone(zone_type="buy_side", level=100.0)

        _mapper(sweep_recency_bars=30)._check_swept(zone, h, lo, c, n)

        # First violation (10) pairs with first later reclaim (15)
        assert zone.swept is True
        assert zone.swept_at == 10.0
        assert zone.reclaimed_at == 15.0


# ─────────────────────────────────────────────────────────────────────────
# Integration — full detect_zones flow exercises the new path
# ─────────────────────────────────────────────────────────────────────────


class TestDetectZonesIntegration:
    """detect_zones runs _check_swept on each zone with closes passed."""

    def test_detect_zones_passes_closes_through(self) -> None:
        """Verify the detect_zones call path actually feeds closes in.

        Pre-fix the call signature was ``_check_swept(zone, highs, lows, n)``.
        Phase 1c extends it to ``_check_swept(zone, highs, lows, closes, n)``.
        Calling detect_zones with synthetic data shouldn't raise.
        """
        n = 60
        highs = np.linspace(99.5, 100.5, n)
        lows = np.linspace(98.5, 99.5, n)
        closes = np.linspace(99.0, 100.0, n)

        mapper = _mapper()
        zones = mapper.detect_zones(
            highs=highs,
            lows=lows,
            closes=closes,
            current_price=100.0,
            swing_highs=[],
            swing_lows=[],
        )

        assert isinstance(zones, list)
        for z in zones:
            assert hasattr(z, "swept")
            assert hasattr(z, "swept_at")
            assert hasattr(z, "reclaimed_at")


# ─────────────────────────────────────────────────────────────────────────
# Schema — LiquidityZone.to_dict round-trips reclaimed_at
# ─────────────────────────────────────────────────────────────────────────


class TestSchema:
    def test_unswept_zone_serializes_with_reclaimed_at_none(self) -> None:
        zone = LiquidityZone(zone_type="buy_side", level=100.0)
        d = zone.to_dict()

        assert d["swept"] is False
        assert d["swept_at"] == 0.0
        assert d["reclaimed_at"] is None

    def test_swept_zone_serializes_with_reclaimed_at_set(self) -> None:
        zone = LiquidityZone(
            zone_type="buy_side",
            level=100.0,
            swept=True,
            swept_at=5.0,
            reclaimed_at=10.0,
        )
        d = zone.to_dict()

        assert d["swept"] is True
        assert d["swept_at"] == 5.0
        assert d["reclaimed_at"] == 10.0
