"""Phase 2 behavioral self-verification — hardened trail (techniques 2 + 4).

Confirms, without running a live tick:
  1. The ATR-zero fallback chain never returns zero: live -> entry-ATR ->
     percent-of-price floor, with the correct source label each time.
  2. The time dial feeds a tightening ATR multiple as a trade ages, so the
     trail width shrinks with age.
  3. The age/deadline helper falls back safely when there is no TradePlan.

Run from the project root:  PYTHONPATH=. python scripts/verify_profit_fetching_phase2.py
Exit code 0 = all checks pass.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.workers.profit_sniper import ProfitSniper


def main() -> int:
    settings = Settings.load(config_path="config.toml")
    # Construct with a stub db; no live services needed for these pure helpers.
    sniper = ProfitSniper(settings=settings, db=SimpleNamespace())
    pf = settings.profit_fetching

    # ── 1. ATR-zero fallback chain ──
    live_atr, src = sniper._pf_effective_atr(2.0, 1.0, 100.0)
    assert (live_atr, src) == (2.0, "live"), (live_atr, src)

    entry_atr, src = sniper._pf_effective_atr(0.0, 1.5, 100.0)
    assert (entry_atr, src) == (1.5, "entry_atr"), (entry_atr, src)

    floor_atr, src = sniper._pf_effective_atr(0.0, 0.0, 100.0)
    expected_floor = 100.0 * (pf.atr_zero_fallback_pct / 100.0)
    assert src == "pct_floor" and abs(floor_atr - expected_floor) < 1e-9, (floor_atr, src)
    assert floor_atr > 0.0, "fallback must never be zero — the hole this fixes"
    print(
        f"ATR fallback OK: live=2.0->live | 0,1.5->entry_atr | "
        f"0,0->pct_floor={floor_atr} (={pf.atr_zero_fallback_pct}% of price)"
    )

    # ── 2. Time-dialed ATR multiple tightens with age ──
    d_young = sniper._time_dial.resolve(0.0, 50.0)
    d_old = sniper._time_dial.resolve(50.0, 50.0)
    assert d_young.atr_multiple == pf.atr_multiple_young
    assert d_old.atr_multiple == pf.atr_multiple_old
    assert d_old.atr_multiple < d_young.atr_multiple, "trail must tighten with age"
    print(
        f"Dial OK: atr_multiple young={d_young.atr_multiple} -> old={d_old.atr_multiple}"
    )

    # ── 3. Age/deadline fallback with no coordinator/plan ──
    age, deadline = sniper._pf_age_and_deadline("NOSUCHUSDT")
    assert deadline == pf.default_deadline_minutes, deadline
    assert age >= 0.0
    print(f"Age/deadline fallback OK: age={age:.2f} deadline={deadline}")

    print("\nPHASE_2_SELF_VERIFY: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
