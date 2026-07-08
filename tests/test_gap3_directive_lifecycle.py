"""Gap 3 fix (2026-05-19) — directive lifecycle rejection event tests.

The fix adds a single canonical ``STRAT_DIRECTIVE_REJECTED`` log event at every
silent-skip path in ``LayerManager._execute_new_trades``. Tests verify:

- The helper ``_emit_directive_rejected`` formats the event correctly.
- Every rejection path (invalid_directive, pos_gate, gate_rejected,
  strategy_worker rejection, exception, pnl_manager halt, enforcer halt)
  emits the event.
- The originating brain ``did`` propagates via the loop-entry snapshot.
- The success path emits NO ``STRAT_DIRECTIVE_REJECTED``.

These are unit tests of the orchestration layer. Behavior of the downstream
blockers (gate.py CHECKs, strategy_worker internals) is OUT OF SCOPE — they
already have their own test suites and Phase 3 of Gap 3 explicitly does not
modify any of those files.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.layer_manager import LayerManager
from src.core.log_context import new_decision_id

# ── Helpers ─────────────────────────────────────────────────────────────────


@dataclass
class _PlanShim:
    """Minimal stand-in for ``StrategicPlan`` carrying just the directive list."""

    new_trades: list[Any]


def _build_lm(
    *,
    pnl_can_trade: tuple[bool, str] = (True, "ok"),
    enforcer_should_allow: tuple[bool, str] = (True, "ok"),
    strategy_worker_returns: tuple[bool, str] = (True, "ok"),
    strategy_worker_raises: Exception | None = None,
    gate_rejects_with: str | None = None,
    position_symbols: set[str] | None = None,
) -> LayerManager:
    """Construct a LayerManager wired only with the services Gap 3 cares about.

    Uses ``__new__`` to skip the real ``__init__`` per the precedent in
    ``tests/test_layer_manager_cold_start.py``.
    """
    lm = LayerManager.__new__(LayerManager)
    lm._executing_lock = asyncio.Lock()
    lm._currently_executing = set()
    lm._coin_packages = {}

    # pnl_manager
    pnl = MagicMock()
    pnl.can_trade = MagicMock(return_value=pnl_can_trade)

    # enforcer
    enforcer = MagicMock()
    enforcer.check_and_enforce = AsyncMock()
    enforcer.should_allow_trade = MagicMock(return_value=enforcer_should_allow)

    # strategy_worker
    sw = MagicMock()
    if strategy_worker_raises is not None:
        sw._execute_claude_trade = AsyncMock(side_effect=strategy_worker_raises)
    else:
        sw._execute_claude_trade = AsyncMock(return_value=strategy_worker_returns)

    # gate (sets _gate_rejected via validate)
    if gate_rejects_with is not None:
        async def _validate(trade):
            trade["_gate_rejected"] = gate_rejects_with
            return trade
    else:
        async def _validate(trade):
            return trade
    gate = MagicMock()
    gate.validate = AsyncMock(side_effect=_validate)

    # position_service
    position_service = MagicMock()
    pos_objs = [MagicMock(symbol=s) for s in (position_symbols or set())]
    position_service.get_positions = AsyncMock(return_value=pos_objs)

    lm.services = {
        "pnl_manager": pnl,
        "enforcer": enforcer,
        "strategy_worker": sw,
        "apex_gate": gate,
        "position_service": position_service,
        "apex_optimizer": None,  # skip APEX optimization in tests
    }
    return lm


def _collect_emit_calls(lm: LayerManager) -> list[dict[str, str]]:
    """Replace _emit_directive_rejected with a spy that captures kwargs."""
    calls: list[dict[str, str]] = []

    def _spy(**kwargs: Any) -> None:
        calls.append(kwargs)

    lm._emit_directive_rejected = _spy  # type: ignore[assignment]
    return calls


# ── Section 1 — helper formatting ───────────────────────────────────────────


def test_emit_helper_formats_event_with_all_fields(caplog) -> None:
    """``_emit_directive_rejected`` produces a STRAT_DIRECTIVE_REJECTED log
    line containing every required field in the expected key=value shape."""
    lm = LayerManager.__new__(LayerManager)
    captured: list[str] = []
    with patch("src.core.layer_manager.log") as mock_log:
        mock_log.info.side_effect = lambda msg: captured.append(msg)
        lm._emit_directive_rejected(
            sym="MNTUSDT",
            direction="Buy",
            rsn="gate_rejected",
            detail="reentry_learning_gate_same_conditions",
            blocker_layer="gate",
            did="d-1234",
        )
    assert len(captured) == 1
    msg = captured[0]
    assert "STRAT_DIRECTIVE_REJECTED" in msg
    assert "sym=MNTUSDT" in msg
    assert "dir=Buy" in msg
    assert "rsn=gate_rejected" in msg
    assert "detail='reentry_learning_gate_same_conditions'" in msg
    assert "blocker_layer=gate" in msg
    assert "did=d-1234" in msg


def test_emit_helper_truncates_long_detail() -> None:
    """The detail field is clipped to 120 chars so log lines stay readable."""
    lm = LayerManager.__new__(LayerManager)
    long_detail = "x" * 500
    captured: list[str] = []
    with patch("src.core.layer_manager.log") as mock_log:
        mock_log.info.side_effect = lambda msg: captured.append(msg)
        lm._emit_directive_rejected(
            sym="BTCUSDT", direction="Sell", rsn="exception",
            detail=long_detail, blocker_layer="orchestration", did="d-x",
        )
    msg = captured[0]
    # Detail substring after detail=' must be 120 chars max
    m = re.search(r"detail='([^']*)'", msg)
    assert m is not None
    assert len(m.group(1)) == 120


# ── Section 2 — per-blocker rejection emits ─────────────────────────────────


def test_invalid_directive_emits_rejected(event_loop) -> None:
    """A non-dict entry in plan.new_trades triggers a STRAT_DIRECTIVE_REJECTED
    with rsn=invalid_directive, blocker_layer=orchestration."""
    lm = _build_lm()
    calls = _collect_emit_calls(lm)
    plan = _PlanShim(new_trades=["not-a-dict", 42, None])
    event_loop.run_until_complete(lm._execute_new_trades(plan))
    assert len(calls) == 3
    for c in calls:
        assert c["rsn"] == "invalid_directive"
        assert c["blocker_layer"] == "orchestration"


def test_pos_gate_emits_rejected(event_loop) -> None:
    """When a symbol already has an open position, the directive is rejected
    with rsn=pos_gate, blocker_layer=orchestration."""
    lm = _build_lm(position_symbols={"BTCUSDT"})
    calls = _collect_emit_calls(lm)
    plan = _PlanShim(new_trades=[{"symbol": "BTCUSDT", "direction": "Buy"}])
    event_loop.run_until_complete(lm._execute_new_trades(plan))
    assert len(calls) == 1
    assert calls[0]["sym"] == "BTCUSDT"
    assert calls[0]["direction"] == "Buy"
    assert calls[0]["rsn"] == "pos_gate"
    assert calls[0]["blocker_layer"] == "orchestration"
    assert calls[0]["detail"] in ("open_position", "executing")


def test_gate_rejected_emits_rejected_with_check_detail(event_loop) -> None:
    """When ``gate.validate`` sets ``_gate_rejected``, the event surfaces the
    full CHECK-specific reason string in detail, with blocker_layer=gate."""
    lm = _build_lm(gate_rejects_with="reentry_learning_gate_same_conditions")
    calls = _collect_emit_calls(lm)
    plan = _PlanShim(new_trades=[{"symbol": "HYPEUSDT", "direction": "Buy"}])
    event_loop.run_until_complete(lm._execute_new_trades(plan))
    assert len(calls) == 1
    assert calls[0]["rsn"] == "gate_rejected"
    assert calls[0]["blocker_layer"] == "gate"
    assert "reentry_learning_gate_same_conditions" in calls[0]["detail"]


def test_strategy_worker_reject_emits_rejected(event_loop) -> None:
    """When ``_execute_claude_trade`` returns ``(False, reason_code)``, the
    event surfaces the reason_code with blocker_layer=strategy_worker."""
    lm = _build_lm(strategy_worker_returns=(False, "xray_skip"))
    calls = _collect_emit_calls(lm)
    plan = _PlanShim(new_trades=[{"symbol": "ETHUSDT", "direction": "Sell"}])
    event_loop.run_until_complete(lm._execute_new_trades(plan))
    assert len(calls) == 1
    assert calls[0]["rsn"] == "xray_skip"
    assert calls[0]["blocker_layer"] == "strategy_worker"


def test_exception_in_strategy_worker_emits_rejected(event_loop) -> None:
    """An exception from ``_execute_claude_trade`` emits a STRAT_DIRECTIVE_
    REJECTED with rsn=exception, blocker_layer=orchestration."""
    lm = _build_lm(strategy_worker_raises=RuntimeError("simulated"))
    calls = _collect_emit_calls(lm)
    plan = _PlanShim(new_trades=[{"symbol": "SOLUSDT", "direction": "Buy"}])
    event_loop.run_until_complete(lm._execute_new_trades(plan))
    assert len(calls) == 1
    assert calls[0]["rsn"] == "exception"
    assert calls[0]["blocker_layer"] == "orchestration"


# ── Section 3 — halt path: one emit per pending directive ──────────────────


def test_pnl_manager_halt_emits_one_event_per_pending_directive(event_loop) -> None:
    """When pnl_manager.can_trade returns False, the early-return path emits
    one STRAT_DIRECTIVE_REJECTED per directive in plan.new_trades."""
    lm = _build_lm(pnl_can_trade=(False, "daily_loss_cap"))
    calls = _collect_emit_calls(lm)
    plan = _PlanShim(new_trades=[
        {"symbol": "BTCUSDT", "direction": "Buy"},
        {"symbol": "ETHUSDT", "direction": "Sell"},
        {"symbol": "SOLUSDT", "direction": "Buy"},
    ])
    event_loop.run_until_complete(lm._execute_new_trades(plan))
    assert len(calls) == 3
    syms = {c["sym"] for c in calls}
    assert syms == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    for c in calls:
        assert c["rsn"] == "halt"
        assert c["blocker_layer"] == "halt"
        assert "daily_loss_cap" in c["detail"]


def test_enforcer_halt_emits_one_event_per_pending_directive(event_loop) -> None:
    """When enforcer halts (after pnl_manager passes), each pending directive
    emits a STRAT_DIRECTIVE_REJECTED with rsn=halt."""
    lm = _build_lm(enforcer_should_allow=(False, "level_3_lockout"))
    calls = _collect_emit_calls(lm)
    plan = _PlanShim(new_trades=[
        {"symbol": "BTCUSDT", "direction": "Buy"},
        {"symbol": "ETHUSDT", "direction": "Sell"},
    ])
    event_loop.run_until_complete(lm._execute_new_trades(plan))
    assert len(calls) == 2
    for c in calls:
        assert c["rsn"] == "halt"
        assert c["blocker_layer"] == "halt"
        assert "level_3_lockout" in c["detail"]


# ── Section 4 — success path produces no rejection event ────────────────────


def test_success_path_emits_no_rejection_event(event_loop) -> None:
    """A directive that passes through gate and strategy_worker produces zero
    STRAT_DIRECTIVE_REJECTED events."""
    lm = _build_lm()  # all defaults = success path
    calls = _collect_emit_calls(lm)
    plan = _PlanShim(new_trades=[{"symbol": "BTCUSDT", "direction": "Buy"}])
    event_loop.run_until_complete(lm._execute_new_trades(plan))
    assert calls == []


# ── Section 5 — did propagation ────────────────────────────────────────────


def test_did_propagates_to_emit_via_loop_snapshot(event_loop) -> None:
    """Setting the decision-ID contextvar before invoking the loop attaches
    the same did to every emitted STRAT_DIRECTIVE_REJECTED event."""
    lm = _build_lm(strategy_worker_returns=(False, "service_missing"))
    calls = _collect_emit_calls(lm)

    async def _run_with_did():
        did = new_decision_id()  # sets contextvar
        plan = _PlanShim(new_trades=[
            {"symbol": "BTCUSDT", "direction": "Buy"},
            {"symbol": "ETHUSDT", "direction": "Sell"},
        ])
        await lm._execute_new_trades(plan)
        return did

    expected_did = event_loop.run_until_complete(_run_with_did())
    assert len(calls) == 2
    for c in calls:
        assert c["did"] == expected_did
        assert c["did"].startswith("d-")


# ── Pytest fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def event_loop():
    """Per-test event loop so each scenario gets a clean asyncio context."""
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()
