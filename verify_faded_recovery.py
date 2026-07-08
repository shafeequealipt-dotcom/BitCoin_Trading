#!/usr/bin/env python3
"""Verify faded-winner (F5) and recovery-capture (G3) under the live R-geometry.

Read-only. These two master items are handled by the geometry being live and
R-relative (Commit 2), not by changing ownership (unchanged) — so this is a
verification, per the design. It exercises the REAL wired _compute_ladder_floor:

  F5 faded winner: a trade that peaked well above 1R then craters keeps its
     R-level lock, because the ladder is driven by the monotonic HIGH-WATER peak
     and is tighten-only — so the lock holds at the peak's R-level (NOT the old
     breakeven sliver) as the trade fades.

  G3 recovered fighter: a trade that returns to green just above the arm arms the
     ladder and locks a real fee-cleared R-level (NOT a breakeven sliver), so the
     bounce is captured.

The loss-side final-phase recovery candidate (_lc_recovery_candidate) is already
ATR-scaled (R-relative) and stays inside the sacred cap; it is left unchanged.
Exits non-zero on any mismatch.
"""
import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.workers.profit_sniper import ProfitSniper, LadderResult
from src.analysis import vol_scale as g

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


s = Settings._load_fresh()
ae = s.adaptive_exit
ae.enabled = True
ae.r_smoothing_alpha = 1.0
_F = SimpleNamespace(settings=s, _pf=s.profit_fetching, _smoothed_r={},
                     _last_ladder_adaptive_logged={}, _last_breakeven_floor_logged={})
_F._adaptive_r = lambda sym, raw, a: ProfitSniper._adaptive_r(_F, sym, raw, a)

R = 0.20
entry = 100.0
atr_value = R / 100.0 * entry      # atr_value/entry*100 == R
dialed = SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3)
fee = g.fee_floor_pct(ae)

print("=== F5: faded winner keeps its R-level lock (driven by high-water peak) ===")
# Peaked at 3R (0.60%) then faded; the ladder reads the HIGH-WATER peak, so the
# lock holds at the R-level regardless of the now-lower current pnl.
peak = 0.60   # 3R
res = ProfitSniper._compute_ladder_floor(
    _F, SimpleNamespace(entry_price=entry, direction="Buy", peak_pnl_pct=peak, symbol="FADE"),
    dialed, 0.0, atr_value=atr_value)
exp = g.profit_lock_pct(peak, R, ae)   # = max(fee, 0.60-0.10=0.50, staged 1.5R=0.30) = 0.50
check("faded winner locks the R-level (not the breakeven sliver)",
      isinstance(res, LadderResult) and abs(res.lock_pct - round(exp, 4)) < 1e-4
      and res.lock_pct > fee + 0.2,   # clearly above a breakeven sliver
      f"lock={res.lock_pct}% (R-level {round(exp,4)}%, fee floor {fee}%)")
# tighten-only: as it fades, a current_sl already at the lock is not loosened.
res2 = ProfitSniper._compute_ladder_floor(
    _F, SimpleNamespace(entry_price=entry, direction="Buy", peak_pnl_pct=peak, symbol="FADE"),
    dialed, current_sl=entry * (1 + res.lock_pct / 100.0), atr_value=atr_value)
check("faded winner lock is tighten-only (held, not loosened)",
      not res2.should_apply or res2.ladder_stop_price >= entry * (1 + res.lock_pct / 100.0) - 1e-6,
      f"should_apply={res2.should_apply}")

print("\n=== G3: recovered fighter (back to green just above arm) captures a real lock ===")
peak = 0.25   # just above the arm (max(0.5R=0.10, fee 0.11) = 0.11)
res3 = ProfitSniper._compute_ladder_floor(
    _F, SimpleNamespace(entry_price=entry, direction="Buy", peak_pnl_pct=peak, symbol="RECOV"),
    dialed, 0.0, atr_value=atr_value)
exp3 = g.profit_lock_pct(peak, R, ae)   # = max(0.11, 0.25-0.10=0.15) = 0.15
check("recovered fighter locks a fee-cleared R-level (not a breakeven sliver)",
      isinstance(res3, LadderResult) and abs(res3.lock_pct - round(exp3, 4)) < 1e-4
      and res3.lock_pct >= fee - 1e-9,
      f"lock={res3.lock_pct}% (>= fee {fee}%)")

print()
if FAILS:
    print(f"RESULT: FAIL — {len(FAILS)}: {', '.join(FAILS)}")
    sys.exit(1)
print("RESULT: PASS — faded winner keeps its R-level lock (tighten-only on the "
      "high-water peak); recovered fighter captures a fee-cleared R-level. F5/G3 "
      "handled by the live R-geometry; ownership unchanged.")
sys.exit(0)
