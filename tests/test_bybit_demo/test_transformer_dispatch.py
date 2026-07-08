"""Smoke test for Transformer 3-mode dispatch (shadow / bybit / bybit_demo).

Verifies the additive bybit_demo slot in Transformer routes correctly
without breaking the existing shadow / bybit dispatch.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.transformer import Transformer


class _StubAccountSvc:
    """Stand-in account service that just identifies its mode."""

    def __init__(self, label: str) -> None:
        self.label = label

    async def health_check(self) -> bool:
        return True

    async def get_wallet_balance(self) -> Any:  # not exercised here
        return None


def _make_transformer() -> Transformer:
    """Build a Transformer with no DB (we won't call initialize)."""
    return Transformer(db=None, config=None)  # type: ignore[arg-type]


def test_three_mode_apply_routes_to_correct_dict() -> None:
    """_apply_mode picks the right service set for each of 3 modes."""
    t = _make_transformer()
    t.set_services(
        shadow_order="SHADOW_ORDER",
        shadow_position="SHADOW_POS",
        shadow_account="SHADOW_ACC",
        bybit_order="BYBIT_ORDER",
        bybit_position="BYBIT_POS",
        bybit_account="BYBIT_ACC",
        bybit_demo_order="DEMO_ORDER",
        bybit_demo_position="DEMO_POS",
        bybit_demo_account="DEMO_ACC",
    )

    # Shadow mode (default).
    t._current_mode = "shadow"
    t._apply_mode()
    assert t._active_services.get("order") == "SHADOW_ORDER"

    # Live Bybit mode.
    t._current_mode = "bybit"
    t._apply_mode()
    assert t._active_services.get("order") == "BYBIT_ORDER"

    # Bybit demo mode (the new slot).
    t._current_mode = "bybit_demo"
    t._apply_mode()
    assert t._active_services.get("order") == "DEMO_ORDER"
    assert t._active_services.get("position") == "DEMO_POS"
    assert t._active_services.get("account") == "DEMO_ACC"


def test_set_services_existing_callers_still_work() -> None:
    """Existing callers passing only shadow+bybit kwargs still work (additive contract)."""
    t = _make_transformer()
    # Old call signature — no bybit_demo kwargs.
    t.set_services(
        shadow_order="S",
        shadow_position="P",
        shadow_account="A",
        bybit_order=None,
        bybit_position=None,
        bybit_account=None,
    )
    # Demo slot is empty (None values), bybit_demo dispatch yields None.
    assert t._bybit_demo_services == {
        "order": None,
        "position": None,
        "account": None,
    }


def test_properties_for_three_modes() -> None:
    t = _make_transformer()

    t._current_mode = "shadow"
    assert t.is_shadow is True
    assert t.is_bybit is False
    assert t.is_bybit_demo is False
    assert "SHADOW" in t.mode_label.upper()

    t._current_mode = "bybit"
    assert t.is_shadow is False
    assert t.is_bybit is True
    assert t.is_bybit_demo is False
    assert "LIVE" in t.mode_label.upper()

    t._current_mode = "bybit_demo"
    assert t.is_shadow is False
    assert t.is_bybit is False
    assert t.is_bybit_demo is True
    assert "DEMO" in t.mode_label.upper()


@pytest.mark.asyncio
async def test_switch_to_validation_accepts_bybit_demo() -> None:
    t = _make_transformer()
    t._current_mode = "shadow"

    # Invalid mode rejected.
    res = await t.switch_to("garbage")
    assert res["success"] is False
    assert "Invalid mode" in res["error"]

    # Same-mode rejected.
    res = await t.switch_to("shadow")
    assert res["success"] is False
    assert "Already on" in res["error"]

    # bybit (live) requires confirmation.
    res = await t.switch_to("bybit", confirmed=False)
    assert res["success"] is False
    assert "confirmation" in res["error"].lower()

    # bybit_demo does NOT require confirmation. Will fail at the
    # reachability check (no services configured), but the validation
    # gate must accept it as a valid mode value.
    res = await t.switch_to("bybit_demo", confirmed=False)
    # We should get past validation; failure is now "services not configured"
    # or "not reachable", NOT "Invalid mode" or "confirmation".
    assert "Invalid mode" not in res.get("error", "")
    assert "confirmation" not in res.get("error", "").lower()
