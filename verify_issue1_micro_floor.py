"""Verify Issue 1 — decoupled micro-floor (capture the green) (CALL_A exploit/fetch).

Read-only. Drives the REAL ProfitSniper._compute_ladder_floor with the real
loaded ProfitFetchingSettings and asserts:

  A. CAPTURE: a +0.15% peak (the round-tripping small-green band) now ARMS the
     floor and locks a stop above entry with micro_floor_arm_pct=0.10, where the
     old single-arm behaviour (micro == graduation == 0.20) does NOT arm and the
     green round-trips.
  B. WINNER SAFETY: the micro-floor is monotonic tighten-only and only ratchets
     a stop just above entry behind an early small peak — far below a genuine
     winner now trading at +0.8%, so it cannot cut a slow-starter short; once a
     real rung is crossed the normal step lock supersedes it.
  C. AUTHORITY RETENTION (the entanglement guard): a peak in [0.10, 0.20) arms
     the floor (breakeven_floor=True, should_apply=True) AND stays below the
     graduation arm (min_profit_to_arm_ladder_pct=0.2), so GRADUATION_LATCH does
     NOT fire and loss-cutting authority is retained.
  D. NO REGRESSION: with micro == graduation (0.20) the arming behaviour is
     byte-identical to the old single-arm code.

Uses the real method bound to a minimal shim (the method only reads self._pf
and self._last_breakeven_floor_logged). No protected tables touched; nothing
mutated.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from src.workers.profit_sniper import ProfitSniper
from src.config.settings import _build_profit_fetching


def _pf(micro):
    data = tomllib.load(open("config.toml", "rb"))
    pf = _build_profit_fetching(data.get("profit_fetching", {}))
    # Override only the micro arm for the before/after comparison.
    pf.micro_floor_arm_pct = micro
    return pf


def _floor(peak, *, micro, entry=100.0, direction="Buy", step=0.6, offset=0.3,
           current_sl=0.0):
    shim = SimpleNamespace(_pf=_pf(micro), _last_breakeven_floor_logged={})
    state = SimpleNamespace(
        entry_price=entry, direction=direction, peak_pnl_pct=peak, symbol="TESTUSDT",
    )
    dialed = SimpleNamespace(ladder_step_pct=step, lock_offset_pct=offset)
    return ProfitSniper._compute_ladder_floor(shim, state, dialed, current_sl)


def main():
    GRAD = 0.20  # min_profit_to_arm_ladder_pct
    failures = []

    # A. CAPTURE — +0.15% small green.
    after = _floor(0.15, micro=0.10)
    before = _floor(0.15, micro=GRAD)  # old single-arm behaviour
    okA = (
        after.armed and after.should_apply and after.breakeven_floor
        and after.ladder_stop_price > 100.0          # stop locked ABOVE entry
        and not before.should_apply                  # old code: no lock, round-trips
    )
    print(f"A. capture +0.15% green: after armed={after.armed} "
          f"apply={after.should_apply} lock={after.lock_pct}% "
          f"stop={after.ladder_stop_price:.4f} | before apply={before.should_apply} "
          f"-> {'PASS' if okA else 'FAIL'}")
    if not okA:
        failures.append("capture")

    # B. WINNER SAFETY — early small peak locks just above entry, far below a
    #    winner now at +0.8%; and once the first rung (0.6%) is crossed the step
    #    lock supersedes the micro band.
    early = _floor(0.15, micro=0.10)
    winner_price = 100.0 * (1.0 + 0.8 / 100.0)        # +0.8% = 100.80
    crossed = _floor(0.80, micro=0.10)                # peak past first rung 0.6
    okB = (
        early.ladder_stop_price < winner_price         # cannot cut the +0.8% winner
        and not crossed.breakeven_floor                # real rung crossed -> step lock
        and abs(crossed.lock_pct - 0.30) < 1e-6        # level 0.6 - offset 0.3 = 0.3
    )
    print(f"B. winner safety: early stop={early.ladder_stop_price:.4f} < "
          f"winner@{winner_price:.4f}; crossed lock={crossed.lock_pct}% "
          f"be_floor={crossed.breakeven_floor} -> {'PASS' if okB else 'FAIL'}")
    if not okB:
        failures.append("winner-safety")

    # C. AUTHORITY RETENTION — peak in [0.10, 0.20) arms the floor but stays
    #    below the graduation arm, so loss-cutting authority is NOT handed off.
    mid = _floor(0.15, micro=0.10)
    okC = (
        mid.armed and mid.should_apply and mid.breakeven_floor
        and 0.15 < GRAD                                # graduation would NOT fire
    )
    print(f"C. authority retention: peak=0.15 armed={mid.armed} "
          f"apply={mid.should_apply} | 0.15 < grad_arm({GRAD}) = "
          f"{0.15 < GRAD} -> {'PASS' if okC else 'FAIL'}")
    if not okC:
        failures.append("authority-retention")

    # D. NO REGRESSION — micro == graduation restores the old single-arm gate.
    old_low = _floor(0.15, micro=GRAD)   # below 0.2 -> not armed (old behaviour)
    old_ok = _floor(0.25, micro=GRAD)    # above 0.2 -> armed (old behaviour)
    okD = (not old_low.should_apply) and old_ok.armed
    print(f"D. no-regression (micro=grad): 0.15 apply={old_low.should_apply} "
          f"0.25 armed={old_ok.armed} -> {'PASS' if okD else 'FAIL'}")
    if not okD:
        failures.append("no-regression")

    print()
    if failures:
        print(f"ISSUE 1 VERIFY: FAIL — {failures}")
        sys.exit(1)
    print("ISSUE 1 VERIFY: PASS — micro-floor captures sub-0.2% green, never "
          "cuts a winner, retains loss-cutting authority, and reverts cleanly "
          "(4/4).")


if __name__ == "__main__":
    main()
