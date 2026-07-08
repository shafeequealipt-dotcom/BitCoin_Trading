#!/usr/bin/env python3
"""PF/LC Top-15 Problems 3.2 + 3.5 — real M5 ATR feed + warm-cache fallback cut.

3.2: the per-tick Chandelier trail should size its leash from the precise M5
     Wilder ATR (the warm-seeded _get_current_atr the loss path already uses)
     rather than the cold ring-buffer atr_current / price-range/4 proxy, with
     the live -> entry-ATR -> floor fallback preserved for cold-start.
3.5: once the warm cache is consulted per tick, the SNIPER_ATR_FALLBACK flood
     stops, so it is demoted to DEBUG.

Verified: the config flag (default off); the tick() routing (warm M5 source when
on, demoted log); and the _pf_effective_atr fallback chain that makes a non-zero
M5 ATR resolve as "live" (no fallback) while a cold zero falls back to entry-ATR.

Run: python3 verify_pf_lc_top15_3_2_and_3_5.py
"""
import inspect
import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.workers.profit_sniper import ProfitSniper

fails = []

# ── Config: the flag exists and defaults off (ships inert). ──
pf = Settings.load().profit_fetching
if not hasattr(pf, "trail_live_m5_atr_enabled"):
    fails.append("profit_fetching.trail_live_m5_atr_enabled is missing")
elif pf.trail_live_m5_atr_enabled is not False:
    fails.append("trail_live_m5_atr_enabled must default to False (ship inert)")

# ── Structural: tick() routes the warm M5 source when the flag is on and demotes
#    the fallback log. ──
src = inspect.getsource(ProfitSniper.tick)
if "self._pf.trail_live_m5_atr_enabled" not in src:
    fails.append("tick() does not gate the trail ATR source on the new flag")
if "await self._get_current_atr(symbol)" not in src:
    fails.append("tick() does not consult the warm _get_current_atr for the trail")
if "log.debug(_atr_fb_msg)" not in src:
    fails.append("the SNIPER_ATR_FALLBACK log is not demoted to DEBUG when on (3.5)")
if "log.info(_atr_fb_msg)" not in src:
    fails.append("the off-path SNIPER_ATR_FALLBACK should stay INFO (unchanged)")

# ── Behavioural: the fallback chain that 3.2 relies on. ──
fake = SimpleNamespace(_pf=SimpleNamespace(atr_zero_fallback_pct=0.3))
eff = ProfitSniper._pf_effective_atr.__get__(fake, ProfitSniper)
# A non-zero live M5 ATR resolves as "live" → no fallback, no log flood.
val, srcname = eff(0.0013, 0.0011, 100.0)
if srcname != "live" or abs(val - 0.0013) > 1e-12:
    fails.append(f"warm M5 ATR should resolve live, got ({val}, {srcname})")
# A cold zero (the old per-tick reality) falls back to entry-ATR → the flood.
val, srcname = eff(0.0, 0.0011, 100.0)
if srcname != "entry_atr":
    fails.append(f"cold zero should fall back to entry_atr, got {srcname}")

if __name__ == "__main__":
    if fails:
        print("FAIL — PF/LC 3.2 + 3.5 verification:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS — PF/LC 3.2 + 3.5: flag defaults off; tick() consults the warm M5 ATR "
          "when on and demotes the fallback log to DEBUG; a non-zero M5 ATR resolves "
          "as 'live' (no fallback) while a cold zero falls back to entry-ATR.")
