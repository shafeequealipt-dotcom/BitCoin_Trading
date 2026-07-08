"""X-RAY Phase 4: Fair Value Gap (FVG) detection.

Identifies price imbalance zones where the middle candle in a 3-candle
sequence moved so aggressively that a gap exists between the first and
third candles. Unfilled FVGs act as magnets — price tends to return to
fill them, creating high-probability entry zones.

Pure numpy implementation.
"""

import numpy as np
from numpy.typing import NDArray

from src.analysis.structure.models.structure_types import FairValueGap
from src.config.settings import StructureSettings
from src.core.logging import get_logger

log = get_logger("xray")

FloatArray = NDArray[np.float64]


def _classify_displacement(ratio: float) -> str:
    """Classify displacement body-to-range ratio into semantic strength."""
    if ratio >= 0.75:
        return "strong"
    elif ratio >= 0.5:
        return "moderate"
    return "weak"


class FairValueGapDetector:
    """Detects Fair Value Gaps from OHLCV candle data.

    Algorithm:
      1. Scan 3-candle windows looking for gaps
      2. Bullish FVG: candle3.low > candle1.high (gap above)
      3. Bearish FVG: candle3.high < candle1.low (gap below)
      4. Track fill status from subsequent candles
      5. Filter by minimum gap size and recency

    Args:
        settings: StructureSettings configuration.
    """

    def __init__(self, settings: StructureSettings) -> None:
        self._settings = settings

    def detect(
        self,
        highs: FloatArray,
        lows: FloatArray,
        closes: FloatArray,
        opens: FloatArray,
        current_price: float,
    ) -> list[FairValueGap]:
        """Detect Fair Value Gaps in candle data.

        Args:
            highs: numpy array of high prices.
            lows: numpy array of low prices.
            closes: numpy array of close prices.
            opens: numpy array of open prices.
            current_price: Current market price.

        Returns:
            List of FairValueGap objects sorted by created_index descending.
        """
        n = len(highs)
        if n < 10:
            return []

        min_gap_pct = self._settings.fvg_min_gap_pct / 100.0
        max_age = self._settings.fvg_max_age_candles
        start_idx = max(2, n - max_age)

        fvgs: list[FairValueGap] = []

        for i in range(start_idx, n - 1):
            # 3-candle window: candle at i-1, i, i+1
            c1_high = highs[i - 1]
            c1_low = lows[i - 1]
            c2_open = opens[i]
            c2_close = closes[i]
            c2_high = highs[i]
            c2_low = lows[i]
            c3_high = highs[i + 1] if i + 1 < n else highs[i]
            c3_low = lows[i + 1] if i + 1 < n else lows[i]

            # Bullish FVG: gap between candle1.high and candle3.low
            if c3_low > c1_high:
                gap_bottom = c1_high
                gap_top = c3_low
                midpoint = (gap_top + gap_bottom) / 2
                gap_size_pct = (gap_top - gap_bottom) / midpoint if midpoint > 0 else 0

                if gap_size_pct < min_gap_pct:
                    continue

                # Displacement strength: body-to-range ratio of middle candle
                c2_range = c2_high - c2_low
                c2_body = abs(c2_close - c2_open)
                disp_ratio = c2_body / c2_range if c2_range > 0 else 0
                disp_label = _classify_displacement(disp_ratio)

                # Check fill status from subsequent candles
                filled, partially_filled, fill_pct = self._check_fill(
                    lows, i + 2, n, gap_bottom, gap_top, midpoint, is_bullish=True,
                )

                fvgs.append(FairValueGap(
                    direction="bullish",
                    top=round(gap_top, 8),
                    bottom=round(gap_bottom, 8),
                    midpoint=round(midpoint, 8),
                    created_index=i,
                    created_at=float(i),
                    filled=filled,
                    partially_filled=partially_filled,
                    fill_percentage=fill_pct,
                    gap_size_pct=round(gap_size_pct * 100, 4),
                    displacement_strength=disp_label,
                    displacement_ratio=round(disp_ratio, 4),
                ))

            # Bearish FVG: gap between candle1.low and candle3.high
            if c3_high < c1_low:
                gap_top = c1_low
                gap_bottom = c3_high
                midpoint = (gap_top + gap_bottom) / 2
                gap_size_pct = (gap_top - gap_bottom) / midpoint if midpoint > 0 else 0

                if gap_size_pct < min_gap_pct:
                    continue

                c2_range = c2_high - c2_low
                c2_body = abs(c2_close - c2_open)
                disp_ratio = c2_body / c2_range if c2_range > 0 else 0
                disp_label = _classify_displacement(disp_ratio)

                filled, partially_filled, fill_pct = self._check_fill(
                    highs, i + 2, n, gap_bottom, gap_top, midpoint, is_bullish=False,
                )

                fvgs.append(FairValueGap(
                    direction="bearish",
                    top=round(gap_top, 8),
                    bottom=round(gap_bottom, 8),
                    midpoint=round(midpoint, 8),
                    created_index=i,
                    created_at=float(i),
                    filled=filled,
                    partially_filled=partially_filled,
                    fill_percentage=fill_pct,
                    gap_size_pct=round(gap_size_pct * 100, 4),
                    displacement_strength=disp_label,
                    displacement_ratio=round(disp_ratio, 4),
                ))

        # Sort by created_index descending (most recent first)
        fvgs.sort(key=lambda f: f.created_index, reverse=True)

        active = [f for f in fvgs if not f.filled]
        log.debug(
            f"XRAY_FVG | detected={len(fvgs)} active={len(active)} "
            f"filled={len(fvgs) - len(active)}"
        )

        return fvgs

    @staticmethod
    def _check_fill(
        price_data: FloatArray,
        start: int,
        end: int,
        gap_bottom: float,
        gap_top: float,
        midpoint: float,
        is_bullish: bool,
    ) -> tuple[bool, bool, float]:
        """Check if a FVG has been filled by subsequent candles.

        For bullish FVG: filled when a candle's low goes below gap_bottom.
        For bearish FVG: filled when a candle's high goes above gap_top.

        Returns:
            (filled, partially_filled, fill_percentage) tuple.
        """
        gap_range = gap_top - gap_bottom
        if gap_range <= 0 or start >= end:
            return False, False, 0.0

        max_penetration = 0.0

        for j in range(start, end):
            if is_bullish:
                # Bullish FVG fills when price drops into the gap
                if price_data[j] <= gap_bottom:
                    return True, True, 1.0
                if price_data[j] < gap_top:
                    penetration = (gap_top - price_data[j]) / gap_range
                    max_penetration = max(max_penetration, penetration)
            else:
                # Bearish FVG fills when price rises into the gap
                if price_data[j] >= gap_top:
                    return True, True, 1.0
                if price_data[j] > gap_bottom:
                    penetration = (price_data[j] - gap_bottom) / gap_range
                    max_penetration = max(max_penetration, penetration)

        partially = max_penetration > 0.3
        return False, partially, round(max_penetration, 4)
