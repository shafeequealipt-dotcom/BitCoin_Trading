"""Phase 5 behavioral self-verification — ride-the-winner past deadline +
winner-cutter subordination (Full reconciliation).

Drives the real PositionWatchdog._monitor_position with a mock self to confirm:
  1. Binary-fallback deadline: a still-profitable expired trade is NOT closed
     when enabled (rides the sniper trail); IS closed when disabled (legacy).
  2. A LOSING expired trade is still closed when enabled (non-climber backstop).
  3. SENTINEL deadline: a profitable expired trade (TIER profit) is NOT closed
     when enabled.
  4. The three Phase-5 switches load from config as expected.

Run from the project root:  PYTHONPATH=. python scripts/verify_profit_fetching_phase5.py
Exit code 0 = all checks pass.
"""

from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.config.settings import Settings
from src.core.trade_plan import TradePlan
from src.sentinel.deadline import DeadlineEngine
from src.workers.position_watchdog import PositionWatchdog


def _expired_plan(direction: str = "Buy", entry: float = 100.0) -> TradePlan:
    p = TradePlan(symbol="X", direction=direction, entry_price=entry, max_hold_minutes=1)
    p.opened_at = time.time() - 3600.0  # far past its 1-minute deadline
    return p


def _mock_wd(pf, sentinel) -> MagicMock:
    s = MagicMock()
    s.settings = Settings.load(config_path="config.toml")
    s._pf = pf
    s.ta_engine = None
    s._wd_klines_m5 = {}
    s.event_buffer = None
    s._sentinel_deadline = sentinel
    s._calculate_pnl_pct = MagicMock(return_value=0.0)
    s.market_service = MagicMock()
    s.position_service = MagicMock()
    s.position_service.close_position = AsyncMock(return_value=True)
    s.coordinator = MagicMock()
    s.coordinator.resolve_authoritative_pnl = AsyncMock(return_value=(0.0, 0.0, "x", None))
    s.coordinator.on_trade_closed = MagicMock()
    s.coordinator.remove_trade_plan = MagicMock()
    return s


async def _run(pf, price: float, plan: TradePlan, sentinel=None) -> MagicMock:
    s = _mock_wd(pf, sentinel)
    s.coordinator.get_trade_plan = MagicMock(return_value=plan)
    s.market_service.get_ticker = AsyncMock(
        return_value=SimpleNamespace(last_price=price)
    )
    pos = SimpleNamespace(
        symbol="X", mark_price=price, unrealized_pnl=0.0,
        side=SimpleNamespace(value="Buy"),
    )
    await PositionWatchdog._monitor_position(s, pos)
    return s


def _pf(enabled: bool):
    return SimpleNamespace(
        enabled=enabled,
        ride_winner_past_deadline=True,
        subordinate_profit_take=True,
        subordinate_watchdog_trail_exit=True,
    )


def main() -> int:
    on, off = _pf(True), _pf(False)

    # 1. Binary fallback, enabled + profitable expired -> RIDE (no close).
    s = asyncio.run(_run(on, price=105.0, plan=_expired_plan()))
    s.position_service.close_position.assert_not_awaited()
    print("Ride OK (binary): profitable expired trade NOT closed when enabled")

    # 2. Binary fallback, disabled + profitable expired -> CLOSE (legacy).
    s = asyncio.run(_run(off, price=105.0, plan=_expired_plan()))
    s.position_service.close_position.assert_awaited()
    print("Legacy OK (binary): profitable expired trade closed when disabled")

    # 3. enabled + LOSING expired -> CLOSE (non-climber backstop intact).
    s = asyncio.run(_run(on, price=95.0, plan=_expired_plan()))
    s.position_service.close_position.assert_awaited()
    print("Backstop OK: losing expired trade still closed when enabled")

    # 4. SENTINEL path, enabled + profitable expired (TIER profit) -> RIDE.
    s = asyncio.run(_run(on, price=105.0, plan=_expired_plan(), sentinel=DeadlineEngine()))
    s.position_service.close_position.assert_not_awaited()
    print("Ride OK (SENTINEL): profitable TIER-profit expiry NOT closed when enabled")

    # 5. Config flags load as expected.
    pf = Settings.load(config_path="config.toml").profit_fetching
    assert pf.ride_winner_past_deadline is True
    assert pf.subordinate_profit_take is True
    assert pf.subordinate_watchdog_trail_exit is True
    print("Config OK: ride_winner / subordinate_profit_take / subordinate_trail all true")

    print("\nPHASE_5_SELF_VERIFY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
