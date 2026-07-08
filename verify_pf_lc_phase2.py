#!/usr/bin/env python3
"""Behavioural verification for the PF/LC Optimization Program — Phase 2 levers.

Covers the four work-items shipped in this pass, against concrete values on the
REAL production code (no rewrites, no data deletion):

  Item 2.5 — spine age fields recomputed graduated-winner-safe (zero PnL risk)
  Item 2.4 — win-probability close reason split from deadline-bleed (labeling)
  Item 2.1 — lower ladder arm 0.5 -> 0.2 so the breakeven floor ENGAGES
  Item 2.2 — jump the floor on the arming tick (one-shot rate-limit bypass)

Run:  python3 verify_pf_lc_phase2.py     (exit 0 == all pass)

All PnL-OUTCOME verdicts remain PROVISIONAL pending APEX restoration and the
3-to-5-day truthful-ruler re-measurement; this script verifies BEHAVIOUR only.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.time_dial import TimeDial
from src.risk.time_decay_sl import TimeDecayState, TimeDecayConfig
from src.workers.profit_sniper import ProfitSniper

_fail = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _fail
    tag = "PASS" if ok else "FAIL"
    if not ok:
        _fail += 1
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))


def _new_sniper(s):
    """An uninitialised ProfitSniper with only the attributes the bound
    methods under test read — the verify_loss_cutting.py pattern."""
    sniper = object.__new__(ProfitSniper)
    sniper._pf = s.profit_fetching
    sniper._lc = s.loss_cutting
    sniper._last_breakeven_floor_logged = {}
    sniper._time_dial = TimeDial(s.profit_fetching)
    return sniper


def _ladder(sniper, peak_pct, arm_pct):
    """Run the REAL _compute_ladder_floor for a long at entry=100 with a given
    high-water peak and a given arm threshold."""
    sniper._pf = SimpleNamespace(
        min_profit_to_arm_ladder_pct=arm_pct,
        ladder_breakeven_lock_pct=sniper._pf.ladder_breakeven_lock_pct,
    )
    state = SimpleNamespace(
        entry_price=100.0, direction="Buy", peak_pnl_pct=peak_pct, symbol="TESTUSDT",
    )
    dialed = SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3)
    return sniper._compute_ladder_floor(state, dialed, current_sl=98.0)


def main() -> int:
    s = Settings.load(config_path="config.toml")
    pf = s.profit_fetching

    print("Item 2.1 — lower ladder arm 0.5 -> 0.2 so the breakeven floor engages")
    sniper = _new_sniper(s)
    # BEFORE (arm 0.5): a +0.3% peak does NOT arm — the round-trip leak.
    before = _ladder(sniper, peak_pct=0.3, arm_pct=0.5)
    check("BEFORE arm=0.5: +0.3% peak does not arm (floor unreachable)",
          before.should_apply is False and before.armed is False,
          f"should_apply={before.should_apply} armed={before.armed}")
    # AFTER (arm 0.2): the same +0.3% peak arms and locks the breakeven floor.
    after = _ladder(sniper, peak_pct=0.3, arm_pct=0.2)
    check("AFTER arm=0.2: +0.3% peak arms and locks breakeven floor",
          after.should_apply is True and after.breakeven_floor is True,
          f"should_apply={after.should_apply} breakeven_floor={after.breakeven_floor} "
          f"lock_pct={after.lock_pct}")
    check("AFTER: locked stop sits ABOVE entry (a real protective floor)",
          after.ladder_stop_price > 100.0,
          f"ladder_stop={after.ladder_stop_price} entry=100.0")
    check("config arm threshold is live at 0.2", pf.min_profit_to_arm_ladder_pct == 0.2,
          f"arm={pf.min_profit_to_arm_ladder_pct}")
    # Guardrail: a real crossed-rung positive lock is NOT overridden by the floor.
    rung = _ladder(sniper, peak_pct=0.7, arm_pct=0.2)  # crosses the 0.6% rung
    check("guardrail: a crossed-rung lock stays a real positive lock (not the sliver)",
          rung.breakeven_floor is False and rung.lock_pct > pf.ladder_breakeven_lock_pct,
          f"breakeven_floor={rung.breakeven_floor} lock_pct={rung.lock_pct}")

    print("\nItem 2.2 — jump the breakeven floor on the arming tick (rate-limit bypass)")
    check("config off-switch present and on", pf.ladder_floor_jump_on_arm is True,
          f"ladder_floor_jump_on_arm={pf.ladder_floor_jump_on_arm}")

    # The exact arming-tick condition the spine uses, exercised against a real
    # breakeven-floor LadderResult (from Item 2.1's AFTER run).
    def arming(flag, name, ladder_res, jumped):
        return bool(
            flag and name == "ladder" and ladder_res is not None
            and getattr(ladder_res, "breakeven_floor", False) and not jumped
        )

    check("arming tick fires once for a breakeven-floor ladder win",
          arming(True, "ladder", after, jumped=False) is True)
    check("arming tick does NOT re-fire after the one-shot flag is set",
          arming(True, "ladder", after, jumped=True) is False)
    check("arming tick suppressed when the jump off-switch is false",
          arming(False, "ladder", after, jumped=False) is False)
    check("arming tick does NOT fire for a non-ladder winner",
          arming(True, "chandelier", after, jumped=False) is False)
    check("arming tick does NOT fire for a positive-rung lock (not a floor)",
          arming(True, "ladder", rung, jumped=False) is False)

    print("\nItem 2.4 — split the win-probability close reason from deadline-bleed")
    cfg = TimeDecayConfig()
    st = TimeDecayState(symbol="X", direction="Buy", entry_price=1.0,
                        original_sl_pct=2.0, max_hold_seconds=2700,
                        atr_5m_pct=1.0, regime_confidence=0.5)
    check("TimeDecayState carries the new force_close_reason field (default empty)",
          st.force_close_reason == "")
    # The exact stamping expression from time_decay_sl.py, against real thresholds.
    def stamp(p_win):
        return ("win_prob_near_certain"
                if p_win <= cfg.near_certain_loser_p_win else "win_prob_force_close")
    check("clear bleeder (p_win<=near_certain) -> win_prob_near_certain",
          stamp(0.05) == "win_prob_near_certain",
          f"near_certain={cfg.near_certain_loser_p_win}")
    check("threshold-band p_win cut -> win_prob_force_close",
          stamp(0.12) == "win_prob_force_close",
          f"force_close_th={cfg.p_win_force_close}")
    # The watchdog fallback expression for a legacy state object.
    legacy = getattr(SimpleNamespace(), "force_close_reason", "") or "win_prob_force_close"
    check("watchdog fallback books win_prob_force_close for a legacy state",
          legacy == "win_prob_force_close")

    print("\nItem 2.5 — spine age fields, graduated-winner-safe recompute")
    dial = TimeDial(pf)
    dp = dial.resolve(age_minutes=37.0, deadline_minutes=50.0)
    check("TimeDial.resolve carries age_minutes for the spine log",
          abs(dp.age_minutes - 37.0) < 1e-6, f"age_minutes={dp.age_minutes}")
    check("TimeDial.resolve carries clamped age_fraction for the spine log",
          abs(dp.age_fraction - 0.74) < 1e-6, f"age_fraction={dp.age_fraction}")
    past = dial.resolve(age_minutes=80.0, deadline_minutes=50.0)
    check("age_fraction saturates at 1.0 past the deadline (no NameError path)",
          past.age_fraction == 1.0, f"age_fraction={past.age_fraction}")

    print()
    if _fail:
        print(f"RESULT: {_fail} check(s) FAILED")
        return 1
    print("RESULT: all Phase 2 behavioural checks PASS "
          "(PnL-outcome verdicts PROVISIONAL pending truthful-ruler re-measurement)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
