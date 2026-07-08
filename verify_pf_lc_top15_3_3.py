#!/usr/bin/env python3
"""PF/LC Top-15 Problem 3.3 — confirm the trail-width tunables are centralized.

Problem 3.3 is measure-then-tune (after Problem 2.4 has run live); there is no
code change beyond the already-centralized tunables. This confirms those knobs
exist and load from config, so the operator can follow the procedure in
PF_LC_TOP15_3_3_CHANDELIER_BAND_TUNING.md.

Run: python3 verify_pf_lc_top15_3_3.py
"""
import sys

from src.config.settings import Settings

s = Settings.load()
fails = []

base = getattr(s.mode4, "base_atr_multiplier", None)
if base is None:
    fails.append("mode4.base_atr_multiplier (the primary trail-width tunable) is missing")

ay = getattr(s.profit_fetching, "atr_multiple_young", None)
if ay is None:
    fails.append("profit_fetching.atr_multiple_young (the dial-glide tunable) is missing")

# 2.4 must already be in place for 3.3 to make sense (trail aligned to the arm).
trail = getattr(s.profit_fetching, "min_profit_for_trail_pct", None)
arm = getattr(s.profit_fetching, "min_profit_to_arm_ladder_pct", None)
if trail is None or arm is None or abs(trail - arm) > 1e-9:
    fails.append("Problem 2.4 prerequisite not in place (trail not aligned to the arm)")

if __name__ == "__main__":
    if fails:
        print("FAIL — PF/LC 3.3 tunable check:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print(f"PASS — PF/LC 3.3: trail-width tunables are centralized "
          f"(base_atr_multiplier={base}, atr_multiple_young={ay}); the 2.4 alignment "
          f"is in place (trail {trail}% == arm {arm}%). Measure-then-tune procedure "
          f"in PF_LC_TOP15_3_3_CHANDELIER_BAND_TUNING.md, after 2.4 has run live.")
