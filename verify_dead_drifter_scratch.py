#!/usr/bin/env python3
"""Verify the Commit 4 dead-drifter scratch in _lc_stall_decision.

Read-only, no DB/exchange. Drives the REAL _lc_stall_decision with stubbed
async ATR + close, asserting: a proven-dead drifter (lifetime peak < 1R, past
the scratch age, no signs of life) is scratched EARLY; the signs-of-life veto
still spares a late-bloomer; a trade that moved >1R is NOT a dead drifter; and
when disabled the legacy behaviour (ride to the dialed stall age) holds.
The live config lands DORMANT (dead_drifter_enabled=false). Exits non-zero on
any mismatch.
"""
import asyncio
import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.workers.profit_sniper import ProfitSniper

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


async def run_case(name, *, peak, age, stall_min_age, profit_ratio,
                   enabled, dd_enabled, expect_close):
    s = Settings._load_fresh()
    s.adaptive_exit.enabled = enabled
    s.adaptive_exit.dead_drifter_enabled = dd_enabled
    # Control the veto deterministically via profit_ratio only (single-tick call:
    # windowed/sustained/peak-rise vetoes can't trip on one tick anyway).
    s.loss_cutting.stall_veto_windowed_profit_ratio_enabled = False
    s.loss_cutting.stall_signs_of_life_sustained_improving_enabled = False

    closed = {"called": False}

    async def fake_close(*a, **k):
        closed["called"] = True
        return True

    async def fake_atr(symbol):
        return 0.2   # entry 100 -> R = 0.2/100*100 = 0.20% ; 1R = 0.20%

    fake = SimpleNamespace(
        settings=s, _lc=s.loss_cutting, layer4_protection=None,
        _get_current_atr=fake_atr, _execute_full_close=fake_close,
    )
    state = SimpleNamespace(peak_pnl_pct=peak, entry_price=100.0,
                            profit_ratio=profit_ratio)
    res = await ProfitSniper._lc_stall_decision(
        fake, "TESTUSDT", SimpleNamespace(), {}, state,
        pnl_pct=-0.10, is_long=True, age_fraction=age,
        stall_min_age_fraction=stall_min_age,
    )
    check(name, (closed["called"] == expect_close) and (bool(res) == expect_close),
          f"closed={closed['called']} res={res} (expected {expect_close})")


async def main():
    # R=0.20% -> 1R=0.20%. Young dial stall_min_age=1.1 (never stall-cut normally).
    await run_case("dead drifter (peak<1R, age 0.75, no life) -> scratched",
                   peak=0.05, age=0.75, stall_min_age=1.1, profit_ratio=0.0,
                   enabled=True, dd_enabled=True, expect_close=True)
    await run_case("late-bloomer (signs of life) -> veto spares it",
                   peak=0.05, age=0.75, stall_min_age=1.1, profit_ratio=1.0,
                   enabled=True, dd_enabled=True, expect_close=False)
    await run_case("moved >1R (peak 0.30 >= 0.20) -> NOT a dead drifter",
                   peak=0.30, age=0.75, stall_min_age=1.1, profit_ratio=0.0,
                   enabled=True, dd_enabled=True, expect_close=False)
    await run_case("disabled -> legacy rides to the dialed stall age",
                   peak=0.05, age=0.75, stall_min_age=1.1, profit_ratio=0.0,
                   enabled=False, dd_enabled=False, expect_close=False)
    await run_case("dead drifter too young (age 0.50 < 0.70) -> not yet scratched",
                   peak=0.05, age=0.50, stall_min_age=1.1, profit_ratio=0.0,
                   enabled=True, dd_enabled=True, expect_close=False)

    print()
    if FAILS:
        print(f"RESULT: FAIL — {len(FAILS)}: {', '.join(FAILS)}")
        sys.exit(1)
    print("RESULT: PASS — dead-drifter scratch fires only on a proven-dead drifter "
          "past the scratch age, veto intact, >1R and young and disabled all ride.")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    asyncio.get_event_loop().run_until_complete(main())
