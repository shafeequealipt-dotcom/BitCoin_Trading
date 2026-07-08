"""Smoke test for ExchangeSwitcher — validation + position handling.

Verifies the orchestrator's pre-restart phases (validate, inventory,
pre-flight) without actually triggering systemctl. The systemctl call
is the LAST thing that happens; mocking subprocess.Popen is sufficient
to verify the orchestration ran correctly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.exchanges.switching.exchange_switcher import (
    POST_SWITCH_SENTINEL_PATH,
    ExchangeSwitcher,
)


class _FakePosition:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


class _FakePositionSvc:
    def __init__(self, positions: list[_FakePosition]) -> None:
        self._positions = list(positions)
        self.close_calls: list[tuple[str, str]] = []

    async def get_positions(self) -> list[_FakePosition]:
        return list(self._positions)

    async def close_position(self, symbol: str, *, purpose: str = "") -> Any:
        self.close_calls.append((symbol, purpose))
        # Simulate immediate close — drop from list so subsequent
        # get_positions returns empty (matches Bybit/Shadow semantics).
        self._positions = [p for p in self._positions if p.symbol != symbol]
        return None


class _FakeTransformer:
    def __init__(self, current_mode: str, positions: list[_FakePosition]) -> None:
        self.current_mode = current_mode
        self.is_switching = False
        self._is_switching = False
        self._switching_to = None
        self.active_position_service = _FakePositionSvc(positions)
        self.record_calls: list[dict[str, Any]] = []
        self.switching_state_calls: list[tuple[str | None, bool]] = []
        self.persist_target_mode_calls: list[str] = []

    async def set_switching_state(
        self,
        target_mode: str | None,
        switching: bool,
        *,
        persist: bool = True,
    ) -> None:
        self.switching_state_calls.append((target_mode, switching))
        self._is_switching = switching
        self._switching_to = target_mode if switching else None
        self.is_switching = switching

    async def record_switch(self, **kwargs: Any) -> None:
        self.record_calls.append(kwargs)

    async def persist_target_mode(self, target_mode: str) -> None:
        self.persist_target_mode_calls.append(target_mode)
        self.current_mode = target_mode
        self._is_switching = False
        self._switching_to = None
        self.is_switching = False


@pytest.mark.asyncio
async def test_invalid_target_mode_rejected() -> None:
    t = _FakeTransformer("shadow", [])
    s = ExchangeSwitcher(t)

    res = await s.execute_switch_with_restart("garbage")
    assert res["success"] is False
    assert "Restart-based switching" in res["error"]


@pytest.mark.asyncio
async def test_live_bybit_target_rejected_in_restart_path() -> None:
    """Live bybit must use Transformer.switch_to(), not the switcher."""
    t = _FakeTransformer("shadow", [])
    s = ExchangeSwitcher(t)

    res = await s.execute_switch_with_restart("bybit")
    assert res["success"] is False
    assert "bybit" in res["error"]


@pytest.mark.asyncio
async def test_same_mode_rejected() -> None:
    t = _FakeTransformer("shadow", [])
    s = ExchangeSwitcher(t)

    res = await s.execute_switch_with_restart("shadow")
    assert res["success"] is False
    assert "Already on" in res["error"]


@pytest.mark.asyncio
async def test_open_positions_without_force_rejected() -> None:
    positions = [_FakePosition("BTCUSDT"), _FakePosition("ETHUSDT")]
    t = _FakeTransformer("shadow", positions)
    s = ExchangeSwitcher(t)

    res = await s.execute_switch_with_restart("bybit_demo", force=False)
    assert res["success"] is False
    assert res["open_positions"] == 2


@pytest.mark.asyncio
async def test_zero_positions_triggers_restart() -> None:
    """Happy path with no open positions — close-all skipped, restart fires."""
    t = _FakeTransformer("shadow", [])
    s = ExchangeSwitcher(t)

    # Clean up any leftover sentinel from earlier tests.
    if POST_SWITCH_SENTINEL_PATH.exists():
        POST_SWITCH_SENTINEL_PATH.unlink()

    with patch("src.exchanges.switching.exchange_switcher.subprocess.Popen") as mock_popen:
        res = await s.execute_switch_with_restart("bybit_demo", force=True)

    assert res["success"] is True
    assert res["from_mode"] == "shadow"
    assert res["to_mode"] == "bybit_demo"
    assert res["positions_closed"] == 0
    # systemctl was invoked.
    assert mock_popen.called
    args = mock_popen.call_args[0][0]
    assert args[0] == "systemctl"
    assert args[1] == "restart"
    assert "trading-workers" in args
    assert "trading-mcp-sse" in args
    # Sentinel was written.
    assert POST_SWITCH_SENTINEL_PATH.exists()
    POST_SWITCH_SENTINEL_PATH.unlink()  # cleanup


@pytest.mark.asyncio
async def test_force_close_all_then_restart() -> None:
    """With force=True and N positions, switcher closes them all before restart."""
    positions = [_FakePosition("BTCUSDT"), _FakePosition("ETHUSDT")]
    t = _FakeTransformer("shadow", positions)
    s = ExchangeSwitcher(t)

    if POST_SWITCH_SENTINEL_PATH.exists():
        POST_SWITCH_SENTINEL_PATH.unlink()

    with patch("src.exchanges.switching.exchange_switcher.subprocess.Popen") as mock_popen:
        res = await s.execute_switch_with_restart("bybit_demo", force=True)

    assert res["success"] is True
    assert res["positions_closed"] == 2
    assert mock_popen.called
    # _record_switch called with reason prefix.
    assert len(t.record_calls) == 1
    assert t.record_calls[0]["from_mode"] == "shadow"
    assert t.record_calls[0]["to_mode"] == "bybit_demo"
    assert t.record_calls[0]["positions_closed"] == 2
    POST_SWITCH_SENTINEL_PATH.unlink()  # cleanup


@pytest.mark.asyncio
async def test_persist_target_mode_routes_through_transformer() -> None:
    """Regression: ExchangeSwitcher must NOT touch the DB directly.

    The post-encapsulation contract is that every state mutation goes
    through Transformer's public surface (set_switching_state,
    record_switch, persist_target_mode). This test verifies the
    target-mode flip during the restart-based switch goes through
    `Transformer.persist_target_mode` rather than a direct SQL UPDATE.
    Re-introducing the direct DB call would let the dashboard handler
    "Transformer or database not available" bug recur.
    """
    t = _FakeTransformer("shadow", [])
    s = ExchangeSwitcher(t)  # No db argument — required by post-fix contract

    if POST_SWITCH_SENTINEL_PATH.exists():
        POST_SWITCH_SENTINEL_PATH.unlink()

    with patch("src.exchanges.switching.exchange_switcher.subprocess.Popen"):
        res = await s.execute_switch_with_restart("bybit_demo", force=True)

    assert res["success"] is True
    assert t.persist_target_mode_calls == ["bybit_demo"], (
        f"Expected exactly one persist_target_mode call, got "
        f"{t.persist_target_mode_calls!r}"
    )
    # And the in-process state on the transformer reflects the new mode
    # so any subsequent .current_mode read returns the target.
    assert t.current_mode == "bybit_demo"
    assert t.is_switching is False
    POST_SWITCH_SENTINEL_PATH.unlink()


@pytest.mark.asyncio
async def test_constructor_signature_does_not_take_db() -> None:
    """Regression: ExchangeSwitcher(transformer, alert_manager) only.

    The pre-fix constructor was (transformer, db, alert_manager) — that
    forced the dashboard handler to do an `_svc(context, "db")` lookup
    that returned None (DB was never registered in the bot_data dict)
    and surfaced as "Transformer or database not available." This test
    pins the new signature.
    """
    import inspect
    sig = inspect.signature(ExchangeSwitcher)
    params = [name for name in sig.parameters if name != "self"]
    assert params == ["transformer", "alert_manager"], (
        f"ExchangeSwitcher signature drifted: {params}. The post-fix "
        f"contract is (transformer, alert_manager). Re-introducing "
        f"`db` would break the dashboard handler again."
    )
