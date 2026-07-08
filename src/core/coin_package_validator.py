"""Phase 5 (output-quality) — CoinPackage content validator.

Pure-function validator for ``CoinPackage`` instances. Returns a structured
verdict (``ok`` / ``warn`` / ``fail``) plus a completeness score (0..1) plus
the lists of missing and stale fields. ScannerWorker quarantines any
package that scores ``fail`` (not included in the dict written for Stage 2)
so the brain never operates on degenerate data.

Why a separate validator instead of inline checks in ScannerWorker:
    * Pure function — no service dependencies — trivial to unit test.
    * Reusable from /health, tests, and any future replay/diagnostic
      tooling.
    * Threshold values (fail/warn cutoffs, staleness window) live in
      ``settings.coin_package_validator`` so operators can tune via
      ``config.toml`` without redeploy.

Validation rules:

    Required (each contributes 1.0 to the score):
        symbol non-empty
        qualified is bool
        opportunity_score finite in [0, 1]
        price_data.current > 0
        built_at within ``staleness_fail_seconds`` of ``time.time()``

    Optional (each contributes 0.5 to the score):
        xray.setup_type != "none" (force-included may have "none" — OK)
        xray.structural_levels.suggested_sl > 0 (only when setup_type ≠ "none")
        xray.structural_levels.suggested_tp > 0 (same)
        xray.structural_levels.rr_ratio > 0 (same)
        strategies.fired_count >= 0
        signals.confidence finite in [0, 1]
        price_data.regime non-empty
        alt_data.fear_greed > 0
        (Issue E12) strategies.ensemble_consensus not NONE-by-failure
        (Issue E12) signals.direction not neutral-by-failure
        (Issue E12) alt_data.funding_rate not zero-by-failure
        (Issue E12) signals.confidence not exactly 0.0 (fabricated neutral)
      The four E12 checks fire only when corroborating failure evidence is
      present (a build blocker, or confidence exactly 0.0), so a genuinely-
      neutral-but-real package keeps its score (no over-quarantine).

    Score formula:
        completeness = (sum_required + 0.5 * sum_optional)
                       / (count_required + 0.5 * count_optional)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from src.core.coin_package import CoinPackage

VERDICT_OK = "ok"
VERDICT_WARN = "warn"
VERDICT_FAIL = "fail"

# Direction-reconcile fix (2026-06-04, Problem 6 / F22) — canonical blocker-token
# categories, the SINGLE source of truth shared by this validator and the
# strategist prompt render, so the prompt's data-quality signals match the
# completeness score (they previously disagreed). ``blockers_observed`` is a
# mixed list: some tokens are genuine data/compute SOURCE FAILURES, others are
# ADVISORY state/gating flags (recent_loss_within_*, manipulation_likely_session)
# that do NOT mean a data source failed.
#   STRATEGY_INPUT_FAILURE_MARKERS — the strategy/structure-input failures that
#     reduce completeness; the "strategy inputs were incomplete" prompt note must
#     fire on exactly these (plus missing_fields), so it agrees with completeness.
#   SOURCE_FAILURE_MARKERS — all real data/compute source failures; only these may
#     render as ``source_failed``. Any blocker NOT in this set is advisory.
STRATEGY_INPUT_FAILURE_MARKERS = frozenset({
    "signal_missing",
    "xray_missing",
    "xray_extract_failed",
    "state_labeler_failed",
})
SOURCE_FAILURE_MARKERS = STRATEGY_INPUT_FAILURE_MARKERS | frozenset({
    "funding_missing",
    "oi_missing",
    "ticker_missing",
    "interestingness_failed",
})


@dataclass(frozen=True)
class ValidationResult:
    """Immutable result of a CoinPackage validation pass.

    Attributes:
        verdict: ``"ok"`` / ``"warn"`` / ``"fail"``.
        completeness: 0..1 score from the rule weighting.
        missing_fields: required fields that are absent / default.
        stale_fields: fields that are populated but past their freshness
            window (today: just ``built_at`` if older than the staleness
            window).
    """
    verdict: str
    completeness: float
    missing_fields: list[str]
    stale_fields: list[str]


def validate_package(
    pkg: CoinPackage,
    *,
    fail_below: float = 0.50,
    warn_below: float = 0.85,
    staleness_fail_seconds: float = 300.0,
    now_unix: float | None = None,
) -> ValidationResult:
    """Validate a single CoinPackage against required + optional rules.

    Args:
        pkg: The CoinPackage instance to validate.
        fail_below: Verdict ``"fail"`` when completeness < this value.
            Default 0.50.
        warn_below: Verdict ``"warn"`` when completeness < this value
            (and >= fail_below). Default 0.85.
        staleness_fail_seconds: ``built_at`` older than this many
            seconds → field counted missing AND added to
            ``stale_fields``. Default 300 (5 min).
        now_unix: Override "now" for deterministic testing.

    Returns:
        Frozen ``ValidationResult``.
    """
    now = float(now_unix if now_unix is not None else time.time())
    missing: list[str] = []
    stale: list[str] = []
    req_score = 0.0
    req_count = 0
    opt_score = 0.0
    opt_count = 0

    def _req(condition: bool, name: str) -> None:
        nonlocal req_score, req_count
        req_count += 1
        if condition:
            req_score += 1.0
        else:
            missing.append(name)

    def _opt(condition: bool, name: str) -> None:
        nonlocal opt_score, opt_count
        opt_count += 1
        if condition:
            opt_score += 1.0
        else:
            missing.append(name)

    # ── Required fields ────────────────────────────────────────────
    _req(bool(pkg.symbol) and isinstance(pkg.symbol, str), "symbol")
    _req(isinstance(pkg.qualified, bool), "qualified")
    _req(
        isinstance(pkg.opportunity_score, (int, float))
        and math.isfinite(float(pkg.opportunity_score))
        and 0.0 <= float(pkg.opportunity_score) <= 1.0,
        "opportunity_score",
    )
    _req(
        pkg.price_data is not None
        and isinstance(pkg.price_data.current, (int, float))
        and pkg.price_data.current > 0,
        "price_data.current",
    )
    age_s = now - float(pkg.built_at or 0.0)
    is_fresh = age_s < staleness_fail_seconds
    _req(is_fresh, "built_at")
    if not is_fresh:
        stale.append("built_at")

    # ── Optional fields ────────────────────────────────────────────
    setup_type = (pkg.xray.setup_type if pkg.xray else "none") or "none"
    setup_present = setup_type != "none"
    _opt(setup_present, "xray.setup_type")

    if setup_present:
        sl_ok = bool(pkg.xray.structural_levels and pkg.xray.structural_levels.suggested_sl > 0)
        tp_ok = bool(pkg.xray.structural_levels and pkg.xray.structural_levels.suggested_tp > 0)
        rr_ok = bool(pkg.xray.structural_levels and pkg.xray.structural_levels.rr_ratio > 0)
        _opt(sl_ok, "xray.structural_levels.suggested_sl")
        _opt(tp_ok, "xray.structural_levels.suggested_tp")
        _opt(rr_ok, "xray.structural_levels.rr_ratio")

    _opt(
        pkg.strategies is not None
        and isinstance(pkg.strategies.fired_count, int)
        and pkg.strategies.fired_count >= 0,
        "strategies.fired_count",
    )
    _opt(
        pkg.signals is not None
        and isinstance(pkg.signals.confidence, (int, float))
        and math.isfinite(float(pkg.signals.confidence))
        and 0.0 <= float(pkg.signals.confidence) <= 1.0,
        "signals.confidence",
    )
    _opt(
        pkg.price_data is not None and bool(pkg.price_data.regime),
        "price_data.regime",
    )
    _opt(
        pkg.alt_data is not None and int(pkg.alt_data.fear_greed) > 0,
        "alt_data.fear_greed",
    )

    # ── Issue E12 (2026-05-27): count the decisive failure-defaults ──
    # A NONE consensus, a neutral direction, a zero funding, and a zero
    # signal confidence otherwise pass silently, so a package shipping
    # neutral-by-failure scored a misleadingly perfect completeness and the
    # quarantine / cold-start gates never saw the degradation. Count them —
    # but ONLY when corroborating failure evidence is present, so a
    # genuinely-neutral-but-real package is NOT penalised (the
    # over-quarantine safeguard). The only reliable failure evidence is a
    # build blocker, or a confidence of exactly 0.0 (a real SignalWorker
    # reading is virtually never exactly 0.0; 0.0 means no signal was read).
    # This makes completeness meaningful, which the batch-1 #12 provenance
    # render then surfaces to the brain.
    _blockers = set(pkg.blockers_observed or [])
    _consensus = (pkg.strategies.ensemble_consensus if pkg.strategies else "") or ""
    _direction = (pkg.signals.direction if pkg.signals else "") or ""
    _funding = float(pkg.alt_data.funding_rate) if pkg.alt_data else 0.0
    _confidence = float(pkg.signals.confidence) if pkg.signals else 0.0
    _consensus_failed = _consensus in ("NONE", "") and bool(
        _blockers & STRATEGY_INPUT_FAILURE_MARKERS
    )
    _direction_failed = _direction in ("neutral", "") and "signal_missing" in _blockers
    _funding_failed = _funding == 0.0 and "funding_missing" in _blockers
    _confidence_fabricated = _confidence == 0.0
    _opt(not _consensus_failed, "strategies.ensemble_consensus")
    _opt(not _direction_failed, "signals.direction")
    _opt(not _funding_failed, "alt_data.funding_rate")
    _opt(not _confidence_fabricated, "signals.confidence_zero")

    # ── Completeness score ─────────────────────────────────────────
    denom = req_count + 0.5 * opt_count
    completeness = (req_score + 0.5 * opt_score) / denom if denom > 0 else 0.0
    completeness = max(0.0, min(1.0, completeness))

    if completeness < fail_below:
        verdict = VERDICT_FAIL
    elif completeness < warn_below:
        verdict = VERDICT_WARN
    else:
        verdict = VERDICT_OK

    return ValidationResult(
        verdict=verdict,
        completeness=round(completeness, 3),
        missing_fields=missing,
        stale_fields=stale,
    )
