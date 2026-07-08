"""H3 self-verification — respect the brain's weak-setup size; skip below the
real exchange minimum (no arbitrary up-floor).

Drives the real APEX sizing (TradeOptimizer._apply_constraints) and asserts a
small conviction-scaled size is PRESERVED, not floored to $100. Also source-
checks that the strategy_worker and gate arbitrary floors are removed and that
the real exchange-minimum check still SKIPS (qty<=0 -> qty_zero).

Run: .venv/bin/python verify_h3_size.py
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.apex.models import OptimizedTrade
from src.apex.optimizer import TradeOptimizer

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name, ok, detail):
    results.append((name, PASS if ok else FAIL, detail))


def _settings():
    return SimpleNamespace(
        max_position_size_usd=1200.0,
        apex_size_cap_pct_of_equity=0.0,
        apex_size_conviction_floor=0.5,
        max_leverage=3,
        min_tp_pct=0.3,
    )


def _trade(size, conf):
    return OptimizedTrade(
        symbol="ALGOUSDT", direction="Buy", sl_pct=1.0, tp_pct=2.0,
        tp_mode="fixed", position_size_usd=size, leverage=2,
        entry_timing="immediate", add_on_pullback=False, confidence=conf,
    )


def main() -> int:
    opt = TradeOptimizer(qwen_client=None, assembler=None, settings=_settings())

    # Case A: tiny pre_cap, full conviction -> preserved at 50 (was floored 100).
    tA = _trade(50.0, 1.0)
    opt._apply_constraints(tA)
    check("H3 APEX: tiny size preserved, not floored to $100",
          abs(tA.position_size_usd - 50.0) < 1e-6,
          f"final={tA.position_size_usd} (expect 50.0)")

    # Case B: ALGO-like weak setup -> 40 * max(0.5, 0.3) = 20, preserved.
    tB = _trade(40.0, 0.3)
    opt._apply_constraints(tB)
    check("H3 APEX: weak low-conviction size stands (~$20)",
          abs(tB.position_size_usd - 20.0) < 1e-6,
          f"final={tB.position_size_usd} (expect 20.0)")

    # Case C: a normal size is unchanged by the floor removal.
    tC = _trade(800.0, 1.0)
    opt._apply_constraints(tC)
    check("H3 APEX: normal size unaffected",
          abs(tC.position_size_usd - 800.0) < 1e-6,
          f"final={tC.position_size_usd} (expect 800.0)")

    # Source checks — arbitrary floors removed at all sites.
    sw = Path("src/workers/strategy_worker.py").read_text()
    opt_src = Path("src/apex/optimizer.py").read_text()
    gate_src = Path("src/apex/gate.py").read_text()

    check("H3 strategy_worker $100 floor removed (2596)",
          "max(min(size_usd, max_size), 100)" not in sw
          and "size_usd = min(max(size_usd, 0.0), max_size)" in sw,
          "primary $100 floor replaced by cap+nonneg guard")
    check("H3 strategy_worker enforcer $100 floor removed (2612)",
          "max(round(size_usd * sz_mult, 2), 100)" not in sw
          and "size_usd = round(size_usd * sz_mult, 2)" in sw,
          "enforcer-path $100 floor removed")
    check("H3 APEX $100 floor removed (1048)",
          "max(100.0, _scaled)" not in opt_src
          and "trade.position_size_usd = round(_scaled, 2)" in opt_src,
          "APEX $100 floor removed; conviction-scaled size stands")
    check("H3 gate $50 floor removed (CHECK 7)",
          "trade[\"size_usd\"] = min_size" not in gate_src
          and "respect the brain's risk read (H3)" in gate_src,
          "gate $50 floor removed")

    # The real exchange-minimum SKIP still exists (skip, not floor).
    check("H3 exchange-minimum check still SKIPS (qty_zero)",
          'return (False, "qty_zero")' in sw
          and "min_size_usd = step * current_price" in sw,
          "below-exchange-minimum sizes are skipped downstream, not oversized")

    print("\nH3 WEAK-SETUP SIZE — SELF-VERIFICATION\n")
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
