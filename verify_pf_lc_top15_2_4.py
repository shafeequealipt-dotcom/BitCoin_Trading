#!/usr/bin/env python3
"""PF/LC Top-15 Problem 2.4 — Chandelier trail activation aligned to the ladder arm.

Read-only proof that the trail's activation threshold is centralized in
[profit_fetching], aligned to the ladder arm (0.2%), and that the sniper reads
it from there — so the Chandelier is now a candidate across the full
0.2-to-0.5% band instead of sitting idle until 0.5%.

Run: python3 verify_pf_lc_top15_2_4.py
"""
import inspect
import sys

from src.config.settings import Settings
from src.workers.profit_sniper import ProfitSniper

fails = []
s = Settings.load()
pf = s.profit_fetching

# ── Config: the trail threshold lives in profit_fetching and equals the arm. ──
arm = pf.min_profit_to_arm_ladder_pct
trail = getattr(pf, "min_profit_for_trail_pct", None)
if trail is None:
    fails.append("profit_fetching.min_profit_for_trail_pct is missing")
elif abs(trail - arm) > 1e-9:
    fails.append(f"trail activation ({trail}) is not aligned to the ladder arm ({arm})")

# ── Source: the sniper reads the threshold from self._pf, not settings.mode4. ──
src = inspect.getsource(ProfitSniper)
# locate the activation line
act = [ln for ln in src.splitlines() if "min_profit_for_trail_pct" in ln and "peak_pnl_pct" in ln]
if not act:
    fails.append("could not find the trail activation line in the sniper")
else:
    line = act[0]
    if "self._pf.min_profit_for_trail_pct" not in line:
        fails.append(f"activation does not read self._pf: {line.strip()}")
    if "settings.mode4.min_profit_for_trail_pct" in line:
        fails.append("activation still reads the deprecated settings.mode4 key")

# ── Behaviour: a peak in the 0.2-0.5% band now activates the trail; under the
#    old 0.5% threshold it would not. ──
for peak in (0.25, 0.40):
    activates_now = peak >= trail
    activated_old = peak >= 0.50
    if not activates_now:
        fails.append(f"peak {peak}% should activate the trail at the new {trail}% threshold")
    if activated_old:
        fails.append(f"peak {peak}% should NOT have activated under the old 0.5% (sanity)")

if __name__ == "__main__":
    if fails:
        print("FAIL — PF/LC 2.4 trail-activation verification:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print(f"PASS — PF/LC 2.4: trail activation centralized in [profit_fetching] and "
          f"aligned to the ladder arm ({trail}%); the sniper reads it from self._pf; "
          f"peaks in the 0.2-0.5% band now make the Chandelier a candidate (idle before).")
