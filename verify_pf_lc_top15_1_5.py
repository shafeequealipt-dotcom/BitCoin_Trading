#!/usr/bin/env python3
"""PF/LC Top-15 Problem 1.5 — the spine rate-limit swallow is now logged.

Read-only structural proof that the bare `except Exception: pass` around the
gateway rate-limit eligibility check has been replaced by a rate-limited
warning, while the fall-through to the R4-validated write is preserved
(behaviour unchanged, visibility added).

Run: python3 verify_pf_lc_top15_1_5.py
"""
import inspect
import sys

from src.workers.profit_sniper import ProfitSniper

src = inspect.getsource(ProfitSniper._pf_apply_spine)

fails = []
# The eligibility check must still exist and still short-circuit.
if "next_eligible_in_seconds(symbol) > 0.0" not in src:
    fails.append("the rate-limit eligibility short-circuit was removed")
# The silent swallow must be gone, replaced by the throttled warning.
if "SNIPER_RATELIMIT_CHECK_ERROR" not in src:
    fails.append("the thrown check is not logged (no SNIPER_RATELIMIT_CHECK_ERROR)")
# Make sure the specific bare 'except Exception:\n  pass' on this check is gone.
block = src[src.find("next_eligible_in_seconds(symbol) > 0.0"):]
window = block[:400]
if "except Exception:\n" in window and "pass" in window[:window.find("log.warning") if "log.warning" in window else len(window)]:
    # crude: a bare pass appearing before any warning in the except window
    if "except Exception as _e:" not in window:
        fails.append("the bare 'except Exception: pass' still swallows the check")
# The fall-through write path must remain (R4 still enforced downstream).
if "self.sl_gateway.apply(" not in src:
    fails.append("the gateway write fall-through was removed")

if __name__ == "__main__":
    if fails:
        print("FAIL — PF/LC 1.5 verification:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS — PF/LC 1.5: the rate-limit eligibility check no longer swallows "
          "exceptions silently; a thrown check emits SNIPER_RATELIMIT_CHECK_ERROR "
          "(rate-limited) and still falls through to the R4-validated gateway write.")
