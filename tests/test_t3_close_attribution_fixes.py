"""T3-2 + T3-3 + T3-4 close-attribution smoke tests (six-tier-fixes 2026-05-11).

Covers:
1. _build_close_order generates synthetic non-empty order_id when
   none is provided (T3-2 blank-PK collision fix).
2. _build_close_order forwards a provided order_id verbatim.
3. Synthetic order_id format matches bd-close-{symbol}-{epoch_ms}.

Pure-function tests.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_build_close_order_generates_synthetic_id_when_empty():
    from src.bybit_demo.bybit_demo_adapter import _build_close_order
    from src.core.types import Side

    order = _build_close_order(
        "TESTUSDT", Side.SELL, 10.0, 1.234, order_id="",
    )
    assert order.order_id != ""
    assert order.order_id.startswith("bd-close-TESTUSDT-")


def test_build_close_order_forwards_provided_id():
    from src.bybit_demo.bybit_demo_adapter import _build_close_order
    from src.core.types import Side

    order = _build_close_order(
        "TESTUSDT", Side.BUY, 5.0, 100.0,
        order_id="real-bybit-order-id-12345",
    )
    assert order.order_id == "real-bybit-order-id-12345"


def test_build_close_order_synthetic_format_matches_pattern():
    """Synthetic ID is greppable for audit pivots."""
    from src.bybit_demo.bybit_demo_adapter import _build_close_order
    from src.core.types import Side

    order = _build_close_order("FILUSDT", Side.SELL, 1.0, 1.0, order_id="")
    pattern = re.compile(r"^bd-close-FILUSDT-\d{10,16}$")
    assert pattern.match(order.order_id) is not None


def test_build_close_order_default_param_yields_synthetic_id():
    """Backwards-compat call (no order_id kwarg) still gets a synthetic id."""
    from src.bybit_demo.bybit_demo_adapter import _build_close_order
    from src.core.types import Side

    order = _build_close_order("FILUSDT", Side.SELL, 1.0, 1.0)
    assert order.order_id.startswith("bd-close-FILUSDT-")
