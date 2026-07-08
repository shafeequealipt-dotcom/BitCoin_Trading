"""End-to-end PIPELINE check — Profit-Fetching Exit System on the REAL project.

Unlike the per-phase verify scripts (which exercise units), this drives the
REAL objects wired the way the WorkerManager wires them, and only stubs the
exchange boundary (position_service.set_stop_loss / close_position) and price:

  config.toml -> Settings -> REAL SLGateway (all four rules enforcing)
  REAL TradeCoordinator (TradePlan: per-trade max_hold) -> REAL TimeDial
  REAL ProfitSniper._compute_ladder_floor / _pf_select_stop / _pf_apply_spine
  REAL DeadlineEngine + REAL PositionWatchdog._monitor_position

It proves: the ladder lock actually clears the real gateway's R3 via the
bypass while R1/R2/R4 still enforce; the safety floor attaches on a naked
position through the real gateway; the time dial changes the real ladder lock
with age; and the watchdog rides a profitable expiry but still closes a loser —
all through the real code paths, with the stop/close observed at the exchange
boundary.

Run from the project root:  PYTHONPATH=. python scripts/pipeline_check_profit_fetching.py
Exit code 0 = the whole pipeline behaves correctly end-to-end.
"""

from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.sl_gateway import SLGateway
from src.core.trade_coordinator import TradeCoordinator
from src.core.trade_plan import TradePlan
from src.sentinel.deadline import DeadlineEngine
from src.workers.position_watchdog import PositionWatchdog
from src.workers.profit_sniper import ProfitSniper
from src.workers.sniper_ring_buffer import PositionProfitState


class FakeExchange:
    """The exchange boundary — records the SL/closes the real code would send."""
    def __init__(self):
        self.sl_writes = []   # list of (symbol, sl)
        self.closes = []      # list of (symbol, trigger)

    async def set_stop_loss(self, symbol, sl):
        self.sl_writes.append((symbol, round(float(sl), 8)))
        return True

    async def close_position(self, symbol, close_trigger="x"):
        self.closes.append((symbol, close_trigger))
        return True

    async def get_last_close(self, symbol):
        return {"net_pnl_usd": -5.0, "net_pnl_pct": -5.0, "exit_price": 95.0}


def banner(t):
    print(f"\n=== {t} ===")


async def section_a_real_gateway_rules(settings):
    """REAL SLGateway: R3 enforces for the trail, is bypassed for the ladder,
    and R1/R4 still enforce even for the ladder source."""
    banner("A. REAL SLGateway — R3 bypass scoping + R1 + R4")
    ex = FakeExchange()
    gw = SLGateway(settings=settings, position_service=ex, market_service=SimpleNamespace(),
                   volatility_profiler=None)

    # A1: a 1.0% step via the Chandelier source (no bypass) must be REJECTED by R3.
    r = await gw.apply(symbol="AAA", new_sl=101.0, source="profit_sniper_trail",
                       direction="Buy", current_sl=100.0, current_price=101.5)
    assert not r.accepted and r.reason == "step_exceeded", (r.accepted, r.reason)
    print(f"A1 R3 enforces for trail: 1.0% step rejected ({r.reason})")

    # A2: the SAME 1.0% step via the ladder source + bypass must be ACCEPTED
    # (R1 tighten ok, R2 0.49% > 0.3% ok, R4 first-write ok).
    r = await gw.apply(symbol="AAA", new_sl=101.0, source="profit_sniper_ladder",
                       direction="Buy", current_sl=100.0, current_price=101.5,
                       bypass_step_cap_for_breakeven=True)
    assert r.accepted, (r.accepted, r.reason)
    assert ("AAA", 101.0) in ex.sl_writes, ex.sl_writes
    print(f"A2 R3 bypassed for ladder: 1.0% step accepted -> exchange wrote {ex.sl_writes[-1]}")

    # A3: R1 tighten-only is NEVER bypassed — a loosening ladder write is rejected.
    r = await gw.apply(symbol="AAA", new_sl=100.5, source="profit_sniper_ladder",
                       direction="Buy", current_sl=101.0, current_price=101.5,
                       bypass_step_cap_for_breakeven=True)
    assert not r.accepted and r.reason == "loosening", (r.accepted, r.reason)
    print(f"A3 R1 enforces for ladder: loosening rejected ({r.reason})")

    # A4: R4 rate-limit is NEVER bypassed — a second write within 30s is rejected.
    r = await gw.apply(symbol="AAA", new_sl=101.5, source="profit_sniper_ladder",
                       direction="Buy", current_sl=101.0, current_price=102.0,
                       bypass_step_cap_for_breakeven=True)
    assert not r.accepted and r.reason == "rate_limit", (r.accepted, r.reason)
    print(f"A4 R4 enforces for ladder: 2nd write in 30s rejected ({r.reason})")


async def section_b_full_sniper_pipeline(settings):
    """coordinator plan -> dial -> ladder -> spine -> REAL gateway -> exchange."""
    banner("B. FULL sniper pipeline (real coordinator/plan/dial/ladder/spine/gateway)")
    ex = FakeExchange()
    gw = SLGateway(settings=settings, position_service=ex, market_service=SimpleNamespace(),
                   volatility_profiler=None)
    coord = TradeCoordinator()
    sniper = ProfitSniper(settings=settings, db=SimpleNamespace())
    sniper.sl_gateway = gw
    sniper.trade_coordinator = coord

    sym = "BBB"
    entry = 100.0
    # Register a REAL TradePlan with a per-trade deadline. register_trade_plan
    # stamps opened_at=now (the real open-time behavior), so we age it AFTER
    # registration to simulate an 11-minute-old mid-trade position.
    plan = TradePlan(symbol=sym, direction="Buy", entry_price=entry, max_hold_minutes=50)
    coord.register_trade_plan(sym, plan)
    coord.get_trade_plan(sym).opened_at = time.time() - 11 * 60  # 11 minutes old

    # Seed a REAL PositionProfitState with a +1.7% high-water peak.
    st = PositionProfitState(symbol=sym, entry_price=entry, direction="Buy",
                             atr_at_entry=0.8, opened_at=plan.opened_at)
    st.peak_pnl_pct = 1.7
    st.peak_price = entry * 1.017
    sniper._profit_states[sym] = st

    # Drive the REAL age/deadline + dial + ladder exactly as tick() does.
    age, deadline = sniper._pf_age_and_deadline(sym)
    assert abs(deadline - 50.0) < 1e-6 and age > 10.0, (age, deadline)
    dialed = sniper._time_dial.resolve(age, deadline)
    ladder = sniper._compute_ladder_floor(st, dialed, current_sl=100.3)
    assert ladder.should_apply, ladder
    print(f"   plan: max_hold=50 age={age:.1f}min -> dial step={dialed.ladder_step_pct:.3f} "
          f"offset={dialed.lock_offset_pct:.3f} -> ladder lock=+{ladder.lock_pct:.2f}% "
          f"sl={ladder.ladder_stop_price:.4f}")

    # Position carries the prior SL (a +0.3% lock); current price near peak.
    pos = SimpleNamespace(symbol=sym, stop_loss=100.3,
                          side=SimpleNamespace(value="Buy"), mark_price=101.7)
    tracked = {"last_trail": None, "last_ladder": ladder}
    wrote = await sniper._pf_apply_spine(sym, pos, tracked, current_price=101.7)
    assert wrote, "spine should have written the ladder lock"
    assert ex.sl_writes and abs(ex.sl_writes[-1][1] - ladder.ladder_stop_price) < 1e-6, ex.sl_writes
    # The local plan SL was synced too (coordinator wiring).
    assert abs(coord.get_trade_plan(sym).stop_loss_price - ladder.ladder_stop_price) < 1e-6
    print(f"   spine -> REAL gateway -> exchange wrote {ex.sl_writes[-1]} (ladder lock); "
          f"plan SL synced. FULL CHAIN OK")


async def section_c_naked_safety(settings):
    """A naked position gets a safety stop attached through the REAL gateway."""
    banner("C. Naked-position safety floor through the REAL gateway")
    ex = FakeExchange()
    gw = SLGateway(settings=settings, position_service=ex, market_service=SimpleNamespace(),
                   volatility_profiler=None)
    sniper = ProfitSniper(settings=settings, db=SimpleNamespace())
    sniper.sl_gateway = gw
    sniper.trade_coordinator = None
    sym = "CCC"
    sniper._profit_states[sym] = PositionProfitState(
        symbol=sym, entry_price=100.0, direction="Buy", atr_at_entry=0.8)
    pos = SimpleNamespace(symbol=sym, stop_loss=None,  # NAKED
                          side=SimpleNamespace(value="Buy"), mark_price=101.0)
    wrote = await sniper._pf_apply_spine(sym, pos, {}, current_price=101.0)
    assert wrote and ex.sl_writes, ex.sl_writes
    # Fresh naked above entry -> entry-based 2.5% floor = 97.5, a valid below-price stop.
    assert abs(ex.sl_writes[-1][1] - 97.5) < 1e-6, ex.sl_writes
    print(f"   naked -> REAL gateway accepted first-placement -> exchange wrote {ex.sl_writes[-1]} (=2.5% floor)")


def section_d_time_dial_age_effect(settings):
    """The REAL dial makes the REAL ladder lock tighter late in the trade."""
    banner("D. Time dial age effect on the REAL ladder lock")
    sniper = ProfitSniper(settings=settings, db=SimpleNamespace())
    st = PositionProfitState(symbol="DDD", entry_price=100.0, direction="Buy", atr_at_entry=0.8)
    st.peak_pnl_pct = 1.7
    young = sniper._compute_ladder_floor(st, sniper._time_dial.resolve(0.0, 50.0), 0.0)
    old = sniper._compute_ladder_floor(st, sniper._time_dial.resolve(50.0, 50.0), 0.0)
    assert old.ladder_stop_price > young.ladder_stop_price, (young.ladder_stop_price, old.ladder_stop_price)
    print(f"   peak +1.7%: young lock=+{young.lock_pct:.2f}% ({young.ladder_stop_price:.4f}) -> "
          f"old lock=+{old.lock_pct:.2f}% ({old.ladder_stop_price:.4f}) — tighter when old")


async def section_e_watchdog_deadline(settings):
    """REAL watchdog._monitor_position + REAL DeadlineEngine + REAL coordinator plan:
    a profitable expiry rides (no close); a losing expiry still closes."""
    banner("E. Watchdog deadline ride/close through the real decision path")

    def build_wd(ex):
        coord = TradeCoordinator()
        wd = PositionWatchdog(settings=settings, db=SimpleNamespace(),
                              position_service=ex, market_service=SimpleNamespace())
        wd.coordinator = coord
        wd._sentinel_deadline = DeadlineEngine()
        wd.ta_engine = None
        wd._wd_klines_m5 = {}
        wd.event_buffer = None
        wd._calculate_pnl_pct = lambda pos, price: 0.0
        return wd, coord

    def register_expired(coord, direction="Buy"):
        # register_trade_plan stamps opened_at=now, so age it AFTER registering
        # to make the plan genuinely EXPIRED (so the deadline block actually runs).
        p = TradePlan(symbol="EEE", direction=direction, entry_price=100.0, max_hold_minutes=1)
        coord.register_trade_plan("EEE", p)
        coord.get_trade_plan("EEE").opened_at = time.time() - 3600.0
        assert coord.get_trade_plan("EEE").is_expired, "plan must be expired for this test"

    # Profitable expiry -> RIDE (no close), with the plan GENUINELY expired so
    # the deadline decision path is actually exercised.
    ex = FakeExchange()
    wd, coord = build_wd(ex)
    register_expired(coord)
    pos = SimpleNamespace(symbol="EEE", stop_loss=98.0, unrealized_pnl=5.0,
                          side=SimpleNamespace(value="Buy"), mark_price=105.0, size=1.0, entry_price=100.0)
    wd.market_service.get_ticker = _ticker(105.0)
    await wd._monitor_position(pos)
    assert not ex.closes, f"profitable EXPIRED trade must NOT close (ride), got {ex.closes}"
    print("   profitable EXPIRED trade: NOT closed (rides the sniper trail) — SNIPER_DEADLINE_RIDE")

    # Losing expiry -> still CLOSED (non-climber backstop intact).
    ex2 = FakeExchange()
    wd2, coord2 = build_wd(ex2)
    register_expired(coord2)
    pos2 = SimpleNamespace(symbol="EEE", stop_loss=98.0, unrealized_pnl=-5.0,
                           side=SimpleNamespace(value="Buy"), mark_price=95.0, size=1.0, entry_price=100.0)
    wd2.market_service.get_ticker = _ticker(95.0)
    await wd2._monitor_position(pos2)
    assert ex2.closes, "losing EXPIRED trade must still close (backstop)"
    print(f"   losing EXPIRED trade: CLOSED {ex2.closes[-1]} (non-climber backstop intact)")


def _ticker(price):
    async def _f(symbol):
        return SimpleNamespace(last_price=price)
    return _f


def main() -> int:
    settings = Settings.load(config_path="config.toml")
    asyncio.run(section_a_real_gateway_rules(settings))
    asyncio.run(section_b_full_sniper_pipeline(settings))
    asyncio.run(section_c_naked_safety(settings))
    section_d_time_dial_age_effect(settings)
    asyncio.run(section_e_watchdog_deadline(settings))
    print("\nPIPELINE_CHECK: PASS — every fixed path verified end-to-end through the real project")
    return 0


if __name__ == "__main__":
    sys.exit(main())
