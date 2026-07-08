"""Self-verification for E28 — single-strategy dominance cap 1.0 -> 0.4.

The cap (single_strategy_max_share) was already wired in ensemble.py but set to
1.0 (disabled). E28 sets it to 0.4 so a STRONG consensus requires breadth (no
single strategy may supply >40% of the agreeing side), and adds a binding
sentinel. The cap FORMULA is unchanged (covered by test_ensemble_single_strategy_cap.py).

Confirms:
  A. STATIC: config value is 0.4; ENSEMBLE_DOMINANCE_CAP_BOUND sentinel is wired
     in BOTH the live and the shadow (regime-weighted) contribution paths.
  B. LIVE CONFIG: Settings._load_fresh().strategy_engine.single_strategy_max_share == 0.4.
  C. BEHAVIOR (formula, tied to the live STRONG floor): a single-dominant side is
     clamped below the STRONG floor (downgraded), while a balanced multi-voter
     STRONG stays above the floor (preserved — frequency not culled).

Read-only / in-memory.
"""


def _capped(contribs, cap_share):
    """The exact cap formula ensemble.py applies (sum of clamped contributions)."""
    if not contribs or cap_share >= 1.0:
        return sum(contribs)
    total = sum(contribs)
    out = 0.0
    for c in contribs:
        rest = total - c
        ceiling = rest * cap_share / max(1.0 - cap_share, 1e-9)
        out += min(c, ceiling)
    return out


def static_check():
    cfgtxt = open("config.toml").read()
    en = open("src/strategies/ensemble.py").read()
    return {
        "config single_strategy_max_share = 0.4": "single_strategy_max_share = 0.4" in cfgtxt,
        "sentinel wired (live path)": "ENSEMBLE_DOMINANCE_CAP_BOUND | path=live" in en,
        "sentinel wired (shadow path)": "ENSEMBLE_DOMINANCE_CAP_BOUND | path=shadow" in en,
    }


def main():
    from src.config.settings import Settings

    s = static_check()

    se = Settings._load_fresh().strategy_engine
    live_ok = float(getattr(se, "single_strategy_max_share", 1.0)) == 0.4
    strong_floor = float(getattr(se, "min_ensemble_agreement_strong", 4.0))

    # Single-dominant: one voter would alone clear STRONG; cap must downgrade it.
    dominant = [9.0, 0.2, 0.2]   # uncapped sum 9.4 (STRONG); one voter dominates
    dom_capped = _capped(dominant, 0.4)
    downgraded = dom_capped < strong_floor

    # Balanced: 5 voters ~0.9 each = 4.5 (STRONG); cap must NOT bind.
    balanced = [0.9, 0.9, 0.9, 0.9, 0.9]
    bal_capped = _capped(balanced, 0.4)
    preserved = bal_capped >= strong_floor and abs(bal_capped - sum(balanced)) < 1e-9

    print("E28 VERIFICATION — single-strategy dominance cap 1.0 -> 0.4 (completes #19)")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  LIVE CONFIG single_strategy_max_share == 0.4: {live_ok}")
    print(f"  STRONG floor = {strong_floor}")
    print(f"  single-dominant {dominant} -> capped {dom_capped:.3f} < floor -> DOWNGRADED: {downgraded}")
    print(f"  balanced {balanced} -> capped {bal_capped:.3f} (uncapped {sum(balanced)}) -> PRESERVED: {preserved}")

    ok = all(s.values()) and live_ok and downgraded and preserved
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
