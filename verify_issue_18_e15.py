"""Self-verification for Issue #18 + E15 — inverted ensemble-tier defaults.

Confirms:
  A. STATIC: corrected defaults + loader fallbacks + cache defaults + the
     boot self-check now auto-corrects.
  B. CONFIG-LESS LADDER: StrategyEngineSettings() (no config) now has STRONG
     legitimately stricter than GOOD (agree 4.0 > 2.5; opp 1.5 < 2.5).
  C. LIVE UNCHANGED: Settings._load_fresh().strategy_engine (config.toml
     overrides) still yields GOOD 2.5/2.5, STRONG 4.0/1.5 — byte-identical to
     pre-fix runtime (config.toml untouched).
  D. AUTO-CORRECT: a deliberately re-inverted config, fed to EnsembleVoter,
     gets STRONG clamped to be at least as strict as GOOD at boot.

Read-only / in-memory; no writes.
"""


def static_check():
    s = open("src/config/settings.py").read()
    e = open("src/strategies/ensemble.py").read()
    return {
        "GOOD default corrected (2.5/2.5)": "min_ensemble_agreement: float = 2.5" in s
        and "max_ensemble_opposition: float = 2.5" in s,
        "STRONG default intact (4.0/1.5)": "min_ensemble_agreement_strong: float = 4.0" in s
        and "max_ensemble_opposition_strong: float = 1.5" in s,
        "loader fallback corrected": 'data.get("min_ensemble_agreement", 2.5)' in s
        and 'data.get("max_ensemble_opposition", 2.5)' in s,
        "cache default corrected": "self._good_agree: float = 2.5" in e
        and "self._good_opp: float = 2.5" in e,
        "boot self-check auto-corrects": "BOOT_ENSEMBLE_THRESHOLDS_AUTOCORRECTED" in e
        and "_strong_agree = max(_strong_agree, _good_agree)" in e,
    }


def main():
    from src.config.settings import StrategyEngineSettings, Settings

    s = static_check()

    # B. config-less ladder
    d = StrategyEngineSettings()
    ladder_ok = (
        d.min_ensemble_agreement_strong > d.min_ensemble_agreement
        and d.max_ensemble_opposition_strong < d.max_ensemble_opposition
    )

    # C. live config unchanged
    live = Settings._load_fresh().strategy_engine
    live_ok = (
        live.min_ensemble_agreement == 2.5 and live.max_ensemble_opposition == 2.5
        and live.min_ensemble_agreement_strong == 4.0
        and live.max_ensemble_opposition_strong == 1.5
    )

    # D. auto-correct on a re-inverted config
    autocorrect_ok = False
    note = ""
    try:
        from src.strategies.ensemble import EnsembleVoter
        from src.strategies.registry import StrategyRegistry
        bad = Settings._load_fresh()
        # deliberately re-invert: STRONG below GOOD
        bad.strategy_engine.min_ensemble_agreement = 5.0
        bad.strategy_engine.max_ensemble_opposition = 1.0
        bad.strategy_engine.min_ensemble_agreement_strong = 3.0
        bad.strategy_engine.max_ensemble_opposition_strong = 2.0
        reg = StrategyRegistry(regime_filter_enabled=bad.strategy_engine.strategy_regime_filter_enabled)
        EnsembleVoter(reg, bad, state_cache=None, regime_weighter=None)
        se = bad.strategy_engine
        autocorrect_ok = (
            se.min_ensemble_agreement_strong >= se.min_ensemble_agreement   # 3.0 -> 5.0
            and se.max_ensemble_opposition_strong <= se.max_ensemble_opposition  # 2.0 -> 1.0
        )
        note = (f"clamped STRONG to agree={se.min_ensemble_agreement_strong} "
                f"opp={se.max_ensemble_opposition_strong} (GOOD agree={se.min_ensemble_agreement} "
                f"opp={se.max_ensemble_opposition})")
    except Exception as ex:
        note = f"voter-construction skipped: {str(ex)[:100]}"

    print("ISSUE #18 + E15 VERIFICATION — inverted ensemble-tier defaults")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  CONFIG-LESS LADDER (STRONG stricter than GOOD): {ladder_ok} "
          f"(STRONG agree {d.min_ensemble_agreement_strong} > GOOD {d.min_ensemble_agreement}; "
          f"STRONG opp {d.max_ensemble_opposition_strong} < GOOD {d.max_ensemble_opposition})")
    print(f"  LIVE UNCHANGED (config.toml): {live_ok} "
          f"(GOOD {live.min_ensemble_agreement}/{live.max_ensemble_opposition}, "
          f"STRONG {live.min_ensemble_agreement_strong}/{live.max_ensemble_opposition_strong})")
    print(f"  AUTO-CORRECT on re-inverted config: {autocorrect_ok} | {note}")

    ok = all(s.values()) and ladder_ok and live_ok and autocorrect_ok
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
