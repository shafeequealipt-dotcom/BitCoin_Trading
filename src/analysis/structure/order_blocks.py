"""X-RAY Phase 5: Order Block (OB) identification.

Identifies zones where institutions placed large orders — the last
opposing candle before a significant displacement move. OBs with FVG
and BOS confirmation are the highest-quality entry zones.

Pure numpy implementation.
"""

import numpy as np
from numpy.typing import NDArray

from src.analysis.structure.models.structure_types import (
    FairValueGap,
    MarketStructureResult,
    OrderBlock,
)
from src.config.settings import StructureSettings
from src.core.logging import get_logger

log = get_logger("xray")

FloatArray = NDArray[np.float64]


class OrderBlockDetector:
    """Identifies Order Blocks from OHLCV candle data.

    Algorithm:
      1. Find displacement candles (large body-to-range ratio)
      2. The OB is the last opposing candle before displacement
      3. Validate with FVG co-occurrence and BOS events
      4. Track retests and freshness
      5. Score each OB (0-100)

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
        fvgs: list[FairValueGap] | None = None,
        market_structure: MarketStructureResult | None = None,
    ) -> list[OrderBlock]:
        """Detect Order Blocks in candle data.

        Args:
            highs, lows, closes, opens: numpy arrays of OHLCV data.
            current_price: Current market price.
            fvgs: FVG list from Phase 4 (for co-occurrence validation).
            market_structure: Market structure from Phase 1 (for BOS validation).

        Returns:
            List of OrderBlock objects sorted by strength_score descending.
        """
        n = len(highs)
        if n < 10:
            return []

        fvgs = fvgs or []
        disp_min = self._settings.ob_displacement_min
        max_age = self._settings.ob_max_age_candles
        start_idx = max(1, n - max_age)

        order_blocks: list[OrderBlock] = []

        # Pre-compute FVG indices for quick lookup
        fvg_indices = {f.created_index for f in fvgs}

        # Check if BOS exists
        has_bos = (
            market_structure is not None
            and market_structure.last_bos is not None
        )

        for i in range(start_idx, n):
            # Check if this is a displacement candle
            candle_range = highs[i] - lows[i]
            if candle_range <= 0:
                continue
            body = abs(closes[i] - opens[i])
            body_ratio = body / candle_range

            if body_ratio < disp_min:
                continue

            is_bullish_displacement = closes[i] > opens[i]

            # Find the OB: last opposing candle before displacement
            ob_idx = None
            for j in range(i - 1, max(start_idx - 2, -1), -1):
                if is_bullish_displacement:
                    # Bullish displacement → OB is last bearish candle before
                    if closes[j] < opens[j]:
                        ob_idx = j
                        break
                else:
                    # Bearish displacement → OB is last bullish candle before
                    if closes[j] > opens[j]:
                        ob_idx = j
                        break

            if ob_idx is None:
                continue

            ob_high = float(highs[ob_idx])
            ob_low = float(lows[ob_idx])
            ob_mid = (ob_high + ob_low) / 2
            direction = "bullish" if is_bullish_displacement else "bearish"

            # Check FVG co-occurrence (within 2 candles of displacement)
            has_fvg = any(
                abs(f_idx - i) <= 2 for f_idx in fvg_indices
            )

            # Check BOS validation
            broke_structure = False
            if has_bos and market_structure and market_structure.last_bos:
                bos = market_structure.last_bos
                if bos.direction == direction:
                    broke_structure = True

            # Track retests: count candles after OB that entered the zone
            retests = 0
            for k in range(i + 1, n):
                # Price entered OB zone
                if lows[k] <= ob_high and highs[k] >= ob_low:
                    retests += 1
            fresh = retests == 0

            # Score (0-100)
            score = 40.0  # base
            score += 20.0 if has_fvg else 0.0
            score += 20.0 if broke_structure else 0.0
            score += 10.0 if fresh else 0.0
            score += min(10.0, body_ratio * 10.0)  # displacement strength bonus
            score = min(100.0, score)

            # Classify displacement strength
            if body_ratio >= 0.75:
                disp_label = "strong"
            elif body_ratio >= 0.5:
                disp_label = "moderate"
            else:
                disp_label = "weak"

            order_blocks.append(OrderBlock(
                direction=direction,
                high=round(ob_high, 8),
                low=round(ob_low, 8),
                midpoint=round(ob_mid, 8),
                created_index=ob_idx,
                created_at=float(ob_idx),
                retests=retests,
                fresh=fresh,
                displacement_strength=disp_label,
                displacement_ratio=round(body_ratio, 4),
                has_fvg=has_fvg,
                broke_structure=broke_structure,
                strength_score=round(score, 2),
            ))

        # Remove weak OBs and exhausted ones
        order_blocks = [
            ob for ob in order_blocks
            if ob.strength_score >= 40.0 and ob.retests < 3
        ]

        # Sort by strength descending, limit to top 10
        order_blocks.sort(key=lambda ob: ob.strength_score, reverse=True)
        order_blocks = order_blocks[:10]

        fresh_count = sum(1 for ob in order_blocks if ob.fresh)
        log.debug(
            f"XRAY_OB | total={len(order_blocks)} fresh={fresh_count} "
            f"fvg_confirmed={sum(1 for ob in order_blocks if ob.has_fvg)} "
            f"bos_confirmed={sum(1 for ob in order_blocks if ob.broke_structure)}"
        )

        return order_blocks
