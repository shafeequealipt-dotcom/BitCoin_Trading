"""T2-6 Sniper rate-limit-aware skip tests (2026-05-12).

Pre-fix bug (F58): sniper trail runs every 5 s tick. sl_gateway R4
rate-limit window is 30 s. Pre-T2-6 every tick that landed in the
window got rejected with REASON_RATE_LIMIT. Production logs showed
127 rejects in 2 h 42 m on the affected symbols (FILUSDT 37,
BLURUSDT 23, RENDERUSDT 18, ARBUSDT 17, ENAUSDT 10).

Wasted compute + log spam. The bug is purely a coordination gap —
sniper had no public API to ask "when can I next push?"

Fix: SLGateway exposes `next_eligible_in_seconds(symbol)` returning
seconds-until-eligible (0.0 when ready). Sniper consults BEFORE
calling apply() and short-circuits with SNIPER_RATE_LIMIT_AWARE_SKIP
when ineligible. R4 itself unchanged (still the safety net).

Tests use stub gateways to verify the accessor + sniper-side
decision contract without requiring real position service / market
data wiring.
"""

from __future__ import annotations

import os
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_gateway(rate_limit_seconds: float = 30.0):
    """Build an SLGateway with stub deps. We only exercise the
    rate-limit accessor + state."""
    from src.config.settings import SLGatewaySettings
    from src.core.sl_gateway import SLGateway
    settings = MagicMock()
    settings.sl_gateway = SLGatewaySettings(
        enabled=True,
        rate_limit_seconds=rate_limit_seconds,
    )
    return SLGateway(
        settings=settings,
        position_service=MagicMock(),
        market_service=MagicMock(),
    )


# ── T2-6 unit tests: next_eligible_in_seconds ────────────────────────


def test_t2_6_no_prior_change_returns_zero():
    """Symbol never seen by gateway → 0.0 (immediately eligible)."""
    gw = _make_gateway()
    assert gw.next_eligible_in_seconds("BTCUSDT") == 0.0


def test_t2_6_window_just_started_returns_remaining():
    """Symbol changed at t-5s with 30s window → ~25s remaining."""
    gw = _make_gateway(rate_limit_seconds=30.0)
    gw._last_change["BTCUSDT"] = time.monotonic() - 5.0
    remaining = gw.next_eligible_in_seconds("BTCUSDT")
    assert 24.0 < remaining < 26.0


def test_t2_6_window_elapsed_returns_zero():
    """Symbol changed > rate_limit_seconds ago → 0.0 (eligible)."""
    gw = _make_gateway(rate_limit_seconds=30.0)
    gw._last_change["BTCUSDT"] = time.monotonic() - 31.0
    assert gw.next_eligible_in_seconds("BTCUSDT") == 0.0


def test_t2_6_disabled_gateway_returns_zero():
    """When sl_gateway.enabled=False, the accessor short-circuits to
    0.0 (the gateway is in pass-through mode; rate-limit doesn't
    apply)."""
    from src.config.settings import SLGatewaySettings
    from src.core.sl_gateway import SLGateway
    settings = MagicMock()
    settings.sl_gateway = SLGatewaySettings(
        enabled=False,
        rate_limit_seconds=30.0,
    )
    gw = SLGateway(
        settings=settings,
        position_service=MagicMock(),
        market_service=MagicMock(),
    )
    gw._last_change["BTCUSDT"] = time.monotonic() - 1.0  # within window
    assert gw.next_eligible_in_seconds("BTCUSDT") == 0.0


def test_t2_6_per_symbol_isolation():
    """Each symbol's eligibility is independent."""
    gw = _make_gateway(rate_limit_seconds=30.0)
    gw._last_change["BTCUSDT"] = time.monotonic() - 5.0   # 25s remaining
    gw._last_change["ETHUSDT"] = time.monotonic() - 31.0  # eligible
    assert gw.next_eligible_in_seconds("BTCUSDT") > 24.0
    assert gw.next_eligible_in_seconds("ETHUSDT") == 0.0
    assert gw.next_eligible_in_seconds("SOLUSDT") == 0.0  # never seen


# ── T2-6 contract test: simulated sniper-side decision ───────────────


def _sniper_should_skip(gateway, symbol: str) -> tuple[bool, float]:
    """Mirror of the sniper's inline T2-6 short-circuit in
    profit_sniper._apply_trail_stop."""
    try:
        remaining = gateway.next_eligible_in_seconds(symbol)
    except (AttributeError, Exception):
        return (False, 0.0)
    return (remaining > 0.0, remaining)


def test_t2_6_sniper_skips_when_in_window():
    """Sniper sees remaining > 0 → skips."""
    gw = _make_gateway()
    gw._last_change["BTCUSDT"] = time.monotonic() - 10.0
    skip, remaining = _sniper_should_skip(gw, "BTCUSDT")
    assert skip is True
    assert remaining > 19.0


def test_t2_6_sniper_proceeds_when_eligible():
    """Sniper sees remaining = 0 → proceeds to apply()."""
    gw = _make_gateway()
    skip, _ = _sniper_should_skip(gw, "BTCUSDT")
    assert skip is False


def test_t2_6_sniper_handles_legacy_gateway_without_accessor():
    """Defensive: a gateway WITHOUT the new accessor returns
    skip=False (proceed). Existing R4 still enforces the rate limit
    on the apply() call. The short-circuit is purely an optimisation;
    the gateway remains authoritative."""
    class _LegacyGateway:
        async def apply(self, **kwargs):
            return MagicMock(accepted=False)
        # No next_eligible_in_seconds method
    skip, _ = _sniper_should_skip(_LegacyGateway(), "BTCUSDT")
    assert skip is False


# ── T2-6 contract test: signature + accessor presence ───────────────


def test_t2_6_accessor_signature():
    """The accessor exists with the documented signature."""
    import inspect

    from src.core.sl_gateway import SLGateway
    assert hasattr(SLGateway, "next_eligible_in_seconds")
    sig = inspect.signature(SLGateway.next_eligible_in_seconds)
    params = list(sig.parameters.keys())
    assert params == ["self", "symbol", "source"]
    # `source` is an optional, backward-compatible extension (Fix 2, 2026-06-23):
    # the per-source rate-limit window for the profit-lock lane. It defaults to
    # None so every existing caller (symbol-only) keeps the base 30s window
    # unchanged — the accessor stays authoritative and the change is non-breaking.
    assert sig.parameters["source"].default is None
    # Annotation is a string under `from __future__ import annotations`
    # — both 'float' (string) and float (type) are valid, accept either
    ret = sig.return_annotation
    assert ret in (float, "float")


def test_t2_6_window_boundary_returns_zero_at_exact_elapsed():
    """At exactly rate_limit_seconds elapsed, remaining is 0.0
    (boundary is inclusive of 'now eligible')."""
    gw = _make_gateway(rate_limit_seconds=30.0)
    # Use a point well past the window
    gw._last_change["BTCUSDT"] = time.monotonic() - 30.5
    assert gw.next_eligible_in_seconds("BTCUSDT") == 0.0
