"""X-RAY structural analysis data types.

Defines all dataclasses produced by the X-RAY structural intelligence engine.
These are the contracts between X-RAY and every downstream consumer
(scorer, Claude context, APEX, Watchdog, etc.).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class SetupType(str, Enum):
    """Categorical classification of an X-RAY structural setup.

    Phase 2 of the Layer 1 restructure introduces this categorical
    label alongside the existing numeric ``setup_score``. ScannerWorker's
    qualitative filter (Phase 5) checks for ``setup_type != NONE`` —
    high score is necessary but not sufficient evidence of a complete
    pattern.

    The enum is intentionally short (10 directional + NONE). New
    patterns add a value rather than overload an existing one. The
    ``str`` mixin lets the value serialize cleanly to JSON without a
    custom encoder.

    Counter variants (``BULLISH_FVG_OB_COUNTER``, ``BEARISH_FVG_OB_COUNTER``)
    are emitted when the suggested direction's in-direction zones are
    missing but the OPPOSITE direction has tradeable FVG+OB structure
    near price. They represent characterize-and-rank output for coins
    that would otherwise classify as NONE — the actual trade direction
    is opposite to ``suggested_direction`` (see StructuralAnalysis.
    ``trade_direction``). Counter setups carry reduced confidence
    (×``counter_confidence_multiplier``, default 0.7) so downstream
    ranking honors the lower conviction.
    """

    NONE = "none"
    BULLISH_FVG_OB = "bullish_fvg_ob"
    BULLISH_FVG_OB_COUNTER = "bullish_fvg_ob_counter"
    BULLISH_STRUCTURAL_BREAK = "bullish_structural_break"
    BULLISH_LIQUIDITY_SWEEP = "bullish_liquidity_sweep"
    BULLISH_RANGE_BREAKOUT = "bullish_range_breakout"
    BEARISH_FVG_OB = "bearish_fvg_ob"
    BEARISH_FVG_OB_COUNTER = "bearish_fvg_ob_counter"
    BEARISH_STRUCTURAL_BREAK = "bearish_structural_break"
    BEARISH_LIQUIDITY_SWEEP = "bearish_liquidity_sweep"
    BEARISH_RANGE_BREAKDOWN = "bearish_range_breakdown"


@dataclass
class PriceLevel:
    """A detected support or resistance price level with zone boundaries."""
    price: float
    level_type: str  # "support" or "resistance"
    strength: float = 0.0  # 0.0 to 5.0
    touches: int = 0
    last_tested: float = 0.0  # timestamp (monotonic or epoch)
    timeframe: str = "60"  # which timeframe detected on
    zone_low: float = 0.0
    zone_high: float = 0.0

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "level_type": self.level_type,
            "strength": round(self.strength, 2),
            "touches": self.touches,
            "timeframe": self.timeframe,
            "zone_low": self.zone_low,
            "zone_high": self.zone_high,
        }


@dataclass
class StructureEvent:
    """A Break of Structure (BOS) or Change of Character (CHoCH) event."""
    event_type: str  # "bos" or "choch"
    direction: str  # "bullish" or "bearish"
    price: float = 0.0
    timestamp: float = 0.0  # epoch or index
    significance: str = "minor"  # "major" or "minor"

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "direction": self.direction,
            "price": self.price,
            "significance": self.significance,
        }


@dataclass
class MarketStructureResult:
    """Market structure classification for one symbol."""
    structure: str = "unknown"  # "uptrend", "downtrend", "ranging", "unknown"
    strength: str = "weak"  # "strong", "medium", "weak"
    swing_highs: list = field(default_factory=list)  # list of (index, price) tuples
    swing_lows: list = field(default_factory=list)
    last_bos: StructureEvent | None = None
    last_choch: StructureEvent | None = None
    invalidation_level: float = 0.0
    swing_count: int = 0

    def to_dict(self) -> dict:
        return {
            "structure": self.structure,
            "strength": self.strength,
            "last_bos": self.last_bos.to_dict() if self.last_bos else None,
            "last_choch": self.last_choch.to_dict() if self.last_choch else None,
            "invalidation_level": self.invalidation_level,
            "swing_count": self.swing_count,
        }


@dataclass
class StructuralPlacement:
    """Structural SL/TP calculation result with Risk:Reward ratio."""
    structural_sl: float = 0.0
    structural_tp: float = 0.0
    rr_ratio: float = 0.0  # Backward compat — set to rr_best (max of both directions)
    rr_quality: str = "skip"  # "excellent" (>=3), "good" (2-3), "poor" (1.5-2), "skip" (<1.5)
    # Dual-direction R:R
    rr_long: float = 0.0          # R:R for LONG (Buy) direction
    rr_short: float = 0.0         # R:R for SHORT (Sell) direction
    rr_best: float = 0.0          # max(rr_long, rr_short)
    rr_best_direction: str = ""   # "long" or "short" — which direction has better R:R
    long_sl_price: float = 0.0    # Where SL would go for a long
    long_tp_price: float = 0.0    # Where TP would go for a long
    short_sl_price: float = 0.0   # Where SL would go for a short
    short_tp_price: float = 0.0   # Where TP would go for a short
    entry_quality: str = "mid_range"  # "ideal", "good", "mid_range", "poor"
    entry_zone_low: float = 0.0
    entry_zone_high: float = 0.0
    sl_reference: str = ""  # e.g. "below_support_$72000"
    tp_reference: str = ""  # e.g. "at_resistance_$74500"
    direction: str = ""  # "long" or "short"
    is_fallback_rr: bool = False  # True when SL or TP used percentage fallback (not structural)
    # Issue 1 of 2026-05-19 direction-bias fix Phase C — flag set when
    # the raw structural_tp (computed from nearest_res/nearest_sup +
    # tp_buffer) would have landed on the WRONG SIDE of current_price
    # (i.e. structural_tp <= current_price for a long, or >= for a
    # short). This occurs when price is at or above the resistance
    # zone (long case) or at/below support (short case), and was
    # historically masked by the abs() call in the reward formula.
    # The clamped `structural_tp` is still emitted (forced to be at
    # least tp_min_distance_pct away from current_price) so that
    # rr_long/rr_short never collapse to ~0, but downstream consumers
    # may use this flag to qualify their handling (e.g. APEX optimizer
    # may reduce sizing, the watchdog may skip force-close decisions
    # premised on the structural placement).
    is_structurally_invalid: bool = False
    # Gap 2 fix (2026-05-19) — bidirectional clamp flags. The legacy
    # ``is_structurally_invalid`` field above represents the CHOSEN
    # direction's placement only. To surface both directions' clamp
    # state to the brain prompt (so Claude can distinguish a real RR
    # asymmetry from a clamp-floor synthetic asymmetry), the structure
    # engine populates these two bidirectional fields on the chosen
    # placement using the values from ``long_pl.is_structurally_invalid``
    # and ``short_pl.is_structurally_invalid``. Both are ALWAYS populated
    # so the brain sees a uniform ``INVALID_LONG=Y/N INVALID_SHORT=Y/N``
    # annotation per coin. INFORMATIONAL flags — brain decides what to do.
    is_long_invalid: bool = False
    is_short_invalid: bool = False

    def to_dict(self) -> dict:
        return {
            "structural_sl": self.structural_sl,
            "structural_tp": self.structural_tp,
            "rr_ratio": round(self.rr_ratio, 2),
            "rr_quality": self.rr_quality,
            "rr_long": round(self.rr_long, 2),
            "rr_short": round(self.rr_short, 2),
            "rr_best": round(self.rr_best, 2),
            "rr_best_direction": self.rr_best_direction,
            "long_sl_price": self.long_sl_price,
            "long_tp_price": self.long_tp_price,
            "short_sl_price": self.short_sl_price,
            "short_tp_price": self.short_tp_price,
            "entry_quality": self.entry_quality,
            "sl_reference": self.sl_reference,
            "tp_reference": self.tp_reference,
            "direction": self.direction,
            "is_fallback_rr": self.is_fallback_rr,
            "is_structurally_invalid": self.is_structurally_invalid,
            "is_long_invalid": self.is_long_invalid,
            "is_short_invalid": self.is_short_invalid,
        }


@dataclass
class FairValueGap:
    """A Fair Value Gap — price imbalance zone between three consecutive candles."""
    direction: str = ""  # "bullish" or "bearish"
    top: float = 0.0  # upper boundary of gap
    bottom: float = 0.0  # lower boundary of gap
    midpoint: float = 0.0  # (top + bottom) / 2
    created_index: int = 0  # candle index where gap formed
    created_at: float = 0.0  # timestamp or epoch of creation
    filled: bool = False
    partially_filled: bool = False
    fill_percentage: float = 0.0  # 0.0 to 1.0
    timeframe: str = "60"
    gap_size_pct: float = 0.0  # gap size as % of price
    displacement_strength: str = "weak"  # "strong", "moderate", "weak"
    displacement_ratio: float = 0.0  # raw body-to-range ratio

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "top": self.top,
            "bottom": self.bottom,
            "midpoint": round(self.midpoint, 8),
            "filled": self.filled,
            "partially_filled": self.partially_filled,
            "fill_percentage": round(self.fill_percentage, 4),
            "gap_size_pct": round(self.gap_size_pct, 4),
            "displacement_strength": self.displacement_strength,
        }


@dataclass
class OrderBlock:
    """An Order Block — last opposing candle before a displacement move."""
    direction: str = ""  # "bullish" or "bearish"
    high: float = 0.0
    low: float = 0.0
    midpoint: float = 0.0
    created_index: int = 0
    created_at: float = 0.0  # timestamp or epoch of creation
    retests: int = 0
    fresh: bool = True  # has not been revisited
    displacement_strength: str = "weak"  # "strong", "moderate", "weak"
    displacement_ratio: float = 0.0  # raw body-to-range ratio
    has_fvg: bool = False  # displacement created a FVG
    broke_structure: bool = False  # displacement broke market structure (BOS)
    timeframe: str = "60"
    strength_score: float = 0.0  # 0-100 composite score

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "high": self.high,
            "low": self.low,
            "midpoint": round(self.midpoint, 8),
            "retests": self.retests,
            "fresh": self.fresh,
            "displacement_strength": self.displacement_strength,
            "has_fvg": self.has_fvg,
            "broke_structure": self.broke_structure,
            "strength_score": round(self.strength_score, 2),
        }


@dataclass
class NearestFVGResult:
    """Result of ``StructureEngine._find_nearest_fvg`` (XRAY counter-setup Phase 3).

    Surfaces both the in-direction nearest unfilled FVG (matching the
    ``suggested_direction`` derived from market structure) AND the
    counter-direction nearest unfilled FVG within the same window —
    so the Phase 4 classifier can emit ``BULLISH_FVG_OB_COUNTER`` or
    ``BEARISH_FVG_OB_COUNTER`` when in-direction structure is missing
    but the OPPOSITE direction has tradeable structure.

    Pre-Phase-3 the finder returned ``Optional[FairValueGap]`` for the
    in-direction zone only. Information about counter-direction zones
    was silently discarded — the philosophical bug Phase 4 fixes.

    Attributes:
        in_direction: Nearest unfilled FVG matching ``suggested_direction``.
            None when no such zone exists within the proximity window.
        counter_direction: Nearest unfilled FVG matching the OPPOSITE
            direction. None when no such zone exists within the window.
        in_distance_pct: Distance of ``in_direction`` from current price
            as percent of price. None when ``in_direction`` is None.
        counter_distance_pct: Mirror for ``counter_direction``.
        suggested_direction: The direction passed to the finder
            (``"long"`` / ``"short"`` / ``""``). Echoed back for callers
            that don't already track it.
    """
    in_direction: FairValueGap | None = None
    counter_direction: FairValueGap | None = None
    in_distance_pct: float | None = None
    counter_distance_pct: float | None = None
    suggested_direction: str = ""


@dataclass
class NearestOBResult:
    """Result of ``StructureEngine._find_nearest_ob`` (XRAY counter-setup Phase 3).

    Mirror of ``NearestFVGResult`` for fresh OBs. See that docstring for
    the full contract rationale. Counter-direction OB is necessary
    because Phase 4's ``*_FVG_OB_COUNTER`` branches require BOTH a
    counter-direction unfilled FVG AND a counter-direction fresh OB
    to fire — same compound prerequisite as the in-direction branches.
    """
    in_direction: OrderBlock | None = None
    counter_direction: OrderBlock | None = None
    in_distance_pct: float | None = None
    counter_distance_pct: float | None = None
    suggested_direction: str = ""


@dataclass
class LiquidityZone:
    """A liquidity zone — cluster of stops at equal highs/lows or round numbers.

    ``swept_at`` records the candle index of the violation (wick beyond
    level). ``reclaimed_at`` records the candle index of the reclaim
    (close back through the level on a later bar). Both populated by
    ``LiquidityMapper._check_swept`` when the zone matches the canonical
    SMC sweep+reclaim pattern within the recency window. ``reclaimed_at``
    stays ``None`` for unswept zones and for zones whose violation has
    no corresponding reclaim in the window — the latter signals an
    *in-progress* sweep where the zone's stops were taken but price has
    not yet rotated, which downstream consumers may treat differently
    from a fully closed sweep.
    """
    zone_type: str = ""  # "buy_side" or "sell_side"
    level: float = 0.0  # price level where liquidity concentrates
    zone_high: float = 0.0
    zone_low: float = 0.0
    strength: float = 0.0  # 0.0-5.0
    source: str = ""  # "equal_highs", "equal_lows", "round_number"
    equal_count: int = 0  # how many equal highs/lows form cluster
    swept: bool = False
    swept_at: float = 0.0  # candle index of the violation bar
    # Phase 1c — XRAY confidence reachability fix. Index of the reclaim
    # bar (close back through level) that closes the sweep cycle. None
    # when the zone is unswept OR when violation has no corresponding
    # reclaim in the recency window.
    reclaimed_at: float | None = None
    created_at: float = 0.0  # timestamp or epoch of creation
    timeframe: str = "60"

    def to_dict(self) -> dict:
        return {
            "zone_type": self.zone_type,
            "level": self.level,
            "zone_high": self.zone_high,
            "zone_low": self.zone_low,
            "strength": round(self.strength, 2),
            "source": self.source,
            "equal_count": self.equal_count,
            "swept": self.swept,
            "swept_at": self.swept_at,
            "reclaimed_at": self.reclaimed_at,
        }


@dataclass
class LiquiditySweep:
    """A detected liquidity sweep — wick beyond a zone followed by reversal."""
    sweep_type: str = ""  # "bullish_sweep" or "bearish_sweep"
    level_swept: float = 0.0
    wick_extreme: float = 0.0  # how far past the level the wick went
    sweep_depth_pct: float = 0.0  # depth as percentage of price
    reversal_candle_index: int = 0
    reversal_strength: str = "weak"  # "strong", "moderate", "weak"
    reversal_ratio: float = 0.0  # raw body-to-range ratio
    reversal_body_pct: float = 0.0  # body as % of price
    timestamp: float = 0.0  # candle index of sweep
    age_candles: int = 0  # how many candles ago
    signal: str = ""  # "high_probability_long" | "moderate_long" | "weak_long" | mirror-short
    timeframe: str = "60"
    associated_zone: LiquidityZone | None = None

    def to_dict(self) -> dict:
        return {
            "sweep_type": self.sweep_type,
            "level_swept": self.level_swept,
            "wick_extreme": self.wick_extreme,
            "sweep_depth_pct": round(self.sweep_depth_pct, 4),
            "reversal_candle_index": self.reversal_candle_index,
            "reversal_strength": self.reversal_strength,
            "reversal_body_pct": round(self.reversal_body_pct, 4),
            "signal": self.signal,
        }


@dataclass
class VolumeProfile:
    """Volume-at-price distribution — identifies price magnets and air pockets."""
    poc: float = 0.0  # Point of Control (highest volume price)
    poc_volume: float = 0.0
    value_area_high: float = 0.0  # upper boundary of 70% volume
    value_area_low: float = 0.0  # lower boundary of 70% volume
    value_area_pct: float = 70.0
    high_volume_nodes: list = field(default_factory=list)  # [(price, rel_vol), ...]
    low_volume_nodes: list = field(default_factory=list)  # [(price_start, price_end), ...]
    current_vs_poc: str = "at_poc"  # "above_poc", "at_poc", "below_poc"
    current_vs_value_area: str = "inside_va"  # "above_va", "inside_va", "below_va"
    num_bins: int = 50
    timeframe: str = "60"

    def to_dict(self) -> dict:
        return {
            "poc": round(self.poc, 2) if self.poc > 0 else None,
            "value_area_high": round(self.value_area_high, 2),
            "value_area_low": round(self.value_area_low, 2),
            "current_vs_poc": self.current_vs_poc,
            "current_vs_value_area": self.current_vs_value_area,
            "hvn_count": len(self.high_volume_nodes),
            "lvn_count": len(self.low_volume_nodes),
        }


@dataclass
class FibSwing:
    """Fibonacci retracement/extension levels for one significant swing."""
    swing_low: float = 0.0
    swing_high: float = 0.0
    swing_direction: str = "up"  # "up" or "down"
    swing_range: float = 0.0
    swing_range_pct: float = 0.0
    retracement_levels: dict = field(default_factory=dict)  # {"0.382": price, ...}
    extension_levels: dict = field(default_factory=dict)  # {"1.618": price, ...}
    key_level: float | None = None  # single most important Fib level
    confluence_with: str | None = None  # "support_$72000 + OB_$71800"
    confluence_level: float | None = None
    timeframe: str = "60"

    def to_dict(self) -> dict:
        return {
            "swing_low": round(self.swing_low, 2),
            "swing_high": round(self.swing_high, 2),
            "swing_direction": self.swing_direction,
            "swing_range_pct": round(self.swing_range_pct, 2),
            "retracement_levels": {k: round(v, 2) for k, v in self.retracement_levels.items()},
            "extension_levels": {k: round(v, 2) for k, v in self.extension_levels.items()},
            "key_level": round(self.key_level, 2) if self.key_level else None,
            "confluence_with": self.confluence_with,
        }


@dataclass
class TFStructureView:
    """Issue #5 (2026-05-31): a lightweight structural summary of ONE higher
    timeframe (H4 or D1), produced by StructureEngine.analyze_direction_only and
    consumed by MTFConfluenceScorer to score cross-timeframe direction agreement.

    The cross-TF agreement scorer reads `structure`, `last_bos_direction` and
    `has_data`; the remaining fields (`direction`, nearest level prices,
    `current_price`) are carried for observability/logging and for a future
    "at higher-TF level" confluence factor. `has_data` is the explicit
    "thin/missing higher-TF" sentinel — when False the scorer excludes this TF
    from the agreement so behaviour degrades gracefully to H1-only.
    """
    timeframe: str = ""              # "240" (H4) or "D" (D1)
    structure: str = "unknown"       # uptrend / downtrend / ranging / unknown
    direction: str = ""              # long / short / "" (derived from structure)
    last_bos_direction: str = ""     # bullish / bearish / ""
    nearest_support: float = 0.0
    nearest_resistance: float = 0.0
    current_price: float = 0.0
    has_data: bool = False


@dataclass
class MTFConfluence:
    """Multi-timeframe confluence analysis result."""
    timeframe_analyses: dict = field(default_factory=dict)  # {tf: {bias, at_level, trigger}}
    direction_alignment: str = "mixed"  # "fully_aligned", "mostly_aligned", "mixed", "conflicting"
    aligned_direction: str | None = None  # "long", "short", or None
    score: int = 0  # 0-10
    quality: str = "none"  # "maximum" (8-10), "good" (5-7), "weak" (3-4), "none" (0-2)
    missing_factors: list[str] = field(default_factory=list)
    strongest_timeframe: str = ""
    weakest_timeframe: str = ""
    # Issue 3 (structure confluence, 2026-06-06) — each higher timeframe's OWN
    # structural bias ("long"/"short"/""), derived from its trend (or last BOS when
    # ranging). Surfaced so the scanner's interestingness confluence anchor-count
    # can include H4 and D1 agreement, not only H1. Stays "" when the higher-TF
    # feature (structure.mtf_multi_timeframe_enabled) is off — then no anchor added.
    h4_bias: str = ""
    d1_bias: str = ""

    def to_dict(self) -> dict:
        return {
            "direction_alignment": self.direction_alignment,
            "aligned_direction": self.aligned_direction,
            "score": self.score,
            "quality": self.quality,
            "missing_factors": self.missing_factors,
            "strongest_timeframe": self.strongest_timeframe,
            "weakest_timeframe": self.weakest_timeframe,
            "h4_bias": self.h4_bias,
            "d1_bias": self.d1_bias,
        }


@dataclass
class SessionContext:
    """Current institutional trading session state."""
    current_session: str = ""  # "asian", "london", "new_york", "late_ny"
    session_phase: str = ""  # "early", "mid", "late"
    session_start_utc: str = ""  # e.g., "08:00 UTC"
    session_elapsed_minutes: int = 0
    session_remaining_minutes: int = 0
    asian_range_high: float | None = None
    asian_range_low: float | None = None
    asian_range_broken: str | None = None  # "broken_above", "broken_below", "both_broken", None
    previous_session_high: float | None = None
    previous_session_low: float | None = None
    manipulation_likely: bool = False
    trading_recommendation: str = ""
    next_session: str = ""
    next_session_starts_in_minutes: int = 0

    def to_dict(self) -> dict:
        return {
            "current_session": self.current_session,
            "session_phase": self.session_phase,
            "session_elapsed_minutes": self.session_elapsed_minutes,
            "session_remaining_minutes": self.session_remaining_minutes,
            "asian_range_high": self.asian_range_high,
            "asian_range_low": self.asian_range_low,
            "asian_range_broken": self.asian_range_broken,
            "manipulation_likely": self.manipulation_likely,
            "trading_recommendation": self.trading_recommendation,
            "next_session": self.next_session,
        }


@dataclass
class StructuralSetup:
    """A ranked structural setup from the Setup Scanner."""
    symbol: str = ""
    rank: int = 0
    setup_score: int = 0
    setup_quality: str = "SKIP"
    confluence_score: int = 0
    confluence_quality: str = "none"
    total_confluence_factors: int = 0
    suggested_direction: str = ""
    entry_quality: str = "mid_range"
    rr_ratio: float = 0.0
    rr_quality: str = "skip"
    structural_sl: float = 0.0
    structural_tp: float = 0.0
    active_signals: list[str] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)
    setup_description: str = ""
    session_favorable: bool = True
    ranking_score: float = 0.0  # composite score used for sorting

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "rank": self.rank,
            "setup_score": self.setup_score,
            "setup_quality": self.setup_quality,
            "confluence_score": self.confluence_score,
            "confluence_quality": self.confluence_quality,
            "suggested_direction": self.suggested_direction,
            "rr_ratio": round(self.rr_ratio, 2),
            "rr_quality": self.rr_quality,
            "active_signals": self.active_signals,
            "missing_signals": self.missing_signals,
            "setup_description": self.setup_description,
            "session_favorable": self.session_favorable,
        }


@dataclass
class StructuralAnalysis:
    """Complete X-RAY structural analysis for one symbol at one point in time.

    This is the MAIN top-level output cached per symbol and read by all
    downstream consumers (scorer, Claude context, SL/TP validator, APEX, etc.).
    """
    symbol: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    current_price: float = 0.0

    # Phase 1: Support & Resistance
    support_levels: list[PriceLevel] = field(default_factory=list)
    resistance_levels: list[PriceLevel] = field(default_factory=list)
    nearest_support: PriceLevel | None = None
    nearest_resistance: PriceLevel | None = None
    position_in_range: float = 0.5  # 0.0 at support, 1.0 at resistance
    # Four-Element Prompt Recalibration, Element 3 (2026-06-11) —
    # pre-clamp range truth. position_in_range stays clamped 0..1 for
    # the bounds-assuming consumers; these two fields carry what the
    # clamp discards so a breakdown below the range is distinguishable
    # from a price sitting at the range low (June-11 DYDX read 0.00 on
    # all 24 submissions while price fell THROUGH the range).
    # range_breakout: "" (in range) | "below" | "above".
    # range_overshoot_pct: unsigned magnitude of the break as a percent
    # of the broken boundary's price (price-denominated like ATR%).
    range_breakout: str = ""
    range_overshoot_pct: float = 0.0

    # Phase 2: Market Structure
    market_structure: MarketStructureResult = field(default_factory=MarketStructureResult)

    # Phase 3: Structural SL/TP
    structural_placement: StructuralPlacement | None = None

    # Overall quality
    setup_score: int = 0  # 0-100
    setup_quality: str = "SKIP"  # "A+", "A", "B", "C", "SKIP"
    suggested_direction: str = ""  # "long", "short", or ""

    # Phase 4: Fair Value Gaps
    fvgs: list[FairValueGap] = field(default_factory=list)
    nearest_fvg: FairValueGap | None = None
    # XRAY counter-setup Phase 3 — counter-direction nearest unfilled FVG.
    # Set by StructureEngine._find_nearest_fvg when a coin has no
    # in-direction zone but has an opposite-direction zone within the
    # ATR-scaled window. Phase 4's classifier reads this to emit
    # BULLISH_FVG_OB_COUNTER / BEARISH_FVG_OB_COUNTER. None when no
    # counter-direction zone is in range.
    nearest_fvg_counter: FairValueGap | None = None

    # Phase 5: Order Blocks
    order_blocks: list[OrderBlock] = field(default_factory=list)
    nearest_ob: OrderBlock | None = None
    # XRAY counter-setup Phase 3 — counter-direction nearest fresh OB.
    # Phase 4 requires BOTH counter-direction FVG AND counter-direction
    # OB present to emit a *_FVG_OB_COUNTER setup. None when no
    # counter-direction fresh OB is in range.
    nearest_ob_counter: OrderBlock | None = None

    # Phase 6: Liquidity Zones
    liquidity_zones: list[LiquidityZone] = field(default_factory=list)
    nearest_unswept_liquidity: LiquidityZone | None = None

    # Phase 7: Liquidity Sweeps
    recent_sweeps: list[LiquiditySweep] = field(default_factory=list)
    active_sweep_signal: LiquiditySweep | None = None

    # Smart Money Concepts confluence
    smc_confluence: int = 0  # 0-100 composite SMC score
    # XRAY phase-1 fix — per-component SMC contribution captured at
    # ``_compute_smc_confluence`` time so ``classify_setup`` (and the
    # forensic ``XRAY_CONFIDENCE_DETAIL`` log) can show each branch's
    # contribution without re-iterating the candidate lists. Keys:
    #   "fvg" (0 or 25), "ob" (0 or 30),
    #   "liq" (0 or 15), "sweep" (0 or 30).
    # Empty dict on legacy paths or when smc_confluence wasn't computed
    # (e.g., classify_setup called on a stub analysis).
    smc_breakdown: dict[str, int] = field(default_factory=dict)

    # Phase 3a: Volume Profile
    volume_profile: VolumeProfile | None = None
    poc_price: float | None = None  # convenience: volume_profile.poc

    # Phase 3b: Fibonacci
    fibonacci: FibSwing | None = None
    fib_key_level: float | None = None  # convenience: fibonacci.key_level

    # Phase 3c: Multi-Timeframe Confluence
    mtf_confluence: MTFConfluence | None = None
    mtf_confluence_score: int = 0  # convenience: mtf_confluence.score
    confluence_quality: str = "none"  # convenience: mtf_confluence.quality
    total_confluence_factors: int = 0  # count of independent factors agreeing

    # Phase 4: Session Timing + Setup Scanner
    session_context: SessionContext | None = None
    is_setup: bool = False  # qualifies as tradeable setup
    setup_rank: int | None = None  # rank if is_setup=True

    # Layer 1 restructure Phase 2 — categorical pattern classification.
    # ``setup_score`` (above) stays as the numeric score; ``setup_type``
    # provides the categorical evidence ScannerWorker's Phase 5 qualitative
    # filter requires. Defaults preserve backward-compat for any consumer
    # that constructs a StructuralAnalysis without the new fields.
    setup_type: SetupType = SetupType.NONE
    setup_type_confidence: float = 0.0  # 0.0..1.0; 0 = NONE or no signal

    # XRAY counter-setup Phase 4 — trade direction implied by the
    # categorical setup_type. For in-direction setups (BULLISH_FVG_OB,
    # BEARISH_FVG_OB, *_STRUCTURAL_BREAK, *_LIQUIDITY_SWEEP, *_RANGE_*)
    # trade_direction equals suggested_direction. For counter setups
    # (BULLISH_FVG_OB_COUNTER, BEARISH_FVG_OB_COUNTER) trade_direction
    # is the OPPOSITE of suggested_direction — the trade plays against
    # the structural bias because the in-direction zones are missing
    # but the opposite-direction zones are present near price.
    #
    # Downstream consumers (TradeScorer, ScannerWorker._qualifies,
    # brain prompt) should read trade_direction when they need to know
    # which side the structural setup recommends. suggested_direction
    # remains the raw market_structure-derived label.
    #
    # Empty string when setup_type == NONE (no trade implied).
    trade_direction: str = ""

    # XRAY counter-setup Phase 2 — H1 NATR captured at analyze() time so
    # _find_nearest_fvg/ob can size their distance windows by volatility
    # rather than fixed percentages. Computed inline in
    # ``StructureEngine._compute_h1_natr_pct`` so the structure pipeline
    # is independent of the volatility_profile worker (which is a
    # separate cache with its own cold-start path). Value is the
    # 14-bar mean true range divided by current price, expressed as
    # percent (typical range 0.3% - 2.0%). 0.0 when insufficient candles.
    atr_pct_h1: float = 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "current_price": self.current_price,
            "support_levels": [s.to_dict() for s in self.support_levels],
            "resistance_levels": [r.to_dict() for r in self.resistance_levels],
            "nearest_support": self.nearest_support.to_dict() if self.nearest_support else None,
            "nearest_resistance": self.nearest_resistance.to_dict() if self.nearest_resistance else None,
            "position_in_range": round(self.position_in_range, 4),
            "range_breakout": self.range_breakout,
            "range_overshoot_pct": round(self.range_overshoot_pct, 2),
            "market_structure": self.market_structure.to_dict(),
            "structural_placement": self.structural_placement.to_dict() if self.structural_placement else None,
            "setup_score": self.setup_score,
            "setup_quality": self.setup_quality,
            "suggested_direction": self.suggested_direction,
            # Phase 2: Smart Money Concepts
            "fvgs": [f.to_dict() for f in self.fvgs],
            "order_blocks": [ob.to_dict() for ob in self.order_blocks],
            "liquidity_zones": [lz.to_dict() for lz in self.liquidity_zones],
            "recent_sweeps": [sw.to_dict() for sw in self.recent_sweeps],
            "nearest_fvg": self.nearest_fvg.to_dict() if self.nearest_fvg else None,
            "nearest_ob": self.nearest_ob.to_dict() if self.nearest_ob else None,
            "nearest_unswept_liquidity": self.nearest_unswept_liquidity.to_dict() if self.nearest_unswept_liquidity else None,
            "active_sweep_signal": self.active_sweep_signal.to_dict() if self.active_sweep_signal else None,
            "smc_confluence": self.smc_confluence,
            # Per-component breakdown matched to smc_confluence; empty dict
            # on legacy paths. Surfaced in dict view so brain prompt can
            # render fvg/ob/liq/sweep contributions per coin.
            "smc_breakdown": dict(self.smc_breakdown),
            # Phase 3: Confluence
            "volume_profile": self.volume_profile.to_dict() if self.volume_profile else None,
            "fibonacci": self.fibonacci.to_dict() if self.fibonacci else None,
            "mtf_confluence": self.mtf_confluence.to_dict() if self.mtf_confluence else None,
            "poc_price": round(self.poc_price, 2) if self.poc_price else None,
            "fib_key_level": round(self.fib_key_level, 2) if self.fib_key_level else None,
            "confluence_quality": self.confluence_quality,
            "total_confluence_factors": self.total_confluence_factors,
            # Phase 4: Session + Scanner
            "session_context": self.session_context.to_dict() if self.session_context else None,
            "is_setup": self.is_setup,
            "setup_rank": self.setup_rank,
            # Layer 1 restructure Phase 2: categorical setup classification.
            "setup_type": self.setup_type.value,
            "setup_type_confidence": round(self.setup_type_confidence, 4),
            # XRAY counter-setup Phase 2/3/4 — feature parity between
            # StructuralAnalysis (object) and to_dict() (dict). Strategy
            # Worker passes ``analysis.to_dict()`` to TradeScorer via
            # ``structural_map``; counter-setup downstream consumers
            # need these fields present in the dict view too.
            "atr_pct_h1": round(self.atr_pct_h1, 4),
            "trade_direction": self.trade_direction,
            "nearest_fvg_counter": (
                self.nearest_fvg_counter.to_dict()
                if self.nearest_fvg_counter else None
            ),
            "nearest_ob_counter": (
                self.nearest_ob_counter.to_dict()
                if self.nearest_ob_counter else None
            ),
        }
