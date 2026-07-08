"""Chart pattern detection: double top/bottom, head & shoulders, triangles.

Requires 20-100+ candles. Uses peak/trough detection with numpy.
"""

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


class ChartPatternDetector:
    """Detects multi-candle chart patterns using peak/trough analysis.

    Args:
        min_pattern_bars: Minimum bars required for pattern formation.
    """

    def __init__(self, min_pattern_bars: int = 20) -> None:
        self.min_pattern_bars = min_pattern_bars

    def detect_all(
        self,
        highs: FloatArray,
        lows: FloatArray,
        closes: FloatArray,
    ) -> list[dict]:
        """Run all chart pattern detectors.

        Args:
            highs: High prices.
            lows: Low prices.
            closes: Close prices.

        Returns:
            List of detected patterns with name, type, confidence, start/end index.
        """
        if len(closes) < self.min_pattern_bars:
            return []

        patterns: list[dict] = []

        for detector, name, ptype in [
            (self._double_top, "double_top", "bearish"),
            (self._double_bottom, "double_bottom", "bullish"),
            (self._head_and_shoulders, "head_and_shoulders", "bearish"),
            (self._inverse_head_and_shoulders, "inverse_head_and_shoulders", "bullish"),
            (self._ascending_triangle, "ascending_triangle", "bullish"),
            (self._descending_triangle, "descending_triangle", "bearish"),
        ]:
            result = detector(highs, lows, closes)
            if result is not None:
                result["name"] = name
                result["type"] = ptype
                patterns.append(result)

        return patterns

    # --- Pattern detectors ---

    def _double_top(self, highs: FloatArray, lows: FloatArray, closes: FloatArray) -> dict | None:
        """Detect double top: two peaks at similar price with trough between."""
        peaks = self._find_local_maxima(highs, order=5)
        if len(peaks) < 2:
            return None

        # Check last two peaks
        for i in range(len(peaks) - 1, 0, -1):
            p1, p2 = peaks[i - 1], peaks[i]
            if p2 - p1 < 10:
                continue
            if self._is_near(highs[p1], highs[p2], 2.0):
                trough = np.min(lows[p1:p2 + 1])
                if trough < min(highs[p1], highs[p2]) * 0.97:
                    return {
                        "confidence": 0.7,
                        "start_index": int(p1),
                        "end_index": int(p2),
                    }
        return None

    def _double_bottom(self, highs: FloatArray, lows: FloatArray, closes: FloatArray) -> dict | None:
        """Detect double bottom: two troughs at similar price with peak between."""
        troughs = self._find_local_minima(lows, order=5)
        if len(troughs) < 2:
            return None

        for i in range(len(troughs) - 1, 0, -1):
            t1, t2 = troughs[i - 1], troughs[i]
            if t2 - t1 < 10:
                continue
            if self._is_near(lows[t1], lows[t2], 2.0):
                peak = np.max(highs[t1:t2 + 1])
                if peak > max(lows[t1], lows[t2]) * 1.03:
                    return {
                        "confidence": 0.7,
                        "start_index": int(t1),
                        "end_index": int(t2),
                    }
        return None

    def _head_and_shoulders(self, highs: FloatArray, lows: FloatArray, closes: FloatArray) -> dict | None:
        """Detect head and shoulders: left shoulder, head (higher), right shoulder."""
        peaks = self._find_local_maxima(highs, order=5)
        if len(peaks) < 3:
            return None

        for i in range(len(peaks) - 2):
            left, head, right = peaks[i], peaks[i + 1], peaks[i + 2]
            if highs[head] > highs[left] and highs[head] > highs[right]:
                if self._is_near(highs[left], highs[right], 5.0):
                    if head - left >= 5 and right - head >= 5:
                        return {
                            "confidence": 0.65,
                            "start_index": int(left),
                            "end_index": int(right),
                        }
        return None

    def _inverse_head_and_shoulders(self, highs: FloatArray, lows: FloatArray, closes: FloatArray) -> dict | None:
        """Detect inverse head and shoulders: three troughs with middle lowest."""
        troughs = self._find_local_minima(lows, order=5)
        if len(troughs) < 3:
            return None

        for i in range(len(troughs) - 2):
            left, head, right = troughs[i], troughs[i + 1], troughs[i + 2]
            if lows[head] < lows[left] and lows[head] < lows[right]:
                if self._is_near(lows[left], lows[right], 5.0):
                    if head - left >= 5 and right - head >= 5:
                        return {
                            "confidence": 0.65,
                            "start_index": int(left),
                            "end_index": int(right),
                        }
        return None

    def _ascending_triangle(self, highs: FloatArray, lows: FloatArray, closes: FloatArray) -> dict | None:
        """Detect ascending triangle: flat resistance + rising support (higher lows)."""
        n = len(closes)
        window = min(n, 50)
        recent_highs = highs[-window:]
        recent_lows = lows[-window:]

        peaks = self._find_local_maxima(recent_highs, order=3)
        troughs = self._find_local_minima(recent_lows, order=3)

        if len(peaks) < 2 or len(troughs) < 2:
            return None

        peak_vals = recent_highs[peaks]
        trough_vals = recent_lows[troughs]

        if self._is_flat(peak_vals, 2.0) and self._is_trending_up(trough_vals):
            return {
                "confidence": 0.6,
                "start_index": int(n - window),
                "end_index": int(n - 1),
            }
        return None

    def _descending_triangle(self, highs: FloatArray, lows: FloatArray, closes: FloatArray) -> dict | None:
        """Detect descending triangle: flat support + falling resistance (lower highs)."""
        n = len(closes)
        window = min(n, 50)
        recent_highs = highs[-window:]
        recent_lows = lows[-window:]

        peaks = self._find_local_maxima(recent_highs, order=3)
        troughs = self._find_local_minima(recent_lows, order=3)

        if len(peaks) < 2 or len(troughs) < 2:
            return None

        peak_vals = recent_highs[peaks]
        trough_vals = recent_lows[troughs]

        if self._is_flat(trough_vals, 2.0) and self._is_trending_down(peak_vals):
            return {
                "confidence": 0.6,
                "start_index": int(n - window),
                "end_index": int(n - 1),
            }
        return None

    # --- Helpers ---

    @staticmethod
    def _find_local_maxima(data: FloatArray, order: int = 5) -> list[int]:
        """Find local peaks by comparing with N neighbors on each side."""
        peaks = []
        for i in range(order, len(data) - order):
            if data[i] == np.max(data[i - order:i + order + 1]):
                peaks.append(i)
        return peaks

    @staticmethod
    def _find_local_minima(data: FloatArray, order: int = 5) -> list[int]:
        """Find local troughs by comparing with N neighbors on each side."""
        troughs = []
        for i in range(order, len(data) - order):
            if data[i] == np.min(data[i - order:i + order + 1]):
                troughs.append(i)
        return troughs

    @staticmethod
    def _is_near(a: float, b: float, tolerance_pct: float = 2.0) -> bool:
        """Check if two values are within tolerance percentage of each other."""
        if a == 0 and b == 0:
            return True
        avg = (abs(a) + abs(b)) / 2
        if avg == 0:
            return True
        return abs(a - b) / avg * 100 <= tolerance_pct

    @staticmethod
    def _is_trending_up(data: FloatArray) -> bool:
        """Check if data has a positive linear regression slope."""
        if len(data) < 2:
            return False
        x = np.arange(len(data), dtype=np.float64)
        coeffs = np.polyfit(x, data, 1)
        return coeffs[0] > 0

    @staticmethod
    def _is_trending_down(data: FloatArray) -> bool:
        """Check if data has a negative linear regression slope."""
        if len(data) < 2:
            return False
        x = np.arange(len(data), dtype=np.float64)
        coeffs = np.polyfit(x, data, 1)
        return coeffs[0] < 0

    @staticmethod
    def _is_flat(data: FloatArray, tolerance_pct: float = 2.0) -> bool:
        """Check if all values are within tolerance of the mean."""
        if len(data) < 2:
            return True
        mean = np.mean(data)
        if mean == 0:
            return True
        max_dev = np.max(np.abs(data - mean)) / abs(mean) * 100
        return max_dev <= tolerance_pct
