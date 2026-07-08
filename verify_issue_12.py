"""Self-verification for Issue #12 — silently-degraded packages carry provenance.

Offline check against CURRENT code. Three parts:

  A. STATIC: CoinPackage now has missing_fields/stale_fields; the scanner
     write-back carries them and logs PACKAGE_BLOCKERS; the brain formatter
     renders a "Data quality" line with missing + source_failed.
  B. VALIDATOR (real code): a package degraded by source failures (blank regime,
     NONE consensus, neutral direction, none setup) yields completeness < 1.0
     and a non-empty missing_fields, while a clean package scores higher — so
     the provenance the render consumes is real.
  C. RENDER LOGIC: the exact conditional shipped in the formatter produces a
     "Data quality ... source_failed=[...]" line for a degraded package and
     stays silent for a fully-clean one.

Run: .venv/bin/python verify_issue_12.py
"""
from src.core.coin_package import CoinPackage
from src.core.coin_package_validator import validate_package


def static_check():
    cp = open("src/core/coin_package.py").read()
    sc = open("src/workers/scanner_worker.py").read()
    st = open("src/brain/strategist.py").read()
    return {
        "CoinPackage has missing_fields": "missing_fields: list[str]" in cp,
        "CoinPackage has stale_fields": "stale_fields: list[str]" in cp,
        "scanner carries pkg.missing_fields": "pkg.missing_fields = list(vr.missing_fields)" in sc,
        "scanner logs PACKAGE_BLOCKERS": "PACKAGE_BLOCKERS" in sc,
        "brain renders Data quality line": "Data quality:" in st,
        "brain marks source_failed": "source_failed=" in st,
    }


def _degraded_pkg():
    # Defaults ARE the degraded values: regime="", consensus="NONE",
    # direction="neutral", setup_type="none", fear_greed=0. Set a valid price
    # + recent built_at so it is NOT quarantined (the silent-degradation case).
    p = CoinPackage(symbol="DEGRADED", qualified=True, opportunity_score=0.5)
    p.price_data.current = 100.0
    p.blockers_observed = ["signal_missing", "funding_missing"]
    return p


def _clean_pkg():
    p = CoinPackage(symbol="CLEAN", qualified=True, opportunity_score=0.8)
    p.price_data.current = 100.0
    p.price_data.regime = "trending_up"
    p.xray.setup_type = "bullish_fvg_ob"
    p.xray.structural_levels.suggested_sl = 95.0
    p.xray.structural_levels.suggested_tp = 110.0
    p.xray.structural_levels.rr_ratio = 3.0
    p.signals.confidence = 0.7
    p.alt_data.fear_greed = 40
    p.strategies.fired_count = 3
    return p


def validator_check():
    deg = validate_package(_degraded_pkg())
    clean = validate_package(_clean_pkg())
    return {
        "degraded completeness < 1.0": deg.completeness < 1.0,
        "degraded has missing_fields": len(deg.missing_fields) > 0,
        "clean completeness > degraded": clean.completeness > deg.completeness,
    }, deg, clean


def render_logic(pkg):
    """Exact replica of the shipped formatter conditional (strategist.py)."""
    _missing = list(getattr(pkg, "missing_fields", []) or [])
    _blockers = list(getattr(pkg, "blockers_observed", []) or [])
    _completeness = float(getattr(pkg, "completeness", 1.0) or 1.0)
    if _completeness < 1.0 or _missing or _blockers:
        line = f"  Data quality: completeness={_completeness:.2f}"
        if _missing:
            line += f" missing={_missing}"
        if _blockers:
            line += f" source_failed={_blockers}"
        return line
    return None


def main():
    s = static_check()
    v, deg, clean = validator_check()

    # Build a shipped-style degraded package: validator provenance written on.
    dp = _degraded_pkg()
    dp.completeness = deg.completeness
    dp.missing_fields = list(deg.missing_fields)
    deg_line = render_logic(dp)

    cp = _clean_pkg()
    cp.completeness = clean.completeness
    cp.missing_fields = list(clean.missing_fields)
    clean_line = render_logic(cp)

    print("ISSUE #12 VERIFICATION — package provenance to the brain")
    print("  STATIC (fields + write-back + render wired):")
    for k, val in s.items():
        print(f"    {k}: {val}")
    print("  VALIDATOR (real code):")
    for k, val in v.items():
        print(f"    {k}: {val}")
    print(f"    degraded completeness={deg.completeness:.2f} missing={deg.missing_fields}")
    print("  RENDER LOGIC:")
    print(f"    degraded renders provenance: {deg_line is not None}")
    print(f"      -> {deg_line}")
    print(f"    clean stays silent: {clean_line is None}")

    ok = (
        all(s.values())
        and all(v.values())
        and deg_line is not None
        and "source_failed=" in deg_line
        and clean_line is None
    )
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
