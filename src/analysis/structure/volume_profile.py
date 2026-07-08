"""X-RAY Phase 3a: Volume Profile analysis.

Calculates volume-at-price distribution to identify price magnets (POC),
value areas, and air pockets (low volume nodes) where price moves fast.

Pure numpy implementation.
"""

import numpy as np
from numpy.typing import NDArray

from src.analysis.structure.models.structure_types import VolumeProfile
from src.core.utils import format_price
from src.config.settings import StructureSettings
from src.core.logging import get_logger

log = get_logger("xray")

FloatArray = NDArray[np.float64]

NUM_BINS = 50
VALUE_AREA_PCT = 70.0
HVN_THRESHOLD = 1.5  # volume > 1.5x average = High Volume Node
LVN_THRESHOLD = 0.3  # volume < 0.3x average = Low Volume Node


class VolumeProfileCalculator:
    """Calculates volume-at-price distribution from OHLCV data.

    Algorithm:
      1. Determine price range from highs/lows
      2. Create equal-width price bins
      3. Distribute each candle's volume across bins it spans
      4. POC = bin with highest volume
      5. Value Area = expand from POC until 70% of total volume
      6. Identify HVN (support/resistance) and LVN (air pockets)
      7. Classify current price position vs POC and VA

    Args:
        settings: StructureSettings configuration.
    """

    def __init__(self, settings: StructureSettings) -> None:
        self._settings = settings

    def calculate(
        self,
        candles: list,
        current_price: float,
    ) -> VolumeProfile | None:
        """Calculate volume profile from candle data.

        Args:
            candles: List of OHLCV objects with .high, .low, .volume.
            current_price: Current market price.

        Returns:
            VolumeProfile or None if insufficient data.
        """
        n = len(candles)
        if n < 20:
            return None

        highs = np.array([c.high for c in candles], dtype=np.float64)
        lows = np.array([c.low for c in candles], dtype=np.float64)
        volumes = np.array([getattr(c, 'volume', 1.0) for c in candles], dtype=np.float64)

        # Handle zero/missing volumes — return None, don't fabricate data
        if np.sum(volumes) <= 0:
            log.info(f"XRAY_VPOC_SKIP | reason=zero_volume candles={n}")
            return None

        price_min = float(np.min(lows))
        price_max = float(np.max(highs))
        price_range = price_max - price_min

        if price_range <= 0:
            return None

        num_bins = NUM_BINS
        bin_width = price_range / num_bins
        bin_volumes = np.zeros(num_bins)

        # Distribute volume across bins
        for i in range(n):
            candle_low = lows[i]
            candle_high = highs[i]
            candle_vol = volumes[i]
            if candle_vol <= 0 or candle_high <= candle_low:
                continue

            # Which bins does this candle span?
            low_bin = max(0, int((candle_low - price_min) / bin_width))
            high_bin = min(num_bins - 1, int((candle_high - price_min) / bin_width))
            num_spanned = high_bin - low_bin + 1

            if num_spanned > 0:
                vol_per_bin = candle_vol / num_spanned
                for b in range(low_bin, high_bin + 1):
                    bin_volumes[b] += vol_per_bin

        total_volume = float(np.sum(bin_volumes))
        if total_volume <= 0:
            return None

        # POC = bin with highest volume
        poc_bin = int(np.argmax(bin_volumes))
        poc_price = price_min + (poc_bin + 0.5) * bin_width
        poc_volume = float(bin_volumes[poc_bin])

        # Value Area: expand from POC until 70% of total
        va_volume = poc_volume
        va_low_bin = poc_bin
        va_high_bin = poc_bin
        target_volume = total_volume * (VALUE_AREA_PCT / 100.0)

        while va_volume < target_volume:
            # Compare adjacent bins, add the higher one
            add_low = bin_volumes[va_low_bin - 1] if va_low_bin > 0 else 0
            add_high = bin_volumes[va_high_bin + 1] if va_high_bin < num_bins - 1 else 0

            if add_low == 0 and add_high == 0:
                break
            if add_high >= add_low and va_high_bin < num_bins - 1:
                va_high_bin += 1
                va_volume += add_high
            elif va_low_bin > 0:
                va_low_bin -= 1
                va_volume += add_low
            else:
                va_high_bin += 1
                va_volume += add_high

        value_area_low = price_min + va_low_bin * bin_width
        value_area_high = price_min + (va_high_bin + 1) * bin_width

        # HVN and LVN
        avg_vol = total_volume / num_bins
        high_volume_nodes = []
        low_volume_nodes = []

        for b in range(num_bins):
            bin_center = price_min + (b + 0.5) * bin_width
            rel_vol = bin_volumes[b] / avg_vol if avg_vol > 0 else 0

            if rel_vol >= HVN_THRESHOLD:
                high_volume_nodes.append((round(bin_center, 2), round(rel_vol, 2)))
            elif rel_vol <= LVN_THRESHOLD and b > 0 and b < num_bins - 1:
                bin_start = price_min + b * bin_width
                bin_end = price_min + (b + 1) * bin_width
                low_volume_nodes.append((round(bin_start, 2), round(bin_end, 2)))

        # Classify current price position
        poc_dist_pct = abs(current_price - poc_price) / poc_price * 100 if poc_price > 0 else 0
        if poc_dist_pct < 0.2:
            current_vs_poc = "at_poc"
        elif current_price > poc_price:
            current_vs_poc = "above_poc"
        else:
            current_vs_poc = "below_poc"

        if current_price > value_area_high:
            current_vs_va = "above_va"
        elif current_price < value_area_low:
            current_vs_va = "below_va"
        else:
            current_vs_va = "inside_va"

        result = VolumeProfile(
            poc=round(poc_price, 8),
            poc_volume=round(poc_volume, 2),
            value_area_high=round(value_area_high, 8),
            value_area_low=round(value_area_low, 8),
            value_area_pct=VALUE_AREA_PCT,
            high_volume_nodes=high_volume_nodes,
            low_volume_nodes=low_volume_nodes,
            current_vs_poc=current_vs_poc,
            current_vs_value_area=current_vs_va,
            num_bins=num_bins,
        )

        log.debug(
            f"XRAY_VPOC | poc=${format_price(poc_price)} vah=${format_price(value_area_high)} "
            f"val=${format_price(value_area_low)} hvn={len(high_volume_nodes)} "
            f"lvn={len(low_volume_nodes)} vs_poc={current_vs_poc} "
            f"vs_va={current_vs_va}"
        )

        return result
