"""X-RAY Phases 6+7: Liquidity Zone mapping + Sweep detection.

Phase 6: Maps where stop-loss clusters exist — equal highs/lows and
round numbers where liquidity pools form.

Phase 7: Detects when a liquidity zone is swept (wick beyond + reversal),
generating high-probability entry signals.

Pure numpy implementation.
"""

import math

import numpy as np
from numpy.typing import NDArray

from src.analysis.structure.models.structure_types import LiquiditySweep, LiquidityZone
from src.config.settings import StructureSettings
from src.core.logging import get_logger

log = get_logger("xray")

FloatArray = NDArray[np.float64]


def _classify_reversal(ratio: float) -> str:
    """Classify reversal body-to-range ratio into semantic strength."""
    if ratio >= 0.6:
        return "strong"
    elif ratio >= 0.35:
        return "moderate"
    return "weak"


def _classify_signal(direction: str, rev_ratio: float, depth_pct: float) -> str:
    """Classify sweep signal into probability-graded directional labels.

    All return values include the direction substring so downstream
    consumers (e.g. ``_compute_smc_confluence`` in ``structure_engine``)
    can match by direction without losing weak-but-real sweeps. Pre-fix
    weak reversals returned a directionless ``"weak_signal"`` which
    silently failed the downstream substring check, dropping the +30
    sweep contribution to SMC confluence universe-wide.

    Reversal-strength labelling (``_classify_reversal``) is independent
    and unchanged — it produces ``"weak"``/``"moderate"``/``"strong"``
    on a separate field for Claude's reasoning.

    Args:
        direction: ``"long"`` or ``"short"`` — the trade direction the
            sweep favours (sell-side sweep -> long bounce, mirror).
        rev_ratio: Reversal-candle body ratio (body / range), 0..1.
        depth_pct: How far the wick pierced the zone level, in percent.

    Returns:
        ``f"high_probability_{direction}"`` | ``f"moderate_{direction}"``
        | ``f"weak_{direction}"``.
    """
    if rev_ratio >= 0.5 and depth_pct >= 0.1:
        return f"high_probability_{direction}"
    if rev_ratio >= 0.3 or depth_pct >= 0.05:
        return f"moderate_{direction}"
    return f"weak_{direction}"


class LiquidityMapper:
    """Maps liquidity zones and detects sweep events.

    Zone sources:
      - Equal highs (buy-side liquidity above)
      - Equal lows (sell-side liquidity below)
      - Round numbers (psychological levels)

    Sweep detection:
      - Wick beyond zone level + candle closes back inside = sweep
      - Generates bullish_sweep (sell-side swept → long) or bearish_sweep

    Args:
        settings: StructureSettings configuration.
    """

    def __init__(self, settings: StructureSettings) -> None:
        self._settings = settings

    def detect_zones(
        self,
        highs: FloatArray,
        lows: FloatArray,
        closes: FloatArray,
        current_price: float,
        swing_highs: list[tuple[int, float]],
        swing_lows: list[tuple[int, float]],
    ) -> list[LiquidityZone]:
        """Detect liquidity zones from swing points and round numbers.

        Args:
            highs, lows, closes: numpy arrays of OHLCV data.
            current_price: Current market price.
            swing_highs: Pre-computed swing high points from S/R engine.
            swing_lows: Pre-computed swing low points.

        Returns:
            List of LiquidityZone objects sorted by proximity to current price.
        """
        zones: list[LiquidityZone] = []
        n = len(highs)
        tol_pct = self._settings.liq_equal_tolerance_pct / 100.0
        min_count = self._settings.liq_min_equal_count

        # 1. Equal highs → buy-side liquidity (stops sit above)
        if swing_highs:
            eq_highs = self._find_equal_levels(
                swing_highs, tol_pct, current_price, min_count,
            )
            for group in eq_highs:
                prices = [p for _, p in group]
                level = max(prices)  # stops sit ABOVE the equal highs
                zones.append(LiquidityZone(
                    zone_type="buy_side",
                    level=round(level, 8),
                    zone_high=round(max(prices) * (1 + tol_pct), 8),
                    zone_low=round(min(prices), 8),
                    strength=min(5.0, len(group) * 1.5),
                    source="equal_highs",
                    equal_count=len(group),
                ))

        # 2. Equal lows → sell-side liquidity (stops sit below)
        if swing_lows:
            eq_lows = self._find_equal_levels(
                swing_lows, tol_pct, current_price, min_count,
            )
            for group in eq_lows:
                prices = [p for _, p in group]
                level = min(prices)  # stops sit BELOW the equal lows
                zones.append(LiquidityZone(
                    zone_type="sell_side",
                    level=round(level, 8),
                    zone_high=round(max(prices), 8),
                    zone_low=round(min(prices) * (1 - tol_pct), 8),
                    strength=min(5.0, len(group) * 1.5),
                    source="equal_lows",
                    equal_count=len(group),
                ))

        # 3. Round numbers near current price (within 5%)
        step = self._settings.liq_round_number_step
        if step > 0 and current_price > 0:
            lower = current_price * 0.95
            upper = current_price * 1.05
            base = math.floor(lower / step) * step
            level = base
            while level <= upper:
                if level > 0:
                    zone_type = "buy_side" if level > current_price else "sell_side"
                    zones.append(LiquidityZone(
                        zone_type=zone_type,
                        level=round(level, 8),
                        zone_high=round(level + step * 0.001, 8),
                        zone_low=round(level - step * 0.001, 8),
                        strength=1.5,  # round numbers are moderate
                        source="round_number",
                        equal_count=0,
                    ))
                level += step

        # 4. Check swept status for each zone — Phase 1c canonical
        # sweep+reclaim semantic, bounded by ``sweep_recency_bars``.
        # Closes are passed in so the reclaim leg can be evaluated;
        # the legacy wick-only scan iterated only highs/lows.
        for zone in zones:
            self._check_swept(zone, highs, lows, closes, n)

        # Sort by proximity to current price
        zones.sort(key=lambda z: abs(z.level - current_price))

        # Limit to 15 zones
        zones = zones[:15]

        unswept = sum(1 for z in zones if not z.swept)
        # Phase 1c observability: surface reclaimed-vs-violation-only
        # split so operators can confirm the canonical sweep+reclaim
        # path is firing post-fix. ``reclaimed`` count means the zone
        # had a violation+reclaim within ``sweep_recency_bars``;
        # ``unswept`` means no qualifying pattern in the window.
        reclaimed = sum(
            1 for z in zones if z.swept and z.reclaimed_at is not None
        )
        log.debug(
            f"XRAY_LIQ | total={len(zones)} unswept={unswept} "
            f"reclaimed={reclaimed} "
            f"buy_side={sum(1 for z in zones if z.zone_type == 'buy_side')} "
            f"sell_side={sum(1 for z in zones if z.zone_type == 'sell_side')}"
        )

        return zones

    def detect_sweeps(
        self,
        highs: FloatArray,
        lows: FloatArray,
        closes: FloatArray,
        opens: FloatArray,
        current_price: float,
        zones: list[LiquidityZone],
    ) -> list[LiquiditySweep]:
        """Detect liquidity sweep events from recent candles.

        A sweep occurs when price wicks beyond a liquidity zone level
        but closes back on the other side, indicating institutional
        stop-hunting followed by reversal.

        Args:
            highs, lows, closes, opens: numpy arrays of OHLCV data.
            current_price: Current market price.
            zones: Liquidity zones from detect_zones().

        Returns:
            List of LiquiditySweep objects sorted by recency.
        """
        n = len(highs)
        if n < 5:
            return []

        max_age = self._settings.sweep_max_age_candles
        min_wick = self._settings.sweep_min_wick_pct
        start = max(0, n - max_age)
        sweeps: list[LiquiditySweep] = []

        for zone in zones:
            if zone.swept:
                continue  # already swept, no new signal

            for i in range(start, n):
                candle_range = highs[i] - lows[i]
                if candle_range <= 0:
                    continue

                body = abs(closes[i] - opens[i])
                body_ratio = body / candle_range

                if zone.zone_type == "sell_side":
                    # Sell-side sweep: wick goes below zone level, closes above
                    if lows[i] < zone.level and closes[i] > zone.level:
                        wick_extreme = float(lows[i])
                        depth_pct = abs(zone.level - wick_extreme) / zone.level * 100

                        # Reversal: candle closes bullish (close > open)
                        reversal = closes[i] > opens[i]
                        rev_ratio = body_ratio if reversal else body_ratio * 0.5
                        rev_label = _classify_reversal(rev_ratio)
                        signal = _classify_signal("long", rev_ratio, depth_pct)
                        age = n - 1 - i

                        if body_ratio >= min_wick:
                            sweeps.append(LiquiditySweep(
                                sweep_type="bullish_sweep",
                                level_swept=zone.level,
                                wick_extreme=wick_extreme,
                                sweep_depth_pct=round(depth_pct, 4),
                                reversal_candle_index=i,
                                reversal_strength=rev_label,
                                reversal_ratio=round(rev_ratio, 4),
                                reversal_body_pct=round(body_ratio, 4),
                                timestamp=float(i),
                                age_candles=age,
                                signal=signal,
                                associated_zone=zone,
                            ))
                            zone.swept = True
                            zone.swept_at = float(i)
                            break  # one sweep per zone

                elif zone.zone_type == "buy_side":
                    # Buy-side sweep: wick goes above zone level, closes below
                    if highs[i] > zone.level and closes[i] < zone.level:
                        wick_extreme = float(highs[i])
                        depth_pct = abs(wick_extreme - zone.level) / zone.level * 100

                        reversal = closes[i] < opens[i]
                        rev_ratio = body_ratio if reversal else body_ratio * 0.5
                        rev_label = _classify_reversal(rev_ratio)
                        signal = _classify_signal("short", rev_ratio, depth_pct)
                        age = n - 1 - i

                        if body_ratio >= min_wick:
                            sweeps.append(LiquiditySweep(
                                sweep_type="bearish_sweep",
                                level_swept=zone.level,
                                wick_extreme=wick_extreme,
                                sweep_depth_pct=round(depth_pct, 4),
                                reversal_candle_index=i,
                                reversal_strength=rev_label,
                                reversal_ratio=round(rev_ratio, 4),
                                reversal_body_pct=round(body_ratio, 4),
                                timestamp=float(i),
                                age_candles=age,
                                signal=signal,
                                associated_zone=zone,
                            ))
                            zone.swept = True
                            zone.swept_at = float(i)
                            break

        # Sort by recency (most recent first)
        sweeps.sort(key=lambda s: s.reversal_candle_index, reverse=True)

        if sweeps:
            log.debug(
                f"XRAY_SWEEP | count={len(sweeps)} "
                f"latest={sweeps[0].signal}@{sweeps[0].level_swept:.2f} "
                f"rev_str={sweeps[0].reversal_strength}"
            )

        return sweeps

    @staticmethod
    def _find_equal_levels(
        swing_points: list[tuple[int, float]],
        tolerance_pct: float,
        current_price: float,
        min_count: int,
    ) -> list[list[tuple[int, float]]]:
        """Group swing points at approximately equal price levels.

        Returns:
            List of groups, where each group is a list of (index, price) tuples.
        """
        if not swing_points:
            return []

        # Sort by price
        sorted_pts = sorted(swing_points, key=lambda x: x[1])
        threshold = current_price * tolerance_pct

        groups: list[list[tuple[int, float]]] = []
        current_group = [sorted_pts[0]]

        for i in range(1, len(sorted_pts)):
            if sorted_pts[i][1] - current_group[-1][1] <= threshold:
                current_group.append(sorted_pts[i])
            else:
                if len(current_group) >= min_count:
                    groups.append(current_group)
                current_group = [sorted_pts[i]]

        if len(current_group) >= min_count:
            groups.append(current_group)

        return groups

    def _check_swept(
        self,
        zone: LiquidityZone,
        highs: FloatArray,
        lows: FloatArray,
        closes: FloatArray,
        n: int,
    ) -> None:
        """Mark zone swept iff a canonical SMC sweep+reclaim happened in window.

        Phase 1c — XRAY confidence reachability fix. Replaces the legacy
        unbounded historical wick scan that universally marked zones
        swept and collapsed both the +15 unswept-liquidity component and
        the +30 active-sweep component of the SMC confluence formula.

        For ``buy_side`` zones (stops above level): the canonical sweep
        is a wick beyond ``zone.level`` (``highs[j] > zone.level``)
        followed by a later candle that closes back through
        (``closes[k] < zone.level`` for some ``k > j``) within
        ``sweep_recency_bars``. For ``sell_side`` zones: mirror — wick
        below + later close above. Both bounded to the recency window
        so stale historical wicks (e.g. from 200 bars ago) do not
        permanently mark a zone swept; the assumption is that liquidity
        re-forms over time.

        Same-candle violation+reclaim is intentionally **not** caught
        here. ``detect_sweeps`` (line 222 onward) detects that single-
        candle pattern and produces a ``LiquiditySweep`` record for
        downstream scoring (the +30 sweep component). If ``_check_swept``
        marked single-candle sweeps swept first, ``detect_sweeps`` would
        skip them at line 219 and the LiquiditySweep object would be
        lost. The two functions therefore handle disjoint patterns:
        ``_check_swept`` covers multi-bar violation+reclaim where the
        zone has been resolved over time but no fresh single-candle
        reversal exists, and ``detect_sweeps`` covers fresh single-
        candle sweep+reversal events.

        When ``sweep_require_reclaim`` is False, falls back to
        wick-only detection within the recency window (still bounded —
        an improvement over the unbounded legacy scan).

        Side effects:
            Mutates ``zone.swept``, ``zone.swept_at``, and
            ``zone.reclaimed_at``. Leaves the zone unmodified when no
            qualifying pattern is found in the window.

        Args:
            zone: The LiquidityZone to inspect; mutated in place.
            highs, lows, closes: numpy arrays of OHLCV data (must be
                same length).
            n: total number of bars (``len(highs)``).
        """
        recency = max(1, int(self._settings.sweep_recency_bars))
        require_reclaim = bool(self._settings.sweep_require_reclaim)
        start = max(0, n - recency)

        if zone.zone_type == "buy_side":
            for j in range(start, n):
                if highs[j] <= zone.level:
                    continue
                # Found a violation bar at j. If reclaim not required,
                # mark swept directly (bounded variant of legacy).
                if not require_reclaim:
                    zone.swept = True
                    zone.swept_at = float(j)
                    zone.reclaimed_at = None
                    return
                # Skip same-candle reclaim — that pattern belongs to
                # detect_sweeps so it can produce a LiquiditySweep
                # record. We require a *later* bar to close back
                # through the level.
                for k in range(j + 1, n):
                    if closes[k] < zone.level:
                        zone.swept = True
                        zone.swept_at = float(j)
                        zone.reclaimed_at = float(k)
                        return
                # Violation with no reclaim in window: leave unswept
                # so detect_sweeps can still inspect this zone for a
                # single-candle pattern. We do not break — a later
                # violation bar might still pair with a reclaim.
        elif zone.zone_type == "sell_side":
            for j in range(start, n):
                if lows[j] >= zone.level:
                    continue
                if not require_reclaim:
                    zone.swept = True
                    zone.swept_at = float(j)
                    zone.reclaimed_at = None
                    return
                for k in range(j + 1, n):
                    if closes[k] > zone.level:
                        zone.swept = True
                        zone.swept_at = float(j)
                        zone.reclaimed_at = float(k)
                        return
