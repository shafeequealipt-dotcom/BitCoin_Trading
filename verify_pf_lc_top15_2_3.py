#!/usr/bin/env python3
"""PF/LC Top-15 Problem 2.3 — age-aware win-probability band (offline check).

Drives the real TimeDecaySLCalculator.calculate() to prove that, when enabled,
the near-certain-loser carve-out cuts an AGED loser sitting in the (young, old]
p_win band (with stable structure) while still HOLDING a young ambiguous trade —
the blueprint's "win-prob cut tightens with age". With the switch off, behaviour
is unchanged (the whole band is held). Dollar verdict provisional until 1.3 runs.

Run: python3 verify_pf_lc_top15_2_3.py
"""
import sys

from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator


def _state(calc):
    s = calc.create_state(
        symbol="ENAUSDT", direction="Buy", entry_price=100.0,
        original_sl_pct=2.0, max_hold_seconds=2700, atr_5m_pct=0.5,
        regime_confidence=0.6, tick_seconds=5.0,
        entry_xray_confidence=0.65, entry_setup_type="BULLISH_FVG_OB",
        entry_regime_at_open="trending_up", entry_regime_confidence=0.70,
    )
    # calculate() runs the per-tick p_win update before the carve-out, so set
    # the inputs so it lands deterministically inside the band: flat tick (no
    # price penalty), no MAE recovery bonus, regime supports (x1.05). 0.12 x
    # 1.05 = 0.126 — between the young (0.10) and old (0.13) thresholds.
    s.p_win = 0.12
    s.mae_pct = -1.1        # pass the Phase-2 MAE-to-SL gate; recovery = 0
    s.last_pnl_pct = -1.1
    s.prev_pnl_pct = -1.1   # flat vs current → no "deeper this tick" penalty
    return s


def _decide(cfg, age_s):
    calc = TimeDecaySLCalculator(cfg)
    s = _state(calc)
    return calc.calculate(
        s, current_pnl_pct=-1.1, position_age_seconds=age_s,
        regime_still_supports=True,           # x1.05 → p_win lands at 0.126
        velocity_pct_per_s=0.0, acceleration_pct_per_s2=0.0,
        structural_invalidation=False,        # stable structure (no real signal)
        invalidation_reason="stable",
    )


def _run():
    fails = []

    # ── OFF (default): the (0.10, 0.15] stable band is HELD at any age (None). ──
    off = TimeDecayConfig()
    if _decide(off, age_s=700) is not None:
        fails.append("off: aged band trade should be HELD (None) — current behaviour")
    if _decide(off, age_s=400) is not None:
        fails.append("off: young band trade should be HELD (None)")

    # ── ON: aged trade (>=600s) is CUT (-1.0); young trade (<600s) still HELD. ──
    on = TimeDecayConfig(
        winprob_age_aware_band_enabled=True,
        near_certain_loser_p_win_young=0.10,
        near_certain_loser_p_win_old=0.13,
        age_threshold_to_raise_p_win_seconds=600.0,
    )
    aged = _decide(on, age_s=700)
    if aged != -1.0:
        fails.append(f"on: aged band loser (p_win 0.12) should be CUT (-1.0), got {aged}")
    young = _decide(on, age_s=400)
    if young is not None:
        fails.append(f"on: young band trade should be HELD (None), got {young}")

    return fails


if __name__ == "__main__":
    fails = _run()
    if fails:
        print("FAIL — PF/LC 2.3 age-aware band verification:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("PASS — PF/LC 2.3: with the switch off the stable (0.10,0.15] band is held "
          "at any age (unchanged); with it on an aged near-certain loser is cut while "
          "a young ambiguous trade is still held. Dollar verdict provisional until 1.3.")
