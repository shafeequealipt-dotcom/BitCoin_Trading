#!/usr/bin/env python3
"""Verify the Commit 2 wiring of the R-based adaptive ladder into the sniper.

Read-only, no DB/exchange. Exercises the REAL _compute_ladder_floor method (and
the _adaptive_r smoother) with adaptive_exit forced on, asserting the wired path
produces exactly vol_scale.profit_lock_pct, that it is bypassed when disabled
(legacy path intact), and that R smoothing behaves. Exits non-zero on any
mismatch. The live config still lands DORMANT (enabled=false) — this forces the
flag on only in-process to test the wired path before go-live.
"""
import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.workers.profit_sniper import ProfitSniper, LadderResult
from src.analysis import vol_scale as g

FAILS = []


def _fake(settings):
    return SimpleNamespace(
        settings=settings,
        _pf=settings.profit_fetching,
        _smoothed_r={},
        _last_ladder_adaptive_logged={},
        _last_breakeven_floor_logged={},
        _adaptive_r=lambda sym, raw, ae: ProfitSniper._adaptive_r(
            _F, sym, raw, ae),  # bound below
    )


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


s = Settings._load_fresh()
ae = s.adaptive_exit
ae.enabled = True              # force on in-process for the wiring test
ae.r_smoothing_alpha = 1.0     # disable smoothing so R == raw for exact-match asserts

_F = _fake(s)

print("=== adaptive ladder path matches vol_scale.profit_lock_pct ===")
for (R, peak, side) in [(0.20, 0.30, "Buy"), (0.20, 2.00, "Buy"),
                        (0.60, 0.56, "Buy"), (0.20, 0.30, "Sell"),
                        (0.20, 0.02, "Buy")]:
    entry = 100.0
    atr_value = R / 100.0 * entry      # so atr_value/entry*100 == R
    state = SimpleNamespace(entry_price=entry, direction=side, peak_pnl_pct=peak,
                            symbol="TESTUSDT")
    dialed = SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3)
    _F._smoothed_r = {}            # reset per case
    res = ProfitSniper._compute_ladder_floor(_F, state, dialed, 0.0, atr_value=atr_value)
    exp = g.profit_lock_pct(peak, R, ae)
    if exp is None:
        check(f"R={R} peak={peak} {side} below-arm -> not should_apply",
              isinstance(res, LadderResult) and not res.should_apply,
              f"lock_pct={res.lock_pct}")
    else:
        ok = isinstance(res, LadderResult) and abs(res.lock_pct - round(exp, 4)) < 1e-4
        # stop on the correct side of entry
        if side == "Buy":
            ok = ok and res.ladder_stop_price > entry
        else:
            ok = ok and res.ladder_stop_price < entry
        check(f"R={R} peak={peak} {side}", ok,
              f"wired lock={res.lock_pct} expected={round(exp,4)} stop={res.ladder_stop_price}")

print("\n=== legacy path intact when disabled (adaptive branch bypassed) ===")
ae.enabled = False
# peak 0.70 crosses the first rung (step 0.6): legacy step lock = level(0.6) -
# offset(0.3) = 0.30; the R-lock would be profit_lock_pct(0.70, 0.2) = 0.60.
# They differ clearly, so an exact-0.30 result proves the legacy path ran and
# the R-lock was NOT used (the adaptive branch is correctly gated off).
state = SimpleNamespace(entry_price=100.0, direction="Buy", peak_pnl_pct=0.70, symbol="TESTUSDT")
dialed = SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3)
res_legacy = ProfitSniper._compute_ladder_floor(_F, state, dialed, 0.0, atr_value=0.2)
exp_adp = g.profit_lock_pct(0.70, 0.20, s.adaptive_exit)   # = 0.60 (the R-lock, must NOT be used)
check("disabled -> legacy step lock (0.30), not the R-lock (0.60)",
      isinstance(res_legacy, LadderResult)
      and abs(res_legacy.lock_pct - 0.30) < 1e-4
      and abs(res_legacy.lock_pct - round(exp_adp, 4)) > 1e-4,
      f"legacy lock={res_legacy.lock_pct} (R-lock would be {round(exp_adp,4)})")

print("\n=== R smoothing (EMA at the fetch boundary) ===")
ae.enabled = True
ae.r_smoothing_alpha = 0.5
_F._smoothed_r = {}
r1 = ProfitSniper._adaptive_r(_F, "X", 0.40, ae)   # first obs -> raw
r2 = ProfitSniper._adaptive_r(_F, "X", 0.20, ae)   # 0.5*0.20 + 0.5*0.40 = 0.30
check("first observation == raw", abs(r1 - 0.40) < 1e-9, f"r1={r1}")
check("EMA blends toward new value", abs(r2 - 0.30) < 1e-9, f"r2={r2}")

print()
if FAILS:
    print(f"RESULT: FAIL — {len(FAILS)} check(s) failed: {', '.join(FAILS)}")
    sys.exit(1)
print("RESULT: PASS — wired adaptive ladder matches the geometry, legacy intact, smoothing correct.")
sys.exit(0)
