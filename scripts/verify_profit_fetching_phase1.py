"""Phase 1 behavioral self-verification — Profit-Fetching Exit System.

Confirms, without touching any live wiring:
  1. config.toml [profit_fetching] parses into ProfitFetchingSettings (the
     boot-sentinel content the sniper will log in Phase 2).
  2. The TimeDial glides every parameter smoothly from its young anchor toward
     its old anchor as a trade ages, and saturates past the deadline.

Run from the project root:  python scripts/verify_profit_fetching_phase1.py
Exit code 0 = all checks pass.
"""

from __future__ import annotations

import sys

from src.config.settings import Settings
from src.core.time_dial import TimeDial


def main() -> int:
    settings = Settings.load(config_path="config.toml")
    pf = settings.profit_fetching

    # ── 1. Config-loaded sentinel (mirrors the Phase 2 live boot log) ──
    sentinel = (
        f"PROFIT_FETCHING_CONFIG_LOADED | enabled={pf.enabled} "
        f"atr_young={pf.atr_multiple_young} atr_old={pf.atr_multiple_old} "
        f"step_young={pf.ladder_step_pct_young} step_old={pf.ladder_step_pct_old} "
        f"lock_young={pf.lock_offset_pct_young} lock_old={pf.lock_offset_pct_old} "
        f"arm_pct={pf.min_profit_to_arm_ladder_pct} "
        f"safety_pct={pf.safety_stop_pct} "
        f"atr_zero_fallback_pct={pf.atr_zero_fallback_pct} "
        f"peak_min={pf.peak_minutes}"
    )
    print(sentinel)

    assert pf.enabled is True, "operator gate: enabled must default true"
    assert pf.atr_multiple_young > pf.atr_multiple_old, "ATR must tighten with age"
    assert pf.ladder_step_pct_young >= pf.ladder_step_pct_old, "step must not widen with age"
    assert pf.lock_offset_pct_young >= pf.lock_offset_pct_old, "lock must not loosen with age"

    # ── 2. Smooth glide across ages on a typical 50-minute deadline ──
    dial = TimeDial(pf)
    deadline = 50.0
    ages = [0.0, 11.0, 22.0, 40.0, 50.0, 75.0]
    print("\nTime-dial output (deadline = 50 min):")
    print("age_min  frac   atr_mult  step_pct  lock_pct")
    prev = None
    for age in ages:
        d = dial.resolve(age, deadline)
        print(
            f"{age:6.1f}  {d.age_fraction:0.3f}  {d.atr_multiple:7.3f}  "
            f"{d.ladder_step_pct:7.3f}  {d.lock_offset_pct:7.3f}"
        )
        # Monotonic tightening: each value must not increase as age increases.
        if prev is not None:
            assert d.atr_multiple <= prev.atr_multiple + 1e-9, "ATR multiple must not widen with age"
            assert d.ladder_step_pct <= prev.ladder_step_pct + 1e-9, "step must not widen with age"
            assert d.lock_offset_pct <= prev.lock_offset_pct + 1e-9, "lock must not loosen with age"
        prev = d

    # Young end sits at the young anchors; deadline and beyond sit at the old anchors.
    young = dial.resolve(0.0, deadline)
    at_deadline = dial.resolve(deadline, deadline)
    past = dial.resolve(deadline * 1.5, deadline)
    assert abs(young.atr_multiple - pf.atr_multiple_young) < 1e-9, "age 0 must equal young anchor"
    assert abs(at_deadline.atr_multiple - pf.atr_multiple_old) < 1e-9, "deadline must equal old anchor"
    assert past.age_fraction == 1.0, "past-deadline fraction must saturate at 1.0"
    assert abs(past.atr_multiple - pf.atr_multiple_old) < 1e-9, "past-deadline stays at tightest"

    # Safety stop is a constant pass-through, never dialed.
    assert young.safety_stop_pct == pf.safety_stop_pct == past.safety_stop_pct

    # Zero/negative deadline must not divide-by-zero; fraction saturates safely.
    edge = dial.resolve(5.0, 0.0)
    assert edge.age_fraction == 1.0, "zero deadline must clamp fraction to 1.0"

    print("\nPHASE_1_SELF_VERIFY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
