"""APEX data models: dataclasses for the 4-section intelligence package and optimized trade output.

All dataclasses follow the TIAS convention: plain @dataclass, Optional[T] = None for
nullable fields, grouped with comments. No SerializableMixin — these are internal
data containers used only within the APEX pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from src.core.utils import format_price


# Layer 1 Defect 7 — single authoritative TP-cap multiplier map used
# when rendering the TP_CAP value into DeepSeek's prompt. Must mirror
# APEXSettings.tp_cap_multiplier_by_class (settings.py:2237) and the
# optimizer's fallback at optimizer.py:319. The optimizer's __init__
# boot self-check (BOOT_TP_CAP_RECONCILED / _MISMATCH) verifies all
# three sites agree so future drift surfaces loudly.
_CAP_MULT_MAP_DISPLAY: dict[str, float] = {
    "dead": 1.4, "low": 1.5, "medium": 1.6, "high": 1.8, "extreme": 2.0,
}


# =============================================================================
# SECTION 1 — Claude's trade directive (what Claude decided)
# =============================================================================

@dataclass
class DirectiveContext:
    """The incoming trade directive from Claude Brain that APEX will optimize.

    Captures both WHAT Claude wants to trade and WHY, so DeepSeek can
    respect the intent while optimizing the execution parameters.
    """

    # --- Required: core trade parameters ---
    symbol: str
    direction: str          # "Buy" or "Sell"
    sl: float               # Claude's stop-loss price
    tp: float               # Claude's take-profit price
    leverage: float         # Claude's leverage (e.g. 3)
    size_usd: float         # Claude's position size in USD

    # --- Required: Claude's reasoning ---
    reasoning: str          # Claude's STRAT_DIRECTIVE reasoning text
    plan_view: str          # Claude's market view / STRAT_PLAN summary

    # --- Optional: strategy metadata ---
    signal_score: Optional[float] = None
    strategy_name: Optional[str] = None


# =============================================================================
# SECTION 2 — Current coin state (deep real-time data for this one coin)
# =============================================================================

@dataclass
class CoinData:
    """Real-time technical, Mode4, and orderbook data for a single coin.

    This is the MICROSCOPIC view that Claude never sees — full TA precision
    on one coin rather than one-line summaries across 20+ coins.

    Has a format() method to render as human-readable text for DeepSeek prompt inclusion.
    """

    # --- Required ---
    symbol: str
    current_price: float

    # --- Technical indicators ---
    change_24h: Optional[float] = None
    rsi: Optional[float] = None
    macd_line: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    adx: Optional[float] = None
    bollinger_pct: Optional[float] = None       # price position within BB (0-100%)
    stochastic_k: Optional[float] = None
    stochastic_d: Optional[float] = None
    ema_20: Optional[float] = None
    ema_50: Optional[float] = None
    ema_trend: Optional[str] = None             # "bullish" / "bearish"
    atr: Optional[float] = None                 # ATR in price units
    atr_pct: Optional[float] = None             # ATR as % of current price
    volume_ratio: Optional[float] = None        # current vol / average vol

    # --- Mode4 profit-sniper metrics (if position is open) ---
    m4_hurst: Optional[float] = None            # Hurst exponent (>0.5 = trending)
    m4_momentum: Optional[float] = None         # momentum decay score (0-100)
    m4_extension: Optional[float] = None        # ATR extension from entry
    m4_volume_div: Optional[float] = None       # volume divergence score (0-100)
    m4_ev: Optional[float] = None               # expected value ratio to hold
    m4_composite: Optional[float] = None        # composite exit pressure (0-100)
    m4_trail_sl: Optional[float] = None         # current trailing stop-loss price

    # --- Orderbook snapshot ---
    bid_depth: Optional[float] = None           # total bid quantity (top 5 levels)
    ask_depth: Optional[float] = None           # total ask quantity (top 5 levels)
    book_imbalance_pct: Optional[float] = None  # (bid-ask)/(bid+ask)*100 — positive = buy pressure

    # --- Volatility profile (per-coin adaptive parameters) ---
    volatility_class: Optional[str] = None        # "dead"/"low"/"medium"/"high"/"extreme"
    recommended_tp_pct: Optional[float] = None    # volatility-adjusted TP recommendation
    recommended_sl_pct: Optional[float] = None    # volatility-adjusted SL recommendation
    recommended_hold_min: Optional[int] = None    # volatility-adjusted hold time
    recommended_strategy: Optional[str] = None    # "scalp"/"mean_revert"/"breakout"/"momentum"/"trend_follow"

    def format(self) -> str:
        """Render coin data as human-readable text for DeepSeek prompt inclusion."""
        lines = [f"Symbol: {self.symbol} @ ${self.current_price}"]
        if self.change_24h is not None:
            lines.append(f"24h change: {self.change_24h:+.2f}%")
        if self.rsi is not None:
            lines.append(f"RSI(14): {self.rsi:.1f}")
        if self.macd_hist is not None:
            lines.append(f"MACD histogram: {self.macd_hist:+.6f}")
        if self.macd_signal is not None:
            lines.append(f"MACD signal: {self.macd_signal:+.6f}")
        if self.adx is not None:
            lines.append(f"ADX: {self.adx:.1f}")
        if self.atr_pct is not None:
            lines.append(f"ATR: {self.atr_pct:.2f}% of price (${self.atr or 0:.6f})")
        if self.bollinger_pct is not None:
            lines.append(f"Bollinger position: {self.bollinger_pct:.0f}%")
        if self.ema_trend:
            lines.append(
                f"EMA trend: {self.ema_trend}"
                + (f" (20={self.ema_20:.6f}, 50={self.ema_50:.6f})"
                   if self.ema_20 and self.ema_50 else "")
            )
        if self.stochastic_k is not None:
            lines.append(f"Stochastic: K={self.stochastic_k:.1f} D={self.stochastic_d:.1f}")
        if self.volume_ratio is not None:
            lines.append(f"Volume ratio: {self.volume_ratio:.2f}x average")
        if self.m4_composite is not None:
            lines.append(
                f"Mode4: composite={self.m4_composite:.1f}/100 "
                f"hurst={self.m4_hurst:.3f} "
                f"momentum={self.m4_momentum:.1f} "
                f"extension={self.m4_extension:.2f}ATR"
                if self.m4_hurst and self.m4_momentum and self.m4_extension
                else f"Mode4: composite={self.m4_composite:.1f}/100"
            )
        if self.m4_trail_sl is not None:
            lines.append(f"Mode4 trail SL: ${self.m4_trail_sl:.6f}")
        if self.bid_depth is not None:
            lines.append(
                f"Orderbook: bid={self.bid_depth:.0f} ask={self.ask_depth:.0f} "
                f"imbalance={self.book_imbalance_pct:+.1f}%"
                if self.ask_depth is not None and self.book_imbalance_pct is not None
                else f"Orderbook: bid_depth={self.bid_depth:.0f}"
            )
        if self.volatility_class:
            vp_parts = [f"Volatility: {self.volatility_class.upper()}"]
            if self.recommended_tp_pct is not None and self.recommended_sl_pct is not None:
                # Per-class TP cap multiplier — must match
                # APEXSettings.tp_cap_multiplier_by_class (settings.py:2237)
                # and the optimizer's enforcement map at optimizer.py:319.
                # Layer 1 Defect 7 (2026-05-21) corrected the historical
                # drift: this dict was {1.2,1.3,1.3,1.4,1.5} while the
                # optimizer enforced {1.4,1.5,1.6,1.8,2.0} from settings,
                # so DeepSeek saw a tighter TP_CAP in the prompt than the
                # optimizer would have allowed and self-limited its TP
                # recommendations. Aligning the prompt-displayed value
                # to the optimizer enforcement delivers the larger-TP
                # intent introduced in dir-block-fix Phase 5 (2026-05-05).
                # The APEX __init__ boot self-check
                # (BOOT_TP_CAP_RECONCILED / _MISMATCH) loud-errors on
                # any future drift.
                _mult = _CAP_MULT_MAP_DISPLAY.get(self.volatility_class, 1.6)
                tp_cap = round(self.recommended_tp_pct * _mult, 2)
                vp_parts.append(
                    f"(recTP={self.recommended_tp_pct:.1f}% "
                    f"recSL={self.recommended_sl_pct:.1f}% "
                    f"TP_CAP={tp_cap:.1f}% "
                    f"hold={self.recommended_hold_min or '?'}min "
                    f"strategy={self.recommended_strategy or '?'})"
                )
                lines.append(" ".join(vp_parts))
                lines.append(
                    f"  TP HARD CAP: Do NOT set TP above {tp_cap:.1f}% "
                    f"({_mult:.1f}x recTP for {self.volatility_class} class) "
                    f"— coin volatility cannot support higher."
                )
            else:
                lines.append(" ".join(vp_parts))
        return "\n".join(lines)


# =============================================================================
# SECTION 3 — TIAS symbol history (what the data shows for this coin)
# =============================================================================

@dataclass
class TIASSymbolHistory:
    """TIAS historical performance data for a specific symbol.

    Populated by IntelligenceAssembler from the trade_intelligence table.
    Gives DeepSeek the REAL trading record for this coin — not theory.
    """

    symbol: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float             # 0.0-100.0 (percentage from TIAS repo SQL)
    avg_win_pct: float          # average winning trade PnL %
    avg_loss_pct: float         # average losing trade PnL % (negative)
    total_pnl_usd: float        # cumulative PnL in USD
    ev_per_trade: float         # expected value per trade in USD

    # Profit-factor metrics (added for APEX recalibration)
    profit_factor: float = 0.0  # total $ won / total $ lost
    avg_win_usd: float = 0.0    # average winning trade in USD
    avg_loss_usd: float = 0.0   # average losing trade in USD

    # Full list of trade dicts with ds_* analysis fields
    trades: list = field(default_factory=list)

    # Human-readable summary of patterns found across trades
    pattern_summary: str = ""

    # Regime this history was filtered for (empty = all-regime fallback)
    regime: str = ""


# =============================================================================
# SECTION 4 — TIAS situation data (what works in THESE conditions)
# =============================================================================

@dataclass
class TIASSituationData:
    """TIAS performance stats across ALL coins in similar market conditions.

    Tells DeepSeek: in this regime + F&G range, historically which direction
    wins, what TP ranges succeed, and what failure patterns dominate.
    """

    regime: str                         # current market regime
    fear_greed: int                     # current F&G value (0-100)
    total_trades_in_condition: int      # trades in similar conditions
    buy_win_rate: float                 # 0.0-100.0 (percentage, not ratio)
    sell_win_rate: float                # 0.0-100.0
    avg_buy_pnl: float                  # average Buy PnL %
    avg_sell_pnl: float                 # average Sell PnL %
    direction_bias: str                 # "buy" / "sell" / "neutral"

    # TP bucket performance: [{"tp_bucket": "<1%", "win_rate": 0.65}, ...]
    tp_performance: list = field(default_factory=list)

    # Most common DeepSeek failure categories in these conditions
    common_categories: list = field(default_factory=list)

    # Human-readable summary of situation
    condition_summary: str = ""


# =============================================================================
# SECTION 5 — X-RAY Structural Intelligence
# =============================================================================

@dataclass
class StructuralData:
    """X-RAY structural analysis data for APEX optimization.

    Provides Smart Money Concepts context: support/resistance, market
    structure, FVGs, Order Blocks, Liquidity, and Sweeps to help DeepSeek
    optimize entries with structural awareness.
    """

    symbol: str = ""
    current_price: float = 0.0
    setup_quality: str = "SKIP"
    setup_score: int = 0
    suggested_direction: str = ""
    position_in_range: float = 0.5
    # Four-Element Prompt Recalibration, Element 3 (2026-06-11) —
    # pre-clamp range truth mirrored from StructuralAnalysis.
    # position_in_range stays clamped; these carry the discarded
    # overshoot so a breakdown below the range never again reads as
    # "sitting at the range low". range_breakout: "" | "below" |
    # "above"; range_overshoot_pct: unsigned percent of the broken
    # boundary's price. Populated by _gather_structural_data_from_cache.
    range_breakout: str = ""
    range_overshoot_pct: float = 0.0

    # PRIMARY Sell-Bias Fix (2026-05-11) — counter-trade detection.
    # Mirrors ``StructureAnalysis.setup_type`` (a SetupType enum's
    # ``.value`` string such as "BULLISH_FVG_OB" or
    # "BEARISH_FVG_OB_COUNTER"). APEX reads this to detect deliberate
    # contrarian setups it must not flip. Populated by
    # ``_gather_structural_data_from_cache`` in src/apex/assembler.py.
    # Empty string when no setup type is available.
    setup_type: str = ""

    # R1 direction-fix (2026-05-17) — counter-aware trade direction.
    # ``classify_setup`` in structure_engine.py inverts trade_direction
    # vs suggested_direction for counter setups
    # (BULLISH_FVG_OB_COUNTER -> trade_direction="long" even when
    # suggested_direction="short"). The brain prompt already reads this
    # counter-inverted field; APEX previously read only
    # suggested_direction (the regime label) and was blind to the counter
    # signal at the direction-lock decision. This field exposes the
    # setup-payoff direction so APEX can consult it. Empty string when
    # no setup is present or trade_direction was not set.
    trade_direction: str = ""

    # S/R
    nearest_support: Optional[float] = None
    nearest_support_strength: Optional[float] = None
    nearest_resistance: Optional[float] = None
    nearest_resistance_strength: Optional[float] = None

    # Market structure
    structure: Optional[str] = None
    structure_strength: Optional[str] = None
    last_bos: Optional[str] = None
    rr_ratio: Optional[float] = None        # Backward compat — equals rr_best
    rr_quality: Optional[str] = None
    rr_long: Optional[float] = None          # R:R for LONG direction
    rr_short: Optional[float] = None         # R:R for SHORT direction
    rr_best_direction: Optional[str] = None  # "long" or "short"

    # Smart Money
    nearest_fvg_direction: Optional[str] = None
    nearest_fvg_range: Optional[str] = None
    nearest_ob_direction: Optional[str] = None
    nearest_ob_range: Optional[str] = None
    nearest_ob_fresh: Optional[bool] = None
    nearest_ob_score: Optional[float] = None
    active_sweep_signal: Optional[str] = None
    smc_confluence: int = 0
    unswept_liquidity_level: Optional[float] = None

    # Phase 3: Confluence
    poc_price: Optional[float] = None
    poc_vs_current: Optional[str] = None  # "above_poc", "below_poc", "at_poc"
    fib_key_level: Optional[float] = None
    fib_confluence: Optional[str] = None  # "support_$X + OB_$Y"
    mtf_score: Optional[int] = None  # 0-10
    mtf_quality: Optional[str] = None  # "maximum", "good", "weak", "none"
    total_confluence_factors: int = 0

    # Phase 4: Session + Scanner
    session: Optional[str] = None  # "london", "new_york", etc.
    session_phase: Optional[str] = None  # "early", "mid", "late"
    session_recommendation: Optional[str] = None
    setup_rank: Optional[int] = None

    def format(self) -> str:
        """Render structural data as human-readable text for DeepSeek prompt."""
        lines = [f"X-RAY Setup: {self.setup_quality} (score {self.setup_score}/100)"]

        if self.structure:
            lines.append(f"Structure: {self.structure} ({self.structure_strength})")
        if self.nearest_support is not None:
            lines.append(
                f"Nearest support: ${self.nearest_support:,.2f} "
                f"(strength {self.nearest_support_strength:.1f}/5)"
                if self.nearest_support_strength else
                f"Nearest support: ${self.nearest_support:,.2f}"
            )
        if self.nearest_resistance is not None:
            lines.append(
                f"Nearest resistance: ${self.nearest_resistance:,.2f} "
                f"(strength {self.nearest_resistance_strength:.1f}/5)"
                if self.nearest_resistance_strength else
                f"Nearest resistance: ${self.nearest_resistance:,.2f}"
            )
        # Element 3 (2026-06-11) — the clamped percent alone disguised a
        # breakdown as a floor; append the pre-clamp truth when the price
        # is outside the range. Keyed off field presence (the assembler
        # has no settings object): an in-range coin renders the legacy
        # line byte-identically.
        _pir_line = f"Position in range: {self.position_in_range:.0%}"
        if self.range_breakout:
            _pir_word = (
                "BELOW the range low" if self.range_breakout == "below"
                else "ABOVE the range high"
            )
            _pir_line += (
                f" (price is {_pir_word} by {self.range_overshoot_pct:.1f}%)"
            )
        lines.append(_pir_line)
        if self.rr_ratio:
            if self.rr_long is not None and self.rr_short is not None:
                best_l = "<<<" if self.rr_best_direction == "long" else ""
                best_s = "<<<" if self.rr_best_direction == "short" else ""
                lines.append(
                    f"R:R Long: 1:{self.rr_long:.1f}{best_l} | "
                    f"Short: 1:{self.rr_short:.1f}{best_s} ({self.rr_quality})"
                )
            else:
                lines.append(f"R:R ratio: 1:{self.rr_ratio:.1f} ({self.rr_quality})")
        if self.nearest_fvg_range:
            lines.append(f"FVG: {self.nearest_fvg_direction} {self.nearest_fvg_range}")
        if self.nearest_ob_range:
            fresh = "FRESH" if self.nearest_ob_fresh else "retested"
            lines.append(
                f"Order Block: {self.nearest_ob_direction} {self.nearest_ob_range} "
                f"({fresh}, score={self.nearest_ob_score:.0f})"
                if self.nearest_ob_score else
                f"Order Block: {self.nearest_ob_direction} {self.nearest_ob_range} ({fresh})"
            )
        if self.active_sweep_signal:
            lines.append(f"Active sweep: {self.active_sweep_signal}")
        if self.unswept_liquidity_level:
            lines.append(f"Unswept liquidity: ${format_price(self.unswept_liquidity_level)}")
        if self.smc_confluence > 0:
            lines.append(f"SMC confluence: {self.smc_confluence}/100")
        # Phase 3
        if self.poc_price:
            lines.append(f"Volume POC: ${format_price(self.poc_price)} ({self.poc_vs_current})")
        if self.fib_key_level:
            confl = f" ({self.fib_confluence})" if self.fib_confluence else ""
            lines.append(f"Fib key level: ${format_price(self.fib_key_level)}{confl}")
        if self.mtf_score is not None and self.mtf_score > 0:
            lines.append(f"MTF confluence: {self.mtf_score}/10 ({self.mtf_quality})")
        if self.total_confluence_factors > 0:
            lines.append(f"Total factors: {self.total_confluence_factors}")
        # Phase 4
        if self.session:
            lines.append(f"Session: {self.session} ({self.session_phase})")
        if self.session_recommendation:
            lines.append(f"Timing: {self.session_recommendation}")
        if self.setup_rank:
            lines.append(f"Scanner rank: #{self.setup_rank}")
        return "\n".join(lines)


# =============================================================================
# SYMBOL FLIP EVIDENCE — per-coin, per-venue directional history (E26)
# =============================================================================

@dataclass
class SymbolFlipEvidence:
    """Per-symbol, per-venue directional trade evidence (E26, 2026-05-28).

    Isolated by ``exchange_mode`` so a direction-flip decision and the APEX
    prompt use venue-consistent history instead of pooling demo/live/paper
    trades together. Populated by IntelligenceAssembler from the
    ``trade_intelligence`` table via
    ``TIASRepository.get_symbol_flip_evidence``. Consumed by:
      - the APEX flip insufficient-data gate (optimizer.py) — count of trades
        in the flipped direction, venue-isolated; and
      - the APEX user prompt (prompts.py) — per-coin directional win rate,
        rendered only when the venue sample is sufficient.

    ``exchange_mode == ""`` means NO venue filter was applied (pooled) — the
    fail-permissive case when the live mode is unknown; the flip gate then
    treats this evidence as non-authoritative and falls back to the
    regime-filtered (pooled) trades list.
    """

    symbol: str
    exchange_mode: str          # "" = pooled (no venue filter applied)
    regime: str                 # "" = all regimes
    buy_count: int = 0
    sell_count: int = 0
    buy_win_rate: float = 0.0   # 0.0-100.0 (percentage; 0.0 when no Buy trades)
    sell_win_rate: float = 0.0  # 0.0-100.0 (percentage; 0.0 when no Sell trades)
    total: int = 0

    def direction_count(self, direction: str) -> int:
        """Trade count in the given direction ("Buy"/"Sell"); 0 otherwise."""
        if direction == "Buy":
            return self.buy_count
        if direction == "Sell":
            return self.sell_count
        return 0


# =============================================================================
# INTELLIGENCE PACKAGE — the complete 5-section data dossier for one coin
# =============================================================================

@dataclass
class IntelligencePackage:
    """Complete 5-section intelligence package assembled by IntelligenceAssembler.

    Passed to the APEX prompt builder which formats it for DeepSeek.
    Each section may be partially populated if a data source failed — DeepSeek
    is designed to work with whatever is available.
    """

    directive: DirectiveContext         # Section 1: Claude's trade decision
    coin_data: CoinData                 # Section 2: current coin state
    symbol_history: TIASSymbolHistory   # Section 3: TIAS history for this coin
    situation_data: TIASSituationData   # Section 4: TIAS situation context
    structural_data: Optional[StructuralData] = None  # Section 5: X-RAY structural
    # E26 (2026-05-28): per-coin, per-venue directional evidence. None when
    # the assembler could not gather it; never required for construction.
    flip_evidence: Optional[SymbolFlipEvidence] = None


# =============================================================================
# OPTIMIZED TRADE — what DeepSeek returns
# =============================================================================

@dataclass
class OptimizedTrade:
    """APEX-optimized trade parameters returned by DeepSeek via DeepSeekClient.

    These REPLACE Claude's rough parameters before order execution.
    Tracking fields (was_flipped, original_*) record what changed
    for logging, analysis, and future TIAS capture.
    """

    # --- Optimized execution parameters (from DeepSeek JSON) ---
    symbol: str
    direction: str                      # "Buy" or "Sell" — may differ from Claude
    sl_pct: float                       # stop-loss as % from entry price
    tp_pct: float                       # take-profit as % from entry price
    tp_mode: str                        # "fixed" / "trail_only" / "partial_trail"
    position_size_usd: float            # optimized position size in USD
    leverage: int                       # optimized leverage
    entry_timing: str                   # "immediate" / "wait_pullback"
    add_on_pullback: bool               # add to position on pullback?

    # --- Add-on parameters (only if add_on_pullback is True) ---
    add_trigger_pct: Optional[float] = None   # pullback % to trigger add
    add_size_pct: Optional[int] = None        # % of original size to add

    # --- DeepSeek's explanation and conviction ---
    reasoning: str = ""
    confidence: float = 0.0             # 0.0-1.0

    # --- Tracking: what changed from Claude's original ---
    was_flipped: bool = False           # True if direction differs from Claude
    original_direction: Optional[str] = None
    original_sl: Optional[float] = None
    original_tp: Optional[float] = None
    original_size: Optional[float] = None

    # --- API metadata (set by DeepSeekClient, not by DeepSeek output) ---
    apex_response_time_ms: Optional[int] = None
    apex_cost_usd: Optional[float] = None
    apex_model: Optional[str] = None

    # --- Fallback flag (set when APEX failed, original params preserved) ---
    is_fallback: bool = False

    # --- Direction-lock state (Issue 1 fix, 2026-05-11). Mirrors the
    # APEX_DIR_LOCK decision made inside optimize() so downstream layers
    # (layer_manager merge → strategy_worker._execute_claude_trade) can
    # honor the lock. Inside APEX the lock is enforced via
    # APEX_DIR_LOCK_OVERRIDE; outside APEX, consumers should check these
    # fields before any further direction mutation. ---
    is_locked: bool = False
    lock_reason: str = ""
