"""H1 self-verification — cut near-certain losers (struct-guard yields).

Drives the real TimeDecaySLCalculator.calculate() and asserts:

  - A near-certain loser (p_win <= near_certain_loser_p_win=0.10), past the
    min-age and MAE gates, with NO structural-invalidation evidence, is now
    FORCE-CLOSED (-1.0) — the struct-guard YIELDS instead of holding it.
  - The ambiguous band (0.10 < p_win < 0.15) is STILL HELD (None) — the guard's
    caution is preserved exactly where it is right.
  - A healthy position (p_win=0.50) is never force-closed by this path.
  - Disabling the carve-out (near_certain_loser_p_win=0.0) restores the
    block-on-low-p_win behaviour (None).

Run: .venv/bin/python verify_h1_cut_losers.py
"""

from __future__ import annotations

from pathlib import Path

from src.risk.time_decay_sl import TimeDecayConfig, TimeDecaySLCalculator

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name, ok, detail):
    results.append((name, PASS if ok else FAIL, detail))


def _state(calc, p_win):
    state = calc.create_state(
        symbol="H1TEST", direction="Buy", entry_price=100.0,
        original_sl_pct=2.0, max_hold_seconds=2700, atr_5m_pct=0.5,
        regime_confidence=0.6, tick_seconds=5.0,
        entry_xray_confidence=0.65, entry_setup_type="BULLISH_FVG_OB",
        entry_regime_at_open="trending_up", entry_regime_confidence=0.70,
    )
    state.p_win = p_win
    state.mae_pct = -1.1       # ratio 0.55 > 0.50 -> past the MAE gate
    state.last_pnl_pct = -1.1
    return state


def _run(calc, state):
    return calc.calculate(
        state, current_pnl_pct=-1.1, position_age_seconds=400,  # past min-age
        regime_still_supports=False,
        velocity_pct_per_s=-0.05, acceleration_pct_per_s2=-0.01,
        structural_invalidation=False,         # NO structural evidence
        invalidation_reason="stable",
    )


def main() -> int:
    calc = TimeDecaySLCalculator(TimeDecayConfig())

    # Case 1: near-certain loser (p_win clamps to 0.05 <= 0.10) -> force-close.
    out1 = _run(calc, _state(calc, 0.05))
    check("H1 near-certain loser is CUT (force-close)",
          out1 == -1.0, f"calculate -> {out1} (expect -1.0)")

    # Case 2: ambiguous band (0.14, regime supports keeps it >0.10) -> held.
    calc2 = TimeDecaySLCalculator(TimeDecayConfig())
    state2 = _state(calc2, 0.14)
    out2 = calc2.calculate(
        state2, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=True,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=False, invalidation_reason="stable",
    )
    check("H1 ambiguous band still HELD (guard preserved)",
          out2 is None, f"calculate -> {out2} (expect None)")

    # Case 3: healthy position -> never force-closed by this path.
    calc3 = TimeDecaySLCalculator(TimeDecayConfig())
    state3 = _state(calc3, 0.50)
    out3 = calc3.calculate(
        state3, current_pnl_pct=-1.1, position_age_seconds=400,
        regime_still_supports=True,
        velocity_pct_per_s=-0.01, acceleration_pct_per_s2=0.0,
        structural_invalidation=False, invalidation_reason="stable",
    )
    check("H1 healthy position not force-closed",
          out3 != -1.0, f"calculate -> {out3} (expect not -1.0)")

    # Case 4: carve-out disabled (0.0) -> low p_win is blocked again (None).
    calc4 = TimeDecaySLCalculator(
        TimeDecayConfig(near_certain_loser_p_win=0.0))
    out4 = _run(calc4, _state(calc4, 0.05))
    check("H1 carve-out disabled restores block (None)",
          out4 is None, f"calculate -> {out4} (expect None)")

    # Source/wiring checks.
    td_src = Path("src/risk/time_decay_sl.py").read_text()
    check("H1 STRUCT_GUARD_YIELD sentinel present",
          "TIME_DECAY_STRUCT_GUARD_YIELD" in td_src,
          "yield sentinel emitted on cut")
    check("H1 threshold wired in watchdog mapping",
          "near_certain_loser_p_win=float(getattr(" in
          Path("src/workers/position_watchdog.py").read_text(),
          "settings->config mapping present")
    check("H1 config.toml exposes the threshold",
          "near_certain_loser_p_win = 0.10" in Path("config.toml").read_text(),
          "[time_decay] near_certain_loser_p_win present")

    print("\nH1 CUT NEAR-CERTAIN LOSERS — SELF-VERIFICATION\n")
    n_pass = 0
    for name, status, detail in results:
        print(f"  [{status}] {name}")
        print(f"         {detail}")
        if status == PASS:
            n_pass += 1
    print(f"\n  {n_pass}/{len(results)} checks passed\n")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
