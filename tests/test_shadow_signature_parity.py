"""Phase 1 (post-Layer-1 fix) — Shadow ↔ Live signature parity.

Walks every public method on the live Bybit service classes and asserts
that the corresponding ``Shadow*`` adapter exposes a method with an
identical signature: same parameter NAMES, same parameter KINDS
(positional / keyword-only / etc.), same DEFAULT values.

Why this test exists
--------------------

Layer 1 restructure Phase 2 added three keyword-only arguments to
``OrderService.place_order``: ``purpose``, ``layer_snapshot``, ``force``.
The ``ShadowOrderService.place_order`` mirror was not updated. Result:
**every brain-driven paper trade crashed** with::

    TypeError: ShadowOrderService.place_order() got an unexpected
        keyword argument 'purpose'

Confirmed in the 2026-04-27 06:23-06:52 UTC live monitor (4 crashes
across DYDX/RUNE/ETH). See ``dev_notes/phase0_post_layer1_fixes/
issue_1_shadow_signature.md`` for the investigation.

This test prevents the same drift from re-occurring whenever a future
contract change lands on the live side without a matching update on the
Shadow side.

Scope
-----

- ``OrderService`` ↔ ``ShadowOrderService``
- ``PositionService`` ↔ ``ShadowPositionService``
- ``AccountService`` ↔ ``ShadowAccountService``

Direction
---------

The test enforces ``Live ⊆ Shadow`` for **method coverage** and
**signature shape**. Shadow may carry extra helpers (e.g. the
``health_check`` it uses to probe the Shadow HTTP listener) — those
are not required on the live side. Drift is only flagged when a public
live method is missing on Shadow OR when a shared method's signature
diverges.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from src.shadow.shadow_adapter import (
    ShadowAccountService,
    ShadowOrderService,
    ShadowPositionService,
)
from src.trading.services.account_service import AccountService
from src.trading.services.order_service import OrderService
from src.trading.services.position_service import PositionService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _public_methods(cls: type) -> dict[str, inspect.Signature]:
    """Return ``{name: signature}`` for every public async method on ``cls``.

    Public = does not start with ``_``. Async-only filter prevents picking
    up incidental properties / classmethods that are not part of the
    service contract.
    """
    out: dict[str, inspect.Signature] = {}
    for name, member in inspect.getmembers(cls, predicate=inspect.iscoroutinefunction):
        if name.startswith("_"):
            continue
        out[name] = inspect.signature(member)
    return out


def _normalize_param(p: inspect.Parameter) -> tuple[str, str, Any]:
    """Reduce a parameter to (name, kind_name, default) for comparison.

    The annotation is intentionally NOT compared. Annotations diverge
    between the two services in benign ways (Shadow uses string forward
    references for ``LayerSnapshot``; live uses the same). What matters
    for runtime call compatibility is the name, kind, and default.
    """
    return (p.name, p.kind.name, p.default)


def _assert_signature_match(
    live_cls: type,
    shadow_cls: type,
    method: str,
    live_sig: inspect.Signature,
    shadow_sig: inspect.Signature,
) -> None:
    """Compare two signatures parameter-by-parameter, fail with a useful message."""
    live_params = [_normalize_param(p) for p in live_sig.parameters.values()]
    shadow_params = [_normalize_param(p) for p in shadow_sig.parameters.values()]

    if live_params == shadow_params:
        return

    # Build a diff-like message showing exactly which parameters differ
    # so the failing assertion is actionable.
    lines: list[str] = [
        f"\nSignature drift detected on {live_cls.__name__}.{method} ↔ "
        f"{shadow_cls.__name__}.{method}:",
        f"  live   = {live_sig}",
        f"  shadow = {shadow_sig}",
        "",
        "  Per-parameter diff (name, kind, default):",
    ]
    for live_p, shadow_p in zip(live_params, shadow_params, strict=False):
        marker = "  " if live_p == shadow_p else "≠ "
        lines.append(f"    {marker}live={live_p}  shadow={shadow_p}")
    if len(live_params) != len(shadow_params):
        lines.append(
            f"    PARAMETER COUNT DIFFERS: live has {len(live_params)}, "
            f"shadow has {len(shadow_params)}"
        )

    pytest.fail("\n".join(lines))


SERVICE_PAIRS = [
    pytest.param(OrderService, ShadowOrderService, id="OrderService"),
    pytest.param(PositionService, ShadowPositionService, id="PositionService"),
    pytest.param(AccountService, ShadowAccountService, id="AccountService"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("live_cls,shadow_cls", SERVICE_PAIRS)
def test_shadow_implements_every_public_live_method(
    live_cls: type, shadow_cls: type
) -> None:
    """Every public async method on the live service must exist on Shadow."""
    live_methods = set(_public_methods(live_cls).keys())
    shadow_methods = set(_public_methods(shadow_cls).keys())
    missing = sorted(live_methods - shadow_methods)
    assert not missing, (
        f"\n{shadow_cls.__name__} is missing public methods present on "
        f"{live_cls.__name__}: {missing}\n"
        "  Add a Shadow stub with the SAME signature (it may delegate to "
        "an existing Shadow helper or no-op for paper mode)."
    )


@pytest.mark.parametrize("live_cls,shadow_cls", SERVICE_PAIRS)
def test_shadow_method_signatures_match_live(
    live_cls: type, shadow_cls: type
) -> None:
    """For every method shared by Live and Shadow, signatures must match."""
    live_sigs = _public_methods(live_cls)
    shadow_sigs = _public_methods(shadow_cls)
    shared = sorted(set(live_sigs) & set(shadow_sigs))
    assert shared, (
        f"No shared public methods between {live_cls.__name__} and "
        f"{shadow_cls.__name__}. The pairing is broken."
    )

    for method in shared:
        _assert_signature_match(
            live_cls, shadow_cls, method, live_sigs[method], shadow_sigs[method]
        )


def test_place_order_accepts_phase2_kwargs() -> None:
    """Direct regression test for the original bug.

    Calling ``ShadowOrderService.place_order`` with ``purpose``,
    ``layer_snapshot``, ``force`` keyword arguments must NOT raise
    ``TypeError`` at signature binding time. We exercise binding only —
    the actual HTTP call is left untouched (no Shadow listener required).
    """
    from src.core.types import OrderType, Side

    sig = inspect.signature(ShadowOrderService.place_order)
    # Bind a fully populated argument set — equivalent to the real
    # caller in src/workers/strategy_worker.py:1232-1242.
    bound = sig.bind(
        self=None,
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=0.01,
        stop_loss=49000.0,
        take_profit=51000.0,
        leverage=5,
        purpose="layer3_entry",
        layer_snapshot=None,
        force=False,
    )
    # Defaults application is the real test — bound is throwaway.
    bound.apply_defaults()
    assert bound.arguments["purpose"] == "layer3_entry"
    assert bound.arguments["force"] is False
