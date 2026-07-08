"""X-RAY Phase 1: Support & Resistance level detection.

Detects support and resistance zones using swing point detection,
level clustering, and multi-factor scoring across multiple lookbacks.
Pure numpy implementation (no scipy, no pandas).
"""

import time

import numpy as np
from numpy.typing import NDArray

from src.analysis.structure.models.structure_types import PriceLevel
from src.config.settings import StructureSettings
from src.core.logging import get_logger

log = get_logger("xray")

FloatArray = NDArray[np.float64]


class SupportResistanceEngine:
    """Detects and scores support/resistance levels from OHLCV data.

    Algorithm:
      1. Find swing highs/lows using rolling comparison for each lookback
      2. Cluster nearby levels within cluster_pct of each other
      3. Score each cluster by touch count, recency, and rejection strength
      4. Split into support (below price) and resistance (above price)
      5. Sort by proximity to current price, limit to max_levels_per_side

    Args:
        settings: StructureSettings configuration.
    """

    def __init__(self, settings: StructureSettings) -> None:
        self._settings = settings

    def calculate(
        self,
        highs: FloatArray,
        lows: FloatArray,
        closes: FloatArray,
        current_price: float,
    ) -> tuple[list[PriceLevel], list[PriceLevel], dict]:
        """Calculate support and resistance levels.

        Args:
            highs: numpy array of high prices.
            lows: numpy array of low prices.
            closes: numpy array of close prices.
            current_price: Current market price.

        Returns:
            Tuple of (support_levels, resistance_levels, swing_data).
            swing_data is a dict with 'swing_highs' and 'swing_lows' lists
            for reuse by the market structure detector.
        """
        t0 = time.monotonic()
        n = len(highs)
        if n < 20:
            return [], [], {"swing_highs": [], "swing_lows": []}

        all_swing_highs = []  # list of (index, price) tuples
        all_swing_lows = []

        # Run swing detection for each configured lookback
        for lookback in self._settings.swing_lookbacks:
            if lookback >= n // 2:
                continue
            sh = self._find_swing_highs(highs, lookback)
            sl = self._find_swing_lows(lows, lookback)
            all_swing_highs.extend(sh)
            all_swing_lows.extend(sl)

        # Deduplicate: if same index appears multiple times, keep it once
        seen_hi = set()
        unique_highs = []
        for idx, price in all_swing_highs:
            if idx not in seen_hi:
                seen_hi.add(idx)
                unique_highs.append((idx, price))

        seen_lo = set()
        unique_lows = []
        for idx, price in all_swing_lows:
            if idx not in seen_lo:
                seen_lo.add(idx)
                unique_lows.append((idx, price))

        swing_data = {
            "swing_highs": unique_highs,
            "swing_lows": unique_lows,
        }

        # Cluster resistance levels (from swing highs)
        resistance_clusters = self._cluster_levels(
            unique_highs, current_price, self._settings.cluster_pct,
        )
        # Cluster support levels (from swing lows)
        support_clusters = self._cluster_levels(
            unique_lows, current_price, self._settings.cluster_pct,
        )

        # Score and build PriceLevel objects
        resistance_levels = self._score_clusters(
            resistance_clusters, "resistance", n, highs, lows, closes,
        )
        support_levels = self._score_clusters(
            support_clusters, "support", n, highs, lows, closes,
        )

        # Filter: supports below price, resistances above price
        support_levels = [
            s for s in support_levels if s.price < current_price
        ]
        resistance_levels = [
            r for r in resistance_levels if r.price > current_price
        ]

        # Filter by minimum touches
        min_t = self._settings.min_touches
        support_levels = [s for s in support_levels if s.touches >= min_t]
        # Issue 1 of 2026-05-19 direction-bias fix Phase C: previously
        # this filter hardcoded `>= 1`, which kept single-touch swing
        # highs while the support filter dropped single-touch swing
        # lows (min_touches=2). In sustained downtrending markets the
        # asymmetry produced sup=0 res=5 in 80.7% of audited cycles,
        # collapsing rr_long → 0 and cascading Buy → Sell flips. Now
        # config-driven via min_touches_resistance (default 2,
        # symmetric with support). Operator may set
        # ``[analysis.structure] min_touches_resistance = 1`` in
        # config.toml to restore legacy single-touch resistance
        # detection if needed for less-trending markets.
        min_t_resistance = self._settings.min_touches_resistance
        resistance_levels = [
            r for r in resistance_levels if r.touches >= min_t_resistance
        ]

        # Sort by proximity to current price (closest first)
        support_levels.sort(key=lambda s: current_price - s.price)
        resistance_levels.sort(key=lambda r: r.price - current_price)

        # Limit to max levels per side
        max_lvl = self._settings.max_levels_per_side
        support_levels = support_levels[:max_lvl]
        resistance_levels = resistance_levels[:max_lvl]

        elapsed_ms = (time.monotonic() - t0) * 1000
        log.debug(
            f"XRAY_SR | sup={len(support_levels)} res={len(resistance_levels)} "
            f"swH={len(unique_highs)} swL={len(unique_lows)} el={elapsed_ms:.1f}ms"
        )

        return support_levels, resistance_levels, swing_data

    @staticmethod
    def _find_swing_highs(highs: FloatArray, lookback: int) -> list[tuple[int, float]]:
        """Find swing high points using rolling comparison.

        A swing high at index i means highs[i] is the maximum of
        highs[i-lookback : i+lookback+1].

        Returns:
            List of (index, price) tuples.
        """
        n = len(highs)
        results = []
        for i in range(lookback, n - lookback):
            window = highs[i - lookback: i + lookback + 1]
            if highs[i] == np.max(window):
                results.append((i, float(highs[i])))
        return results

    @staticmethod
    def _find_swing_lows(lows: FloatArray, lookback: int) -> list[tuple[int, float]]:
        """Find swing low points using rolling comparison.

        A swing low at index i means lows[i] is the minimum of
        lows[i-lookback : i+lookback+1].

        Returns:
            List of (index, price) tuples.
        """
        n = len(lows)
        results = []
        for i in range(lookback, n - lookback):
            window = lows[i - lookback: i + lookback + 1]
            if lows[i] == np.min(window):
                results.append((i, float(lows[i])))
        return results

    @staticmethod
    def _cluster_levels(
        swing_points: list[tuple[int, float]],
        current_price: float,
        cluster_pct: float,
    ) -> list[dict]:
        """Cluster nearby swing points into zones.

        Groups points whose prices are within cluster_pct% of each other.

        Returns:
            List of cluster dicts: {center, zone_low, zone_high, touches, indices}.
        """
        if not swing_points:
            return []

        # Sort by price
        sorted_pts = sorted(swing_points, key=lambda x: x[1])
        threshold = current_price * (cluster_pct / 100.0)

        clusters = []
        current_cluster = [sorted_pts[0]]

        for i in range(1, len(sorted_pts)):
            if sorted_pts[i][1] - current_cluster[-1][1] <= threshold:
                current_cluster.append(sorted_pts[i])
            else:
                clusters.append(current_cluster)
                current_cluster = [sorted_pts[i]]
        clusters.append(current_cluster)

        result = []
        for cluster in clusters:
            prices = [p for _, p in cluster]
            indices = [idx for idx, _ in cluster]
            result.append({
                "center": sum(prices) / len(prices),
                "zone_low": min(prices),
                "zone_high": max(prices),
                "touches": len(cluster),
                "indices": indices,
            })

        return result

    @staticmethod
    def _score_clusters(
        clusters: list[dict],
        level_type: str,
        total_candles: int,
        highs: FloatArray,
        lows: FloatArray,
        closes: FloatArray,
        timeframe: str = "60",
    ) -> list[PriceLevel]:
        """Score each cluster and build PriceLevel objects.

        Scoring factors (total 0.0-5.0):
          - Touch count (40%): 1→1.0, 2→2.0, 3→3.0, 4+→4.0
          - Recency (25%): time-based buckets (recent = higher)
          - Timeframe (20%): 4H→1.0, 1H→0.8, 15m→0.5, 5m→0.3
          - Rejection strength (15%): average wick size at the level

        Returns:
            List of PriceLevel objects.
        """
        # Timeframe weight: higher TF → stronger level
        tf_weights = {"240": 1.0, "60": 0.8, "15": 0.5, "5": 0.3, "1": 0.2}
        tf_score = tf_weights.get(timeframe, 0.5)

        levels = []
        for cluster in clusters:
            touches = cluster["touches"]
            indices = cluster["indices"]
            center = cluster["center"]

            # Touch count score (0-4, weight 40%)
            touch_score = min(touches, 4.0)

            # Recency score (0-1, weight 25%)
            # Time-based buckets: how recent is the last touch
            last_idx = max(indices)
            candles_ago = total_candles - 1 - last_idx

            # Map candles_ago to recency score using time-approximation buckets
            # For H1 candles: 2 candles ≈ 2 hours, 12 ≈ 12 hours, 24 ≈ 1 day, 72 ≈ 3 days
            if candles_ago <= 2:
                recency_score = 1.0    # within ~2 hours
            elif candles_ago <= 12:
                recency_score = 0.8    # within ~12 hours
            elif candles_ago <= 24:
                recency_score = 0.6    # within ~24 hours
            elif candles_ago <= 72:
                recency_score = 0.4    # within ~3 days
            else:
                recency_score = 0.2    # older

            # Rejection strength score (0-1, weight 15%)
            # Average wick length at touch points relative to price
            wick_sum = 0.0
            wick_count = 0
            for idx in indices:
                if idx < len(highs):
                    if level_type == "support":
                        # Support rejection: wick below the body
                        wick = abs(min(closes[idx], highs[idx]) - lows[idx])
                    else:
                        # Resistance rejection: wick above the body
                        wick = abs(highs[idx] - max(closes[idx], lows[idx]))
                    if center > 0:
                        wick_sum += wick / center
                        wick_count += 1
            avg_wick_pct = (wick_sum / wick_count) if wick_count > 0 else 0
            # Scale: 0.5% wick or more = full score
            rejection_score = min(avg_wick_pct / 0.005, 1.0)

            # Weighted final score (0-5)
            # Weights: touch 40%, recency 25%, timeframe 20%, rejection 15%
            strength = (
                touch_score * 0.40
                + recency_score * 5.0 * 0.25
                + tf_score * 5.0 * 0.20
                + rejection_score * 5.0 * 0.15
            )
            strength = max(0.0, min(5.0, strength))

            levels.append(PriceLevel(
                price=round(center, 8),
                level_type=level_type,
                strength=round(strength, 2),
                touches=touches,
                last_tested=float(last_idx),
                timeframe=timeframe,
                zone_low=round(cluster["zone_low"], 8),
                zone_high=round(cluster["zone_high"], 8),
            ))

        # Sort by strength descending
        levels.sort(key=lambda l: l.strength, reverse=True)
        return levels
