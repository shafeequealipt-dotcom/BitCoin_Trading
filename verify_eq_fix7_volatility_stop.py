"""Entry-Quality Fix 7 self-verification (2026-06-10).

The entry stop is widened to the coin's volatility-recommended distance (floored
at the reference so quiet coins keep the minimum, capped) and the position is cut
proportionally so the dollar risk AT the stop stays within the reference budget —
the per-trade margin cap untouched and absolute. This tests the pure scaling math
(compute_volatility_scaled_stop): volatile coins widen + shrink with dollar-risk
held constant, quiet coins are unchanged, the cap binds, the haircut is
tighten-only, and the config loads default-OFF. Never rewrites data.
"""

from __future__ import annotations

from src.config.settings import Settings
from src.workers.strategy_worker import compute_volatility_scaled_stop

REF = 1.5
CAP = 5.0


def _risk_at_stop(size_usd: float, stop_pct: float) -> float:
    # leverage cancels in the ratio; dollar-risk-at-stop ~ size * stop_fraction.
    return size_usd * stop_pct / 100.0


def test_volatile_coin_widens_and_holds_dollar_risk() -> None:
    # Buy at 100, brain stop at 1.5% (98.5), volatile coin recommends 2.4%.
    new_sl, new_size, target, final = compute_volatility_scaled_stop(
        sl=98.5, current_price=100.0, direction="Buy", size_usd=100.0,
        recommended_sl_pct=2.4, reference_stop_pct=REF, max_cap_pct=CAP,
    )
    assert abs(target - 2.4) < 1e-6, target
    assert abs(final - 2.4) < 1e-6, final
    assert new_sl < 98.5, f"stop should widen below 98.5, got {new_sl}"
    assert new_size < 100.0, "size must shrink on a wider stop"
    # Dollar-risk-at-stop preserved at the reference budget.
    assert abs(_risk_at_stop(new_size, final) - _risk_at_stop(100.0, REF)) < 1e-6
    print(f"PASS: volatile coin widens 1.5%->2.4%, size {100.0:.1f}->{new_size:.2f}, dollar-risk held.")


def test_quiet_coin_unchanged() -> None:
    # Quiet coin recommends 0.8% (< reference) and the placed stop is already 1.5%.
    new_sl, new_size, target, final = compute_volatility_scaled_stop(
        sl=98.5, current_price=100.0, direction="Buy", size_usd=100.0,
        recommended_sl_pct=0.8, reference_stop_pct=REF, max_cap_pct=CAP,
    )
    assert abs(target - REF) < 1e-6, "target floored at reference for a quiet coin"
    assert new_sl == 98.5, "quiet coin stop must not move"
    assert abs(new_size - 100.0) < 1e-9, "quiet coin size must not change"
    print("PASS: quiet coin (recommended < reference) is unchanged — never tightened below the minimum.")


def test_cap_binds() -> None:
    new_sl, new_size, target, final = compute_volatility_scaled_stop(
        sl=98.5, current_price=100.0, direction="Buy", size_usd=100.0,
        recommended_sl_pct=8.0, reference_stop_pct=REF, max_cap_pct=CAP,
    )
    assert abs(target - CAP) < 1e-6, f"target must be capped at {CAP}, got {target}"
    assert abs(final - CAP) < 1e-6
    assert abs(_risk_at_stop(new_size, final) - _risk_at_stop(100.0, REF)) < 1e-6
    print(f"PASS: cap binds (recommended 8% -> {CAP}%), dollar-risk still held at reference.")


def test_sell_side_widens_upward() -> None:
    # Sell at 100, brain stop at 1.5% above (101.5), recommends 3%.
    new_sl, new_size, target, final = compute_volatility_scaled_stop(
        sl=101.5, current_price=100.0, direction="Sell", size_usd=100.0,
        recommended_sl_pct=3.0, reference_stop_pct=REF, max_cap_pct=CAP,
    )
    assert new_sl > 101.5, "sell stop must widen ABOVE entry"
    assert abs(final - 3.0) < 1e-6
    assert new_size < 100.0
    print(f"PASS: sell-side stop widens upward to 3% ({new_sl}), size cut proportionally.")


def test_haircut_is_tighten_only_and_brain_wide_stop_bounded() -> None:
    # Brain set a 3% stop wider than the 2.4% vol target: no widening, but the
    # haircut still bounds dollar risk (3% > reference) -> size cut.
    new_sl, new_size, target, final = compute_volatility_scaled_stop(
        sl=97.0, current_price=100.0, direction="Buy", size_usd=100.0,
        recommended_sl_pct=2.4, reference_stop_pct=REF, max_cap_pct=CAP,
    )
    assert new_sl == 97.0, "a brain stop wider than the target is NOT moved (one-directional widen)"
    assert new_size <= 100.0, "haircut is tighten-only"
    assert abs(_risk_at_stop(new_size, final) - _risk_at_stop(100.0, REF)) < 1e-6
    print("PASS: brain-chosen wide stop passes through; size haircut keeps dollar risk bounded (tighten-only).")


def test_no_profiler_input_is_noop() -> None:
    new_sl, new_size, target, final = compute_volatility_scaled_stop(
        sl=98.5, current_price=100.0, direction="Buy", size_usd=100.0,
        recommended_sl_pct=0.0, reference_stop_pct=REF, max_cap_pct=CAP,
    )
    assert new_sl == 98.5 and abs(new_size - 100.0) < 1e-9
    print("PASS: no profiler input -> target=reference, no change (safe degradation).")


def test_config_loads_enabled() -> None:
    """Shipped OFF 2026-06-10; ENABLED later that day (Five-Fix Follow-Up
    Fix 3) after the losing-window replay passed
    (verify_fix3_losing_window_stop_replay.py: 4 of 7 noise-deaths survive,
    zero budget violations). This assertion tracks the decided live state."""
    s = Settings.load()
    vss = s.risk.volatility_stop_scaling
    assert vss.enabled is True, "operator-approved enabled state (Fix 3)"
    assert vss.reference_stop_pct == 1.5
    assert vss.max_cap_pct == 5.0
    assert vss.use_profiler_recommended_sl is True
    print("PASS: [risk.volatility_stop_scaling] loads ENABLED (ref=1.5, cap=5.0, use_profiler=True).")


def main() -> None:
    print("=== Entry-Quality Fix 7 — volatility-scaled stop verification ===")
    test_volatile_coin_widens_and_holds_dollar_risk()
    test_quiet_coin_unchanged()
    test_cap_binds()
    test_sell_side_widens_upward()
    test_haircut_is_tighten_only_and_brain_wide_stop_bounded()
    test_no_profiler_input_is_noop()
    test_config_loads_enabled()
    print("\nALL FIX-7 CHECKS PASSED.")


if __name__ == "__main__":
    main()
