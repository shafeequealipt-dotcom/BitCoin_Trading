"""X-RAY Phase 2: Market Structure Detection (BOS/CHoCH).

Detects market structure patterns: Higher Highs (HH), Higher Lows (HL),
Lower Highs (LH), Lower Lows (LL), Break of Structure (BOS),
and Change of Character (CHoCH).

Reuses swing points from the S/R engine to avoid recomputation.
"""

import numpy as np
from numpy.typing import NDArray

from src.core.utils import format_price

from src.analysis.structure.models.structure_types import (
    MarketStructureResult,
    StructureEvent,
)
from src.config.settings import StructureSettings
from src.core.logging import get_logger

log = get_logger("xray")

FloatArray = NDArray[np.float64]


class MarketStructureDetector:
    """Detects market structure from swing point sequences.

    Uses swing highs/lows to classify structure as uptrend, downtrend,
    or ranging, and detects Break of Structure (BOS) and Change of
    Character (CHoCH) events.

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
        swing_data: dict | None = None,
    ) -> MarketStructureResult:
        """Detect market structure classification.

        Args:
            highs: numpy array of high prices.
            lows: numpy array of low prices.
            closes: numpy array of close prices.
            swing_data: Pre-computed swing points from S/R engine.
                Expected: {"swing_highs": [(idx, price), ...], "swing_lows": [(idx, price), ...]}

        Returns:
            MarketStructureResult with structure classification, BOS/CHoCH events.
        """
        n = len(closes)
        if n < 20:
            return MarketStructureResult()

        # Get swing points — reuse from S/R or compute with medium lookback
        if swing_data and swing_data.get("swing_highs") and swing_data.get("swing_lows"):
            swing_highs = sorted(swing_data["swing_highs"], key=lambda x: x[0])
            swing_lows = sorted(swing_data["swing_lows"], key=lambda x: x[0])
        else:
            # Fallback: compute using medium lookback
            lookback = self._settings.ms_swing_lookback
            swing_highs = self._find_swings(highs, lookback, find_highs=True)
            swing_lows = self._find_swings(lows, lookback, find_highs=False)

        min_points = self._settings.ms_min_swing_points

        # Need at least 2 swing points of at least one type to classify
        if len(swing_highs) < 2 and len(swing_lows) < 2:
            return MarketStructureResult(
                swing_highs=swing_highs,
                swing_lows=swing_lows,
                swing_count=len(swing_highs) + len(swing_lows),
            )

        # Use available swing points (up to min_points of each)
        recent_highs = swing_highs[-min_points:] if swing_highs else []
        recent_lows = swing_lows[-min_points:] if swing_lows else []

        # Classify structure
        structure, strength, swing_count = self._classify_structure(
            recent_highs, recent_lows, min_points,
        )

        # Detect BOS and CHoCH
        last_bos = self._detect_bos(
            closes, swing_highs, swing_lows, structure,
        )
        last_choch = self._detect_choch(
            closes, swing_highs, swing_lows, structure,
        )

        # Calculate invalidation level
        invalidation = self._calc_invalidation(
            swing_highs, swing_lows, structure,
        )

        result = MarketStructureResult(
            structure=structure,
            strength=strength,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
            last_bos=last_bos,
            last_choch=last_choch,
            invalidation_level=invalidation,
            swing_count=swing_count,
        )

        if last_bos:
            log.debug(
                f"XRAY_BOS | type={last_bos.direction} "
                f"level=${format_price(last_bos.price)}"
            )
        if last_choch:
            log.debug(
                f"XRAY_CHOCH | type={last_choch.direction} "
                f"level=${format_price(last_choch.price)}"
            )

        return result

    @staticmethod
    def _find_swings(
        data: FloatArray, lookback: int, find_highs: bool,
    ) -> list[tuple[int, float]]:
        """Find swing points using rolling comparison."""
        n = len(data)
        results = []
        for i in range(lookback, n - lookback):
            window = data[i - lookback: i + lookback + 1]
            if find_highs:
                if data[i] == np.max(window):
                    results.append((i, float(data[i])))
            else:
                if data[i] == np.min(window):
                    results.append((i, float(data[i])))
        return results

    @staticmethod
    def _classify_structure(
        recent_highs: list[tuple[int, float]],
        recent_lows: list[tuple[int, float]],
        min_points: int,
    ) -> tuple[str, str, int]:
        """Classify market structure from recent swing sequences.

        Returns:
            (structure, strength, confirming_swing_count) tuple.
        """
        # Count Higher Highs vs Lower Highs
        hh_count = 0
        lh_count = 0
        for i in range(1, len(recent_highs)):
            if recent_highs[i][1] > recent_highs[i - 1][1]:
                hh_count += 1
            elif recent_highs[i][1] < recent_highs[i - 1][1]:
                lh_count += 1

        # Count Higher Lows vs Lower Lows
        hl_count = 0
        ll_count = 0
        for i in range(1, len(recent_lows)):
            if recent_lows[i][1] > recent_lows[i - 1][1]:
                hl_count += 1
            elif recent_lows[i][1] < recent_lows[i - 1][1]:
                ll_count += 1

        total_bullish = hh_count + hl_count
        total_bearish = lh_count + ll_count
        swing_count = total_bullish + total_bearish

        # Classification
        # Ideal: both HH+HL for uptrend, both LH+LL for downtrend
        # Relaxed: if only one type is available (e.g. strong trend has few
        # counter-swings), still classify based on the dominant signal
        if (hh_count > 0 and hl_count > 0 and total_bullish > total_bearish):
            structure = "uptrend"
        elif (lh_count > 0 and ll_count > 0 and total_bearish > total_bullish):
            structure = "downtrend"
        elif hh_count > 0 and lh_count == 0 and total_bullish > 0:
            # Only higher highs, no lower highs — strong uptrend
            structure = "uptrend"
        elif ll_count > 0 and hl_count == 0 and total_bearish > 0:
            # Only lower lows, no higher lows — strong downtrend
            structure = "downtrend"
        else:
            structure = "ranging"

        # Strength assessment
        dominant = max(total_bullish, total_bearish)
        if dominant >= 4:
            strength = "strong"
        elif dominant >= 2:
            strength = "medium"
        else:
            strength = "weak"

        return structure, strength, swing_count

    @staticmethod
    def _detect_bos(
        closes: FloatArray,
        swing_highs: list[tuple[int, float]],
        swing_lows: list[tuple[int, float]],
        structure: str,
    ) -> StructureEvent | None:
        """Detect the most recent Break of Structure event.

        BOS: price breaks beyond the most recent swing high (bullish)
        or swing low (bearish), confirming trend continuation.
        """
        current_close = float(closes[-1])

        if structure == "uptrend" and swing_highs:
            # Bullish BOS: close above most recent swing high
            last_sh_price = swing_highs[-1][1]
            if current_close > last_sh_price:
                return StructureEvent(
                    event_type="bos",
                    direction="bullish",
                    price=last_sh_price,
                    timestamp=float(swing_highs[-1][0]),
                    significance="major" if current_close > last_sh_price * 1.001 else "minor",
                )

        elif structure == "downtrend" and swing_lows:
            # Bearish BOS: close below most recent swing low
            last_sl_price = swing_lows[-1][1]
            if current_close < last_sl_price:
                return StructureEvent(
                    event_type="bos",
                    direction="bearish",
                    price=last_sl_price,
                    timestamp=float(swing_lows[-1][0]),
                    significance="major" if current_close < last_sl_price * 0.999 else "minor",
                )

        return None

    @staticmethod
    def _detect_choch(
        closes: FloatArray,
        swing_highs: list[tuple[int, float]],
        swing_lows: list[tuple[int, float]],
        structure: str,
    ) -> StructureEvent | None:
        """Detect Change of Character — first sign of potential reversal.

        CHoCH in uptrend: close drops below the most recent swing low
        (breaks the Higher Low pattern).
        CHoCH in downtrend: close rises above the most recent swing high
        (breaks the Lower High pattern).
        """
        current_close = float(closes[-1])

        if structure == "uptrend" and swing_lows:
            # Bearish CHoCH: close below the most recent swing low
            last_sl_price = swing_lows[-1][1]
            if current_close < last_sl_price:
                return StructureEvent(
                    event_type="choch",
                    direction="bearish",
                    price=last_sl_price,
                    timestamp=float(swing_lows[-1][0]),
                    significance="major",
                )

        elif structure == "downtrend" and swing_highs:
            # Bullish CHoCH: close above the most recent swing high
            last_sh_price = swing_highs[-1][1]
            if current_close > last_sh_price:
                return StructureEvent(
                    event_type="choch",
                    direction="bullish",
                    price=last_sh_price,
                    timestamp=float(swing_highs[-1][0]),
                    significance="major",
                )

        return None

    @staticmethod
    def _calc_invalidation(
        swing_highs: list[tuple[int, float]],
        swing_lows: list[tuple[int, float]],
        structure: str,
    ) -> float:
        """Calculate the invalidation level where current structure breaks.

        Uptrend: most recent swing low (Higher Low).
        Downtrend: most recent swing high (Lower High).
        Ranging: lowest swing low in range.
        """
        if structure == "uptrend" and swing_lows:
            return swing_lows[-1][1]
        elif structure == "downtrend" and swing_highs:
            return swing_highs[-1][1]
        elif swing_lows:
            return min(p for _, p in swing_lows)
        return 0.0
