"""J1 Phase 3 Step B (2026-05-14) — PositionReconciler tests.

Pins:
  * Per-tick POSITION_RECONCILE INFO line shape (count_diff and
    inuse_diff fields, mode tag, streak counters).
  * Count drift alerts at WARNING only after the dwell threshold
    (2 ticks) — single-tick churn does not alarm.
  * Streak resets when drift returns to zero.
  * Margin-in-use drift alerts respect both absolute and proportional
    thresholds (max($1000, 0.5% * bybit_total)).
  * confirmed=False from the position service skips comparison.
  * Missing position_service skips entirely; manager log is the
    "disabled" path elsewhere.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.core.types import AccountInfo, Position, PositionsQueryResult, Side
from src.workers.position_reconciler import PositionReconciler


# --- Helpers ----------------------------------------------------------


def _pos(symbol: str) -> Position:
    from datetime import UTC, datetime
    return Position(
        symbol=symbol, side=Side.BUY, size=1.0,
        entry_price=100.0, mark_price=100.0,
        unrealized_pnl=0.0, realized_pnl=0.0, leverage=1,
        liquidation_price=0.0, stop_loss=None, take_profit=None,
        updated_at=datetime.now(UTC),
    )


class _FakeDB:
    def __init__(self, count: int = 0) -> None:
        self.count = count
        self.queries: list[tuple[str, tuple]] = []

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict[str, Any]:
        self.queries.append((sql, params))
        return {"n": self.count}


class _FakeSettings:
    class fund_manager:
        reconcile_interval_seconds = 60

    class workers:
        max_consecutive_failures = 5
        restart_delay = 1.0


class _FakePositionService:
    def __init__(self, result: PositionsQueryResult, raises: Exception | None = None) -> None:
        self.result = result
        self.raises = raises
        self.calls: int = 0

    async def get_positions_with_confirmation(self) -> PositionsQueryResult:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.result


class _FakeAccountService:
    def __init__(self, total: float, available: float, raises: Exception | None = None) -> None:
        self.total = total
        self.available = available
        self.raises = raises

    async def get_wallet_balance(self) -> AccountInfo:
        if self.raises is not None:
            raise self.raises
        return AccountInfo(
            total_equity=self.total,
            available_balance=self.available,
            used_margin=self.total - self.available,
            unrealized_pnl=0.0,
        )


class _FakeFundManager:
    """Stand-in for FundManager. The PositionReconciler reads
    ``_account_state.total_equity`` and ``.available``."""

    def __init__(self, total: float, available: float) -> None:
        self._account_state = MagicMock()
        self._account_state.total_equity = total
        self._account_state.available = available


class _FakeTransformer:
    def __init__(self, mode: str = "bybit_demo") -> None:
        self.current_mode = mode


# --- Fixtures ---------------------------------------------------------


@pytest.fixture
def loguru_sink():
    records: list[tuple[str, str]] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append(
            (msg.record["level"].name, msg.record["message"])
        ),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records_with_tag(records, tag: str):
    return [r for r in records if r[1].startswith(tag)]


def _parse_kv(msg: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=([\S]+)", msg))


def _build(
    *, db_count: int = 0,
    live_positions: tuple[Position, ...] = (),
    confirmed: bool = True,
    bybit_total: float = 100_000.0,
    bybit_available: float = 100_000.0,
    local_total: float = 100_000.0,
    local_available: float = 100_000.0,
    include_account: bool = True,
    include_fund_manager: bool = True,
    include_position_service: bool = True,
) -> tuple[PositionReconciler, dict[str, Any], _FakeDB]:
    db = _FakeDB(count=db_count)
    services: dict[str, Any] = {"transformer": _FakeTransformer()}
    if include_position_service:
        services["position_service"] = _FakePositionService(
            PositionsQueryResult(
                confirmed=confirmed,
                positions=live_positions,
            ),
        )
    if include_account:
        services["account_service"] = _FakeAccountService(
            total=bybit_total, available=bybit_available,
        )
    if include_fund_manager:
        services["fund_manager"] = _FakeFundManager(
            total=local_total, available=local_available,
        )
    rec = PositionReconciler(
        settings=_FakeSettings(), db=db, services=services,  # type: ignore[arg-type]
    )
    return rec, services, db


# --- Tests ------------------------------------------------------------


@pytest.mark.asyncio
async def test_emits_per_tick_info_line_with_count_diff(loguru_sink) -> None:
    rec, _, _ = _build(db_count=3, live_positions=(_pos("BTC"),))
    await rec.tick()
    infos = _records_with_tag(loguru_sink, "POSITION_RECONCILE |")
    assert len(infos) == 1
    kv = _parse_kv(infos[0][1])
    assert kv["mode"] == "bybit_demo"
    assert kv["db_count"] == "3"
    assert kv["live_count"] == "1"
    assert kv["count_diff"] == "+2"


@pytest.mark.asyncio
async def test_single_tick_drift_does_not_alarm(loguru_sink) -> None:
    rec, _, _ = _build(db_count=4, live_positions=())
    await rec.tick()
    drifts = _records_with_tag(loguru_sink, "POSITION_RECONCILE_DRIFT")
    assert drifts == []


@pytest.mark.asyncio
async def test_two_consecutive_ticks_drift_alarms(loguru_sink) -> None:
    rec, _, _ = _build(db_count=4, live_positions=())
    await rec.tick()
    await rec.tick()
    drifts = _records_with_tag(loguru_sink, "POSITION_RECONCILE_DRIFT")
    assert len(drifts) == 1
    kv = _parse_kv(drifts[0][1])
    assert kv["mode"] == "bybit_demo"
    assert kv["diff"] == "+4"
    assert kv["streak"] == "2"


@pytest.mark.asyncio
async def test_streak_resets_when_drift_clears(loguru_sink) -> None:
    """After two drift ticks, a tick with zero diff resets the
    streak — a subsequent drift tick does NOT immediately re-alarm."""
    rec, services, db = _build(db_count=4, live_positions=())
    await rec.tick()           # streak=1
    await rec.tick()           # streak=2, WARN
    # Update fakes so the third tick has no drift
    db.count = 1
    services["position_service"].result = PositionsQueryResult(
        confirmed=True, positions=(_pos("BTC"),),
    )
    await rec.tick()           # streak resets to 0
    # Now reintroduce drift for one tick only
    db.count = 2
    services["position_service"].result = PositionsQueryResult(
        confirmed=True, positions=(_pos("BTC"),),
    )
    await rec.tick()           # streak=1 only
    drifts = _records_with_tag(loguru_sink, "POSITION_RECONCILE_DRIFT")
    assert len(drifts) == 1


@pytest.mark.asyncio
async def test_inuse_drift_below_threshold_does_not_alarm(loguru_sink) -> None:
    # bybit_total=$100k, inuse_bybit=$50, inuse_local=$0 → $50 gap which
    # is under max($1000, $500) = $1000 → no alarm
    rec, _, _ = _build(
        db_count=0, live_positions=(),
        bybit_total=100_000.0, bybit_available=99_950.0,
        local_total=100_000.0, local_available=100_000.0,
    )
    await rec.tick()
    await rec.tick()
    drifts = _records_with_tag(loguru_sink, "FUND_INUSE_DRIFT")
    assert drifts == []


@pytest.mark.asyncio
async def test_inuse_drift_above_threshold_alarms_after_dwell(loguru_sink) -> None:
    # bybit_total=$100k, inuse_bybit=$50k, inuse_local=$0 → $50k gap
    # which exceeds max($1000, $500) = $1000 → alarms on 2nd tick
    rec, _, _ = _build(
        db_count=0, live_positions=(),
        bybit_total=100_000.0, bybit_available=50_000.0,
        local_total=100_000.0, local_available=100_000.0,
    )
    await rec.tick()
    await rec.tick()
    drifts = _records_with_tag(loguru_sink, "FUND_INUSE_DRIFT")
    assert len(drifts) == 1
    kv = _parse_kv(drifts[0][1])
    assert kv["streak"] == "2"


@pytest.mark.asyncio
async def test_confirmed_false_skips_comparison(loguru_sink) -> None:
    rec, _, _ = _build(db_count=5, live_positions=(), confirmed=False)
    await rec.tick()
    # No INFO line emitted when state is unknown
    assert _records_with_tag(loguru_sink, "POSITION_RECONCILE |") == []
    skips = _records_with_tag(loguru_sink, "POSITION_RECONCILE_SKIP")
    assert len(skips) == 1
    assert "live_unknown_state" in skips[0][1]


@pytest.mark.asyncio
async def test_missing_position_service_skips(loguru_sink) -> None:
    rec, _, _ = _build(db_count=4, include_position_service=False)
    await rec.tick()
    assert _records_with_tag(loguru_sink, "POSITION_RECONCILE |") == []
    skips = _records_with_tag(loguru_sink, "POSITION_RECONCILE_SKIP")
    assert len(skips) == 1
    assert "no_position_service" in skips[0][1]


@pytest.mark.asyncio
async def test_account_service_failure_does_not_break_count_check(loguru_sink) -> None:
    """When wallet read fails, the per-tick INFO line still emits with
    inuse_diff=n/a, and the count-drift check still operates."""
    rec, services, _ = _build(db_count=2, live_positions=())
    services["account_service"].raises = RuntimeError("wallet api down")
    await rec.tick()
    infos = _records_with_tag(loguru_sink, "POSITION_RECONCILE |")
    assert len(infos) == 1
    kv = _parse_kv(infos[0][1])
    assert kv["count_diff"] == "+2"
    assert kv["inuse_diff"] == "n/a"
