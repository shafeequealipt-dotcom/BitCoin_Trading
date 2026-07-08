#!/usr/bin/env python3
"""Live-situation simulation for the PF/LC Optimization Program — Phase 2 levers.

Reproduces the ACTUAL failure situations from the live monitoring (the same data
shapes that motivated each fix) and drives the REAL production methods to confirm
each fix responds as intended -- BEFORE (old behaviour, via the off-switch) vs
AFTER (the shipped fix). The same pattern as simulate_pf_lc_findings.py: real
code, minimal stubs, no data is deleted or rewritten.

  Scenario A — Item 2.1 + 2.2: the modest-peak fader (HBAR +0.59% / ENA +0.24% /
               DYDX): climbs to a modest peak, never reaches the old 0.5% arm,
               fades. BEFORE: no floor -> round-trips to a loss. AFTER: arms at
               0.2%, the breakeven floor locks above entry and the fade is caught
               at ~breakeven (a small win). Plus the arming-tick jump fires once.
  Scenario B — Item 2.4: the FIL near-certain-loser cut (p_win 0.098). Drive the
               REAL TimeDecaySLCalculator to a force-close and confirm it now
               books the TRUTHFUL win-probability reason, split from deadline-bleed.
  Scenario C — Item 2.5: a graduated winner — confirm the spine age fields compute
               with NO NameError (the verbatim _age_min path would have crashed).

Run:  python3 simulate_pf_lc_phase2.py     (exit 0 == every fix responds as wanted)
"""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace

from src.config.settings import Settings
from src.core.time_dial import TimeDial
from src.risk.time_decay_sl import (
    TimeDecaySLCalculator, TimeDecayConfig, observe,
)
from src.workers.profit_sniper import ProfitSniper

_fail = 0


def verdict(name: str, fixed: bool, before: str, after: str) -> None:
    global _fail
    if not fixed:
        _fail += 1
    tag = "RESPONDS AS FIXED" if fixed else "NOT FIXED"
    print(f"\n  >>> {name}: {tag}")
    print(f"      BEFORE: {before}")
    print(f"      AFTER : {after}")


def _ladder_sniper(s):
    sn = object.__new__(ProfitSniper)
    sn._pf = s.profit_fetching
    sn._last_breakeven_floor_logged = {}
    return sn


def _run_fader(sn, arm_pct, be_lock):
    """Drive the REAL _compute_ladder_floor over a modest-peak fader price path
    (a long at 100). Returns (armed_ever, breakeven_floor_ever, final_stop,
    arming_ticks). current_sl starts at the 3xATR-ish initial stop (98 = -2%)."""
    sn._pf = SimpleNamespace(min_profit_to_arm_ladder_pct=arm_pct,
                             ladder_breakeven_lock_pct=be_lock)
    dialed = SimpleNamespace(ladder_step_pct=0.6, lock_offset_pct=0.3)
    # pnl% path: climb to a +0.35% peak (below the old 0.5% arm), then fade
    # through entry and into the red — the HBAR/ENA round-trip shape.
    path = [0.0, 0.12, 0.22, 0.30, 0.35, 0.22, 0.08, -0.05, -0.20, -0.35]
    peak = 0.0
    current_sl = 98.0          # initial 3xATR-ish stop, ~ -2%
    armed_ever = floor_ever = False
    arming_ticks = 0
    be_jumped = False          # the one-shot _be_floor_jumped tracked flag
    for pnl in path:
        peak = max(peak, pnl)
        state = SimpleNamespace(entry_price=100.0, direction="Buy",
                                peak_pnl_pct=peak, symbol="HBARUSDT")
        r = sn._compute_ladder_floor(state, dialed, current_sl)
        armed_ever = armed_ever or bool(r.armed)
        floor_ever = floor_ever or bool(getattr(r, "breakeven_floor", False))
        # The exact arming-tick condition the live spine uses (Item 2.2).
        if (sn._pf.ladder_breakeven_lock_pct > 0 and getattr(r, "breakeven_floor", False)
                and r.should_apply and not be_jumped):
            arming_ticks += 1
            be_jumped = True
        if r.should_apply and r.ladder_stop_price > current_sl:
            current_sl = r.ladder_stop_price   # tighten-only ratchet
    return armed_ever, floor_ever, current_sl, arming_ticks


def scenario_a(s):
    print("Scenario A — Item 2.1 + 2.2: the modest-peak fader (HBAR/ENA/DYDX shape)")
    sn = _ladder_sniper(s)
    be = s.profit_fetching.ladder_breakeven_lock_pct
    # BEFORE: old 0.5% arm (Finding-era behaviour).
    b_armed, b_floor, b_stop, _ = _run_fader(sn, arm_pct=0.5, be_lock=be)
    # AFTER: shipped 0.2% arm + the breakeven floor.
    a_armed, a_floor, a_stop, a_jumps = _run_fader(sn, arm_pct=0.2, be_lock=be)

    fixed = (
        (not b_floor) and b_stop <= 98.0 + 1e-9          # before: never protected
        and a_armed and a_floor and a_stop > 100.0       # after: locked above entry
        and a_jumps == 1                                 # jump fires exactly once
    )
    verdict(
        "Item 2.1+2.2 floor fix on a modest-peak fader", fixed,
        before=(f"+0.35% peak never armed (arm 0.5%); stop stayed at "
                f"{b_stop:.2f} (entry 100) -> the trade round-trips into a loss"),
        after=(f"+0.30% peak armed; breakeven floor locked the stop at "
               f"{a_stop:.2f} (> entry 100) and the arming-tick jump fired "
               f"{a_jumps}x -> the fade is caught at ~breakeven (a small win)"),
    )


def _new_calc_state():
    calc = TimeDecaySLCalculator()
    st = calc.create_state(symbol="FILUSDT", direction="Buy", entry_price=100.0,
                           original_sl_pct=2.0, max_hold_seconds=2700,
                           atr_5m_pct=0.5, regime_confidence=0.4)
    return calc, st


def _drive_near_certain():
    """Drive the REAL calculator ORGANICALLY to a near-certain-loser force-close:
    a deepening loss with the regime reversed compounds the p_win penalties
    (price >2 ATR ×0.70, regime reversed ×0.60) below near_certain_loser_p_win —
    the real FIL p_win~0.098 shape. Returns (outcome, reason, p_win)."""
    calc, st = _new_calc_state()
    outcome, pnl = None, -0.2
    for _ in range(8):
        pnl -= 0.45                       # deepen the loss each tick (>2 ATR)
        vel, accel = observe(st, pnl)
        outcome = calc.calculate(
            st, current_pnl_pct=pnl, position_age_seconds=600.0,
            regime_still_supports=False, velocity_pct_per_s=vel,
            acceleration_pct_per_s2=accel, structural_invalidation=False,
            invalidation_reason="stable",
        )
        if outcome == -1.0:
            break
    return outcome, getattr(st, "force_close_reason", ""), st.p_win


def _drive_threshold_band():
    """Drive the REAL calculator to a threshold-band cut (near_certain < p_win <
    p_win_force_close): a trade already decayed to ~0.12 that does NOT deepen
    further (flat tick, regime still nominally supporting, so no extra penalty)
    BUT carries real structural-invalidation evidence — so the structural guard
    yields and the force-close fires in the 0.10-0.15 band. Returns
    (outcome, reason, p_win)."""
    calc, st = _new_calc_state()
    st.p_win = 0.12                       # a trade already decayed into the band
    # A genuinely drawn-down trade (mae ~ -1.8% vs the 2.0% original SL) so the
    # MAE/SL-ratio gate is satisfied (a shallow loser is correctly held), but a
    # FLAT tick this round so no extra p_win price-penalty fires and it stays in
    # the (near_certain, force_close) band.
    st.last_pnl_pct = st.prev_pnl_pct = -1.8
    st.mae_pct = -1.8
    vel, accel = observe(st, -1.8)        # flat: velocity ~ 0, not deepening
    outcome = calc.calculate(
        st, current_pnl_pct=-1.8, position_age_seconds=900.0,
        regime_still_supports=True, velocity_pct_per_s=vel,
        acceleration_pct_per_s2=accel, structural_invalidation=True,
        invalidation_reason="regime_inv:trending_down@0.62",
    )
    return outcome, getattr(st, "force_close_reason", ""), st.p_win


def scenario_b(s):
    print("\nScenario B — Item 2.4: the FIL near-certain-loser cut (close-reason split)")
    # Organic deepening with the regime reversed drives p_win below the
    # near-certain band (the real FIL p_win~0.098 situation).
    out1, reason1, pw1 = _drive_near_certain()
    # A threshold-band cut (0.10 < p_win < 0.15) WITH structural invalidation.
    out2, reason2, pw2 = _drive_threshold_band()

    # Watchdog booking expression (what lands in trade_log.close_reason).
    booked1 = reason1 or "win_prob_force_close"
    booked2 = reason2 or "win_prob_force_close"

    fixed = (
        out1 == -1.0 and reason1 == "win_prob_near_certain"
        and out2 == -1.0 and reason2 == "win_prob_force_close"
    )
    verdict(
        "Item 2.4 win-probability close-reason split", fixed,
        before=("a p_win cut was booked as 'time_decay_force_close' -> looked "
                "like a deadline close; near-certain-loser cuts and deadline-bleed "
                "were conflated in the leak attribution (Finding 13)"),
        after=(f"near-certain cut (p_win={pw1:.3f}) booked as '{booked1}'; "
               f"threshold-band cut (p_win={pw2:.3f}) booked as '{booked2}' "
               f"-> the two mechanisms are now distinct in trade_log"),
    )


def scenario_c(s):
    print("\nScenario C — Item 2.5: spine age fields on a graduated winner (no NameError)")
    sn = object.__new__(ProfitSniper)
    sn._pf = s.profit_fetching
    sn._time_dial = TimeDial(s.profit_fetching)
    # A graduated winner WITH a brain TradePlan (the case the verbatim _age_min
    # would NameError on, because that variable is bound only on the loss path).
    plan = SimpleNamespace(age_minutes=42.0, max_hold_minutes=50.0)
    sn.trade_coordinator = SimpleNamespace(get_trade_plan=lambda sym: plan)
    sn._tracked = {}
    raised = None
    try:
        age, deadline = sn._pf_age_and_deadline("BTCUSDT")     # the REAL method
        dial = sn._time_dial.resolve(age, deadline)
        graduated_fields = (dial.age_minutes, dial.age_fraction)
    except Exception as e:                                     # the old hazard
        raised = repr(e)
        graduated_fields = None
    # Also the externally-opened (no-plan) path must work.
    sn.trade_coordinator = SimpleNamespace(get_trade_plan=lambda sym: None)
    sn._tracked = {"ETHUSDT": {"first_seen_at": time.time() - 600.0}}
    age2, deadline2 = sn._pf_age_and_deadline("ETHUSDT")
    noplan_ok = age2 > 0 and deadline2 == s.profit_fetching.default_deadline_minutes

    fixed = raised is None and graduated_fields is not None and noplan_ok
    verdict(
        "Item 2.5 graduated-winner-safe age fields", fixed,
        before=("using the verbatim internal _age_min in the spine log would "
                "NameError on every graduated winner (it is unbound on the "
                "profit-authority path)"),
        after=(f"graduated winner age fields = age_min={graduated_fields[0]:.1f} "
               f"age_frac={graduated_fields[1]:.3f}; no-plan path age_min={age2/60.0:.1f}m "
               f"deadline={deadline2:.0f} -> computed safely, no exception"
               if fixed else f"EXCEPTION: {raised}"),
    )


def main() -> int:
    s = Settings.load(config_path="config.toml")
    print("PF/LC Phase 2 — live-situation simulation (real code, real failure shapes)\n"
          "============================================================================")
    scenario_a(s)
    scenario_b(s)
    scenario_c(s)
    print("\n============================================================================")
    if _fail:
        print(f"RESULT: {_fail} scenario(s) did NOT respond as fixed")
        return 1
    print("RESULT: every Phase 2 fix responds as intended on its real failure situation\n"
          "        (PnL-outcome magnitude PROVISIONAL pending truthful-ruler re-measurement)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
