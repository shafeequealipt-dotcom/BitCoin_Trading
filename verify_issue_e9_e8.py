"""Self-verification for E9 (HIGH) + E8 — plumb real ranker inputs.

The interestingness ranker scored on a hardcoded regime_confidence=0.0 and
blank structure/OI inputs (E9), and the open-interest input was never passed
(E8). Both call sites in scanner_worker.py now pass the real values that are
already in scope (RegimeState confidence/adx/choppiness/volume_ratio, structure
position_in_range + MTF aligned_direction, alt.oi_change_24h_pct).

Confirms:
  A. STATIC: neither call site hardcodes regime_confidence=0.0 (both use _rc);
     the E9 structure kwargs + the E8 oi kwarg are passed; the state=None guard
     and the BRIEFING_INTERESTINGNESS sentinel are present.
  B. BEHAVIORAL (real compute_interestingness): a stub-fed score (old: rc=0.0,
     no structure/OI) differs from a real-fed score (rc=0.8, adx/pir/oi real),
     and the cleanness + extremity components rise with the real inputs — i.e.
     the ranker is no longer scoring on fabricated zeros.

Read-only / in-memory.
"""


def static_check():
    s = open("src/workers/scanner_worker.py").read()
    return {
        "no hardcoded regime_confidence=0.0 left": "regime_confidence=0.0" not in s,
        "both call sites use real regime_confidence (_rc)": s.count("regime_confidence=_rc") >= 2,
        "E9 structure kwargs plumbed": "adx=_adx" in s and "choppiness=_chop" in s
        and "position_in_range=_pir" in s and "volume_ratio=_vr" in s
        and "mtf_h1_bias=_mtf_dir" in s,
        "E8 oi kwarg plumbed": "oi_change_24h_pct=_oi_chg" in s,
        "state=None guard present (E9 unbound-var fix)": "state = None" in s,
        "BRIEFING_INTERESTINGNESS sentinel emitted": "BRIEFING_INTERESTINGNESS" in s,
    }


def main():
    from src.workers.scanner.interestingness import compute_interestingness

    common = dict(
        setup_type="bullish_fvg_ob", setup_type_confidence=0.7, setup_score=70.0,
        trade_direction="long", consensus="GOOD", consensus_direction="long",
        signal_direction="long", regime="trending_up", funding_rate=0.0006,
        fear_greed=20, primary_label="MOMENTUM_IGNITION",
    )
    # OLD (stub): the pre-fix call — regime confidence forced 0, no structure/OI.
    stub = compute_interestingness(
        **common, regime_confidence=0.0, adx=None, choppiness=None,
        volume_ratio=None, position_in_range=None, mtf_h1_bias="", oi_change_24h_pct=0.0,
    )
    # NEW (real): the plumbed inputs that are now passed from the live pipeline.
    real = compute_interestingness(
        **common, regime_confidence=0.8, adx=30.0, choppiness=40.0,
        volume_ratio=2.0, position_in_range=0.05, mtf_h1_bias="long",
        oi_change_24h_pct=10.0,
    )

    s = static_check()
    score_diff = abs(real.score - stub.score) > 1e-6
    score_up = real.score > stub.score
    cleanness_up = real.state_cleanness > stub.state_cleanness  # driven by rc 0.8 vs 0.0
    bd_real = dict(real.breakdown)
    bd_stub = dict(stub.breakdown)
    extremity_up = float(bd_real.get("extremity", 0)) > float(bd_stub.get("extremity", 0))

    print("E9 + E8 VERIFICATION — plumb real ranker inputs (completes #2)")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  BEHAVIORAL (real compute_interestingness):")
    print(f"    stub score={stub.score:.4f} cleanness={stub.state_cleanness:.3f} "
          f"extremity={bd_stub.get('extremity')}")
    print(f"    real score={real.score:.4f} cleanness={real.state_cleanness:.3f} "
          f"extremity={bd_real.get('extremity')}")
    print(f"    score differs (real>stub): {score_diff and score_up}")
    print(f"    cleanness rises with real regime confidence: {cleanness_up}")
    print(f"    extremity rises with real OI/position: {extremity_up}")

    ok = all(s.values()) and score_diff and score_up and cleanness_up and extremity_up
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
