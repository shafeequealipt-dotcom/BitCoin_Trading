#!/usr/bin/env python3
"""PF/LC Top-15 Problem 2.2 — sustained vs single-tick improving reprieve.

Drives the real ProfitSniper._lc_stall_decision to prove the reprieve no longer
fires on a single noise up-tick, while a genuine sustained recovery is still
spared (Rule 9 late-bloomer protection):

  - A flat-bleeding trade with one tiny up-tick (< the floor) is SPARED by the
    old single-tick check (noise rescues a dying trade) but CUT by the
    sustained check.
  - A trade that has genuinely climbed off a recent low by more than the floor
    is SPARED by the sustained check.

Read-only; mutates no data. Dollar verdict provisional until 1.3 runs.
Run: python3 verify_pf_lc_top15_2_2.py
"""
import asyncio
import sys
from types import SimpleNamespace

from src.workers.profit_sniper import ProfitSniper


def _lc(sustained):
    return SimpleNamespace(
        enable_stall_exit=True,
        stall_signs_of_life_lookback_ticks=24,
        stall_signs_of_life_profit_ratio=0.25,
        stall_signs_of_life_peak_improve_pct=0.15,
        stall_veto_windowed_profit_ratio_enabled=False,  # isolate the improving lever
        stall_signs_of_life_sustained_improving_enabled=sustained,
        stall_signs_of_life_improving_lookback_ticks=3,
        stall_signs_of_life_improving_floor_bps=2.0,      # 0.02% of PnL
        stall_veto_budget_warn=8,
        stall_tail_yield_fraction=0.95,
    )


def _fake(sustained):
    f = SimpleNamespace()
    f._lc = _lc(sustained)
    f.layer4_protection = None

    async def _close(*a, **k):
        return True

    f._execute_full_close = _close
    return f


async def _decide(sustained, *, pnl_hist, prev, pnl_pct):
    f = _fake(sustained)
    fn = ProfitSniper._lc_stall_decision.__get__(f, ProfitSniper)
    tracked = {
        "_lc_pnl_hist": list(pnl_hist),
        "_lc_peak_hist": [2.0, 2.0, 2.0],   # flat → no peak_rise veto
        "_lc_pnl_prev": prev,
    }
    state = SimpleNamespace(profit_ratio=0.0, peak_pnl_pct=2.0)  # building=False
    return await fn("TESTUSDT", object(), tracked, state, pnl_pct, True, 0.7, 0.5)


def _run():
    loop = asyncio.get_event_loop()
    fails = []

    # ── Single-tick noise blip: flat at -0.5, one tiny up-tick to -0.499
    #    (a 0.001% rise, below the 0.02% floor). ──
    blip = dict(pnl_hist=[-0.5, -0.5, -0.5], prev=-0.5, pnl_pct=-0.499)
    spared_single = not loop.run_until_complete(_decide(False, **blip))
    cut_sustained = loop.run_until_complete(_decide(True, **blip))
    if not spared_single:
        fails.append("noise-blip: single-tick check should SPARE it (the bug)")
    if not cut_sustained:
        fails.append("noise-blip: sustained check should CUT it (the fix)")

    # ── Genuine sustained recovery: climbed off -0.6 to -0.50 over the window
    #    (a 0.10% rise, well above the floor). ──
    rec = dict(pnl_hist=[-0.6, -0.55, -0.52], prev=-0.52, pnl_pct=-0.50)
    spared_recovery = not loop.run_until_complete(_decide(True, **rec))
    if not spared_recovery:
        fails.append("recovery: sustained check must SPARE a genuine climb (Rule 9)")

    return fails


if __name__ == "__main__":
    fails = _run()
    if fails:
        print("FAIL — PF/LC 2.2 sustained-improving verification:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS — PF/LC 2.2: a single-tick noise up-tick no longer spares a dying "
          "trade, while a genuine sustained recovery off a recent low is still "
          "spared. Dollar verdict provisional until 1.3 runs.")
