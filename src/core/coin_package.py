"""CoinPackage — self-contained per-coin data bundle for Stage 2 (Phase 6).

Layer 1 restructure Phase 6 introduces CoinPackage as the contract
between ScannerWorker (the selector + builder) and Stage 2 (the
strategist that prompts Claude). Each selected coin gets one package
containing every per-coin fact Claude needs at decision time, so
Stage 2 stops querying 12 services per cycle (HR-3 in the blueprint).

Schema follows blueprint Section 11.2 verbatim. Sub-dataclasses keep
the structure self-documenting and let pure-function callers (tests,
formatters) refer to nested fields by attribute rather than dict
lookup. ``to_dict()`` serializes to a plain dict for prompt assembly.

Backward compatibility: this dataclass is forward-only — Phases 7+
read from packages, never write to them, so the schema is the
contract. Add new fields, never remove.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field


@dataclass
class StructuralLevels:
    """SL/TP suggestion levels and reward-to-risk."""
    current_price: float = 0.0
    suggested_sl: float = 0.0
    suggested_tp: float = 0.0
    rr_ratio: float = 0.0


@dataclass
class XrayBlock:
    """X-RAY structural classification + key features per coin."""
    setup_type: str = "none"
    setup_score: float = 0.0
    setup_type_confidence: float = 0.0
    # XRAY counter-setup Phase 5d — trade_direction implied by setup_type.
    # For in-direction setups (BULLISH_FVG_OB, BEARISH_FVG_OB,
    # *_STRUCTURAL_BREAK, *_LIQUIDITY_SWEEP, *_RANGE_*) trade_direction
    # equals suggested_direction. For COUNTER setups
    # (BULLISH_FVG_OB_COUNTER, BEARISH_FVG_OB_COUNTER) trade_direction
    # is the OPPOSITE of suggested_direction — the trade plays against
    # the structural bias because in-direction zones are missing but
    # opposite-direction zones are present. Empty string when setup_type
    # is "none" (no trade implied). Stage 2 brain prompt surfaces this
    # alongside setup_type so the brain can factor counter-trade context
    # into its decision.
    trade_direction: str = ""
    structural_levels: StructuralLevels = field(default_factory=StructuralLevels)
    mtf_confluence: str = ""
    session: str = ""
    session_phase: str = ""
    key_features: list[str] = field(default_factory=list)


@dataclass
class StrategiesBlock:
    """Stage 1 ensemble outputs flattened per coin."""
    fired_count: int = 0
    fired_strategies: list[str] = field(default_factory=list)
    ensemble_consensus: str = "NONE"
    consensus_score: float = 0.0
    total_score: float = 0.0
    # Issue E25 (2026-05-28): the regime the strategy worker actually SCORED
    # this coin under (captured fresh at the start of the scoring cycle). It
    # travels WITH the consensus/votes above so the brain can render the
    # regime label that matches the scores it shows, instead of separately
    # re-reading the detector cache (which can have drifted). Empty string
    # means the scorer did not tag a regime this cycle -> the brain falls back
    # to the detector cache (pre-E25 behavior), so frequency is never reduced.
    scoring_regime: str = ""
    # Issue #2 (2026-05-31): the SCORED regime's own metrics, captured beside
    # scoring_regime so the brain's candidate `Regime:` line shows the scoring
    # WORD with the numbers from the SAME snapshot (word + metrics describe one
    # regime), instead of gluing the scoring word onto the live-cache metrics of
    # a possibly-drifted regime. Field names mirror RegimeState; defaults are
    # neutral so a package built outside the scoring path (legacy/unscored)
    # renders cleanly and the pre-#2 live-cache fallback still applies.
    # `scoring_regime_volume_ratio_known` bridges to Issue #3A: it lets the
    # renderer show vol_ratio=n/a for a scored-but-volume-missing coin.
    scoring_regime_confidence: float = 0.0
    scoring_regime_adx: float = 0.0
    scoring_regime_atr_percentile: float = 0.0
    scoring_regime_choppiness: float = 0.0
    scoring_regime_volume_ratio: float = 0.0
    scoring_regime_volume_ratio_known: bool = True
    scoring_regime_trend_direction: int = 0


@dataclass
class SignalsBlock:
    """SignalWorker confidence + sentiment."""
    confidence: float = 0.0
    direction: str = "neutral"
    sentiment_score: float = 0.0
    sentiment_articles_count: int = 0


@dataclass
class AltDataBlock:
    """Funding + OI + Fear & Greed slice per coin."""
    funding_rate: float = 0.0
    funding_signal: str = "neutral"
    oi_change_24h_pct: float = 0.0  # Issue #8 fix: real ~24h OI delta (was the never-populated oi_change_4h_pct)
    fear_greed: int = 0


@dataclass
class PriceDataBlock:
    """Real-time price summary for the coin at package-build time."""
    current: float = 0.0
    change_24h_pct: float = 0.0
    volume_24h_usd: float = 0.0
    regime: str = ""


@dataclass
class StateLabelBlock:
    """Phase 3 of the 1D briefing rewrite — opportunity-state classification.

    Produced by ``src.workers.scanner.state_labeler.label_state``. The
    primary label is the highest-base-weight trigger that fired for the
    coin's current state; secondaries carry the rest. The confidence is
    the trigger's own confidence value (typically derived from
    ``setup_type_confidence`` or the magnitude past a threshold —
    e.g. how far past the funding-extreme cutoff the rate is).

    Defaults to ``NO_TRADEABLE_STATE`` (the labeler's no-fire fallback)
    so a CoinPackage built without a labeler invocation still validates.
    """
    primary: str = "NO_TRADEABLE_STATE"
    secondary: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class CoinPackage:
    """Self-contained per-coin data bundle Stage 2 reads instead of querying services.

    HR-3 in the blueprint. Built by ScannerWorker after qualitative
    filter + ranking, written to ``layer_manager._coin_packages``,
    consumed by ``strategist._format_packages_for_prompt`` in Phase 7.

    Attributes:
        symbol: e.g. ``"BTCUSDT"``.
        qualified: True when the coin passed the Phase 5 checklist.
            False when force-included (open position) bypassed the gate.
        opportunity_score: composite ranking score (0..1 normalized).
        qualification_reasons: ordered list of human-readable reasons
            captured by ``_qualifies``.
        price_data: market summary (current price, 24h change/volume,
            regime label).
        xray: structural classification block.
        strategies: ensemble output block.
        signals: signal confidence + sentiment block.
        alt_data: funding + OI + fear/greed.
        open_position: dict with the active position details if the
            coin was force-included; else None.
        blockers_observed: list of blocker labels recorded for
            transparency. May be non-empty even when ``qualified`` is
            True if the coin's near-failure was worth surfacing.
        built_at: ``time.time()`` at construction. Stage 2 can use this
            to detect stale packages.
        completeness: 0..1 score from CoinPackageValidator.validate_package
            written by ScannerWorker after each package is built. Defaults
            to 1.0 so legacy callers that construct a CoinPackage outside
            the scanner path don't trip the cold-start gate inadvertently.
            Definitive-fix Phase 6 (2026-04-28).
    """
    symbol: str
    qualified: bool
    opportunity_score: float
    qualification_reasons: list[str] = field(default_factory=list)
    price_data: PriceDataBlock = field(default_factory=PriceDataBlock)
    xray: XrayBlock = field(default_factory=XrayBlock)
    strategies: StrategiesBlock = field(default_factory=StrategiesBlock)
    signals: SignalsBlock = field(default_factory=SignalsBlock)
    alt_data: AltDataBlock = field(default_factory=AltDataBlock)
    open_position: dict | None = None
    blockers_observed: list[str] = field(default_factory=list)
    built_at: float = field(default_factory=time.time)
    completeness: float = 1.0
    # Issue #12 fix (2026-05-27): provenance carried forward from the validator
    # so the brain prompt can tell fabricated neutrality (a source errored and
    # defaulted to a blank regime / NONE consensus / neutral direction) from
    # real market neutrality. Written by ScannerWorker after validate_package;
    # rendered per-coin in the brain prompt's "Data quality" line.
    missing_fields: list[str] = field(default_factory=list)
    stale_fields: list[str] = field(default_factory=list)
    # Phase 3 of the 1D briefing rewrite — opportunity-state classification.
    # Populated by ScannerWorker._build_package via state_labeler.label_state.
    # Defaults to NO_TRADEABLE_STATE so a package built outside the
    # briefing pipeline still validates and renders without surprises.
    state_label: StateLabelBlock = field(default_factory=StateLabelBlock)
    # Phase 4 of the 1D briefing rewrite — continuous interestingness
    # score (0..1) computed by interestingness.compute_interestingness
    # over the assembled per-coin state. The briefing-mode scanner
    # (Phase 5) sorts top-N by this number; the brain prompt (Phase 6)
    # surfaces it alongside state_label so Claude can read the system's
    # ranked-attractiveness signal.
    interestingness_score: float = 0.0
    # Per-component contribution dict (already-weighted; sums to
    # interestingness_score). Surfaced for transparency in the brain
    # prompt and BRIEFING_INTERESTINGNESS log line. Phase 6+ format
    # this for human consumption.
    interestingness_breakdown: dict = field(default_factory=dict)
    # Raw cleanness component (0..1, unweighted). Convenience accessor
    # for callers that don't want to unpack ``interestingness_breakdown``.
    state_cleanness: float = 0.0
    # Number of directional anchors that aligned (0..N). Indicates
    # how much the coin's state speaks with one voice — high count
    # is a confluence signal independent of the weighted score.
    confluence_count: int = 0

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict (used for logging/diffing/prompts)."""
        return asdict(self)

    def size_bytes(self) -> int:
        """Approximate JSON size — drives blueprint Section 14.5 ``packages_total_size_bytes``."""
        return len(json.dumps(self.to_dict(), default=str))
