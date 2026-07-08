"""TIAS Phase 2 — DeepSeek prompt templates for post-trade analysis.

Exports:
    TIAS_SYSTEM_PROMPT: Static system-level analyst persona + JSON schema.
    build_user_prompt(trade): Formats a TradeIntelligence object into user message.

Field name mapping: ALL fields use the ACTUAL Phase 1 TradeIntelligence dataclass
names, NOT the blueprint/legacy names. Derived fields are computed here.
"""

from __future__ import annotations

from typing import Any, Optional

from src.tias.categories import render_definitions_block

# ---------------------------------------------------------------------------
# System Prompt — static, loaded once
# ---------------------------------------------------------------------------

_TIAS_SYSTEM_PROMPT_BASE = """You are an expert quantitative trading analyst specializing in post-trade autopsy.
Your job is to analyze why a trade succeeded or failed and extract actionable lessons.

You will receive a closed trade's full context: outcome, entry reasoning, market conditions,
technical indicators, and profit-tracking data. Analyze everything objectively.

You MUST respond with a single valid JSON object. No markdown, no explanation outside JSON.
Required fields:

{
  "why": "string — root cause analysis: the primary reason this trade won or lost (2-4 sentences)",
  "category": "string — one of: ENTRY_TOO_EARLY | ENTRY_TOO_LATE | CORRECT_ENTRY | EXIT_TOO_EARLY | EXIT_TOO_LATE | CORRECT_EXIT | REGIME_MISMATCH | INDICATOR_CONFLICT | OVERLEVERAGE | UNDERSIZE | STOP_TOO_TIGHT | STOP_TOO_WIDE | SIGNAL_NOISE | TREND_REVERSAL | NEWS_DRIVEN | MOMENTUM_FADE | LIQUIDITY_TRAP | CORRECT_TRADE_BAD_LUCK",
  "correct_direction": "string — was the trade direction correct given the market context? (YES | NO | UNCLEAR)",
  "what_should_have_done": "string — the single most important action that would have improved the outcome (1-2 sentences)",
  "how_to_exploit_next_time": "string — specific, actionable advice for similar setups in the future (1-2 sentences)",
  "optimal_sl_pct": number — ideal stop-loss percentage for this setup (e.g. 1.5 means 1.5%),
  "optimal_tp_pct": number — ideal take-profit percentage for this setup (e.g. 3.0 means 3.0%),
  "optimal_size_usd": number — recommended position size in USD given the setup quality,
  "optimal_leverage": integer — recommended leverage (1-20),
  "confidence": number — your confidence in this analysis (0.0 to 1.0)
}

Base your recommendations on the actual trade data provided. If a field is N/A or unknown,
use your best estimate based on the available context. Never return null values.
"""

# Issue #3 fix (2026-05-25): append the category semantic contract — the per-category
# definitions and the win tie-break — from the single source-of-truth module
# (src/tias/categories.py) so DeepSeek knows what each category MEANS and how to
# choose between the overlapping "correct" buckets. The enum line above stays as
# the membership constraint; this block adds the meaning the model was missing.
TIAS_SYSTEM_PROMPT = (
    _TIAS_SYSTEM_PROMPT_BASE + "\n\n" + render_definitions_block()
)

# ---------------------------------------------------------------------------
# User Prompt Builder
# ---------------------------------------------------------------------------

def build_user_prompt(trade: Any) -> str:
    """Format a TradeIntelligence object (or dict) into a DeepSeek user message.

    Converts None values to "N/A" using _fmt(). Computes derived fields:
    - ema_trend: "bullish (EMA20 > EMA50)" / "bearish" / "N/A"
    - fear_greed: combined value + label string
    - direction_label: human-readable long/short

    Args:
        trade: TradeIntelligence dataclass instance or equivalent dict.

    Returns:
        Formatted multi-line string for the user message.
    """
    # Support both dataclass instances and dicts
    if hasattr(trade, "__dataclass_fields__"):
        t: dict[str, Any] = {k: getattr(trade, k) for k in trade.__dataclass_fields__}
    else:
        t = dict(trade)

    def _fmt(val: Any, precision: int = 4) -> str:
        """Convert a value to string, None/NaN → 'N/A'."""
        if val is None:
            return "N/A"
        try:
            import math
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                return "N/A"
        except Exception:
            pass
        if isinstance(val, float):
            return f"{val:.{precision}f}"
        return str(val)

    def _fmt_pct(val: Any) -> str:
        """Format percentage value with 2 decimal places."""
        return _fmt(val, precision=2)

    # Derived: EMA trend
    ema_20 = t.get("ema_20")
    ema_50 = t.get("ema_50")
    if ema_20 is not None and ema_50 is not None:
        try:
            ema_trend = "bullish (EMA20 > EMA50)" if float(ema_20) > float(ema_50) else "bearish (EMA20 < EMA50)"
        except (TypeError, ValueError):
            ema_trend = "N/A"
    else:
        ema_trend = "N/A"

    # Derived: Fear & Greed combined string
    fg_val = t.get("fear_greed_value")
    fg_label = t.get("fear_greed_label")
    if fg_val is not None and fg_label:
        fear_greed = f"{fg_val} ({fg_label})"
    elif fg_val is not None:
        fear_greed = str(fg_val)
    else:
        fear_greed = "N/A"

    # Direction label
    direction = t.get("direction", "")
    direction_label = "LONG" if "long" in str(direction).lower() else "SHORT" if "short" in str(direction).lower() else _fmt(direction)

    # win/loss label
    win = t.get("win", False)
    outcome = "WIN" if win else "LOSS"

    return f"""=== TRADE AUTOPSY REQUEST ===

--- OUTCOME ---
Symbol:           {_fmt(t.get('symbol'))}
Direction:        {direction_label}
Result:           {outcome}
PnL:              {_fmt_pct(t.get('pnl_pct'))}% ({_fmt(t.get('pnl_usd'), 2)} USD)
Hold Time:        {_fmt(t.get('hold_seconds'), 0)} seconds
Entry Price:      {_fmt(t.get('entry_price'), 6)}
Exit Price:       {_fmt(t.get('exit_price'), 6)}
Closed By:        {_fmt(t.get('closed_by'))}

--- ENTRY DECISION ---
Strategy:         {_fmt(t.get('strategy_name'))}
Category:         {_fmt(t.get('strategy_category'))}
Source:           {_fmt(t.get('source'))}
Entry Score:      {_fmt(t.get('entry_score'))}
Ensemble Votes:   {_fmt(t.get('ensemble_votes'))}
Leverage:         {_fmt(t.get('leverage'))}x
Position Size:    {_fmt(t.get('position_size_usd'), 2)} USD

--- CLAUDE'S ENTRY REASONING ---
Claude Signal:    {_fmt(t.get('claude_signal'))}
Claude Thesis:    {_fmt(t.get('claude_thesis'))}

--- CONDITIONS AT ENTRY (snapshot when order was placed) ---
Regime:           {_fmt(t.get('entry_regime'))}
RSI:              {_fmt(t.get('entry_rsi'))}
MACD Histogram:   {_fmt(t.get('entry_macd_hist'))}
ATR %:            {_fmt(t.get('entry_atr_pct'))}%

--- MARKET CONDITIONS AT CLOSE ---
Regime:           {_fmt(t.get('regime'))}
Fear & Greed:     {fear_greed}

--- TECHNICAL INDICATORS AT CLOSE ---
RSI:              {_fmt(t.get('rsi'))}
MACD Histogram:   {_fmt(t.get('macd_hist'))}
MACD Signal:      {_fmt(t.get('macd_signal'))}
Bollinger %B:     {_fmt(t.get('bollinger_pct'))}%
EMA Trend:        {ema_trend}
EMA 20:           {_fmt(t.get('ema_20'), 6)}
EMA 50:           {_fmt(t.get('ema_50'), 6)}
Stochastic K:     {_fmt(t.get('stochastic_k'))}
Stochastic D:     {_fmt(t.get('stochastic_d'))}
ADX:              {_fmt(t.get('adx'))}
ATR Value:        {_fmt(t.get('atr_value'), 6)}
ATR %:            {_fmt(t.get('atr_pct'))}%
Volume Ratio:     {_fmt(t.get('volume_ratio'))}
Price vs VWAP:    {_fmt(t.get('price_vs_vwap'))}%

--- MODE4 PROFIT TRACKING ---
Peak PnL:         {_fmt_pct(t.get('m4_peak_pnl_pct'))}%
Ticks in Profit:  {_fmt(t.get('m4_ticks_in_profit'))} / {_fmt(t.get('m4_ticks_total'))}
Composite Score:  {_fmt(t.get('m4_composite_score'))}
Hurst Value:      {_fmt(t.get('m4_hurst_value'))}
Momentum Decay:   {_fmt(t.get('m4_momentum_decay'))}
Extension Score:  {_fmt(t.get('m4_extension_score'))}
EV Ratio:         {_fmt(t.get('m4_ev_ratio'))}
Volume Div Score: {_fmt(t.get('m4_volume_div_score'))}

=== ANALYSE THIS TRADE AND RESPOND WITH JSON ONLY ==="""
