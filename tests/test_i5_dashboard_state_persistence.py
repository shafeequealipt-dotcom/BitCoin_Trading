"""Issue I5 (F-32) — Dashboard state persistence across workers.py restart.

Pre-I5 a SEGV or graceful restart wiped TradeCoordinator._trades and
the DailyPnLManager daily counters. The dashboard showed 0 age / 0
PnL for ~6 minutes after restart while the watchdog's reactive
WD_CLOSE_THESIS_RECOVERY caught up. Post-I5:

  * TradeCoordinator.recover_state_from_db reads open theses and
    rebuilds TradeState entries on boot
  * DailyPnLManager._restore_today_from_db reads daily_pnl WHERE
    date=today on initialize() AFTER the zero-block, so genuine
    new-day boots keep the zeros and restart boots restore
  * register_trade_plan emits TRADEPLAN_PERSISTED
  * worker manager calls recover_state_from_db immediately after
    coordinator construction

Coverage focuses on:
  * recover_state_from_db restores from a synthetic open-thesis row
  * recover_state_from_db is idempotent (doesn't overwrite live trades)
  * recover_state_from_db handles DB-query failure gracefully
  * register_trade_plan emits TRADEPLAN_PERSISTED
  * source-pins on the new emissions + manager.py boot wiring
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger as _loguru_logger

from src.core.trade_coordinator import TradeCoordinator


@pytest.fixture
def loguru_sink():
    records: list[tuple[str, str]] = []
    handler_id = _loguru_logger.add(
        lambda msg: records.append((msg.record["level"].name, msg.record["message"])),
        level="DEBUG",
        format="{message}",
    )
    yield records
    _loguru_logger.remove(handler_id)


def _records_with_tag(records, tag: str):
    return [r for r in records if r[1].startswith(tag)]


# ─── recover_state_from_db ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recover_state_restores_open_thesis_to_trades(loguru_sink) -> None:
    """A row in trade_thesis with status='open' becomes a TradeState entry."""
    coord = TradeCoordinator()
    fake_db = MagicMock()
    fake_db.fetch_all = AsyncMock(return_value=[
        {
            "symbol": "BTCUSDT",
            "direction": "Buy",
            "entry_price": 80000.0,
            "size_usd": 4000.0,
            "leverage": 5,
            "opened_at": "2026-05-14T08:00:00+00:00",
            "order_id": "ORD-1",
            "exchange_mode": "bybit_demo",
        }
    ])
    restored = await coord.recover_state_from_db(fake_db)
    assert restored == 1
    assert "BTCUSDT" in coord._trades
    state = coord._trades["BTCUSDT"]
    assert state.entry_price == 80000.0
    assert state.side == "Buy"
    assert state.exchange_mode == "bybit_demo"
    # qty derived from size_usd * leverage / entry_price
    expected_qty = (4000.0 * 5) / 80000.0
    assert abs(state.size - expected_qty) < 1e-9
    # DASHBOARD_STATE_RECOVERED log fired
    events = _records_with_tag(loguru_sink, "DASHBOARD_STATE_RECOVERED")
    assert len(events) >= 1
    assert any("BTCUSDT" in m for _, m in events)


@pytest.mark.asyncio
async def test_recover_state_preserves_existing_trades() -> None:
    """Idempotent: symbols already in _trades are not overwritten."""
    coord = TradeCoordinator()
    coord.register_trade(
        symbol="BTCUSDT", side="Buy", size=0.05, entry_price=82000.0,
        decision_id="d-live",
    )
    fake_db = MagicMock()
    fake_db.fetch_all = AsyncMock(return_value=[{
        "symbol": "BTCUSDT", "direction": "Buy", "entry_price": 80000.0,
        "size_usd": 4000.0, "leverage": 5, "opened_at": "2026-05-14T08:00:00+00:00",
        "order_id": "ORD-1", "exchange_mode": "bybit_demo",
    }])
    restored = await coord.recover_state_from_db(fake_db)
    assert restored == 0
    # Existing state preserved
    assert coord._trades["BTCUSDT"].entry_price == 82000.0
    assert coord._trades["BTCUSDT"].brain_decision_id == "d-live"


@pytest.mark.asyncio
async def test_recover_state_handles_db_failure(loguru_sink) -> None:
    """Best-effort: fetch_all raises → returns 0, logs RECOVER_FAIL, no crash."""
    coord = TradeCoordinator()
    fake_db = MagicMock()
    fake_db.fetch_all = AsyncMock(side_effect=RuntimeError("db locked"))
    restored = await coord.recover_state_from_db(fake_db)
    assert restored == 0
    fails = _records_with_tag(loguru_sink, "DASHBOARD_STATE_RECOVER_FAIL")
    assert len(fails) >= 1


@pytest.mark.asyncio
async def test_recover_state_empty_result_is_noop(loguru_sink) -> None:
    """No open theses → returns 0 cleanly, no warnings."""
    coord = TradeCoordinator()
    fake_db = MagicMock()
    fake_db.fetch_all = AsyncMock(return_value=[])
    restored = await coord.recover_state_from_db(fake_db)
    assert restored == 0
    fails = _records_with_tag(loguru_sink, "DASHBOARD_STATE_RECOVER_FAIL")
    assert len(fails) == 0


@pytest.mark.asyncio
async def test_recover_state_handles_none_db() -> None:
    """No DB attached → returns 0 cleanly."""
    coord = TradeCoordinator()
    restored = await coord.recover_state_from_db(None)
    assert restored == 0


# ─── TRADEPLAN_PERSISTED emission ────────────────────────────────────────


def test_register_trade_plan_emits_persisted(loguru_sink) -> None:
    """register_trade_plan emits TRADEPLAN_PERSISTED with key fields."""
    coord = TradeCoordinator()
    plan = SimpleNamespace(
        direction="Buy", entry_price=80000.0, peak_price=80000.0,
        stop_loss_price=78000.0, target_price=84000.0,
        max_hold_minutes=120, trailing_activation_pct=2.0,
        size_tier="standard", opened_at=0,
    )
    coord.register_trade_plan("BTCUSDT", plan)
    events = _records_with_tag(loguru_sink, "TRADEPLAN_PERSISTED")
    assert len(events) == 1
    msg = events[0][1]
    assert "sym=BTCUSDT" in msg
    assert "dir=Buy" in msg
    assert "entry=80000" in msg
    assert "sl=78000" in msg
    assert "tp=84000" in msg
    assert "hold_min=120" in msg
    assert "tier=standard" in msg


# ─── Source-level pins ──────────────────────────────────────────────────


def test_recover_state_method_exists_on_coordinator() -> None:
    """Source-pin: the new method is defined in trade_coordinator.py."""
    src = open("src/core/trade_coordinator.py").read()
    assert "async def recover_state_from_db" in src


def test_dashboard_state_recovered_emission_registered() -> None:
    """Source-pin: DASHBOARD_STATE_RECOVERED in trade_coordinator."""
    src = open("src/core/trade_coordinator.py").read()
    assert "DASHBOARD_STATE_RECOVERED" in src


def test_manager_wires_recover_state_on_boot() -> None:
    """Source-pin: worker manager calls recover_state_from_db after
    coordinator construction. Pre-I5 the coordinator's _trades dict was
    always empty at boot."""
    src = open("src/workers/manager.py").read()
    assert "recover_state_from_db" in src, (
        "Issue I5: manager boot must call recover_state_from_db"
    )
    # And it should emit BOOT_STATE_RECOVERED on success
    assert "BOOT_STATE_RECOVERED" in src


def test_pnl_manager_has_restore_method() -> None:
    """Source-pin: DailyPnLManager has _restore_today_from_db."""
    src = open("src/strategies/pnl_manager.py").read()
    assert "async def _restore_today_from_db" in src


def test_pnl_manager_emits_dashboard_state_recovered() -> None:
    """Source-pin: PnL manager's restore emits the canonical event tag."""
    src = open("src/strategies/pnl_manager.py").read()
    assert "DASHBOARD_STATE_RECOVERED" in src, (
        "Issue I5: PnL manager restore must emit DASHBOARD_STATE_RECOVERED"
    )


def test_pnl_manager_restore_called_from_initialize() -> None:
    """Source-pin: the restore method is invoked from initialize()
    (otherwise the recovery is dead code)."""
    src = open("src/strategies/pnl_manager.py").read()
    # initialize() must call _restore_today_from_db
    m = re.search(
        r"async def initialize.*?_restore_today_from_db",
        src, re.DOTALL,
    )
    assert m is not None, (
        "Issue I5: initialize() must call _restore_today_from_db"
    )
