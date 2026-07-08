"""P3-2 (2026-05-13) — Watchdog SL gateway rate-limit-aware short-circuit.

Validates the change to ``PositionWatchdog._push_sl_to_shadow`` that
extends the T2-6 sniper pattern to the four uncoordinated SL update
sources that funnel through this wrapper:

  - ``trail_update``
  - ``sentinel_deadline``
  - ``sentinel_advisor``
  - ``trail_activation``

The fix adds a single pre-check at the top of ``_push_sl_to_shadow``:
if ``sl_gateway.next_eligible_in_seconds(symbol) > 0`` the helper
emits ``SNIPER_RATE_LIMIT_AWARE_SKIP src=<source>`` at INFO and
returns ``False`` without calling ``sl_gateway.apply`` — saving a
guaranteed-to-fail apply round-trip while preserving the gateway's
R4 enforcement as a safety net.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from tests.test_watchdog.test_position_watchdog import _make_watchdog


def _build_gateway_with_remaining_seconds(remaining: float) -> MagicMock:
    """Stub a gateway whose ``next_eligible_in_seconds`` returns ``remaining``
    and whose ``apply`` would succeed if called (so we can assert it ISN'T)."""
    gw = MagicMock()
    gw.next_eligible_in_seconds = MagicMock(return_value=float(remaining))
    gw.apply = AsyncMock(return_value=MagicMock(accepted=True))
    return gw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "trail_update",
        "sentinel_deadline",
        "sentinel_advisor",
        "trail_activation",
    ],
)
async def test_rate_limit_aware_skip_fires_for_each_uncoordinated_source(
    watchdog_settings, source
) -> None:
    """Each of the 4 sources hits the new short-circuit when the gateway is
    rate-limited and the SKIP tag carries the correct ``src=`` value."""
    gw = _build_gateway_with_remaining_seconds(5.7)
    wd = _make_watchdog(watchdog_settings)
    wd.sl_gateway = gw

    captured: list[str] = []
    sink = logger.add(
        lambda m: captured.append(str(m)), level="INFO",
    )
    try:
        result = await wd._push_sl_to_shadow(
            symbol="BTCUSDT",
            new_sl=29000.0,
            plan=MagicMock(stop_loss_price=29500.0),
            current_shadow_sl=29500.0,
            direction="Sell",
            source=source,
        )
    finally:
        logger.remove(sink)

    assert result is False, f"source={source} should return False on rate-limited gateway"
    gw.apply.assert_not_awaited(), (
        f"source={source}: apply must NOT be called when gateway is rate-limited"
    )
    joined = "\n".join(captured)
    assert "SNIPER_RATE_LIMIT_AWARE_SKIP" in joined, (
        f"source={source}: missing SKIP tag: {joined[:500]}"
    )
    assert f"src={source}" in joined, (
        f"source={source}: SKIP tag must carry src=<source>: {joined[:500]}"
    )
    assert "next_eligible_in_s=5.7" in joined, (
        f"source={source}: SKIP must report remaining seconds: {joined[:500]}"
    )


@pytest.mark.asyncio
async def test_rate_limit_aware_skip_does_not_fire_when_eligible(
    watchdog_settings,
) -> None:
    """When the gateway is eligible (remaining=0.0), apply proceeds normally."""
    gw = _build_gateway_with_remaining_seconds(0.0)
    wd = _make_watchdog(watchdog_settings)
    wd.sl_gateway = gw

    captured: list[str] = []
    sink = logger.add(
        lambda m: captured.append(str(m)), level="INFO",
    )
    try:
        # New SL is different enough from current to not trip the no-op
        # guard, and far enough from current that the coalesce window is
        # inactive (since this is a fresh PositionWatchdog).
        result = await wd._push_sl_to_shadow(
            symbol="ETHUSDT",
            new_sl=3500.0,
            plan=MagicMock(stop_loss_price=3700.0),
            current_shadow_sl=3700.0,
            direction="Sell",
            source="trail_update",
        )
    finally:
        logger.remove(sink)

    # SKIP must NOT fire on an eligible window.
    joined = "\n".join(captured)
    assert "SNIPER_RATE_LIMIT_AWARE_SKIP" not in joined, (
        f"eligible window should not produce SKIP: {joined[:500]}"
    )
    # Apply SHOULD have been called.
    gw.apply.assert_awaited_once()
    assert result is True


@pytest.mark.asyncio
async def test_rate_limit_aware_skip_does_not_advance_coalesce_window(
    watchdog_settings,
) -> None:
    """A blocked call must NOT update the source-specific coalesce timestamp,
    so the next legitimate retry after the 30 s window passes is not
    silently coalesced away."""
    gw = _build_gateway_with_remaining_seconds(5.0)
    wd = _make_watchdog(watchdog_settings)
    wd.sl_gateway = gw

    await wd._push_sl_to_shadow(
        symbol="SOLUSDT",
        new_sl=100.0,
        plan=MagicMock(stop_loss_price=110.0),
        current_shadow_sl=110.0,
        direction="Sell",
        source="trail_update",
    )
    # If the blocked call had advanced the coalesce timestamp, the dict
    # would contain SOLUSDT. The fix is correct ONLY if the coalesce
    # window is untouched on rate-limit blocked calls.
    last_trail = getattr(wd, "_last_trail_push_at", {})
    assert "SOLUSDT" not in last_trail, (
        f"rate-limit-skipped call must not advance trail coalesce: {last_trail}"
    )


@pytest.mark.asyncio
async def test_no_gateway_falls_back_to_legacy_path(watchdog_settings) -> None:
    """Backwards-compat: when ``sl_gateway`` is None, _push_sl_to_shadow still
    follows the legacy direct-set_stop_loss path (no new rate-limit check)."""
    wd = _make_watchdog(watchdog_settings)
    wd.sl_gateway = None
    wd.position_service = MagicMock()
    wd.position_service.set_stop_loss = AsyncMock(return_value=True)

    captured: list[str] = []
    sink = logger.add(
        lambda m: captured.append(str(m)), level="INFO",
    )
    try:
        result = await wd._push_sl_to_shadow(
            symbol="LINKUSDT",
            new_sl=15.0,
            plan=MagicMock(stop_loss_price=17.0),
            current_shadow_sl=17.0,
            direction="Sell",
            source="trail_update",
        )
    finally:
        logger.remove(sink)

    # Legacy path called set_stop_loss directly; no SKIP must fire because
    # the gateway is None (the pre-check guards on sl_gateway is not None).
    joined = "\n".join(captured)
    assert "SNIPER_RATE_LIMIT_AWARE_SKIP" not in joined, joined[:500]
    wd.position_service.set_stop_loss.assert_awaited_once()
    assert result is True
