#!/usr/bin/env python3
"""PF/LC Top-15 Problem 2.1 — windowed stall signs-of-life veto (offline check).

Drives the real ProfitSniper._lc_stall_decision to prove the WINDOWED in-profit
ratio fixes the stale-evidence flaw without harming late-bloomers (Rule 9):

  - A faded early-winner (healthy early, now bleeding with no recent profit) is
    SPARED by the cumulative ratio (the bug) but CUT by the windowed ratio.
  - A recently-green late-bloomer is SPARED by the windowed ratio (and would in
    fact be CUT under the cumulative ratio) — so the windowed veto is, if
    anything, MORE protective of a genuine late-bloomer.

Outcome dollar-verdicts remain provisional until the truthful ruler (1.3) has
run; this proves the veto LOGIC. Read-only; mutates no data.

Run: python3 verify_pf_lc_top15_2_1.py
"""
import asyncio
import sys
from types import SimpleNamespace

from src.workers.profit_sniper import ProfitSniper


def _lc(windowed):
    return SimpleNamespace(
        enable_stall_exit=True,
        stall_signs_of_life_lookback_ticks=24,
        stall_signs_of_life_profit_ratio=0.25,
        stall_signs_of_life_peak_improve_pct=0.15,
        stall_veto_windowed_profit_ratio_enabled=windowed,
        # Problem 2.2 fields (kept off here so this test isolates the 2.1 lever).
        stall_signs_of_life_sustained_improving_enabled=False,
        stall_signs_of_life_improving_lookback_ticks=3,
        stall_signs_of_life_improving_floor_bps=2.0,
        stall_veto_budget_warn=8,
        stall_tail_yield_fraction=0.95,
    )


def _fake(windowed):
    f = SimpleNamespace()
    f._lc = _lc(windowed)
    f.layer4_protection = None

    async def _close(*a, **k):
        return True  # the "cut" sentinel

    f._execute_full_close = _close
    return f


async def _decide(windowed, *, pnl_hist, peak_hist, prev, pnl_pct,
                  cumulative_ratio, peak_pnl_pct, age_fraction=0.7,
                  stall_min=0.5):
    f = _fake(windowed)
    fn = ProfitSniper._lc_stall_decision.__get__(f, ProfitSniper)
    tracked = {
        "_lc_pnl_hist": list(pnl_hist),
        "_lc_peak_hist": list(peak_hist),
        "_lc_pnl_prev": prev,
    }
    state = SimpleNamespace(profit_ratio=cumulative_ratio, peak_pnl_pct=peak_pnl_pct)
    cut = await fn("TESTUSDT", object(), tracked, state, pnl_pct, True,
                   age_fraction, stall_min)
    return cut  # True == force-closed (cut), False == spared


def _run():
    loop = asyncio.get_event_loop()
    fails = []

    # ── Faded early-winner: 23 recent red ticks, flat high peak, cumulative
    #    ratio 0.80 (it was green most of its life). No improving, no peak rise. ──
    fw = dict(pnl_hist=[-0.5] * 23, peak_hist=[2.0] * 23, prev=-0.5,
              pnl_pct=-0.5, cumulative_ratio=0.80, peak_pnl_pct=2.0)
    spared_cumulative = not loop.run_until_complete(_decide(False, **fw))
    cut_windowed = loop.run_until_complete(_decide(True, **fw))
    if not spared_cumulative:
        fails.append("faded-winner: cumulative ratio should SPARE it (the bug)")
    if not cut_windowed:
        fails.append("faded-winner: windowed ratio should CUT it (the fix)")

    # ── Recently-green late-bloomer: 23 recent green ticks, then a small red dip;
    #    cumulative ratio only 0.10 (it struggled early). ──
    lb = dict(pnl_hist=[0.3] * 23, peak_hist=[0.5] * 23, prev=0.3,
              pnl_pct=-0.1, cumulative_ratio=0.10, peak_pnl_pct=0.5)
    spared_windowed = not loop.run_until_complete(_decide(True, **lb))
    if not spared_windowed:
        fails.append("late-bloomer: windowed ratio must SPARE a recently-green trade")

    # ── Optional: replay real LOSS_STALL_VETO lines if present (report only). ──
    import glob
    import re
    logs = glob.glob("data/logs/workers*.log")
    flips = total = 0
    for lf in logs[:3]:
        try:
            with open(lf, errors="ignore") as fh:
                for line in fh:
                    if "LOSS_STALL_VETO" not in line:
                        continue
                    m = re.search(r"profit_ratio=([\-0-9.]+)", line)
                    if not m:
                        continue
                    total += 1
                    # cumulative >= 0.25 spared; we cannot recompute the window
                    # from a single line, so just count how many sparings leaned
                    # on a high cumulative ratio (candidates the window may flip).
                    if float(m.group(1)) >= 0.25:
                        flips += 1
        except OSError:
            pass
    if total:
        print(f"  (replay note: {flips}/{total} historical stall-vetoes leaned on "
              f"cumulative ratio >= 0.25 — candidates the window re-evaluates)")

    return fails


if __name__ == "__main__":
    fails = _run()
    if fails:
        print("FAIL — PF/LC 2.1 windowed stall-veto verification:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS — PF/LC 2.1: the windowed in-profit ratio cuts a faded early-winner "
          "the cumulative ratio wrongly spared, and spares a recently-green "
          "late-bloomer (Rule 9 protected). Dollar verdict provisional until 1.3 runs.")
