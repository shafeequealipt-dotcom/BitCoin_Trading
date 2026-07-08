"""Candlestick pattern recognition: 16 patterns across bullish, bearish, and neutral.

Each pattern detector examines the last 1-5 candles and returns detection result
with confidence score and signal direction.
"""

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


class CandlestickDetector:
    """Detects candlestick patterns from OHLC data.

    Thresholds are class constants for easy tuning.
    """

    DOJI_THRESHOLD: float = 0.1
    SHADOW_RATIO: float = 2.0
    ENGULFING_MARGIN: float = 0.0
    SMALL_BODY_RATIO: float = 0.3
    LARGE_BODY_RATIO: float = 0.6

    def detect_all(
        self,
        opens: FloatArray,
        highs: FloatArray,
        lows: FloatArray,
        closes: FloatArray,
    ) -> list[dict]:
        """Run all pattern detectors on the data.

        Examines the last 5 candles for single and multi-candle patterns.

        Args:
            opens: Open prices array.
            highs: High prices array.
            lows: Low prices array.
            closes: Close prices array.

        Returns:
            List of detected patterns with name, type, confidence, and index.
        """
        n = len(opens)
        if n < 3:
            return []

        patterns: list[dict] = []
        i = n - 1  # Latest candle index

        # Single candle patterns (on latest candle)
        single_checks = [
            (self._doji, "doji", "neutral"),
            (self._spinning_top, "spinning_top", "neutral"),
            (self._hammer, "hammer", "bullish"),
            (self._inverted_hammer, "inverted_hammer", "bullish"),
            (self._hanging_man, "hanging_man", "bearish"),
            (self._shooting_star, "shooting_star", "bearish"),
            (self._dragonfly_doji, "dragonfly_doji", "bullish"),
            (self._gravestone_doji, "gravestone_doji", "bearish"),
        ]
        for check_fn, name, ptype in single_checks:
            conf = check_fn(opens[i], highs[i], lows[i], closes[i])
            if conf > 0:
                patterns.append({"name": name, "type": ptype, "confidence": conf, "index": i})

        # Two candle patterns (latest 2 candles)
        if n >= 2:
            j = i - 1
            two_checks = [
                (self._bullish_engulfing, "bullish_engulfing", "bullish"),
                (self._bearish_engulfing, "bearish_engulfing", "bearish"),
                (self._piercing_line, "piercing_line", "bullish"),
                (self._dark_cloud_cover, "dark_cloud_cover", "bearish"),
            ]
            for check_fn, name, ptype in two_checks:
                conf = check_fn(opens[j], highs[j], lows[j], closes[j],
                                opens[i], highs[i], lows[i], closes[i])
                if conf > 0:
                    patterns.append({"name": name, "type": ptype, "confidence": conf, "index": i})

        # Three candle patterns
        if n >= 3:
            k = i - 2
            j = i - 1
            three_checks = [
                (self._morning_star, "morning_star", "bullish"),
                (self._evening_star, "evening_star", "bearish"),
                (self._three_white_soldiers, "three_white_soldiers", "bullish"),
                (self._three_black_crows, "three_black_crows", "bearish"),
            ]
            for check_fn, name, ptype in three_checks:
                conf = check_fn(
                    opens[k], highs[k], lows[k], closes[k],
                    opens[j], highs[j], lows[j], closes[j],
                    opens[i], highs[i], lows[i], closes[i],
                )
                if conf > 0:
                    patterns.append({"name": name, "type": ptype, "confidence": conf, "index": i})

        return patterns

    # --- Helpers ---

    @staticmethod
    def _body_size(o: float, c: float) -> float:
        return abs(c - o)

    @staticmethod
    def _upper_shadow(o: float, h: float, c: float) -> float:
        return h - max(o, c)

    @staticmethod
    def _lower_shadow(o: float, l: float, c: float) -> float:
        return min(o, c) - l

    @staticmethod
    def _is_bullish(o: float, c: float) -> bool:
        return c > o

    @staticmethod
    def _is_bearish(o: float, c: float) -> bool:
        return c < o

    @staticmethod
    def _body_to_range_ratio(o: float, h: float, l: float, c: float) -> float:
        total_range = h - l
        if total_range == 0:
            return 0.0
        return abs(c - o) / total_range

    # --- Single candle patterns ---

    def _doji(self, o: float, h: float, l: float, c: float) -> float:
        ratio = self._body_to_range_ratio(o, h, l, c)
        if ratio < self.DOJI_THRESHOLD and (h - l) > 0:
            return 0.8
        return 0.0

    def _spinning_top(self, o: float, h: float, l: float, c: float) -> float:
        body = self._body_size(o, c)
        us = self._upper_shadow(o, h, c)
        ls = self._lower_shadow(o, l, c)
        total = h - l
        if total == 0:
            return 0.0
        ratio = body / total
        if self.DOJI_THRESHOLD <= ratio <= self.SMALL_BODY_RATIO and us > 0 and ls > 0:
            shadow_balance = min(us, ls) / max(us, ls) if max(us, ls) > 0 else 0
            if shadow_balance > 0.4:
                return 0.7
        return 0.0

    def _hammer(self, o: float, h: float, l: float, c: float) -> float:
        body = self._body_size(o, c)
        ls = self._lower_shadow(o, l, c)
        us = self._upper_shadow(o, h, c)
        if body == 0:
            return 0.0
        if ls >= self.SHADOW_RATIO * body and us <= body * 0.5:
            return 0.8
        return 0.0

    def _inverted_hammer(self, o: float, h: float, l: float, c: float) -> float:
        body = self._body_size(o, c)
        us = self._upper_shadow(o, h, c)
        ls = self._lower_shadow(o, l, c)
        if body == 0:
            return 0.0
        if us >= self.SHADOW_RATIO * body and ls <= body * 0.5:
            return 0.7
        return 0.0

    def _hanging_man(self, o: float, h: float, l: float, c: float) -> float:
        # Same shape as hammer — context (uptrend) differentiates, but we detect shape
        return self._hammer(o, h, l, c) * 0.9 if self._is_bearish(o, c) else 0.0

    def _shooting_star(self, o: float, h: float, l: float, c: float) -> float:
        body = self._body_size(o, c)
        us = self._upper_shadow(o, h, c)
        ls = self._lower_shadow(o, l, c)
        if body == 0:
            return 0.0
        if us >= self.SHADOW_RATIO * body and ls <= body * 0.3:
            return 0.8
        return 0.0

    def _dragonfly_doji(self, o: float, h: float, l: float, c: float) -> float:
        ratio = self._body_to_range_ratio(o, h, l, c)
        ls = self._lower_shadow(o, l, c)
        us = self._upper_shadow(o, h, c)
        total = h - l
        if total == 0:
            return 0.0
        if ratio < self.DOJI_THRESHOLD and ls > total * 0.6 and us < total * 0.1:
            return 0.8
        return 0.0

    def _gravestone_doji(self, o: float, h: float, l: float, c: float) -> float:
        ratio = self._body_to_range_ratio(o, h, l, c)
        us = self._upper_shadow(o, h, c)
        ls = self._lower_shadow(o, l, c)
        total = h - l
        if total == 0:
            return 0.0
        if ratio < self.DOJI_THRESHOLD and us > total * 0.6 and ls < total * 0.1:
            return 0.8
        return 0.0

    # --- Two candle patterns ---

    def _bullish_engulfing(self, o1: float, h1: float, l1: float, c1: float,
                           o2: float, h2: float, l2: float, c2: float) -> float:
        if not self._is_bearish(o1, c1) or not self._is_bullish(o2, c2):
            return 0.0
        if o2 <= c1 - self.ENGULFING_MARGIN and c2 >= o1 + self.ENGULFING_MARGIN:
            body_ratio = self._body_size(o2, c2) / max(self._body_size(o1, c1), 1e-10)
            return min(0.6 + body_ratio * 0.1, 0.95)
        return 0.0

    def _bearish_engulfing(self, o1: float, h1: float, l1: float, c1: float,
                           o2: float, h2: float, l2: float, c2: float) -> float:
        if not self._is_bullish(o1, c1) or not self._is_bearish(o2, c2):
            return 0.0
        if o2 >= c1 + self.ENGULFING_MARGIN and c2 <= o1 - self.ENGULFING_MARGIN:
            body_ratio = self._body_size(o2, c2) / max(self._body_size(o1, c1), 1e-10)
            return min(0.6 + body_ratio * 0.1, 0.95)
        return 0.0

    def _piercing_line(self, o1: float, h1: float, l1: float, c1: float,
                       o2: float, h2: float, l2: float, c2: float) -> float:
        if not self._is_bearish(o1, c1) or not self._is_bullish(o2, c2):
            return 0.0
        midpoint = (o1 + c1) / 2
        if o2 < l1 and c2 > midpoint and c2 < o1:
            return 0.75
        return 0.0

    def _dark_cloud_cover(self, o1: float, h1: float, l1: float, c1: float,
                          o2: float, h2: float, l2: float, c2: float) -> float:
        if not self._is_bullish(o1, c1) or not self._is_bearish(o2, c2):
            return 0.0
        midpoint = (o1 + c1) / 2
        if o2 > h1 and c2 < midpoint and c2 > o1:
            return 0.75
        return 0.0

    # --- Three candle patterns ---

    def _morning_star(self, o1: float, h1: float, l1: float, c1: float,
                      o2: float, h2: float, l2: float, c2: float,
                      o3: float, h3: float, l3: float, c3: float) -> float:
        if not self._is_bearish(o1, c1):
            return 0.0
        body2_ratio = self._body_to_range_ratio(o2, h2, l2, c2)
        if body2_ratio > self.SMALL_BODY_RATIO:
            return 0.0
        if not self._is_bullish(o3, c3):
            return 0.0
        midpoint1 = (o1 + c1) / 2
        if c3 > midpoint1:
            return 0.85
        return 0.0

    def _evening_star(self, o1: float, h1: float, l1: float, c1: float,
                      o2: float, h2: float, l2: float, c2: float,
                      o3: float, h3: float, l3: float, c3: float) -> float:
        if not self._is_bullish(o1, c1):
            return 0.0
        body2_ratio = self._body_to_range_ratio(o2, h2, l2, c2)
        if body2_ratio > self.SMALL_BODY_RATIO:
            return 0.0
        if not self._is_bearish(o3, c3):
            return 0.0
        midpoint1 = (o1 + c1) / 2
        if c3 < midpoint1:
            return 0.85
        return 0.0

    def _three_white_soldiers(self, o1: float, h1: float, l1: float, c1: float,
                              o2: float, h2: float, l2: float, c2: float,
                              o3: float, h3: float, l3: float, c3: float) -> float:
        if not (self._is_bullish(o1, c1) and self._is_bullish(o2, c2) and self._is_bullish(o3, c3)):
            return 0.0
        if not (c2 > c1 and c3 > c2):
            return 0.0
        # Small upper shadows
        for o, h, c in [(o1, h1, c1), (o2, h2, c2), (o3, h3, c3)]:
            body = self._body_size(o, c)
            us = self._upper_shadow(o, h, c)
            if body > 0 and us > body * 0.5:
                return 0.0
        return 0.85

    def _three_black_crows(self, o1: float, h1: float, l1: float, c1: float,
                           o2: float, h2: float, l2: float, c2: float,
                           o3: float, h3: float, l3: float, c3: float) -> float:
        if not (self._is_bearish(o1, c1) and self._is_bearish(o2, c2) and self._is_bearish(o3, c3)):
            return 0.0
        if not (c2 < c1 and c3 < c2):
            return 0.0
        for o, l, c in [(o1, l1, c1), (o2, l2, c2), (o3, l3, c3)]:
            body = self._body_size(o, c)
            ls = self._lower_shadow(o, l, c)
            if body > 0 and ls > body * 0.5:
                return 0.0
        return 0.85
