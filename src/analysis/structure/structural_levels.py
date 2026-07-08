"""X-RAY Phase 3: Structural SL/TP Placement with R:R calculation.

Places stop-loss and take-profit at structurally significant levels
rather than arbitrary percentages, and calculates Risk:Reward ratio.
"""

from src.analysis.structure.models.structure_types import (
    MarketStructureResult,
    PriceLevel,
    StructuralPlacement,
)
from src.config.settings import StructureSettings
from src.core.utils import format_price
from src.core.logging import get_logger

log = get_logger("xray")


class StructuralLevelCalculator:
    """Calculates structural SL/TP placement with R:R assessment.

    Places SL below support (for longs) or above resistance (for shorts)
    with a configurable buffer, and TP at the opposite structural level.
    Falls back to percentage-based placement when no levels are available.

    Args:
        settings: StructureSettings configuration.
    """

    def __init__(self, settings: StructureSettings) -> None:
        self._settings = settings

    def calculate(
        self,
        current_price: float,
        direction: str,
        support_levels: list[PriceLevel],
        resistance_levels: list[PriceLevel],
        market_structure: MarketStructureResult,
        position_in_range: float,
        atr_pct_h1: float = 0.0,
    ) -> StructuralPlacement | None:
        """Calculate structural SL/TP placement.

        Args:
            current_price: Current market price.
            direction: Trade direction ("long" or "short").
            support_levels: Detected support levels (sorted by proximity).
            resistance_levels: Detected resistance levels (sorted by proximity).
            market_structure: Current market structure state.
            position_in_range: Price position between support and resistance (0-1).

        Returns:
            StructuralPlacement with structural or fallback SL/TP levels.
            Returns None only if direction is unrecognized.
        """
        if direction == "long":
            return self._calc_long(
                current_price, support_levels, resistance_levels,
                market_structure, position_in_range, atr_pct_h1,
            )
        elif direction == "short":
            return self._calc_short(
                current_price, support_levels, resistance_levels,
                market_structure, position_in_range, atr_pct_h1,
            )
        return None

    def _calc_long(
        self,
        current_price: float,
        supports: list[PriceLevel],
        resistances: list[PriceLevel],
        ms: MarketStructureResult,
        position: float,
        atr_pct_h1: float = 0.0,
    ) -> StructuralPlacement:
        """Calculate SL/TP for a long position.

        SL: below nearest support zone (with buffer).
        TP: at nearest resistance zone (just below).
        """
        sl_buffer = self._settings.sl_buffer_pct / 100.0
        tp_buffer = self._settings.tp_buffer_pct / 100.0

        # SL placement
        structural_sl = 0.0
        sl_ref = ""
        if supports:
            nearest_sup = supports[0]
            structural_sl = nearest_sup.zone_low - (nearest_sup.price * sl_buffer)
            sl_ref = f"below_support_${format_price(nearest_sup.price)}"
        else:
            # Fallback: configurable percentage below current price
            fb = self._settings.sl_fallback_pct / 100.0
            structural_sl = current_price * (1 - fb)
            sl_ref = f"fallback_{self._settings.sl_fallback_pct}pct_below"

        # TP placement
        structural_tp = 0.0
        tp_ref = ""
        # Issue 1 of 2026-05-19 direction-bias fix Phase C: track the
        # "structurally invalid" flag for callers that want to qualify
        # their handling. Flag is set when the raw resistance-based TP
        # would have landed on the WRONG SIDE of current_price (i.e. at
        # or below current_price for a long), indicating price is at or
        # above the nearest resistance zone. Without the clamp introduced
        # below, that condition collapsed reward → 0 and rr_long → 0.
        is_structurally_invalid = False
        if resistances:
            nearest_res = resistances[0]
            raw_tp = nearest_res.zone_low - (nearest_res.price * tp_buffer)
            # Minimum-edge floor: TP must be at least tp_min_distance_pct
            # above current_price for a long. When the raw value violates
            # that, clamp UP to the floor and flag the placement as
            # structurally invalid for downstream consumers.
            min_tp_distance = current_price * (
                self._settings.tp_min_distance_pct / 100.0
            )
            min_tp = current_price + min_tp_distance
            if raw_tp < min_tp:
                is_structurally_invalid = True
                structural_tp = min_tp
                tp_ref = (
                    f"clamped_min_edge_${format_price(nearest_res.price)}_"
                    f"floor={self._settings.tp_min_distance_pct:.2f}pct"
                )
            else:
                structural_tp = raw_tp
                tp_ref = f"at_resistance_${format_price(nearest_res.price)}"
        else:
            # Fallback: configurable percentage above current price
            fb = self._settings.tp_fallback_pct / 100.0
            structural_tp = current_price * (1 + fb)
            tp_ref = f"fallback_{self._settings.tp_fallback_pct}pct_above"

        # Fix2 (2026-06-05) — with-trend continuation TP. In a confirmed UPTREND a
        # with-trend long whose structural resistance TP is valid (not clamped)
        # but sits too close (reward < continuation_tp_min_atr_mult x ATR) reads
        # as 'no reward room' (RR<1) and the brain skips the profitable with-trend
        # long. Re-anchor the TP to an ATR-projected continuation target further
        # ABOVE, taking the FURTHER of the two so the TP is NEVER made closer.
        # Trend-side only; ranging/downtrend/ATR<=0/already-clamped untouched;
        # flag-gated. Pure ATR projection (no level-walking -> no sort-order
        # pitfall), bounded by continuation_tp_atr_mult. flip_tp_capper still caps
        # an over-distant TP downstream.
        if (
            getattr(self._settings, "xray_continuation_tp_enabled", True)
            and ms.structure == "uptrend"
            and not is_structurally_invalid
            and atr_pct_h1 > 0
            and structural_tp > 0
        ):
            _atr_abs = current_price * (atr_pct_h1 / 100.0)
            _min_mult = float(getattr(
                self._settings, "continuation_tp_min_atr_mult", 1.5,
            ))
            _reward_now = structural_tp - current_price
            if 0 < _reward_now < _min_mult * _atr_abs:
                _proj_mult = float(getattr(
                    self._settings, "continuation_tp_atr_mult", 2.5,
                ))
                _proj_tp = current_price + _proj_mult * _atr_abs
                if _proj_tp > structural_tp:  # further above = more reward only
                    structural_tp = _proj_tp
                    tp_ref = f"continuation_atr_proj_{_proj_mult:.1f}xATR"

        # R:R calculation
        risk = abs(current_price - structural_sl)
        reward = abs(structural_tp - current_price)
        rr_ratio = reward / risk if risk > 0 else 0.0

        # R:R quality — mark as "unknown" when both SL and TP are fallbacks
        _is_full_fallback = ("fallback" in sl_ref and "fallback" in tp_ref)
        if _is_full_fallback:
            rr_quality = "unknown"
        else:
            rr_quality = self._classify_rr(rr_ratio)

        # Entry quality based on position in range
        entry_quality = self._classify_entry_long(position)

        # Entry zone
        entry_zone_low = structural_sl
        entry_zone_high = current_price

        log.debug(
            f"XRAY_LEVELS | dir=long sl=${format_price(structural_sl)} "
            f"tp=${format_price(structural_tp)} rr={rr_ratio:.2f} q={rr_quality} "
            f"invalid={is_structurally_invalid}"
        )

        return StructuralPlacement(
            structural_sl=round(structural_sl, 8),
            structural_tp=round(structural_tp, 8),
            rr_ratio=round(rr_ratio, 2),
            rr_quality=rr_quality,
            entry_quality=entry_quality,
            entry_zone_low=round(entry_zone_low, 8),
            entry_zone_high=round(entry_zone_high, 8),
            sl_reference=sl_ref,
            tp_reference=tp_ref,
            direction="long",
            is_fallback_rr=("fallback" in sl_ref or "fallback" in tp_ref),
            is_structurally_invalid=is_structurally_invalid,
        )

    def _calc_short(
        self,
        current_price: float,
        supports: list[PriceLevel],
        resistances: list[PriceLevel],
        ms: MarketStructureResult,
        position: float,
        atr_pct_h1: float = 0.0,
    ) -> StructuralPlacement:
        """Calculate SL/TP for a short position (mirror of long)."""
        sl_buffer = self._settings.sl_buffer_pct / 100.0
        tp_buffer = self._settings.tp_buffer_pct / 100.0

        # SL: above nearest resistance (with buffer)
        structural_sl = 0.0
        sl_ref = ""
        if resistances:
            nearest_res = resistances[0]
            structural_sl = nearest_res.zone_high + (nearest_res.price * sl_buffer)
            sl_ref = f"above_resistance_${format_price(nearest_res.price)}"
        else:
            fb = self._settings.sl_fallback_pct / 100.0
            structural_sl = current_price * (1 + fb)
            sl_ref = f"fallback_{self._settings.sl_fallback_pct}pct_above"

        # TP: at nearest support (just above)
        # Issue 1 of 2026-05-19 direction-bias fix Phase C — mirror of
        # the long-side clamp. Flag set when raw support-based TP would
        # have landed on or above current_price (the wrong side for a
        # short), and clamp DOWN to the min_tp_distance floor.
        structural_tp = 0.0
        tp_ref = ""
        is_structurally_invalid = False
        if supports:
            nearest_sup = supports[0]
            raw_tp = nearest_sup.zone_high + (nearest_sup.price * tp_buffer)
            min_tp_distance = current_price * (
                self._settings.tp_min_distance_pct / 100.0
            )
            max_tp = current_price - min_tp_distance
            if raw_tp > max_tp:
                is_structurally_invalid = True
                structural_tp = max_tp
                tp_ref = (
                    f"clamped_min_edge_${format_price(nearest_sup.price)}_"
                    f"floor={self._settings.tp_min_distance_pct:.2f}pct"
                )
            else:
                structural_tp = raw_tp
                tp_ref = f"at_support_${format_price(nearest_sup.price)}"
        else:
            fb = self._settings.tp_fallback_pct / 100.0
            structural_tp = current_price * (1 - fb)
            tp_ref = f"fallback_{self._settings.tp_fallback_pct}pct_below"

        # Fix2 (2026-06-05) — with-trend continuation TP (mirror of long). In a
        # confirmed DOWNTREND a with-trend short whose structural support TP is
        # valid (not clamped) but sits too close (reward < min_atr_mult x ATR)
        # reads as 'no reward room' (RR<1) and the brain skips the profitable
        # with-trend short — the artifact that suppressed volume. Re-anchor the TP
        # to an ATR-projected continuation target further BELOW, taking the
        # FURTHER of the two so the TP is NEVER made closer. Trend-side only;
        # ranging/uptrend/ATR<=0/already-clamped untouched; flag-gated. Pure ATR
        # projection (no level-walking -> no signed-sort-order pitfall), bounded.
        if (
            getattr(self._settings, "xray_continuation_tp_enabled", True)
            and ms.structure == "downtrend"
            and not is_structurally_invalid
            and atr_pct_h1 > 0
            and structural_tp > 0
        ):
            _atr_abs = current_price * (atr_pct_h1 / 100.0)
            _min_mult = float(getattr(
                self._settings, "continuation_tp_min_atr_mult", 1.5,
            ))
            _reward_now = current_price - structural_tp
            if 0 < _reward_now < _min_mult * _atr_abs:
                _proj_mult = float(getattr(
                    self._settings, "continuation_tp_atr_mult", 2.5,
                ))
                _proj_tp = current_price - _proj_mult * _atr_abs
                if 0 < _proj_tp < structural_tp:  # further below = more reward only
                    structural_tp = _proj_tp
                    tp_ref = f"continuation_atr_proj_{_proj_mult:.1f}xATR"

        risk = abs(structural_sl - current_price)
        reward = abs(current_price - structural_tp)
        rr_ratio = reward / risk if risk > 0 else 0.0

        # R:R quality — mark as "unknown" when both SL and TP are fallbacks
        _is_full_fallback = ("fallback" in sl_ref and "fallback" in tp_ref)
        if _is_full_fallback:
            rr_quality = "unknown"
        else:
            rr_quality = self._classify_rr(rr_ratio)
        entry_quality = self._classify_entry_short(position)

        log.debug(
            f"XRAY_LEVELS | dir=short sl=${format_price(structural_sl)} "
            f"tp=${format_price(structural_tp)} rr={rr_ratio:.2f} q={rr_quality} "
            f"invalid={is_structurally_invalid}"
        )

        return StructuralPlacement(
            structural_sl=round(structural_sl, 8),
            structural_tp=round(structural_tp, 8),
            rr_ratio=round(rr_ratio, 2),
            rr_quality=rr_quality,
            entry_quality=entry_quality,
            entry_zone_low=round(current_price, 8),
            entry_zone_high=round(structural_sl, 8),
            sl_reference=sl_ref,
            tp_reference=tp_ref,
            direction="short",
            is_fallback_rr=("fallback" in sl_ref or "fallback" in tp_ref),
            is_structurally_invalid=is_structurally_invalid,
        )

    @staticmethod
    def _classify_rr(rr: float) -> str:
        """Classify Risk:Reward ratio quality."""
        if rr >= 3.0:
            return "excellent"
        elif rr >= 2.0:
            return "good"
        elif rr >= 1.5:
            return "poor"
        return "skip"

    @staticmethod
    def _classify_entry_long(position: float) -> str:
        """Classify entry quality for longs based on position in range."""
        if position < 0.15:
            return "ideal"  # near support
        elif position < 0.30:
            return "good"
        elif position <= 0.70:
            return "mid_range"
        return "poor"  # near resistance for longs

    @staticmethod
    def _classify_entry_short(position: float) -> str:
        """Classify entry quality for shorts based on position in range."""
        if position > 0.85:
            return "ideal"  # near resistance
        elif position > 0.70:
            return "good"
        elif position >= 0.30:
            return "mid_range"
        return "poor"  # near support for shorts
