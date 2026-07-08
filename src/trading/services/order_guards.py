"""Order-placement safety guards extracted for cross-mode reuse.

P6 of P1-P10 fix series. The audit (CRITICAL-3, L4-G1, L5-G1) found
that BybitDemoOrderService.place_order bypasses every safety gate that
lives in the live ``OrderService._enforce_layer3_gate``. The most
operator-impactful regression: when Layer 3 is toggled OFF mid-cycle,
in-flight bybit_demo directives still execute (live correctly blocks).

This module re-exports the Layer-3 race-check + toggle gate as a pure
function, callable from ``Transformer._OrderProxy.place_order`` BEFORE
dispatch to the active order service. The live ``OrderService``
continues to run its own ``_enforce_layer3_gate`` (no behaviour change
for live mode); this module is invoked specifically when
``current_mode == "bybit_demo"``.

Out of scope (deferred to P11+): position-size cap, per-trade max-loss
cap, mandatory-SL guard, leverage cap, idempotent orderLinkId retry,
post-place SL verify. Those gates have larger surfaces (account-state
reads, instrument-info lookup, second-call SL verify) that warrant
their own targeted phases. The Layer-3 gate is shipped now because it
is the headline audit concern and a self-contained pure check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.log_context import ctx
from src.core.logging import get_logger

if TYPE_CHECKING:
    from src.core.layer_manager import LayerManager, LayerSnapshot

log = get_logger("worker")

# Purposes that participate in Layer-3 enforcement. Layer-4 management
# closes (layer4_close, layer4_sl) intentionally bypass — operator's
# stop-loss and position-management surfaces must remain functional
# even when Layer 3 is toggled off.
_GATED_PURPOSES = frozenset({"layer3_entry", "telegram_manual", "mcp_tool"})

# Mapping of refusal reason → operator-facing severity tag fragment.
# Used in the BYBIT_DEMO_ORDER_GATED log line so a grep for the tag
# tells operators which gate fired without parsing the message.
_REASON_TO_TAG = {
    "layer3_off": "L3_OFF",
    "layer3_race": "L3_RACE",
    "no_layer_manager": "L3_NO_LM",
}


def check_layer3_for_bybit_demo(
    *,
    layer_manager: "LayerManager | None",
    purpose: str,
    layer_snapshot: "LayerSnapshot | None",
    force: bool,
    symbol: str = "",
) -> tuple[bool, str]:
    """Decide whether a bybit_demo order should be allowed past the L3 gate.

    Returns ``(allowed, reason)``. When ``allowed`` is False, the
    Transformer proxy returns a REJECTED Order to the caller — the
    place_order call never reaches the bybit_demo adapter.

    The four refusal paths mirror live ``OrderService._enforce_layer3_gate``
    semantics but in a single pure function with no I/O (no side
    effects, no logging — caller logs the BYBIT_DEMO_ORDER_GATED tag).

    Path 1 — layer3_entry with L3 OFF: block. ``force=True`` does NOT
    bypass for layer3_entry (operator overrides go through manual
    purposes).

    Path 2 — telegram_manual / mcp_tool with L3 OFF AND ``force=False``:
    block. ``force=True`` is allowed (auditable bypass).

    Path 3 — layer3_entry with snapshot diverging from live LayerManager:
    block (L3 flipped off mid-call).

    Path 4 — LayerManager not yet attached (boot window): block gated
    purposes; allow Layer-4 management and other purposes through.

    Args:
        layer_manager: Live LayerManager instance, or None during boot.
        purpose: Caller's purpose tag (e.g., ``"layer3_entry"``).
        layer_snapshot: Optional snapshot captured at directive time.
        force: Whether the caller passed ``force=True``.
        symbol: Symbol being placed (used only in caller-side logging).

    Returns:
        ``(True, "ok")`` when the order should proceed, else
        ``(False, "<reason>")`` where reason is one of the keys in
        ``_REASON_TO_TAG``.
    """
    # Path 4 — boot window. Live OrderService has a boot deadline check
    # (lm_attach_deadline_sec). For the demo proxy we apply a simpler
    # rule: when LM is None AND purpose is gated, refuse. Layer-4
    # purposes pass-through.
    if layer_manager is None:
        if purpose in _GATED_PURPOSES:
            return False, "no_layer_manager"
        return True, "ok"

    try:
        live_l3 = bool(layer_manager.is_layer_active(3))
    except Exception:
        # If the LM check itself fails, fail-safe to allow rather than
        # silently halt all trading. Logged by caller.
        return True, "ok"

    # Path 3 — race check (only for layer3_entry with snapshot)
    if (
        purpose == "layer3_entry"
        and layer_snapshot is not None
    ):
        try:
            snap_l3 = bool(layer_snapshot.is_layer_active(3))
            if snap_l3 != live_l3:
                return False, "layer3_race"
        except Exception:
            # Snapshot inspection failed — fall through to live check.
            pass

    # Paths 1 + 2 — L3 toggle off
    if not live_l3:
        if purpose == "layer3_entry":
            return False, "layer3_off"  # force does NOT bypass entries
        if purpose in ("telegram_manual", "mcp_tool") and not force:
            return False, "layer3_off"
        # All other purposes (e.g., Layer-4 management) pass through.
        return True, "ok"

    return True, "ok"


def reason_to_tag_fragment(reason: str) -> str:
    """Map a check_layer3_for_bybit_demo refusal reason to its
    grep-friendly tag fragment for the BYBIT_DEMO_ORDER_GATED log line.
    """
    return _REASON_TO_TAG.get(reason, reason.upper())


# ═══════════════════════════════════════════════════════════════════════
# T3-1 / F-4 six-tier-fixes (2026-05-11) — five additional safety gates
# the Phase 5 audit flagged as absent on the bybit_demo path.
# orderLinkId idempotent retry is already shipped at adapter.py:968.
# ═══════════════════════════════════════════════════════════════════════


def check_mandatory_sl_for_bybit_demo(
    *, stop_loss: float | None, purpose: str,
) -> tuple[bool, str]:
    """Gate 1 — reject naked positions.

    A position opened without a stop_loss leaves the system blind to
    a worst-case adverse move. Bybit's server does NOT enforce a
    mandatory SL; without this local gate, a directive with
    ``stop_loss=None`` will silently place a naked position.

    Layer-4 management closes (purposes ``layer4_close`` / ``layer4_sl``)
    legitimately have no stop_loss — they ARE the close. Pass-through
    for those.
    """
    if purpose in ("layer4_close", "layer4_sl", "reduce_fallback"):
        return True, "ok"
    if stop_loss is None or float(stop_loss) <= 0:
        return False, "mandatory_sl_missing"
    return True, "ok"


def check_leverage_cap_for_bybit_demo(
    *, leverage: int | None, max_leverage: int,
) -> tuple[bool, str]:
    """Gate 2 — reject leverage above the operator-set cap.

    Bybit's server-side cap applies (typically 100x), but this local
    gate enforces the operator's own ceiling (e.g. 25x on conservative
    capital tier). Skipped when leverage is None or 0 (defaults
    handled elsewhere).
    """
    if leverage is None or leverage <= 0:
        return True, "ok"
    if int(leverage) > int(max_leverage):
        return False, "leverage_cap_exceeded"
    return True, "ok"


async def check_position_size_and_max_loss_for_bybit_demo(
    *,
    services,
    settings,
    symbol: str,
    qty: float,
    stop_loss: float | None,
    leverage: int | None,
    price: float | None,
) -> tuple[bool, str, dict]:
    """Gates 3 + 4 — position-size cap + per-trade max-loss cap.

    Mirrors the inline logic in live ``OrderService.place_order``
    lines 540-585 but applied to the bybit_demo path. Reads the
    account service for current equity (key tolerated in both the
    Transformer-style short form ``"account"`` and the
    WorkerManager-style long form ``"account_service"`` so the same
    helper works with either dict), the market service for a price
    fallback when the directive did not include one (same key-form
    tolerance), and ``settings.risk.max_position_size_pct`` for the
    cap.

    Gate 3 — notional value of the trade must not exceed
    ``max_position_size_pct`` of equity.
    Gate 4 — potential loss at stop_loss (with leverage) must not
    exceed 2 % of equity.

    Fails OPEN (returns True, "ok") on any I/O error so a transient
    account-service failure does not halt all trading. The caller
    sees the warning in the third element of the tuple.
    """
    try:
        # Service-key tolerance: Transformer's _active_services uses
        # short keys ("account", "position"); tests and WorkerManager
        # use the long form ("account_service", "position_service").
        # Try both so the same gate works in both contexts.
        account_svc = services.get("account_service") or services.get("account")
        if account_svc is None:
            return True, "ok", {"warn": "no_account_service"}
        account = await account_svc.get_wallet_balance()
        equity = float(getattr(account, "total_equity", 0) or 0)
        if equity <= 0:
            return True, "ok", {"warn": "equity_zero_or_unknown"}

        # Resolve a notional price for the cap math.
        eff_price = float(price) if price else 0.0
        if eff_price <= 0:
            market_svc = (
                services.get("market_service") or services.get("market")
            )
            if market_svc is not None:
                try:
                    ticker = await market_svc.get_ticker(symbol)
                    eff_price = float(getattr(ticker, "last_price", 0) or 0)
                except Exception:
                    eff_price = 0.0
        if eff_price <= 0:
            return True, "ok", {"warn": "no_price_for_cap_math"}

        notional = float(qty) * eff_price
        max_pct = float(getattr(settings.risk, "max_position_size_pct", 5.0))
        max_usd = equity * (max_pct / 100.0)
        if max_usd > 0 and notional > max_usd:
            return False, "position_size_cap_exceeded", {
                "notional": round(notional, 2),
                "max_usd": round(max_usd, 2),
                "equity": round(equity, 2),
                "max_pct": max_pct,
            }

        # Gate 4 — per-trade max-loss
        if stop_loss is not None and float(stop_loss) > 0:
            sl_dist = abs(eff_price - float(stop_loss))
            eff_lev = int(leverage) if leverage and int(leverage) > 0 else 1
            potential_loss = sl_dist * float(qty) * eff_lev
            max_loss = equity * 0.02
            if max_loss > 0 and potential_loss > max_loss and sl_dist > 0:
                return False, "per_trade_max_loss_exceeded", {
                    "potential_loss": round(potential_loss, 2),
                    "max_loss": round(max_loss, 2),
                    "equity": round(equity, 2),
                    "sl_dist": round(sl_dist, 6),
                    "eff_lev": eff_lev,
                }
        return True, "ok", {}
    except Exception as e:
        return True, "ok", {"warn": f"safety_gate_io_error:{str(e)[:60]}"}


async def verify_post_place_sl_for_bybit_demo(
    *,
    services,
    symbol: str,
    expected_sl: float | None,
    drift_tolerance_pct: float = 5.0,
) -> tuple[bool, str, dict]:
    """Gate 6 — post-place SL verification.

    After the order returns FILLED, re-fetches the position from the
    exchange and confirms a stop_loss is attached at approximately
    the expected value. If Bybit silently drops the SL (qty
    mismatch with positionIdx, instrument tick, etc.), the position
    is naked and the system would not know — this verify surfaces
    that case at WARN.

    Fails OPEN on any I/O error (returns True so the caller does not
    cascade an alert on transient failures). When ``expected_sl`` is
    None, skips with "no_expected_sl" (a directive without SL would
    have been blocked by gate 1 already).
    """
    if expected_sl is None or float(expected_sl) <= 0:
        return True, "no_expected_sl", {}
    try:
        # Same service-key tolerance pattern as
        # check_position_size_and_max_loss_for_bybit_demo above.
        position_svc = (
            services.get("position_service") or services.get("position")
        )
        if position_svc is None:
            return True, "ok", {"warn": "no_position_service"}
        pos = await position_svc.get_position(symbol)
        if pos is None:
            return False, "position_not_found_after_place", {}
        attached = getattr(pos, "stop_loss", None)
        if attached is None or float(attached) <= 0:
            return False, "stop_loss_not_attached", {
                "expected_sl": float(expected_sl),
            }
        drift_pct = (
            abs(float(attached) - float(expected_sl))
            / max(abs(float(expected_sl)), 1e-12)
            * 100.0
        )
        if drift_pct > drift_tolerance_pct:
            return False, "stop_loss_drift", {
                "expected_sl": float(expected_sl),
                "attached_sl": float(attached),
                "drift_pct": round(drift_pct, 3),
                "tolerance_pct": drift_tolerance_pct,
            }
        return True, "ok", {
            "attached_sl": float(attached),
            "drift_pct": round(drift_pct, 3),
        }
    except Exception as e:
        return True, "ok", {"warn": f"sl_verify_io_error:{str(e)[:60]}"}
