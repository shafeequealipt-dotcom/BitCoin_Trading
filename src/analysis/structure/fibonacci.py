"""X-RAY Phase 3b: Fibonacci Retracement & Extensions.

Calculates Fibonacci retracement levels (23.6%, 38.2%, 50%, 61.8%, 78.6%)
and extension targets (127.2%, 161.8%, 200%) from the most significant
recent swing. Auto-detects confluence with Phase 1 S/R and Phase 2 OBs.

Pure numpy implementation.
"""

import numpy as np

from src.analysis.structure.models.structure_types import (
    FibSwing,
    OrderBlock,
    PriceLevel,
)
from src.config.settings import StructureSettings
from src.core.logging import get_logger

log = get_logger("xray")

# Standard Fibonacci ratios
RETRACE_RATIOS = {"0.236": 0.236, "0.382": 0.382, "0.500": 0.500, "0.618": 0.618, "0.786": 0.786}
EXTEND_RATIOS = {"1.000": 1.000, "1.272": 1.272, "1.618": 1.618, "2.000": 2.000}

# Confluence detection tolerance
CONFLUENCE_TOLERANCE_PCT = 0.5  # within 0.5% of price

# Minimum swing range as % of price
MIN_SWING_PCT = 2.0


class FibonacciCalculator:
    """Calculates Fibonacci levels from the most significant recent swing.

    Algorithm:
      1. Find most significant recent swing (>= 2% range)
      2. Calculate retracement levels
      3. Calculate extension levels
      4. Detect confluence with S/R and OBs
      5. Set key_level = level with most confluence

    Args:
        settings: StructureSettings configuration.
    """

    def __init__(self, settings: StructureSettings) -> None:
        self._settings = settings

    def calculate(
        self,
        candles: list,
        swing_highs: list[tuple[int, float]],
        swing_lows: list[tuple[int, float]],
        supports: list[PriceLevel],
        resistances: list[PriceLevel],
        order_blocks: list[OrderBlock],
        current_price: float,
    ) -> FibSwing | None:
        """Calculate Fibonacci levels for the most significant recent swing.

        Args:
            candles: OHLCV candle list.
            swing_highs: (index, price) tuples from Phase 1.
            swing_lows: (index, price) tuples from Phase 1.
            supports: Phase 1 support levels.
            resistances: Phase 1 resistance levels.
            order_blocks: Phase 2 order blocks.
            current_price: Current market price.

        Returns:
            FibSwing with retracement/extension levels, or None.
        """
        if not swing_highs or not swing_lows:
            return None

        # Find most significant recent swing
        swing_low_idx, swing_low_price = self._find_significant_low(swing_lows)
        swing_high_idx, swing_high_price = self._find_significant_high(swing_highs)

        if swing_low_price <= 0 or swing_high_price <= 0:
            return None

        swing_range = abs(swing_high_price - swing_low_price)
        swing_range_pct = swing_range / min(swing_low_price, swing_high_price) * 100

        if swing_range_pct < MIN_SWING_PCT:
            return None

        # Determine direction based on which came last
        if swing_high_idx > swing_low_idx:
            direction = "up"  # price went from low to high
        else:
            direction = "down"  # price went from high to low

        # Calculate retracement levels
        retracement_levels = {}
        for name, ratio in RETRACE_RATIOS.items():
            if direction == "up":
                level = swing_high_price - (swing_range * ratio)
            else:
                level = swing_low_price + (swing_range * ratio)
            retracement_levels[name] = round(level, 8)

        # Calculate extension levels
        extension_levels = {}
        for name, ratio in EXTEND_RATIOS.items():
            if direction == "up":
                # Extensions project above the high
                level = swing_low_price + (swing_range * ratio)
            else:
                # Extensions project below the low
                level = swing_high_price - (swing_range * ratio)
            extension_levels[name] = round(level, 8)

        # Detect confluence: which Fib level aligns with S/R or OBs?
        key_level = None
        confluence_with = None
        confluence_level = None
        best_confluence_count = 0

        from src.core.utils import format_price
        all_structural_prices = []
        for s in supports:
            all_structural_prices.append((s.price, f"support_${format_price(s.price)}"))
        for r in resistances:
            all_structural_prices.append((r.price, f"resistance_${format_price(r.price)}"))
        for ob in order_blocks:
            all_structural_prices.append((ob.midpoint, f"OB_${format_price(ob.midpoint)}"))

        for fib_name, fib_price in retracement_levels.items():
            confluence_count = 0
            confluence_parts = []

            for struct_price, struct_label in all_structural_prices:
                if struct_price <= 0 or fib_price <= 0:
                    continue
                dist_pct = abs(fib_price - struct_price) / fib_price * 100
                if dist_pct < CONFLUENCE_TOLERANCE_PCT:
                    confluence_count += 1
                    confluence_parts.append(struct_label)

            if confluence_count > best_confluence_count:
                best_confluence_count = confluence_count
                key_level = fib_price
                confluence_with = " + ".join(confluence_parts[:3])
                confluence_level = fib_price

        # If no confluence found, default to 61.8% (golden ratio)
        if key_level is None and "0.618" in retracement_levels:
            key_level = retracement_levels["0.618"]

        result = FibSwing(
            swing_low=round(swing_low_price, 8),
            swing_high=round(swing_high_price, 8),
            swing_direction=direction,
            swing_range=round(swing_range, 8),
            swing_range_pct=round(swing_range_pct, 2),
            retracement_levels=retracement_levels,
            extension_levels=extension_levels,
            key_level=round(key_level, 8) if key_level else None,
            confluence_with=confluence_with,
            confluence_level=round(confluence_level, 8) if confluence_level else None,
        )

        confl_tag = f"confluence={confluence_with}" if confluence_with else "no_confluence"
        log.debug(
            f"XRAY_FIB | swing=${format_price(swing_low_price)}→${format_price(swing_high_price)}"
            f"({direction}) range={swing_range_pct:.1f}% "
            f"key={f'${format_price(key_level)}' if key_level else 'none'} {confl_tag}"
        )

        return result

    @staticmethod
    def _find_significant_low(swing_lows: list[tuple[int, float]]) -> tuple[int, float]:
        """Find the most recent significant swing low."""
        if not swing_lows:
            return (0, 0.0)
        # Use the lowest of the last 5 swing lows
        recent = swing_lows[-5:]
        lowest = min(recent, key=lambda x: x[1])
        return lowest

    @staticmethod
    def _find_significant_high(swing_highs: list[tuple[int, float]]) -> tuple[int, float]:
        """Find the most recent significant swing high."""
        if not swing_highs:
            return (0, 0.0)
        recent = swing_highs[-5:]
        highest = max(recent, key=lambda x: x[1])
        return highest
