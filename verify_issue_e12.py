"""Self-verification for Issue E12 — validator counts the decisive defaults.

Offline, current code. Confirms:

  A. STATIC: the four E12 optional checks are wired in the validator.
  B. BEHAVIORAL (real validator, three worked packages):
     - a clean package still scores 1.00 / OK (no regression);
     - a FABRICATED-neutral package (NONE consensus + neutral direction +
       zero funding + zero confidence, WITH corroborating build blockers)
       now scores materially lower (~0.63) / WARN, and the four E12 field
       names appear in missing_fields;
     - a GENUINELY-neutral-but-real package (same neutral values but NO
       blockers and a real non-zero confidence) is NOT penalised — it stays
       ~0.95 / OK with NONE of the four E12 names in missing_fields. This is
       the over-quarantine safeguard asserted directly.

Read-only; constructs in-memory packages only.
"""
import time

from src.core.coin_package import (
    AltDataBlock, CoinPackage, PriceDataBlock, SignalsBlock,
    StrategiesBlock, StructuralLevels, XrayBlock,
)
from src.core.coin_package_validator import (
    VERDICT_OK, VERDICT_WARN, validate_package,
)

_E12_NAMES = {
    "strategies.ensemble_consensus", "signals.direction",
    "alt_data.funding_rate", "signals.confidence_zero",
}


def static_check():
    src = open("src/core/coin_package_validator.py").read()
    return {
        "consensus check wired": '_opt(not _consensus_failed, "strategies.ensemble_consensus")' in src,
        "direction check wired": '_opt(not _direction_failed, "signals.direction")' in src,
        "funding check wired": '_opt(not _funding_failed, "alt_data.funding_rate")' in src,
        "confidence-zero check wired": '_opt(not _confidence_fabricated, "signals.confidence_zero")' in src,
        "blocker-gated (over-quarantine safeguard)": "_blockers = set(pkg.blockers_observed or [])" in src,
    }


def _clean():
    return CoinPackage(
        "CLEANUSDT", True, 0.80,
        price_data=PriceDataBlock(current=100.0, regime="trending_up"),
        xray=XrayBlock(setup_type="bullish_fvg_ob",
                       structural_levels=StructuralLevels(suggested_sl=95, suggested_tp=110, rr_ratio=2.0)),
        strategies=StrategiesBlock(fired_count=3, ensemble_consensus="STRONG"),
        signals=SignalsBlock(confidence=0.90, direction="long"),
        alt_data=AltDataBlock(funding_rate=0.0003, fear_greed=55),
        built_at=time.time(), blockers_observed=[],
    )


def _fabricated():
    return CoinPackage(
        "FAILUSDT", True, 0.80,
        price_data=PriceDataBlock(current=100.0, regime=""),
        xray=XrayBlock(setup_type="none"),
        strategies=StrategiesBlock(fired_count=0, ensemble_consensus="NONE"),
        signals=SignalsBlock(confidence=0.0, direction="neutral"),
        alt_data=AltDataBlock(funding_rate=0.0, fear_greed=0),
        built_at=time.time(), blockers_observed=["signal_missing", "funding_missing"],
    )


def _real_neutral():
    return CoinPackage(
        "REALUSDT", True, 0.80,
        price_data=PriceDataBlock(current=100.0, regime="trending_up"),
        xray=XrayBlock(setup_type="none"),
        strategies=StrategiesBlock(fired_count=0, ensemble_consensus="NONE"),
        signals=SignalsBlock(confidence=0.55, direction="neutral"),
        alt_data=AltDataBlock(funding_rate=0.0, fear_greed=50),
        built_at=time.time(), blockers_observed=[],
    )


def main():
    s = static_check()
    clean = validate_package(_clean())
    fab = validate_package(_fabricated())
    real = validate_package(_real_neutral())

    print("ISSUE E12 VERIFICATION — validator counts decisive defaults")
    print("  STATIC (four blocker-gated checks wired):")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print("  BEHAVIORAL (real validator):")
    print(f"    clean:        completeness={clean.completeness:.3f} verdict={clean.verdict}")
    print(f"    fabricated:   completeness={fab.completeness:.3f} verdict={fab.verdict} "
          f"e12_in_missing={sorted(_E12_NAMES & set(fab.missing_fields))}")
    print(f"    real-neutral: completeness={real.completeness:.3f} verdict={real.verdict} "
          f"e12_in_missing={sorted(_E12_NAMES & set(real.missing_fields))}")

    ok = (
        all(s.values())
        and clean.completeness == 1.0 and clean.verdict == VERDICT_OK
        and fab.completeness < 0.85 and fab.verdict == VERDICT_WARN
        and _E12_NAMES.issubset(set(fab.missing_fields))
        and real.completeness >= 0.85 and real.verdict == VERDICT_OK
        and not (_E12_NAMES & set(real.missing_fields))   # safeguard: real-neutral NOT penalised
    )
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
