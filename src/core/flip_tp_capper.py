"""Cap structural TP on XRAY direction-flipped trades.

When the XRAY direction-flip mechanism overrides Claude's chosen
direction (in `strategy_worker._execute_claude_trade`, around line
1701-1716), it attaches `_sp.short_tp_price` or `_sp.long_tp_price` from
the structural placement of the new direction. For thinly-supported or
highly-volatile coins the structural target can sit 15-20%+ from
current price — well outside what a 10-30 minute hold can realistically
reach. The downstream `SLTPValidator.validate_tp` correctly rejects
those as nonsensical, killing the trade entirely.

This module computes the bounded TP the flip path should attach
instead, using:

  1. The volatility profile's `recommended_tp_pct` (already calibrated
     to volatility class and regime) as the soft cap.
  2. A configurable hard ceiling (default 5%) as a strategy-timeframe
     bound regardless of class.
  3. A configurable fallback (default 2%) when the volatility profile
     is unavailable.
  4. A structural buffer multiplier (default 1.0) the operator can
     raise during trial to allow more structural preservation without
     a code change.

The function is pure: no I/O, no logging, no time. Logging is the
caller's concern (Phase 1E adds the `XRAY_FLIP_TP_DERIVATION` event in
`strategy_worker._execute_claude_trade` based on the returned method
and telemetry).

TP-Volume-Closure fix Phase 1C — 2026-05-07.
"""

from __future__ import annotations

from typing import Any

from src.analysis.volatility_profile import CoinVolatilityProfile
from src.config.settings import FlipTPSettings

# Method values returned by `compute_capped_flip_tp`. Stable strings —
# they appear in log events and downstream alerting / dashboards must
# remain compatible with this set.
METHOD_DISABLED = "disabled"
METHOD_STRUCTURAL_KEPT = "structural_kept"
METHOD_VOLATILITY_CAPPED = "volatility_capped"
METHOD_HARD_CEILING = "hard_ceiling"
METHOD_FALLBACK = "fallback"


def compute_capped_flip_tp(
    symbol: str,
    direction: str,
    current_price: float,
    structural_tp: float,
    vol_profile: CoinVolatilityProfile | None,
    settings: FlipTPSettings,
) -> tuple[float, str, dict[str, Any]]:
    """Return the bounded TP price for a flipped trade.

    Args:
        symbol: Trading symbol (only used in telemetry; not in the
            cap math).
        direction: The new (post-flip) direction. "Buy" places the TP
            above `current_price`; "Sell" places it below. Case-
            insensitive; values other than buy/sell are treated as a
            no-op (returns `structural_tp`).
        current_price: Last traded price for the symbol.
        structural_tp: The structural placement's TP target for the
            flipped direction (`_sp.short_tp_price` or
            `_sp.long_tp_price` in the caller).
        vol_profile: Volatility profile from
            `VolatilityProfiler.get_profile`. May be None when the
            profiler is unavailable or a new symbol has not yet been
            classified — in that case the fallback distance is used.
        settings: `FlipTPSettings` with the cap knobs.

    Returns:
        ``(final_tp_price, method, telemetry)`` where:

        * ``final_tp_price`` is the bounded TP price (same units as
          ``structural_tp`` / ``current_price``).
        * ``method`` is one of `METHOD_*` constants describing which
          branch produced the result. Stable for log analysis.
        * ``telemetry`` is a dict for the caller to fold into a
          structured log event. Keys:
            - ``structural_dist_pct`` — abs(structural_tp - price)/price*100
            - ``vol_aware_pct``      — pre-multiplier vol_profile tp%
              (0.0 when `vol_profile` is None).
            - ``vol_aware_capped_pct`` — vol_aware_pct *
              structural_buffer_multiplier, before hard-ceiling clamp.
              0.0 when `vol_profile` is None.
            - ``hard_ceiling_pct``   — settings.hard_ceiling_pct.
            - ``chosen_cap_pct``     — the cap that was compared
              against structural_dist_pct.
            - ``chosen_dist_pct``    — the final TP distance from
              price, in percent.
    """
    # Defensive: if disabled, return the structural value unchanged so
    # the caller behaves identically to pre-fix code.
    if not settings.enabled:
        return structural_tp, METHOD_DISABLED, _telemetry(
            structural_dist_pct=_safe_dist_pct(structural_tp, current_price),
            vol_aware_pct=_vol_pct(vol_profile),
            vol_aware_capped_pct=_vol_pct(vol_profile)
            * settings.structural_buffer_multiplier,
            hard_ceiling_pct=settings.hard_ceiling_pct,
            chosen_cap_pct=0.0,
            chosen_dist_pct=_safe_dist_pct(structural_tp, current_price),
        )

    # Defensive: invalid prices — return as-is. The downstream validator
    # will catch a zero/negative price separately.
    if current_price <= 0 or structural_tp <= 0:
        return structural_tp, METHOD_STRUCTURAL_KEPT, _telemetry(
            structural_dist_pct=0.0,
            vol_aware_pct=_vol_pct(vol_profile),
            vol_aware_capped_pct=_vol_pct(vol_profile)
            * settings.structural_buffer_multiplier,
            hard_ceiling_pct=settings.hard_ceiling_pct,
            chosen_cap_pct=0.0,
            chosen_dist_pct=0.0,
        )

    # Compute the structural distance in percent. abs() because the
    # caller may pass a TP on the wrong side; the validator at the next
    # stage handles wrong-side cases. We just bound the magnitude.
    structural_dist_pct = abs(structural_tp - current_price) / current_price * 100.0

    # Determine the cap.
    vol_aware_pct = _vol_pct(vol_profile)
    vol_aware_capped_pct = vol_aware_pct * settings.structural_buffer_multiplier

    if vol_profile is not None and vol_aware_pct > 0.0:
        # Vol-profile path. Apply the multiplier, then clamp to hard
        # ceiling. The cap-side method is decided by which clamp wins.
        if vol_aware_capped_pct > settings.hard_ceiling_pct:
            cap_pct = settings.hard_ceiling_pct
            cap_method_when_applied = METHOD_HARD_CEILING
        else:
            cap_pct = vol_aware_capped_pct
            cap_method_when_applied = METHOD_VOLATILITY_CAPPED
    else:
        # No vol profile — use the configured fallback. Still clamp to
        # the hard ceiling defensively (operators may set fallback
        # higher than ceiling by mistake).
        cap_pct = min(settings.fallback_tp_distance_pct, settings.hard_ceiling_pct)
        cap_method_when_applied = METHOD_FALLBACK

    # If structural is already within the cap, keep it. The 1e-9
    # tolerance prevents floating-point drift (e.g.,
    # `(price - price * (1 - 0.039)) / price * 100 == 3.9000...0001`)
    # from spuriously tripping the cap branch when structural and cap
    # are conceptually equal.
    if structural_dist_pct <= cap_pct + 1e-9:
        return structural_tp, METHOD_STRUCTURAL_KEPT, _telemetry(
            structural_dist_pct=structural_dist_pct,
            vol_aware_pct=vol_aware_pct,
            vol_aware_capped_pct=vol_aware_capped_pct,
            hard_ceiling_pct=settings.hard_ceiling_pct,
            chosen_cap_pct=cap_pct,
            chosen_dist_pct=structural_dist_pct,
        )

    # Cap kicked in — reconstruct the TP price at `cap_pct` distance,
    # respecting the flipped direction.
    final_tp = _project_price(current_price, direction, cap_pct)
    return final_tp, cap_method_when_applied, _telemetry(
        structural_dist_pct=structural_dist_pct,
        vol_aware_pct=vol_aware_pct,
        vol_aware_capped_pct=vol_aware_capped_pct,
        hard_ceiling_pct=settings.hard_ceiling_pct,
        chosen_cap_pct=cap_pct,
        chosen_dist_pct=cap_pct,
    )


def _vol_pct(vol_profile: CoinVolatilityProfile | None) -> float:
    """Return the profile's recommended_tp_pct or 0.0."""
    if vol_profile is None:
        return 0.0
    return float(getattr(vol_profile, "recommended_tp_pct", 0.0) or 0.0)


def _safe_dist_pct(tp: float, price: float) -> float:
    """abs() distance in percent, guarded against zero price."""
    if price <= 0 or tp <= 0:
        return 0.0
    return abs(tp - price) / price * 100.0


def _project_price(current_price: float, direction: str, dist_pct: float) -> float:
    """Project a TP price at `dist_pct`% from current_price in the
    direction of `direction`.

    Buy / long → TP above price. Sell / short → TP below price. Unknown
    direction → TP above (defensive default; should never happen in
    practice because the flip path always chooses one of Buy/Sell).
    """
    factor = dist_pct / 100.0
    is_short = direction.strip().lower() in ("sell", "short")
    if is_short:
        return current_price * (1.0 - factor)
    return current_price * (1.0 + factor)


def _telemetry(
    *,
    structural_dist_pct: float,
    vol_aware_pct: float,
    vol_aware_capped_pct: float,
    hard_ceiling_pct: float,
    chosen_cap_pct: float,
    chosen_dist_pct: float,
) -> dict[str, Any]:
    """Build the telemetry dict in a fixed key order."""
    return {
        "structural_dist_pct": round(structural_dist_pct, 4),
        "vol_aware_pct": round(vol_aware_pct, 4),
        "vol_aware_capped_pct": round(vol_aware_capped_pct, 4),
        "hard_ceiling_pct": round(hard_ceiling_pct, 4),
        "chosen_cap_pct": round(chosen_cap_pct, 4),
        "chosen_dist_pct": round(chosen_dist_pct, 4),
    }
