"""ATR-scaled distance windows for ``_find_nearest_fvg`` / ``_find_nearest_ob``.

XRAY counter-setup Phase 2. Verifies that the proximity windows scale by
per-coin volatility (atr_pct_h1) with a fixed-percent floor, replacing
the pre-fix 2.0%/3.0% hardcoded windows.

The behavior matrix:

| Coin profile          | atr_pct | FVG window (mult=3) | OB window (mult=4) |
|-----------------------|---------|---------------------|--------------------|
| Very low vol (BTC)    | 0.40%   | floor 2.00%         | floor 3.00%        |
| Mid vol (LINK)        | 0.50%   | floor 2.00%         | floor 3.00%        |
| Borderline (ETH)      | 0.67%   | 3 * 0.67 = 2.01%    | floor 3.00%        |
| High vol (DOGE)       | 0.98%   | 3 * 0.98 = 2.94%    | floor 3.00%        |
| Very high vol (DYDX)  | 1.30%   | 3 * 1.30 = 3.90%    | 4 * 1.30 = 5.20%   |

The probe baseline (cycle 22:00 on 2026-04-30) confirmed BTC=0.42%,
LINK=0.49%, DOGE=0.98%, DYDX=1.30%, AAVE=0.77% so these multipliers
land in the calibration sweet spot for the live universe.
"""

from __future__ import annotations

import pytest

from src.analysis.structure.models.structure_types import FairValueGap, OrderBlock
from src.analysis.structure.structure_engine import StructureEngine
from src.config.settings import SetupTypesSettings


# ──────────────────────────────────────────────────────────────────────────
# _compute_h1_natr_pct — pure ATR computation
# ──────────────────────────────────────────────────────────────────────────


class TestComputeH1NATRPct:
    def test_returns_zero_when_too_few_candles(self) -> None:
        import numpy as np

        # lookback default 14 → need 15 candles minimum.
        highs = np.array([100.0] * 14, dtype=np.float64)
        lows = np.array([99.0] * 14, dtype=np.float64)
        closes = np.array([99.5] * 14, dtype=np.float64)
        assert StructureEngine._compute_h1_natr_pct(highs, lows, closes) == 0.0

    def test_returns_atr_pct_for_simple_series(self) -> None:
        import numpy as np

        # 20 candles with constant TR = 1.0 (range $99-$100, prev close $99.5).
        # Mean TR = 1.0, last close = 99.5 → atr_pct ≈ 1.005%.
        n = 20
        highs = np.array([100.0] * n, dtype=np.float64)
        lows = np.array([99.0] * n, dtype=np.float64)
        closes = np.array([99.5] * n, dtype=np.float64)
        atr_pct = StructureEngine._compute_h1_natr_pct(highs, lows, closes)
        assert pytest.approx(atr_pct, abs=0.01) == 1.005

    def test_returns_zero_when_last_close_zero(self) -> None:
        import numpy as np

        n = 20
        highs = np.array([100.0] * n, dtype=np.float64)
        lows = np.array([99.0] * n, dtype=np.float64)
        closes = np.array([99.5] * (n - 1) + [0.0], dtype=np.float64)
        assert StructureEngine._compute_h1_natr_pct(highs, lows, closes) == 0.0


# ──────────────────────────────────────────────────────────────────────────
# Distance window scaling
# ──────────────────────────────────────────────────────────────────────────


def _cfg(**overrides) -> SetupTypesSettings:
    base = dict(
        fvg_atr_multiplier=3.0,
        ob_atr_multiplier=4.0,
        fvg_min_distance_pct=2.0,
        ob_min_distance_pct=3.0,
    )
    base.update(overrides)
    return SetupTypesSettings(**base)


def _bull_fvg_at_pct(price: float, dist_pct: float) -> FairValueGap:
    """Make a bullish unfilled FVG at ``dist_pct`` above ``price``."""
    midpoint = price * (1.0 + dist_pct / 100.0)
    return FairValueGap(direction="bullish", filled=False, midpoint=midpoint)


def _bull_ob_at_pct(price: float, dist_pct: float) -> OrderBlock:
    midpoint = price * (1.0 + dist_pct / 100.0)
    return OrderBlock(direction="bullish", fresh=True, midpoint=midpoint)


class TestFVGAtrWindow:
    """Phase 2 ATR window tests, updated for the Phase 3 result-dataclass contract.

    The finders return ``NearestFVGResult`` carrying both ``in_direction``
    and ``counter_direction`` slots. These tests assert on
    ``result.in_direction`` to verify the in-direction selection logic
    works the same way as Phase 2 did. Phase 3-specific tests live in
    ``tests/test_structure_engine_nearest_finders.py``.
    """

    def test_low_vol_uses_floor(self) -> None:
        # atr=0.4 → 3*0.4=1.2 vs floor 2.0 → window=2.0
        cfg = _cfg()
        # FVG at 1.5% — within 2% floor → found.
        fvgs = [_bull_fvg_at_pct(100.0, 1.5)]
        result = StructureEngine._find_nearest_fvg(fvgs, 100.0, "long", 0.4, cfg)
        assert result.in_direction is not None
        # FVG at 2.5% — outside 2% floor → not found.
        fvgs2 = [_bull_fvg_at_pct(100.0, 2.5)]
        result2 = StructureEngine._find_nearest_fvg(fvgs2, 100.0, "long", 0.4, cfg)
        assert result2.in_direction is None

    def test_mid_vol_borderline(self) -> None:
        # atr=0.7 → 3*0.7=2.1 vs floor 2.0 → window=2.1
        cfg = _cfg()
        fvgs = [_bull_fvg_at_pct(100.0, 2.05)]
        assert StructureEngine._find_nearest_fvg(fvgs, 100.0, "long", 0.7, cfg).in_direction is not None

    def test_high_vol_expands_beyond_floor(self) -> None:
        # atr=1.3 → 3*1.3=3.9 → window=3.9
        cfg = _cfg()
        # FVG at 3.5% — would have been REJECTED by old fixed 2% window,
        # now WITHIN the ATR-scaled window → found.
        fvgs = [_bull_fvg_at_pct(100.0, 3.5)]
        result = StructureEngine._find_nearest_fvg(fvgs, 100.0, "long", 1.3, cfg)
        assert result.in_direction is not None
        # 4.0% — just outside.
        fvgs2 = [_bull_fvg_at_pct(100.0, 4.0)]
        assert StructureEngine._find_nearest_fvg(fvgs2, 100.0, "long", 1.3, cfg).in_direction is None

    def test_zero_atr_falls_back_to_floor(self) -> None:
        cfg = _cfg()
        fvgs = [_bull_fvg_at_pct(100.0, 1.5)]
        assert StructureEngine._find_nearest_fvg(fvgs, 100.0, "long", 0.0, cfg).in_direction is not None

    def test_no_cfg_uses_legacy_2pct_window(self) -> None:
        # Backward-compat for callers that don't have setup_types config.
        fvgs = [_bull_fvg_at_pct(100.0, 1.5)]
        assert StructureEngine._find_nearest_fvg(fvgs, 100.0, "long", 1.3, None).in_direction is not None
        fvgs2 = [_bull_fvg_at_pct(100.0, 2.5)]
        assert StructureEngine._find_nearest_fvg(fvgs2, 100.0, "long", 1.3, None).in_direction is None

    def test_direction_filter_still_applied(self) -> None:
        # Mismatched direction zone now lands in counter_direction slot,
        # not in_direction. This is the Phase 3 contract change — Phase 2
        # behavior was to discard entirely.
        cfg = _cfg()
        bear_fvg = FairValueGap(
            direction="bearish", filled=False, midpoint=100.0 * 0.985,
        )
        result = StructureEngine._find_nearest_fvg([bear_fvg], 100.0, "long", 0.5, cfg)
        assert result.in_direction is None
        assert result.counter_direction is bear_fvg

    def test_filled_fvg_skipped(self) -> None:
        cfg = _cfg()
        bull_filled = FairValueGap(
            direction="bullish", filled=True, midpoint=100.0 * 1.01,
        )
        bull_unfilled = FairValueGap(
            direction="bullish", filled=False, midpoint=100.0 * 1.015,
        )
        result = StructureEngine._find_nearest_fvg(
            [bull_filled, bull_unfilled], 100.0, "long", 0.5, cfg,
        )
        assert result.in_direction is bull_unfilled


class TestOBAtrWindow:
    def test_low_vol_uses_3pct_floor(self) -> None:
        cfg = _cfg()
        obs = [_bull_ob_at_pct(100.0, 2.5)]
        assert StructureEngine._find_nearest_ob(obs, 100.0, "long", 0.4, cfg).in_direction is not None
        obs2 = [_bull_ob_at_pct(100.0, 3.5)]
        assert StructureEngine._find_nearest_ob(obs2, 100.0, "long", 0.4, cfg).in_direction is None

    def test_high_vol_expands_to_4_atr(self) -> None:
        # atr=1.3 → 4*1.3=5.2 → window=5.2
        cfg = _cfg()
        obs = [_bull_ob_at_pct(100.0, 4.5)]
        assert StructureEngine._find_nearest_ob(obs, 100.0, "long", 1.3, cfg).in_direction is not None
        obs2 = [_bull_ob_at_pct(100.0, 5.3)]
        assert StructureEngine._find_nearest_ob(obs2, 100.0, "long", 1.3, cfg).in_direction is None

    def test_stale_ob_skipped(self) -> None:
        cfg = _cfg()
        stale_ob = OrderBlock(direction="bullish", fresh=False, midpoint=100.0 * 1.02)
        fresh_ob = OrderBlock(direction="bullish", fresh=True, midpoint=100.0 * 1.025)
        result = StructureEngine._find_nearest_ob(
            [stale_ob, fresh_ob], 100.0, "long", 0.4, cfg,
        )
        assert result.in_direction is fresh_ob


# ──────────────────────────────────────────────────────────────────────────
# Settings validation
# ──────────────────────────────────────────────────────────────────────────


class TestSetupTypesAtrSettings:
    def test_atr_multiplier_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="fvg_atr_multiplier"):
            SetupTypesSettings(fvg_atr_multiplier=0.0)
        with pytest.raises(ValueError, match="ob_atr_multiplier"):
            SetupTypesSettings(ob_atr_multiplier=-1.0)

    def test_min_distance_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="fvg_min_distance_pct"):
            SetupTypesSettings(fvg_min_distance_pct=0.0)
        with pytest.raises(ValueError, match="ob_min_distance_pct"):
            SetupTypesSettings(ob_min_distance_pct=-0.5)

    def test_defaults_match_phase2_target(self) -> None:
        s = SetupTypesSettings()
        assert s.fvg_atr_multiplier == 3.0
        assert s.ob_atr_multiplier == 4.0
        assert s.fvg_min_distance_pct == 2.0
        assert s.ob_min_distance_pct == 3.0
