"""SL/TP Validator with headspace buffer.

Ensures every SL/TP sent to Bybit is mechanically valid.

Headspace = small buffer zone around current price.
If Claude's SL is wrong-side but within headspace -> auto-adjust.
If Claude's SL is wrong-side and beyond headspace -> skip.
If Claude's SL is beyond +/-10% of price -> skip (nonsensical).
"""

from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.utils import format_price

log = get_logger("sl_tp_validator")

# Phase 4 (P0-3) — minimum SL≠TP distance as a fraction of entry. The
# brief mandates 0.001 (10 bps). Below this gap the trade is mechanically
# nonsensical (SL/TP collapse, instant close on Shadow), and we refuse
# the directive at the validator boundary so it surfaces as a TRADE_SKIP
# with rsn=sl_equals_tp instead of vanishing as exec=0ms.
SL_TP_MIN_GAP_FRACTION_OF_ENTRY: float = 0.001  # 10 bps


class SLTPValidator:
    """Validates and auto-adjusts SL/TP before sending to Bybit."""

    def __init__(
        self,
        headspace_pct: float = 1.5,
        max_distance_pct: float = 10.0,
        min_sl_distance_pct: float = 1.5,
    ):
        self.headspace_pct = headspace_pct / 100.0
        self.max_distance_pct = max_distance_pct / 100.0
        # F37 (2026-06-05) — minimum SL distance from entry (fraction). A
        # CORRECT-side stop closer than this is clamped OUT to this distance
        # before execution (the prompt's "SL at least 1.5% from entry" rule had
        # no downstream enforcement, so a stop 0.3% from entry passed straight
        # through). Entry-stop safety net only; the exit systems are untouched.
        # Wrong-side stops are already auto-fixed via the headspace branch below.
        self.min_sl_distance_pct = min_sl_distance_pct / 100.0

    def validate_sl(
        self, sl_price: float, current_price: float, direction: str, symbol: str = "",
    ) -> tuple[str, float, str]:
        """Validate a stop-loss price.

        Returns (action, adjusted_price, reason)
        action: "SET" (valid), "ADJUST" (auto-corrected), "SKIP" (too far)
        """
        if current_price <= 0 or sl_price <= 0:
            log.warning(f"SLTP_SKIP | type=SL sym={symbol} reason=invalid_price sl={sl_price} price={current_price} | {ctx()}")
            return "SKIP", 0, "invalid price (zero or negative)"

        distance_pct = abs(sl_price - current_price) / current_price

        if distance_pct > self.max_distance_pct:
            log.warning(f"SLTP_SKIP | type=SL sym={symbol} dist={distance_pct*100:.1f}% max={self.max_distance_pct*100:.0f}% | {ctx()}")
            return "SKIP", 0, (
                f"SL ${format_price(sl_price)} is {distance_pct * 100:.1f}% from price "
                f"${format_price(current_price)} (max {self.max_distance_pct * 100:.0f}%) — nonsensical"
            )

        is_buy = direction.lower() in ("buy", "long")

        if is_buy:
            if sl_price < current_price:
                # F37: correct side, but enforce the minimum distance from entry.
                below_pct = (current_price - sl_price) / current_price
                if below_pct < self.min_sl_distance_pct:
                    adjusted = round(current_price * (1 - self.min_sl_distance_pct), 8)
                    log.warning(
                        f"SLTP_MIN_DISTANCE | type=SL sym={symbol} side=Buy "
                        f"dist={below_pct*100:.2f}% min={self.min_sl_distance_pct*100:.2f}% "
                        f"old={format_price(sl_price)} new={format_price(adjusted)} | {ctx()}"
                    )
                    return "ADJUST", adjusted, (
                        f"min-distance: SL ${format_price(sl_price)} was only "
                        f"{below_pct*100:.2f}% below entry (min "
                        f"{self.min_sl_distance_pct*100:.2f}%) — moved to "
                        f"${format_price(adjusted)}"
                    )
                return "SET", sl_price, "valid (below price for buy)"
            above_pct = (sl_price - current_price) / current_price
            if above_pct <= self.headspace_pct:
                adjusted = round(current_price * (1 - self.headspace_pct), 8)
                log.info(
                    "SL auto-adjusted {sym}: ${old:.4f} -> ${new:.4f} "
                    "(was {pct:.2f}% above, within headspace)",
                    sym=symbol, old=sl_price, new=adjusted, pct=above_pct * 100,
                )
                return "ADJUST", adjusted, (
                    f"auto-adjusted from ${format_price(sl_price)} to ${adjusted:.4f} "
                    f"(was above price by {above_pct * 100:.2f}%, within headspace)"
                )
            else:
                # Auto-fix to correct side instead of rejecting
                adjusted = round(current_price * (1 - self.headspace_pct), 8)
                log.warning(
                    "SL auto-fixed {sym}: ${old:.4f} was {pct:.2f}% wrong-side for Buy -> ${new:.4f}",
                    sym=symbol, old=sl_price, pct=above_pct * 100, new=adjusted,
                )
                return "ADJUST", adjusted, (
                    f"auto-fixed from wrong side ${format_price(sl_price)} to ${adjusted:.4f} "
                    f"(was {above_pct * 100:.2f}% above price for Buy)"
                )
        else:
            if sl_price > current_price:
                # F37: correct side, but enforce the minimum distance from entry.
                above_pct = (sl_price - current_price) / current_price
                if above_pct < self.min_sl_distance_pct:
                    adjusted = round(current_price * (1 + self.min_sl_distance_pct), 8)
                    log.warning(
                        f"SLTP_MIN_DISTANCE | type=SL sym={symbol} side=Sell "
                        f"dist={above_pct*100:.2f}% min={self.min_sl_distance_pct*100:.2f}% "
                        f"old={format_price(sl_price)} new={format_price(adjusted)} | {ctx()}"
                    )
                    return "ADJUST", adjusted, (
                        f"min-distance: SL ${format_price(sl_price)} was only "
                        f"{above_pct*100:.2f}% above entry (min "
                        f"{self.min_sl_distance_pct*100:.2f}%) — moved to "
                        f"${format_price(adjusted)}"
                    )
                return "SET", sl_price, "valid (above price for sell)"
            below_pct = (current_price - sl_price) / current_price
            if below_pct <= self.headspace_pct:
                adjusted = round(current_price * (1 + self.headspace_pct), 8)
                log.info(
                    "SL auto-adjusted {sym}: ${old:.4f} -> ${new:.4f} "
                    "(was {pct:.2f}% below, within headspace)",
                    sym=symbol, old=sl_price, new=adjusted, pct=below_pct * 100,
                )
                return "ADJUST", adjusted, (
                    f"auto-adjusted from ${format_price(sl_price)} to ${adjusted:.4f} "
                    f"(was below price by {below_pct * 100:.2f}%, within headspace)"
                )
            else:
                # Auto-fix to correct side instead of rejecting
                adjusted = round(current_price * (1 + self.headspace_pct), 8)
                log.warning(
                    "SL auto-fixed {sym}: ${old:.4f} was {pct:.2f}% wrong-side for Sell -> ${new:.4f}",
                    sym=symbol, old=sl_price, pct=below_pct * 100, new=adjusted,
                )
                return "ADJUST", adjusted, (
                    f"auto-fixed from wrong side ${format_price(sl_price)} to ${adjusted:.4f} "
                    f"(was {below_pct * 100:.2f}% below price for Sell)"
                )

    def validate_tp(
        self, tp_price: float, current_price: float, direction: str, symbol: str = "",
    ) -> tuple[str, float, str]:
        """Validate a take-profit price. Mirror of SL logic but reversed."""
        if current_price <= 0 or tp_price <= 0:
            return "SKIP", 0, "invalid price"

        distance_pct = abs(tp_price - current_price) / current_price
        if distance_pct > self.max_distance_pct:
            return "SKIP", 0, f"TP ${format_price(tp_price)} is {distance_pct * 100:.1f}% from price — nonsensical"

        is_buy = direction.lower() in ("buy", "long")

        if is_buy:
            if tp_price > current_price:
                return "SET", tp_price, "valid"
            below_pct = (current_price - tp_price) / current_price
            if below_pct <= self.headspace_pct:
                adjusted = round(current_price * (1 + self.headspace_pct), 8)
                return "ADJUST", adjusted, f"auto-adjusted TP to ${adjusted:.4f}"
            else:
                adjusted = round(current_price * (1 + self.headspace_pct), 8)
                return "ADJUST", adjusted, f"auto-fixed TP from wrong side to ${adjusted:.4f} for Buy"
        else:
            if tp_price < current_price:
                return "SET", tp_price, "valid"
            above_pct = (tp_price - current_price) / current_price
            if above_pct <= self.headspace_pct:
                adjusted = round(current_price * (1 - self.headspace_pct), 8)
                return "ADJUST", adjusted, f"auto-adjusted TP to ${adjusted:.4f}"
            else:
                adjusted = round(current_price * (1 - self.headspace_pct), 8)
                return "ADJUST", adjusted, f"auto-fixed TP from wrong side to ${adjusted:.4f} for Sell"

    def validate_sl_structural(
        self,
        sl_price: float,
        current_price: float,
        direction: str,
        symbol: str = "",
        structural_data: dict | None = None,
    ) -> tuple[str, float, str]:
        """Validate SL with X-RAY structural level awareness.

        Runs standard validation first, then checks if the SL sits inside
        a support/resistance zone (stop-hunt risk). If so, suggests moving
        the SL just beyond the structural level.

        Advisory only — logs warnings but does not block trades.
        Falls back to standard validate_sl() when no structural data.
        """
        action, adjusted, reason = self.validate_sl(sl_price, current_price, direction, symbol)

        if action == "SKIP" or not structural_data:
            return action, adjusted, reason

        is_buy = direction.lower() in ("buy", "long")
        levels = (
            structural_data.get("support_levels", []) if is_buy
            else structural_data.get("resistance_levels", [])
        )

        for level in levels:
            level_price = level.get("price", 0)
            if level_price <= 0:
                continue

            dist = abs(adjusted - level_price) / level_price if level_price else 1.0
            if dist < 0.002:  # Within 0.2% of a structural level
                buffer_pct = 0.005  # 0.5% beyond structural level
                if is_buy:
                    better_sl = round(level_price * (1 - buffer_pct), 8)
                else:
                    better_sl = round(level_price * (1 + buffer_pct), 8)

                log.info(
                    f"XRAY_SL_ADJUST | sym={symbol} old_sl={adjusted:.4f} "
                    f"struct_level={format_price(level_price)} new_sl={format_price(better_sl)}"
                    f"touches={level.get('touches', 0)} | {ctx()}"
                )
                return "ADJUST", better_sl, (
                    f"structural: SL moved from ${adjusted:.4f} to ${format_price(better_sl)} "
                    f"(beyond {level.get('level_type', 'S/R')} at ${format_price(level_price)} "
                    f"with {level.get('touches', 0)} touches)"
                )

        # Log structural R:R if placement data available
        placement = structural_data.get("structural_placement")
        if placement and placement.get("rr_ratio"):
            log.info(
                f"XRAY_SLTP | sym={symbol} sl=${adjusted:.4f} "
                f"struct_rr={placement['rr_ratio']:.2f} "
                f"rr_quality={placement.get('rr_quality', 'n/a')} | {ctx()}"
            )

        return action, adjusted, reason

    def validate_tp_structural(
        self,
        tp_price: float,
        current_price: float,
        direction: str,
        symbol: str = "",
        structural_data: dict | None = None,
    ) -> tuple[str, float, str]:
        """Validate TP with X-RAY structural awareness.

        Checks if TP targets are aligned with structural resistance/support.
        Advisory only — logs info but does not block trades.
        """
        action, adjusted, reason = self.validate_tp(tp_price, current_price, direction, symbol)

        if action == "SKIP" or not structural_data:
            return action, adjusted, reason

        is_buy = direction.lower() in ("buy", "long")
        levels = (
            structural_data.get("resistance_levels", []) if is_buy
            else structural_data.get("support_levels", [])
        )

        # Check if TP is beyond the nearest resistance/support
        if levels:
            nearest = levels[0]
            nearest_price = nearest.get("price", 0)
            if nearest_price > 0:
                if is_buy and adjusted > nearest_price:
                    log.info(
                        f"XRAY_TP_NOTE | sym={symbol} tp=${adjusted:.4f} "
                        f"beyond_resistance=${format_price(nearest_price)} "
                        f"(TP may not be reached) | {ctx()}"
                    )
                elif not is_buy and adjusted < nearest_price:
                    log.info(
                        f"XRAY_TP_NOTE | sym={symbol} tp=${adjusted:.4f} "
                        f"beyond_support=${format_price(nearest_price)} "
                        f"(TP may not be reached) | {ctx()}"
                    )

        return action, adjusted, reason

    def set_headspace(self, pct: float) -> None:
        """Update headspace percentage (called from Telegram)."""
        self.headspace_pct = max(0.003, min(0.05, pct / 100.0))
        log.info("Headspace updated to {pct:.1f}%", pct=self.headspace_pct * 100)

    def validate_pair(
        self,
        sl_price: float,
        tp_price: float,
        entry_price: float,
        current_price: float,
        direction: str,
        symbol: str = "",
    ) -> tuple[str, str]:
        """Phase 4 (P0-3 Fix B) — refuse trades whose SL and TP collapse.

        A trade where ``abs(sl - tp) / entry < 0.1%`` is mechanically
        nonsensical: SL and TP are within one tick of each other, so on
        any micro-movement Shadow flips between hit-SL and hit-TP and
        the position closes instantly with nothing to evaluate. The
        previous code path executed the order, Shadow rejected or
        immediately closed it, and the trade vanished from BRAIN_DO_TRADE
        as ``exec=0ms`` with no skip reason logged.

        This validator is invoked BEFORE order placement in
        ``strategy_worker._execute_claude_trade``. On SKIP the caller
        emits ``TRADE_SKIP | rsn=sl_equals_tp`` so the trade is visible
        in the per-trade reason summary at layer_manager.py:744.

        Args:
            sl_price: Stop-loss price.
            tp_price: Take-profit price.
            entry_price: Entry reference (defaults to current_price when
                the trade hasn't opened yet — pass ``current_price`` for
                pre-execution validation).
            current_price: Current market price (used as entry fallback).
            direction: ``"Buy"`` / ``"Sell"`` / ``"Long"`` / ``"Short"``.
            symbol: For logging only.

        Returns:
            ("OK", "")  -> SL/TP gap is acceptable.
            ("SKIP", "sl_equals_tp")  -> gap collapsed below threshold.
            ("SKIP", "invalid_price") -> sl, tp, or entry is non-positive.
            ("SKIP", "wrong_side")    -> sl/tp on the wrong side relative
                to entry for the chosen direction (would close the trade
                immediately at open).
        """
        if sl_price <= 0 or tp_price <= 0:
            log.warning(
                f"SLTP_PAIR_SKIP | sym={symbol} rsn=invalid_price "
                f"sl={sl_price} tp={tp_price} | {ctx()}"
            )
            return "SKIP", "invalid_price"

        ref = entry_price if entry_price > 0 else current_price
        if ref <= 0:
            log.warning(
                f"SLTP_PAIR_SKIP | sym={symbol} rsn=invalid_price "
                f"entry={entry_price} current={current_price} | {ctx()}"
            )
            return "SKIP", "invalid_price"

        gap = abs(sl_price - tp_price)
        gap_frac = gap / ref
        if gap_frac < SL_TP_MIN_GAP_FRACTION_OF_ENTRY:
            log.warning(
                f"SLTP_PAIR_SKIP | sym={symbol} rsn=sl_equals_tp "
                f"sl={format_price(sl_price, ref)} tp={format_price(tp_price, ref)} "
                f"entry={format_price(ref)} delta_bps={gap_frac * 10000:.2f} "
                f"min_bps={SL_TP_MIN_GAP_FRACTION_OF_ENTRY * 10000:.2f} | {ctx()}"
            )
            return "SKIP", "sl_equals_tp"

        # Direction sanity: SL and TP must straddle entry on the right
        # side for the chosen direction. validate_sl/_tp already auto-
        # adjust within headspace — by the time validate_pair runs they
        # should have been straightened. If they're STILL wrong here it
        # means caller skipped the per-leg validation; we refuse loudly.
        is_buy = direction.lower() in ("buy", "long")
        if is_buy and not (sl_price < ref < tp_price):
            log.warning(
                f"SLTP_PAIR_SKIP | sym={symbol} rsn=wrong_side dir=Buy "
                f"sl={format_price(sl_price, ref)} entry={format_price(ref)} "
                f"tp={format_price(tp_price, ref)} | {ctx()}"
            )
            return "SKIP", "wrong_side"
        if (not is_buy) and not (tp_price < ref < sl_price):
            log.warning(
                f"SLTP_PAIR_SKIP | sym={symbol} rsn=wrong_side dir=Sell "
                f"sl={format_price(sl_price, ref)} entry={format_price(ref)} "
                f"tp={format_price(tp_price, ref)} | {ctx()}"
            )
            return "SKIP", "wrong_side"

        # Observability G10 — success-path emission. The audit
        # (2026-05-13) noted SLTP_VALIDATE fires zero times. Investigation
        # confirmed only the SKIP paths emitted via SLTP_PAIR_SKIP; the
        # OK return was silent. Operators could not distinguish "validator
        # ran and passed" from "validator never ran" — the audit's F-69
        # BLUR-invalid-SL case stayed invisible until the trade closed
        # at a wrong price.
        #
        # Field set matches the audit schema: sym, side, sl_pct, tp_pct,
        # delta_bps (existing in skip path), max_dist_pct, min_gap_bps,
        # decision, and ``checks`` (list of gates the directive cleared
        # to reach this return). The checks= field is a static literal
        # documenting validator coverage so a future regression that
        # removes a gate is grep-detectable at log-tail time.
        _sl_pct = abs(sl_price - ref) / ref * 100.0
        _tp_pct = abs(tp_price - ref) / ref * 100.0
        log.info(
            f"SLTP_PAIR_OK | sym={symbol} side={'Buy' if is_buy else 'Sell'} "
            f"sl_pct={_sl_pct:.3f} tp_pct={_tp_pct:.3f} "
            f"delta_bps={gap_frac * 10000:.2f} "
            f"max_dist_pct={self.max_distance_pct * 100:.0f} "
            f"min_gap_bps={SL_TP_MIN_GAP_FRACTION_OF_ENTRY * 10000:.2f} "
            f"decision=OK "
            f"checks=invalid_price,sl_equals_tp,wrong_side | {ctx()}"
        )
        return "OK", ""
