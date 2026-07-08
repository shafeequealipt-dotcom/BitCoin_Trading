#!/usr/bin/env python3
"""Live behavioral simulation of the exit-authority consolidation.

Recreates the ORIGINAL problem situation from the Phase 0 forensics — a green
winner caged by competing stop-writers (the collision), an advisory writer
tightening a green trade (the AAVE clip), a catastrophic crash, and a genuine
grinding loser — and drives each one through the REAL SLGateway tick by tick in
two modes:

  BEFORE  the fix dormant   (owner_switch_enforce=false)  -> all writers compete
  AFTER   the fix enforcing (owner_switch_enforce=true)   -> one owner at a time

For each scenario it reports the realized outcome in both modes and a verdict on
whether the fix responded as intended (let winners run, still cut losers, never
weaken the catastrophic floor). The stop-writer VALUES are identical in both
modes — only WHO is allowed to write changes — so the difference is purely the
collision being resolved, not a re-tuning (Rule 6).

Drives the real gateway.apply() pipeline; no DB or exchange I/O. Read-only.
Exits non-zero if any phase fails to respond as intended.
"""
import asyncio
import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.sl_gateway import SLGateway

VERDICTS = []


def build_gateway(enforce, advisory_enforce=False):
    s = Settings._load_fresh()
    s.sl_gateway.owner_switch_enforce = enforce
    s.sl_gateway.advisory_enforce = advisory_enforce
    # Disable R4 rate-limit for the sim so each simulated tick's writers can
    # compete (in production R4 spaces writes 30s apart; here we model one
    # decision per tick). The owner switch — what we are testing — is unaffected.
    s.sl_gateway.rate_limit_seconds = 0

    class P:
        async def get_position(self, x): return None
        async def set_stop_loss(self, x, v): return True
    class M:
        async def get_ticker(self, x): return None
    class E:
        def add_event(self, *a, **k): pass
    return SLGateway(settings=s, position_service=P(), market_service=M(), event_buffer=E())


def _apply(gw, **kw):
    return asyncio.get_event_loop().run_until_complete(gw.apply(**kw))


def run_trade(gw, symbol, entry, init_stop, is_long, ticks, writers):
    """Drive a trade tick by tick through the real gateway. `writers` is a list
    of (source, fn) where fn(price, peak_pct, entry) -> proposed stop or None.
    Returns the realized outcome dict."""
    gw.reset_symbol(symbol)
    direction = "Buy" if is_long else "Sell"
    # Seed the opening protective stop (always-allowed). bypass_step_cap_for_
    # breakeven + bypass_rate_limit mirror the real spine's urgent/bypass path
    # for these monotonic protective sources (all in _BREAKEVEN_BYPASS_SOURCES),
    # so the stop moves at its true distance per tick (one decision per tick).
    _apply(gw, symbol=symbol, new_sl=init_stop, source="loss_atr_initial",
           direction=direction, current_sl=None, current_price=ticks[0], entry_price=entry,
           bypass_step_cap_for_breakeven=True, bypass_rate_limit=True)
    peak_pct = 0.0
    for price in ticks:
        pnl = (price - entry) / entry * 100 if is_long else (entry - price) / entry * 100
        peak_pct = max(peak_pct, pnl)
        # Does this tick's price hit the resting stop (from prior writes)?
        stop = gw._last_sl.get(symbol, init_stop)
        if (is_long and price <= stop) or ((not is_long) and price >= stop):
            ex = (stop - entry) / entry * 100 if is_long else (entry - stop) / entry * 100
            return {"exited": True, "exit_pnl": ex, "peak_pct": peak_pct, "exit_at": price}
        # Each writer proposes; the gateway's owner gate + R1-R4 decide.
        for src, fn in writers:
            prop = fn(price, peak_pct, entry)
            if prop is None:
                continue
            _apply(gw, symbol=symbol, new_sl=prop, source=src, direction=direction,
                   current_sl=gw._last_sl.get(symbol), current_price=price, entry_price=entry,
                   bypass_step_cap_for_breakeven=True, bypass_rate_limit=True)
    stop = gw._last_sl.get(symbol, init_stop)
    locked = (stop - entry) / entry * 100 if is_long else (entry - stop) / entry * 100
    final = (ticks[-1] - entry) / entry * 100 if is_long else (entry - ticks[-1]) / entry * 100
    return {"exited": False, "final_pnl": final, "locked_pnl": locked, "peak_pct": peak_pct}


# ── Writer models (identical values in BEFORE and AFTER) ──────────────────
def ladder_floor(price, peak_pct, entry):
    # Green owner: arm at +0.2%, then lock a give-back below the peak. Values
    # unchanged between modes; this is the green owner's intended floor — it
    # gives the winner room to breathe through a normal pullback.
    if peak_pct < 0.2:
        return None
    floor_pct = max(0.1, peak_pct - 0.6)
    return round(entry * (1 + floor_pct / 100), 6)

def structure_tight(price, peak_pct, entry):
    # Loss-engine structure stop just under current price (the caging writer);
    # the gateway's R2 clamps it to 0.3% below price, still TIGHTER than the
    # ladder floor, so on a green trade it cages the winner if it is allowed in.
    return round(price * (1 - 0.001), 6)

def brain_tight(price, peak_pct, entry):
    # Advisory brain tighten toward a tight stop under price (the AAVE clip).
    return round(price * (1 - 0.001), 6)

def cap_floor(price, peak_pct, entry):
    # The Head: catastrophic cap at -2.5% from entry (long).
    return round(entry * (1 - 2.5 / 100), 6)


def fmt(o):
    if o["exited"]:
        return f"EXITED at {o['exit_pnl']:+.3f}% (peak was {o['peak_pct']:+.3f}%)"
    return f"STILL RUNNING, price {o['final_pnl']:+.3f}%, stop locked {o['locked_pnl']:+.3f}% (peak {o['peak_pct']:+.3f}%)"


def verdict(name, ok, detail):
    print(f"  VERDICT [{ 'FIXED/AS-INTENDED' if ok else 'NOT AS INTENDED' }]: {detail}")
    VERDICTS.append((name, ok))


def scenario_clipped_winner():
    print("\n## SCENARIO A — clipped winner: loss-engine structure caging a green winner (Phase 1 + 3)")
    # A winner: rises to +1.0% peak, pulls back to +0.5%, then runs to +3.0%.
    ticks = [100.0, 100.5, 101.0, 100.5, 101.3, 102.0, 103.0]
    writers = [("profit_sniper_ladder", ladder_floor), ("loss_structure", structure_tight)]
    before = run_trade(build_gateway(enforce=False), "CLIP", 100.0, 98.0, True, ticks, writers)
    after = run_trade(build_gateway(enforce=True), "CLIP", 100.0, 98.0, True, ticks, writers)
    print(f"  BEFORE (collision): {fmt(before)}")
    print(f"  AFTER  (owner switch): {fmt(after)}")
    # Intended: BEFORE clips the winner on the pullback (loss_structure caged it);
    # AFTER the green owner's looser floor survives the pullback and the winner runs.
    ok = before["exited"] and (not after["exited"])
    verdict("A", ok, "the loss writer caged and clipped the winner BEFORE; AFTER only the "
            "green owner writes, so the winner survives the pullback and runs to its target")


def scenario_advisory_clip():
    print("\n## SCENARIO B — advisory clip: brain_tighten caging a green winner, the AAVE case (Phase 5)")
    ticks = [100.0, 100.5, 101.0, 100.5, 101.3, 102.0, 103.0]
    writers = [("profit_sniper_ladder", ladder_floor), ("brain_tighten", brain_tight)]
    before = run_trade(build_gateway(enforce=False, advisory_enforce=False), "ADV", 100.0, 98.0, True, ticks, writers)
    after = run_trade(build_gateway(enforce=True, advisory_enforce=True), "ADV", 100.0, 98.0, True, ticks, writers)
    print(f"  BEFORE (advisory writes the green trade): {fmt(before)}")
    print(f"  AFTER  (advisory demoted): {fmt(after)}")
    ok = before["exited"] and (not after["exited"])
    verdict("B", ok, "the advisory brain tighten caged and clipped the winner BEFORE; AFTER the "
            "advisory is deferred (advice only) and the green owner lets the winner run")


def scenario_catastrophe_head():
    print("\n## SCENARIO C — catastrophe: a fast crash; the Head (cap) must fire in BOTH modes (Phase 2)")
    # Never meaningfully green; a violent crash straight through the -2.5% cap,
    # which sits inside the -3.5% opening stop. The cap (Head) is the operative
    # floor and must fire identically regardless of the owner switch.
    ticks = [100.0, 99.5, 99.0, 98.0, 97.0]
    writers = [("profit_sniper_ladder", ladder_floor), ("loss_cap", cap_floor)]
    before = run_trade(build_gateway(enforce=False), "CAT", 100.0, 96.5, True, ticks, writers)
    after = run_trade(build_gateway(enforce=True), "CAT", 100.0, 96.5, True, ticks, writers)
    print(f"  BEFORE: {fmt(before)}")
    print(f"  AFTER : {fmt(after)}")
    # Intended: the catastrophic cap fires in BOTH modes at ~-2.5% — the owner
    # switch must never weaken the Head.
    ok = (before["exited"] and after["exited"]
          and abs(before["exit_pnl"] - after["exit_pnl"]) < 0.01
          and after["exit_pnl"] <= -2.0)
    verdict("C", ok, f"the catastrophic Head fired identically in both modes "
            f"(BEFORE {before.get('exit_pnl', 0):+.2f}%, AFTER {after.get('exit_pnl', 0):+.2f}%) — "
            f"the owner switch never weakened the floor")


def scenario_genuine_loser():
    print("\n## SCENARIO D — genuine loser: a red grind; the loss engine must still cut in BOTH modes (Phase 4)")
    # Never meaningfully green; grinds down; the loss-engine structure stop
    # tightens (R2 holds it 0.3% off price) and cuts it early, well inside the
    # -3.5% opening stop.
    ticks = [100.0, 99.8, 99.6, 99.4, 99.2]
    writers = [("profit_sniper_ladder", ladder_floor), ("loss_structure", structure_tight)]
    before = run_trade(build_gateway(enforce=False), "LOSE", 100.0, 96.5, True, ticks, writers)
    after = run_trade(build_gateway(enforce=True), "LOSE", 100.0, 96.5, True, ticks, writers)
    print(f"  BEFORE: {fmt(before)}")
    print(f"  AFTER : {fmt(after)}")
    # Intended: the loss engine cuts the loser in BOTH modes (well inside the -2%
    # opening stop) — the red-owner protection is preserved, never lost.
    ok = (before["exited"] and after["exited"]
          and after["exit_pnl"] > -2.0
          and abs(before["exit_pnl"] - after["exit_pnl"]) < 0.5)
    verdict("D", ok, f"the loss engine cut the loser early in both modes "
            f"(BEFORE {before.get('exit_pnl', 0):+.2f}%, AFTER {after.get('exit_pnl', 0):+.2f}%, "
            f"well inside the -3.5% opening stop) — the red-owner protection is preserved")


def main():
    print("EXIT-AUTHORITY LIVE SIMULATION — recreate the issue, check each fix responds")
    asyncio.set_event_loop(asyncio.new_event_loop())
    scenario_clipped_winner()
    scenario_advisory_clip()
    scenario_catastrophe_head()
    scenario_genuine_loser()
    print("\n" + "=" * 70)
    bad = [n for n, ok in VERDICTS if not ok]
    if bad:
        print(f"RESULT: FAIL — scenarios not as intended: {', '.join(bad)}")
        sys.exit(1)
    print("RESULT: PASS — every phase responded as intended: winners run, the advisory")
    print("no longer cages them, the catastrophic Head is undiminished, and losers are still cut.")
    sys.exit(0)


if __name__ == "__main__":
    main()
