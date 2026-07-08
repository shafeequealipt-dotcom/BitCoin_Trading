"""Phase 2 (post-Layer-1 fix, audit) — attach_layer_manager reaches the
underlying OrderService through the Transformer.

The Transformer wraps the live and shadow services in `_OrderProxy` for
live mode-switching. The proxy does NOT expose ``attach_layer_manager``
(it only forwards ``place_order`` / ``modify_order`` / etc.), so the
naïve ``hasattr(services["order_service"], "attach_layer_manager")``
check skips. WorkerManager must walk the transformer's owned service
sets (``_bybit_services["order"]`` and ``_shadow_services["order"]``)
to actually attach the LM to the underlying ``OrderService`` instance.

Without this, the underlying live OrderService keeps ``_layer_manager
= None`` permanently — meaning the Phase 1 (post-Layer-1) boot-policy
treats every entry-side trade as "boot-window" and rejects with
``Layer3BootNotReadyError``, hard-breaking live Bybit mode.

Investigation: dev_notes/phase0_post_layer1_fixes/issue_2_fail_open_gate.md.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _read_manager_source() -> str:
    from pathlib import Path
    return (
        Path(__file__).parent.parent / "src" / "workers" / "manager.py"
    ).read_text()


def test_manager_walks_transformer_to_attach_layer_manager() -> None:
    """The wiring must walk transformer._bybit_services / _shadow_services
    so the LM reaches the underlying OrderService, not just the proxy."""
    src = _read_manager_source()
    # Both service sets must be visited.
    assert '"_bybit_services"' in src and '"_shadow_services"' in src, (
        "WorkerManager.initialize must walk the transformer's owned service "
        "sets to attach LayerManager to the underlying OrderService. The "
        "proxy from create_proxies has no attach_layer_manager."
    )
    # The walk attaches via hasattr-guarded call (defensive against future
    # service classes that don't expose the method).
    assert 'underlying.attach_layer_manager(layer_manager)' in src


def test_underlying_order_service_has_attach_method() -> None:
    """OrderService must define attach_layer_manager (the contract the
    walk-through relies on)."""
    from src.trading.services.order_service import OrderService
    assert hasattr(OrderService, "attach_layer_manager"), (
        "OrderService dropped attach_layer_manager — Phase 2 contract broken."
    )


def test_proxy_does_not_have_attach_method() -> None:
    """Sanity: this test exists so a future refactor that ADDS
    attach_layer_manager to _OrderProxy doesn't go unnoticed. If the proxy
    grows the method, the manager.py walk-through can be simplified.
    """
    from src.core.transformer import _OrderProxy
    # Today the proxy does NOT have it.
    assert not hasattr(_OrderProxy(MagicMock()), "attach_layer_manager"), (
        "_OrderProxy now exposes attach_layer_manager — the WorkerManager "
        "walk-through can be replaced with a direct call. Update Phase 2 "
        "wiring + this test."
    )


def test_attach_call_is_idempotent() -> None:
    """Calling attach_layer_manager twice with the same LM is a no-op
    (per OrderService.attach_layer_manager docstring)."""
    from src.trading.services.order_service import OrderService

    svc = OrderService.__new__(OrderService)
    svc._layer_manager = None  # type: ignore[attr-defined]

    fake_lm_1 = MagicMock(name="lm1")
    fake_lm_2 = MagicMock(name="lm1")  # same reference behaviour by id
    svc.attach_layer_manager(fake_lm_1)
    assert svc._layer_manager is fake_lm_1
    svc.attach_layer_manager(fake_lm_1)  # idempotent same-instance
    assert svc._layer_manager is fake_lm_1
    # Re-attaching a DIFFERENT LM does swap (rare; covered for completeness).
    svc.attach_layer_manager(fake_lm_2)
    assert svc._layer_manager is fake_lm_2
