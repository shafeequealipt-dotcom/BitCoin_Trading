#!/usr/bin/env python3
"""F37 verification — downstream minimum stop-loss clamp in SLTPValidator.

Exercises the real validator with concrete prices: a correct-side stop closer
than the minimum is clamped OUT to the minimum; an in-bounds stop passes through
unchanged; wrong-side stops keep their existing auto-fix; the minimum is loaded
from centralized [risk] config. Read-only. Exit 0 = all checks pass.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from src.core.sl_tp_validator import SLTPValidator  # noqa: E402
from src.config.settings import Settings  # noqa: E402

_FAIL: list[str] = []


def chk(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        _FAIL.append(name)


print("=" * 70)
print("verify_min_sl_clamp — F37 minimum stop-loss distance")
print("=" * 70)

# Centralized config loads.
S = Settings.load("config.toml")
chk("min_sl_distance_pct loaded from [risk] config",
    abs(float(S.risk.min_sl_distance_pct) - 1.5) < 1e-9, f"={S.risk.min_sl_distance_pct}")

V = SLTPValidator(headspace_pct=2.5, max_distance_pct=15.0, min_sl_distance_pct=1.5)
PRICE = 100.0

# Buy, correct side (SL below) but too close (0.3%) -> clamp to 1.5% below.
act, adj, why = V.validate_sl(99.70, PRICE, "Buy", "T")
chk("Buy too-close SL (0.3%) is clamped", act == "ADJUST" and abs(adj - 98.5) < 1e-6,
    f"act={act} adj={adj}")
# Buy, correct side, far enough (2% below) -> SET unchanged.
act, adj, why = V.validate_sl(98.0, PRICE, "Buy", "T")
chk("Buy in-bounds SL (2%) passes unchanged", act == "SET" and abs(adj - 98.0) < 1e-6,
    f"act={act} adj={adj}")
# Buy, exactly at the minimum (1.5%) -> SET (strict < means at-min is fine).
act, adj, why = V.validate_sl(98.5, PRICE, "Buy", "T")
chk("Buy SL exactly at minimum passes (strict <)", act == "SET", f"act={act}")
# Sell, correct side (SL above) but too close (0.5%) -> clamp to 1.5% above.
act, adj, why = V.validate_sl(100.5, PRICE, "Sell", "T")
chk("Sell too-close SL (0.5%) is clamped", act == "ADJUST" and abs(adj - 101.5) < 1e-6,
    f"act={act} adj={adj}")
# Sell, correct side, far enough (2% above) -> SET unchanged.
act, adj, why = V.validate_sl(102.0, PRICE, "Sell", "T")
chk("Sell in-bounds SL (2%) passes unchanged", act == "SET" and abs(adj - 102.0) < 1e-6,
    f"act={act} adj={adj}")
# Buy wrong-side (SL above entry) -> existing auto-fix preserved (ADJUST below).
act, adj, why = V.validate_sl(100.3, PRICE, "Buy", "T")
chk("Buy wrong-side SL still auto-fixed below entry", act == "ADJUST" and adj < PRICE,
    f"act={act} adj={adj}")
# At-or-through entry for Buy (SL == price) -> not SET-valid (handled by wrong-side).
act, adj, why = V.validate_sl(100.0, PRICE, "Buy", "T")
chk("Buy SL at entry is not passed as valid", act != "SET", f"act={act}")
# Too-far (>15%) still SKIP (existing upper bound intact).
act, adj, why = V.validate_sl(80.0, PRICE, "Buy", "T")
chk("Buy SL beyond max distance still SKIP", act == "SKIP", f"act={act}")

print("\n" + "-" * 70)
if _FAIL:
    print(f"RESULT: FAIL ({len(_FAIL)}): {_FAIL}")
    sys.exit(1)
print("RESULT: PASS — too-close correct-side stops are clamped to the minimum,")
print("in-bounds stops pass unchanged, wrong-side and too-far behavior preserved.")
