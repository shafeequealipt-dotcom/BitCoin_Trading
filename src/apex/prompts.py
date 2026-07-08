"""APEX prompt templates — system prompt + per-trade user prompt builder.

Design mirrors src/tias/prompts.py:
  - A module-level constant (APEX_SYSTEM_PROMPT) for the system message
  - A builder function (build_apex_user_prompt) that renders the 4-section
    IntelligencePackage into human-readable text for DeepSeek

The system prompt is constant across all trades. The user prompt is built
per-trade from the IntelligencePackage assembled by IntelligenceAssembler.
"""

from __future__ import annotations

from src.apex.models import IntelligencePackage


# E26 (2026-05-28): minimum venue-isolated sample before the per-coin
# directional history block is rendered into the prompt. Below this the
# per-coin rates are too sparse to be trustworthy, so the block is omitted
# and DeepSeek relies on the all-coins situation data. Matches the flip
# discipline threshold (apex_min_trades_for_flip, raised to 8 by E27).
_FLIP_EVIDENCE_MIN_SAMPLE = 8


# =============================================================================
# SYSTEM PROMPT — DeepSeek's identity and objective (constant for all trades)
# =============================================================================

APEX_SYSTEM_PROMPT = """You are APEX — an aggressive trade optimizer for crypto futures.

A senior trader has already decided WHAT to trade. Your job is to decide HOW to trade it for MAXIMUM NET DOLLAR PROFIT per trade.

YOU DO NOT REJECT TRADES. YOU DO NOT SAY "SKIP."
Every directive you receive WILL be traded. Your job is to make it the most profitable version possible.

YOUR OPTIMIZATION TARGET: MAXIMUM NET DOLLAR PROFIT PER TRADE.
A trade that wins 60% of the time at $12 average win is BETTER than a trade that wins 90% at $3 average win. Optimize for total dollars captured, not win count.

KEY METRICS TO CONSIDER:
- Profit Factor (total $ won / total $ lost) — target above 2.0
- TP Fill Rate (% of intended TP actually captured) — target above 50%
- If TP Fill Rate in TIAS history is below 30%, your exit parameters are TOO TIGHT
- Avg Win $ vs Avg Loss $ — wins must be bigger than losses in dollars

YOUR MINDSET:
- PROFIT-MAXIMIZING: Larger wins matter more than higher win rate
- DATA-DRIVEN: The TIAS history is REAL trading data from this exact system. Trust it over theory.
- ADAPTIVE: If TIAS data overwhelmingly shows one direction outperforms the other IN THE CURRENT REGIME, consider flipping direction.
- REGIME-DIRECTION AWARENESS (CRITICAL):
  * trending_down regime: Sell is the NATURAL direction. Only flip Sell->Buy if Buy has >65% WR with >5 trades in THIS regime for THIS coin.
  * trending_up regime: Buy is the NATURAL direction. Only flip Buy->Sell if Sell has >65% WR with >5 trades in THIS regime for THIS coin.
  * ranging regime: Both directions valid. Use DIRECTION BREAKDOWN data to decide.
  * volatile regime: Be conservative. Only flip with overwhelming evidence (>70% WR, >8 trades).
- CONTAMINATION AWARENESS: If TIAS shows many LOSING trades in a counter-regime direction (e.g., Buy losses in trending_down), those losses were likely from prior bad flips. Do NOT use losing counter-regime trades as evidence to flip AGAIN.
- INSUFFICIENT DATA: If fewer than 5 trades exist for a direction in the current regime, that is NOT enough to justify a flip. Keep the trader's original direction.

WHAT YOU OPTIMIZE:
1. DIRECTION: Same as the trader OR flipped if TIAS overwhelmingly shows the opposite wins.
2. STOP LOSS: ATR-proportional. Tight enough to limit damage, wide enough to survive noise.
3. TAKE PROFIT: CRITICAL RULE — NEVER set TP below the trader's original TP. The trader set that target based on analysis. Match or EXCEED it. Regime-adjust upward, never downward.
4. POSITION SIZE: Scale by TIAS profit factor. High profit factor (>2.0) coins get MORE capital. Low profit factor (<1.0) coins get LESS.
5. EXIT STRATEGY: Prefer "fixed" mode (fixed TP target). Use "trail_only" ONLY when TIAS shows >70% win rate AND avg capture >1.5% for this coin with trailing exits. Otherwise use "fixed".
6. ADD-ON: Recommend adding to position on pullback ONLY when TIAS shows the coin trends after pullbacks.

VOLATILITY-ADAPTIVE TARGETS:
- Each coin has a Volatility profile in the Coin Data section.
- Set TP/SL proportional to the coin's volatility class:
  * DEAD volatility: TP 0.3-0.5%, SL 0.2-0.3% — anything higher NEVER hits
  * LOW volatility: TP 0.4-0.8%, SL 0.3-0.5%
  * MEDIUM volatility: TP 1.0-2.0%, SL 0.8-1.5%
  * HIGH volatility: TP 2.0-4.0%, SL 1.5-2.5%
  * EXTREME volatility: TP 3.0-8.0%, SL 2.0-4.0%
- Use the recTP% and recSL% values shown in the Volatility line as your starting point.
- TP HARD CAP: NEVER set tp_pct above TP_CAP shown in Coin Data (the per-class multiplier × recTP% shown alongside). The coin cannot reach higher targets — they will time out as losses.
- You may adjust TP ±20% from recTP, but MUST stay under TP_CAP.

CONSTRAINTS:
- Max position size: 1200 USD
- Max leverage: 5x
- SL must be between 0.2% and 5.0%
- TP must be between 0.3% and 8.0% (adapt to coin's volatility class)

Respond ONLY with a valid JSON object. No text outside JSON. No markdown."""


# =============================================================================
# USER PROMPT BUILDER — renders IntelligencePackage into text for DeepSeek
# =============================================================================

def build_apex_user_prompt(package: IntelligencePackage) -> str:
    """Build the per-trade user prompt from an IntelligencePackage.

    Renders all 4 sections into readable text for DeepSeek:
      Section 1: Claude's trade directive
      Section 2: Current coin state (TA, Mode4, orderbook)
      Section 3: TIAS symbol history (past trades + DeepSeek verdicts)
      Section 4: TIAS situation data (regime/F&G cross-coin stats)
      Footer:    Required JSON output format

    Args:
        package: IntelligencePackage from IntelligenceAssembler.assemble()

    Returns:
        str: Complete user prompt (~2,000-4,000 chars depending on history depth)
    """
    d = package.directive
    coin = package.coin_data
    hist = package.symbol_history
    sit = package.situation_data

    # ═══ SECTION 1: Claude's directive ═══
    prompt = f"""TRADE TO OPTIMIZE:
  Symbol: {d.symbol}
  Trader's direction: {d.direction}
  Trader's SL: {d.sl}
  Trader's TP: {d.tp}
  Trader's leverage: {d.leverage}x
  Trader's size: ${d.size_usd}
  Signal score: {d.signal_score or 'unknown'}/100
  Strategy: {d.strategy_name or 'unknown'}
  Reasoning: {d.reasoning[:300] if d.reasoning else 'none'}

CURRENT COIN DATA:
{coin.format()}

"""

    # ═══ SECTION 3: TIAS history for this coin ═══
    if hist.total_trades > 0:
        # win_rate from TIAS repo is 0.0-100.0 (already a percentage)
        prompt += f"""TIAS HISTORY FOR {d.symbol} ({hist.total_trades} past trades):
  Record: {hist.wins}W / {hist.losses}L ({hist.win_rate:.1f}% win rate)
  Avg win: {hist.avg_win_pct:+.2f}%  |  Avg loss: {hist.avg_loss_pct:+.2f}%
  Net profit: ${hist.total_pnl_usd:+.2f}  |  EV per trade: {hist.ev_per_trade:+.3f}%
  Profit Factor: {hist.profit_factor:.2f} | Avg win: ${hist.avg_win_usd:.2f} | Avg loss: ${hist.avg_loss_usd:.2f}
  {hist.pattern_summary}
"""
        # Direction-specific breakdown from trade records
        _buy_trades = [t for t in hist.trades if t.get("direction") == "Buy"]
        _sell_trades = [t for t in hist.trades if t.get("direction") == "Sell"]
        _buy_wins = sum(1 for t in _buy_trades if t.get("win"))
        _sell_wins = sum(1 for t in _sell_trades if t.get("win"))
        _buy_wr = (_buy_wins / len(_buy_trades) * 100) if _buy_trades else 0.0
        _sell_wr = (_sell_wins / len(_sell_trades) * 100) if _sell_trades else 0.0
        _buy_pnl = sum(float(t.get("pnl_usd", 0) or 0) for t in _buy_trades)
        _sell_pnl = sum(float(t.get("pnl_usd", 0) or 0) for t in _sell_trades)
        prompt += f"""  DIRECTION BREAKDOWN (regime={sit.regime}):
    Buy:  {len(_buy_trades)} trades, {_buy_wins}W ({_buy_wr:.0f}% WR), net ${_buy_pnl:+.2f}
    Sell: {len(_sell_trades)} trades, {_sell_wins}W ({_sell_wr:.0f}% WR), net ${_sell_pnl:+.2f}

  Past trades with analysis:
"""
        # Include up to 5 most recent trades with DeepSeek verdicts
        for i, t in enumerate(hist.trades[:5]):
            win_icon = "WIN" if t.get("win") else "LOSS"
            prompt += (
                f"    #{i+1}: {t.get('direction', '')} {win_icon} "
                f"{t.get('pnl_pct', 0):+.2f}% "
                f"({t.get('closed_by', '?')}, "
                f"{(t.get('hold_seconds') or 0) / 60:.0f}min)\n"
            )
            ds_cat = t.get("ds_category", "")
            ds_why = t.get("ds_how_to_exploit", "")
            ds_should = t.get("ds_what_should_done", "")
            if ds_cat:
                prompt += f"      Category: {ds_cat}\n"
            if ds_why:
                prompt += f"      Why: {ds_why[:150]}\n"
            if ds_should:
                prompt += f"      Should have: {ds_should[:150]}\n"
            # Include optimal params if available from DeepSeek analysis
            opt_sl = t.get("ds_optimal_sl_pct")
            opt_tp = t.get("ds_optimal_tp_pct")
            opt_size = t.get("ds_optimal_size_usd")
            opt_dir = t.get("ds_correct_direction")
            if any([opt_sl, opt_tp, opt_size, opt_dir]):
                prompt += (
                    f"      Optimal: dir={opt_dir or '?'} "
                    f"SL={opt_sl or '?'}% TP={opt_tp or '?'}% "
                    f"size=${opt_size or '?'}\n"
                )
            prompt += "\n"
    else:
        prompt += f"""TIAS HISTORY FOR {d.symbol}: No past trades. First time trading this coin.
  Use conservative parameters. Smaller size until track record established.

"""

    # ═══ SECTION 4: TIAS situation data ═══
    if sit.total_trades_in_condition > 0:
        # buy_win_rate/sell_win_rate from TIAS repo are 0.0-100.0 (already percentages)
        prompt += f"""TIAS SITUATION DATA (all coins in similar conditions):
  Conditions: {sit.regime} regime, F&G={sit.fear_greed}
  Trades in similar conditions: {sit.total_trades_in_condition}
  Buy win rate: {sit.buy_win_rate:.1f}% (avg PnL: {sit.avg_buy_pnl:+.2f}%)
  Sell win rate: {sit.sell_win_rate:.1f}% (avg PnL: {sit.avg_sell_pnl:+.2f}%)
  Direction bias: {sit.direction_bias}
"""
        if sit.common_categories:
            # common_categories from TIAS repo is a list of strings, not dicts
            cats = ", ".join(str(c) for c in sit.common_categories[:3])
            prompt += f"  Common issues: {cats}\n"
        prompt += "\n"
    else:
        prompt += """TIAS SITUATION DATA: No historical data for these conditions yet.
  Use the trader's parameters with minor adjustments only.

"""

    # ═══ SECTION 4b: per-coin + per-venue directional history (E26) ═══
    # Venue-isolated win rate for THIS coin, rendered only when the sample is
    # large enough to be meaningful. Sits ALONGSIDE the all-coins situation
    # data above — it does not replace it. Omitted (not shown as zeros) when
    # the venue sample is sparse, so DeepSeek is not fed mostly-null rates.
    _fe = getattr(package, "flip_evidence", None)
    if _fe is not None and getattr(_fe, "total", 0) >= _FLIP_EVIDENCE_MIN_SAMPLE:
        _venue = _fe.exchange_mode or "all-venues"
        _regime = _fe.regime or "all"
        prompt += f"""PER-COIN DIRECTIONAL HISTORY ({_fe.symbol}, venue={_venue}, {_regime} regime):
  Buy: {_fe.buy_win_rate:.1f}% win rate over {_fe.buy_count} trades
  Sell: {_fe.sell_win_rate:.1f}% win rate over {_fe.sell_count} trades
  This is THIS coin's record on THIS venue — prefer it over the all-coins data for this coin's direction.

"""

    # ═══ SECTION 5: X-RAY Structural Intelligence ═══
    if package.structural_data:
        prompt += f"""X-RAY STRUCTURAL ANALYSIS:
{package.structural_data.format()}

"""

    # ═══ OUTPUT FORMAT ═══
    prompt += """Optimize this trade for maximum profit. Output JSON:
{
  "direction": "Buy or Sell",
  "sl_pct": 0.0,
  "tp_pct": 0.0,
  "tp_mode": "fixed or trail_only or partial_trail",
  "position_size_usd": 0,
  "leverage": 0,
  "entry_timing": "immediate or wait_pullback",
  "add_on_pullback": false,
  "add_trigger_pct": 0.0,
  "add_size_pct": 0,
  "reasoning": "why these parameters",
  "confidence": 0.0
}"""

    return prompt
