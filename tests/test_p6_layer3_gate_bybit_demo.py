"""P6 — Layer-3 gate for bybit_demo via Transformer proxy.

Surgical tests for src/trading/services/order_guards.py:
- check_layer3_for_bybit_demo refuses gated purposes when L3 OFF.
- force=True bypasses for telegram_manual but NOT for layer3_entry.
- Race check (snapshot vs live) catches mid-call L3 toggle.
- Layer-4 management purposes pass through even when LM is None.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.trading.services.order_guards import (
    check_layer3_for_bybit_demo,
    reason_to_tag_fragment,
)


def _layer_mgr(active: bool = True):
    lm = MagicMock()
    lm.is_layer_active = MagicMock(return_value=active)
    return lm


def test_layer3_off_blocks_layer3_entry_even_with_force() -> None:
    allowed, reason = check_layer3_for_bybit_demo(
        layer_manager=_layer_mgr(active=False),
        purpose="layer3_entry",
        layer_snapshot=None,
        force=True,
    )
    assert not allowed
    assert reason == "layer3_off"


def test_layer3_off_allows_telegram_manual_with_force() -> None:
    allowed, reason = check_layer3_for_bybit_demo(
        layer_manager=_layer_mgr(active=False),
        purpose="telegram_manual",
        layer_snapshot=None,
        force=True,
    )
    assert allowed
    assert reason == "ok"


def test_layer3_off_blocks_telegram_manual_without_force() -> None:
    allowed, reason = check_layer3_for_bybit_demo(
        layer_manager=_layer_mgr(active=False),
        purpose="telegram_manual",
        layer_snapshot=None,
        force=False,
    )
    assert not allowed
    assert reason == "layer3_off"


def test_layer3_off_allows_layer4_close() -> None:
    """Layer-4 management purposes pass through even when L3 off —
    operator's stop-loss surface must remain functional regardless.
    """
    allowed, reason = check_layer3_for_bybit_demo(
        layer_manager=_layer_mgr(active=False),
        purpose="layer4_close",
        layer_snapshot=None,
        force=False,
    )
    assert allowed


def test_race_check_blocks_when_snapshot_diverges() -> None:
    """Snapshot says L3 was ON when caller captured; live says OFF →
    race detected, block.
    """
    snapshot = MagicMock()
    snapshot.is_layer_active = MagicMock(return_value=True)  # snapshot
    allowed, reason = check_layer3_for_bybit_demo(
        layer_manager=_layer_mgr(active=False),  # live
        purpose="layer3_entry",
        layer_snapshot=snapshot,
        force=False,
    )
    assert not allowed
    # When live is OFF and snapshot is ON, we hit the race detector
    # (more specific than the layer3_off path).
    assert reason == "layer3_race"


def test_no_layer_manager_blocks_gated_allows_layer4() -> None:
    """LM unavailable: gated purposes refused; non-gated allowed."""
    allowed, reason = check_layer3_for_bybit_demo(
        layer_manager=None, purpose="layer3_entry",
        layer_snapshot=None, force=False,
    )
    assert not allowed
    assert reason == "no_layer_manager"

    allowed2, reason2 = check_layer3_for_bybit_demo(
        layer_manager=None, purpose="layer4_close",
        layer_snapshot=None, force=False,
    )
    assert allowed2
    assert reason2 == "ok"


def test_layer3_on_allows_everything() -> None:
    """When L3 is ON the gate is permissive for every purpose."""
    for purpose in ("layer3_entry", "telegram_manual", "mcp_tool", "layer4_close"):
        allowed, _ = check_layer3_for_bybit_demo(
            layer_manager=_layer_mgr(active=True),
            purpose=purpose,
            layer_snapshot=None,
            force=False,
        )
        assert allowed, f"L3 ON should permit purpose={purpose}"


def test_reason_to_tag_fragment_maps_known_reasons() -> None:
    assert reason_to_tag_fragment("layer3_off") == "L3_OFF"
    assert reason_to_tag_fragment("layer3_race") == "L3_RACE"
    assert reason_to_tag_fragment("no_layer_manager") == "L3_NO_LM"
    # Unknown reason: uppercase fallback.
    assert reason_to_tag_fragment("custom") == "CUSTOM"
