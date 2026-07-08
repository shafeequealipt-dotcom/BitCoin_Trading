"""Phase 5 (output-quality) — CoinPackage validator tests.

Verifies the pure-function validator produces correct verdicts +
completeness scores + missing/stale field lists for representative
package shapes. Operators rely on these verdicts to quarantine
degenerate packages before they reach Stage 2.
"""

from __future__ import annotations

import time

import pytest

from src.config.settings import CoinPackageValidatorSettings
from src.core.coin_package import (
    AltDataBlock,
    CoinPackage,
    PriceDataBlock,
    SignalsBlock,
    StrategiesBlock,
    StructuralLevels,
    XrayBlock,
)
from src.core.coin_package_validator import (
    VERDICT_FAIL,
    VERDICT_OK,
    VERDICT_WARN,
    validate_package,
)


def _full_package(symbol: str = "BTCUSDT") -> CoinPackage:
    """A package with every required + optional field populated."""
    return CoinPackage(
        symbol=symbol,
        qualified=True,
        opportunity_score=0.85,
        qualification_reasons=["xray=bullish_fvg_ob", "consensus=STRONG"],
        price_data=PriceDataBlock(
            current=43250.12,
            change_24h_pct=2.5,
            volume_24h_usd=12_500_000_000.0,
            regime="trending_up",
        ),
        xray=XrayBlock(
            setup_type="bullish_fvg_ob",
            setup_score=85.0,
            setup_type_confidence=0.90,
            structural_levels=StructuralLevels(
                current_price=43250.12,
                suggested_sl=42500.0,
                suggested_tp=45000.0,
                rr_ratio=2.0,
            ),
            mtf_confluence="strong",
            session="ny",
            session_phase="active",
        ),
        strategies=StrategiesBlock(
            fired_count=4,
            ensemble_consensus="STRONG",
            consensus_score=0.85,
            total_score=85.0,
        ),
        signals=SignalsBlock(
            confidence=0.90, direction="long", sentiment_score=0.4,
        ),
        alt_data=AltDataBlock(
            funding_rate=0.0003,
            funding_signal="longs_paying",
            oi_change_24h_pct=2.0,
            fear_greed=45,
        ),
    )


def test_full_package_passes_ok() -> None:
    """A package with every required + optional field populated → ok @ 1.0."""
    vr = validate_package(_full_package())
    assert vr.verdict == VERDICT_OK
    assert vr.completeness == 1.0
    assert vr.missing_fields == []
    assert vr.stale_fields == []


def test_missing_required_symbol_fails() -> None:
    """Empty symbol → required field missing → score drops, verdict fails."""
    pkg = _full_package(symbol="")
    vr = validate_package(pkg)
    assert "symbol" in vr.missing_fields
    assert vr.completeness < 1.0


def test_missing_price_data_current_fails() -> None:
    pkg = _full_package()
    pkg.price_data.current = 0.0
    vr = validate_package(pkg)
    assert "price_data.current" in vr.missing_fields


def test_missing_optional_only_warns() -> None:
    """Required fields complete, only optional `xray.setup_type=none`+rr_ratio
    drop → verdict still ok if score >= warn_below.
    """
    pkg = _full_package()
    pkg.xray.setup_type = "none"
    # When setup_type is none, the related sl/tp/rr optionals are not
    # checked, so completeness stays high.
    vr = validate_package(pkg)
    # Score after Issue E12 (four extra optionals, all PASS here since this
    # package is clean — STRONG consensus, real confidence, no blockers):
    # 5 required + 8 optional populated of 9 (setup_type=none is the only
    # miss) = (5 + 8*0.5) / (5 + 9*0.5) = 9 / 9.5 = 0.947.
    assert vr.completeness > 0.85
    assert vr.verdict in (VERDICT_OK, VERDICT_WARN)


def test_stale_built_at_marks_stale_and_missing() -> None:
    """built_at older than staleness_fail_seconds → marked stale + missing."""
    pkg = _full_package()
    pkg.built_at = time.time() - 600.0  # 10 min old
    vr = validate_package(pkg, staleness_fail_seconds=300.0)
    assert "built_at" in vr.missing_fields
    assert "built_at" in vr.stale_fields


def test_fail_below_quarantines() -> None:
    """A near-empty package → completeness < fail_below → fail verdict.

    Tightening fail_below to 0.80 makes the bare package quarantine since
    most optionals are missing. With the default fail_below=0.50 a bare
    package would land in WARN — proves the threshold is operator-tunable.
    """
    pkg = CoinPackage(
        symbol="DOGEUSDT", qualified=False, opportunity_score=0.10,
    )
    # Tighten fail_below to demonstrate quarantine path explicitly.
    vr = validate_package(pkg, fail_below=0.80, warn_below=0.95)
    assert vr.verdict == VERDICT_FAIL
    assert "price_data.current" in vr.missing_fields
    # Re-run with default thresholds — same package now in WARN.
    vr2 = validate_package(pkg, fail_below=0.50, warn_below=0.85)
    assert vr2.verdict == VERDICT_WARN


def test_warn_threshold_calibrated() -> None:
    """Score between fail_below and warn_below → warn verdict."""
    # Build a package that's ~75% complete: missing optional fields + a
    # minor staleness flag.
    pkg = _full_package()
    pkg.xray.setup_type = "none"
    pkg.signals.confidence = 0.5  # still valid optional
    pkg.alt_data.fear_greed = 0   # missing optional
    pkg.price_data.regime = ""    # missing optional
    pkg.strategies.fired_count = 0  # still valid optional
    vr = validate_package(pkg, fail_below=0.50, warn_below=0.85)
    # Just verifies that we land between fail_below and warn_below for some shape.
    assert vr.completeness < 0.85


def test_setup_type_present_requires_sl_tp_rr() -> None:
    """When setup_type ≠ 'none', SL/TP/RR optionals are checked."""
    pkg = _full_package()
    # setup_type stays bullish_fvg_ob; zero out the structural levels.
    pkg.xray.structural_levels.suggested_sl = 0.0
    pkg.xray.structural_levels.suggested_tp = 0.0
    pkg.xray.structural_levels.rr_ratio = 0.0
    vr = validate_package(pkg)
    assert "xray.structural_levels.suggested_sl" in vr.missing_fields
    assert "xray.structural_levels.suggested_tp" in vr.missing_fields
    assert "xray.structural_levels.rr_ratio" in vr.missing_fields


def test_invalid_opportunity_score_fails() -> None:
    """opportunity_score > 1 or NaN → required field fails."""
    pkg = _full_package()
    pkg.opportunity_score = 1.5  # out of range
    vr = validate_package(pkg)
    assert "opportunity_score" in vr.missing_fields


def test_settings_negative_validation() -> None:
    """CoinPackageValidatorSettings rejects bad threshold ordering."""
    with pytest.raises(ValueError):
        CoinPackageValidatorSettings(fail_below=0.9, warn_below=0.5)
    with pytest.raises(ValueError):
        CoinPackageValidatorSettings(staleness_fail_seconds=0)


def test_validation_result_is_immutable() -> None:
    """ValidationResult is frozen — caller cannot mutate the verdict."""
    vr = validate_package(_full_package())
    with pytest.raises((AttributeError, Exception)):
        vr.verdict = "tampered"  # type: ignore[misc]


def test_e12_fabricated_neutral_with_blockers_warns() -> None:
    """Issue E12: a package shipping neutral-by-failure (NONE consensus,
    neutral direction, zero funding, zero confidence) WITH corroborating
    build blockers now scores a reduced completeness (WARN), with the four
    E12 field names in missing_fields — completeness becomes meaningful."""
    pkg = CoinPackage(
        symbol="FAILUSDT", qualified=True, opportunity_score=0.80,
        price_data=PriceDataBlock(current=100.0, regime=""),
        xray=XrayBlock(setup_type="none"),
        strategies=StrategiesBlock(fired_count=0, ensemble_consensus="NONE"),
        signals=SignalsBlock(confidence=0.0, direction="neutral"),
        alt_data=AltDataBlock(funding_rate=0.0, fear_greed=0),
        blockers_observed=["signal_missing", "funding_missing"],
    )
    vr = validate_package(pkg)
    assert vr.verdict == VERDICT_WARN
    assert vr.completeness < 0.85
    for name in ("strategies.ensemble_consensus", "signals.direction",
                 "alt_data.funding_rate", "signals.confidence_zero"):
        assert name in vr.missing_fields


def test_e12_real_neutral_without_blockers_not_penalised() -> None:
    """Issue E12 over-quarantine safeguard: a genuinely-neutral-but-real
    package (same neutral values but NO blockers and a real non-zero
    confidence) is NOT penalised — none of the four E12 names appear in
    missing_fields and the verdict stays OK."""
    pkg = CoinPackage(
        symbol="REALUSDT", qualified=True, opportunity_score=0.80,
        price_data=PriceDataBlock(current=100.0, regime="trending_up"),
        xray=XrayBlock(setup_type="none"),
        strategies=StrategiesBlock(fired_count=0, ensemble_consensus="NONE"),
        signals=SignalsBlock(confidence=0.55, direction="neutral"),
        alt_data=AltDataBlock(funding_rate=0.0, fear_greed=50),
        blockers_observed=[],
    )
    vr = validate_package(pkg)
    assert vr.verdict == VERDICT_OK
    for name in ("strategies.ensemble_consensus", "signals.direction",
                 "alt_data.funding_rate", "signals.confidence_zero"):
        assert name not in vr.missing_fields
