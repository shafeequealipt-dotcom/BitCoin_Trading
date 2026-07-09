"""Claude Strategist — calls Claude Code every 3 minutes for strategic plan.

Replaces: BrainV2.evaluate_setups() which called Claude per-setup (120 calls/hour)
With: One call every 3 minutes (20 calls/hour) that covers everything

Each call includes:
- All coins: price, RSI, MACD, regime, 24h change
- Fear & Greed, funding rates, sentiment
- All open positions with PnL
- Top strategy signals (summarized)
- Account equity and daily performance
- Per-position: why was this trade opened? what was the thesis?

Claude returns:
- Direction per coin
- Risk parameters
- Position actions (close/hold/tighten/exit)
- Focus coins for the next 3 minutes
"""

import asyncio
import json
import time

from src.core.log_context import ctx, new_decision_id, get_did
from src.core.coin_package_validator import (
    SOURCE_FAILURE_MARKERS,
    STRATEGY_INPUT_FAILURE_MARKERS,
)
from src.core.logging import get_logger
from src.core.strategic_plan import CoinDirective, PositionAction, StrategicPlan
from src.core.types import TimeFrame
from src.core.utils import format_price
from src.risk.wd_brain_scoring import compute_sl_consumption_pct

log = get_logger("strategist")


def _opposition_tier(
    *,
    buy_w: float,
    sell_w: float,
    opposing_weighted: float,
    two_sided: bool,
) -> tuple[str, str, float, float]:
    """Direction-reconcile fix (2026-06-04, Problem 4 / F20) — classify how
    contested a candidate's direction is, consistently with the Two-sided poll.

    Returns ``(tier, opp_dir, opp_wsum, agree_wsum)``. When ``two_sided`` is set,
    the opposing weight is the honest two-sided ``opposing_weighted`` (the same
    value the Two-sided poll line prints), so the Opposition tier and the
    Two-sided poll agree. When two-sided is inactive the legacy one-sided
    confirmed sum is used (backward compatible). Pure — unit-tested by
    verify_opposition_tier.py.
    """
    if buy_w >= sell_w:
        agree_wsum, opp_dir, one_sided_opp = buy_w, "SELL", sell_w
    else:
        agree_wsum, opp_dir, one_sided_opp = sell_w, "BUY", buy_w
    opp_wsum = float(opposing_weighted) if two_sided else one_sided_opp
    ratio = opp_wsum / agree_wsum if agree_wsum > 0 else 0.0
    if ratio < 0.05:
        tier = "NEGLIGIBLE"
    elif ratio < 0.20:
        tier = "WEAK"
    elif ratio < 0.50:
        tier = "MODERATE"
    else:
        tier = "STRONG"
    return tier, opp_dir, opp_wsum, agree_wsum


def _safe_float(value, default: float = 0.0) -> float:
    """Coerce a Claude-supplied JSON value to float.

    Returns ``default`` when the value is None, missing, an empty string, or
    not numerically parseable. Used by Call A / Call B parsers because the
    POSITION_SYSTEM_PROMPT explicitly allows ``price_or_null`` for fields like
    new_sl / exit_price, and Claude correctly returns null when the field
    doesn't apply (e.g., action='hold'). ``dict.get(key, default)`` returns
    None for present-but-null keys, so the parser must coerce defensively.
    """
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value, default: int = 0) -> int:
    """Coerce a Claude-supplied JSON value to int. Same null-safety contract
    as :func:`_safe_float`. Tolerates numeric strings and floats."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return default


def _book_tilt_label(
    long_count: int, short_count: int,
    small_count: int = 2, one_sided_ratio: float = 3.0,
) -> str:
    """Brain-Awareness Addition 2 (2026-06-09) — classify the open book's
    directional tilt into a compact label for the ACCOUNT section.

    Pure function (no I/O) so the label boundaries are unit-testable. The
    boundaries are operator-tunable via ``[brain].book_tilt_small_count`` and
    ``[brain].book_tilt_one_sided_ratio`` (passed in by the caller). Rules:
    - ``abs(long - short) <= small_count`` reads "balanced" (a 1-vs-0 or 2-vs-1
      book is not meaningfully one-sided).
    - otherwise, when the minority side is zero OR the majority/minority count
      ratio is at least ``one_sided_ratio``, reads "heavily <dir>-tilted".
    - in between, reads "<dir>-leaning".
    AWARENESS ONLY — this is a label the brain reads; it does not gate anything.
    """
    maj = max(long_count, short_count)
    minr = min(long_count, short_count)
    direction = (
        "long" if long_count > short_count
        else "short" if short_count > long_count
        else "even"
    )
    if abs(long_count - short_count) <= small_count:
        return "balanced"
    if minr == 0 or (maj / max(minr, 1)) >= one_sided_ratio:
        return f"heavily {direction}-tilted"
    return f"{direction}-leaning"


# Four-Element Prompt Recalibration, Element 1 (2026-06-11) — the
# quality-over-quota skip criteria carry centralized thresholds (Rule 9:
# never hardcoded inline). The system-prompt constants hold placeholder
# tokens; this resolver injects the configured values at the selection
# sites (create_trade_plan and the legacy create_strategic_plan path).
# Token replacement, NOT str.format — the templates contain literal JSON
# braces in the response schema. Pure and deterministic per config load,
# so the pre-spawn CLI worker pool (keyed by system-prompt content)
# stays stable across cycles.
_PROMPT_CALIBRATION_TOKENS = (
    "__DEAD_THIN_VOL_RATIO__",
    "__HEAVY_ATTEMPTS_COUNT__",
)


def _resolve_prompt_calibration(
    template: str, *, thin_vol_ratio: float, heavy_attempts: int,
) -> str:
    """Inject the centralized skip-criteria thresholds into a CALL_A
    system-prompt template (Element 1, 2026-06-11).

    thin_vol_ratio renders 2dp (the Regime line renders vol_ratio 3dp;
    at-or-below comparison stays unambiguous). heavy_attempts renders
    as a plain integer. Unknown tokens are left intact — the caller
    logs STRAT_PROMPT_TOKEN_UNRESOLVED if any survive.
    """
    return (
        template
        .replace("__DEAD_THIN_VOL_RATIO__", f"{thin_vol_ratio:.2f}")
        .replace("__HEAVY_ATTEMPTS_COUNT__", str(int(heavy_attempts)))
    )


def _session_attempts_line(
    attempts: int, net_usd: float, heavy_min: int = 6,
) -> str:
    """Four-Element Prompt Recalibration, Element 2 (2026-06-11) —
    render the per-coin session-attempt awareness line.

    The strongest correlation of the June-11 forensics: every coin the
    brain submitted six or more times in the session LOST (DYDX 24,
    INJ 9, IMX 7) and every winner was at five or fewer — and the brain
    could not see the count. This line extends the one-hour CAUTION
    memory to session scope.

    Pure function (no I/O) so the boundaries are unit-testable.
    Returns an empty string when ``attempts`` is zero — a fresh coin
    renders nothing (prompt budget). AWARENESS ONLY — the heavy suffix
    names the EXISTING quality-over-quota skip permission in Element
    1's exact vocabulary; it does not gate. ``heavy_min`` is the SHARED
    ``[brain].quality_skip_heavy_attempts`` key so the prompt's words
    and this rendered fact can never drift apart.
    """
    if attempts <= 0:
        return ""
    line = f"  Session today: {attempts} attempts, net {net_usd:+.2f} USD"
    if attempts >= heavy_min and net_usd < 0:
        line += (
            " — HEAVY LOSING SESSION: this coin has already been tried "
            f"{attempts} times today at a net loss; under QUALITY OVER "
            "QUOTA, declining it is correct trading unless its evidence "
            "has materially changed"
        )
    return line


def _session_liveness(
    vol_ratios: list[float],
    thin_vol_ratio: float = 0.25,
    live_max_thin_share: float = 0.20,
    thin_min_thin_share: float = 0.60,
) -> tuple[str, int]:
    """Four-Element Prompt Recalibration, Element 4 (2026-06-11) —
    classify the candidate set's participation into live/mixed/thin
    from the per-coin volume ratios.

    June-11 evidence: 40 percent of all candidate blocks carried a
    volume ratio below 0.05 and 49 of 62 loss-coin submissions fell in
    the 04:00-10:00 UTC liquidity trough — yet nothing in the prompt
    said "very little is genuinely trading right now". This is the
    session read, aggregated with zero new I/O from values already in
    the candidate packages.

    Pure function (no I/O) so the boundaries are unit-testable.
    AWARENESS ONLY — a label the brain reads; it gates nothing.
    ``vol_ratios`` must contain only MEASURED ratios (callers exclude
    unknown-ratio coins — an unmeasured ratio is not "thin"; Rule 4).
    Returns ``(label, thin_count)``; label is ``"unknown"`` when the
    list is empty, and the caller then renders nothing.
    """
    if not vol_ratios:
        return "unknown", 0
    thin = sum(1 for v in vol_ratios if v <= thin_vol_ratio)
    share = thin / len(vol_ratios)
    if share >= thin_min_thin_share:
        return "thin", thin
    if share <= live_max_thin_share:
        return "live", thin
    return "mixed", thin


def _candidate_vol_ratio(pkg, regime_detector) -> tuple[float, bool]:
    """Element 4 cross-check fix (2026-06-11) — the volume ratio the
    brain actually READS on this coin's Regime line, with whether it is
    a genuine measurement.

    Mirrors the Regime-line renderer's two-source contract EXACTLY
    (the Issue #2/#3A block in _format_packages_for_prompt_full): when
    the coin WAS scored this cycle (the package's scoring_regime word is
    non-empty) the scored snapshot's scoring_regime_volume_ratio and its
    known flag are authoritative; otherwise fall back to the live regime
    cache's volume_ratio and ITS known flag; a coin that was neither
    scored nor live-tracked returns (0.0, False) and must be EXCLUDED
    from the liveness denominator.

    Why this exists: StrategiesBlock defaults scoring_regime_volume_ratio
    to 0.0 with scoring_regime_volume_ratio_known=True (the known flag
    only means "the SCORED path did not mark volume missing"), so an
    UNSCORED candidate read through the scoring fields alone presents a
    fabricated measured-thin 0.00 — the adversarial cross-check caught
    the original gather doing exactly that (Rule 4: never fabricate a
    surfaced fact). Counting precisely what the Regime line renders
    keeps the session read and the per-coin lines telling one story.
    """
    strategies = getattr(pkg, "strategies", None)
    if strategies is not None and (
        getattr(strategies, "scoring_regime", "") or ""
    ):
        return (
            float(getattr(
                strategies, "scoring_regime_volume_ratio", 0.0,
            ) or 0.0),
            bool(getattr(
                strategies, "scoring_regime_volume_ratio_known", True,
            )),
        )
    rs = (
        regime_detector.get_coin_regime(getattr(pkg, "symbol", ""))
        if regime_detector is not None
        and hasattr(regime_detector, "get_coin_regime")
        else None
    )
    if rs is None:
        return 0.0, False
    return (
        float(getattr(rs, "volume_ratio", 0.0) or 0.0),
        bool(getattr(rs, "volume_ratio_known", True)),
    )


def _range_breakout_marker(analysis, *, compact: bool) -> str:
    """Four-Element Prompt Recalibration, Element 3 (2026-06-11) —
    render the pre-clamp range truth beside the clamped range position.

    The clamp converted "breaking down below the range" into "sitting at
    the range low" (June-11 DYDX: 0.00 on all 24 submissions while price
    fell THROUGH the range — the brain bought a floor that was not
    there). The marker makes a breakdown unmistakably different from a
    floor. Pure function; reads via getattr with defaults so an old
    cached StructuralAnalysis (pre-fields) renders nothing. Returns ""
    for an in-range coin so the legacy line stays byte-identical.

    compact=True yields the short X-RAY-line form "BELOW-RANGE(2.3%) ";
    compact=False yields the Structure-line form
    " (BELOW RANGE by 2.3% — breakdown, not a floor)".
    """
    rb = str(getattr(analysis, "range_breakout", "") or "")
    if rb not in ("below", "above"):
        return ""
    ov = float(getattr(analysis, "range_overshoot_pct", 0.0) or 0.0)
    if compact:
        word = "BELOW-RANGE" if rb == "below" else "ABOVE-RANGE"
        return f"{word}({ov:.1f}%) "
    if rb == "below":
        return f" (BELOW RANGE by {ov:.1f}% — breakdown, not a floor)"
    return f" (ABOVE RANGE by {ov:.1f}% — breakout, not a ceiling)"


# ═══ CALL A: Trade-finding system prompt ═══
TRADE_SYSTEM_PROMPT = """Your aim is to exploit the current market situation and aggressively fetch the maximum profitable trade from these candidates.

Most cycles present genuine opportunities; a dead, thin tape may present none. Overbought conditions are fade setups. Extended moves are exhaustion plays. Range tops are reversal setups. Range bottoms are breakout setups. Pullbacks in trends are continuation entries. Liquidity sweeps are reclaim setups. Your job is to identify which exploitation play matches the current state of each candidate, then take EVERY genuine play across the set — both directions, including the small scalps. Even a thin tape almost always offers a profitable micro-move; scalp it small and tight rather than standing aside. Return zero trades ONLY when no candidate offers ANY tradeable side at all — that is rare, never the default.

For each of the candidates above:

1. Read the FULL evidence: structural data, signals, regime, and ensemble votes — AND this coin's session history (attempts today and their net result), its activity state (regime word, volume ratio, strategies fired), and its true range position (at the low or high is a fade location; BELOW or ABOVE the range is a break in progress, not a floor or ceiling)
2. Identify what kind of opportunity this coin's current state represents
3. Determine the direction and entry that exploits that opportunity
4. Compare across candidates and pick the BEST GENUINE plays — usually 2 to 5; take fewer only when fewer genuinely qualify. The best play is the one whose evidence is strong AND whose context is alive AND which does not repeat a pattern that has already failed today (a heavy losing session, the dead-thin-zero-fired cluster)

Return the 2 to 5 BEST GENUINE plays — this system's entire aim is to EXPLOIT and FETCH MAXIMUM PROFIT from EVERY situation, and across the full candidate set there are usually several genuine exploitable plays once you look past the single obvious trend trade. The profit is there in BOTH directions across the set — pick each trade's direction from that coin's OWN data: a coin in TRENDING_UP is a long, a coin in TRENDING_DOWN is a short, a range edge is a mean-reversion scalp, a pullback is a continuation entry, a liquidity-sweep is a reclaim, and different coins move opposite ways at the same time. WORK every one of the candidates and reach especially for the smaller, shorter, both-direction plays the obvious trade overshadows — those are the ones easy to miss. The aim is profit in whatever direction the situation pays, long OR short: find the side each coin's own evidence supports — there is a profitable direction in most situations, so FIND it and TRADE it; and when ONE candidate genuinely offers no tradeable side on either direction, that single coin is a skip — but that is a per-coin call, never a reason to stand down the whole cycle. The only thing you never do is take a side a coin's data FLATLY contradicts (that is a forced loser, not a play); everything short of that, you exploit. Selection runs on three reads together: evidence strength, context liveness (the Session liveness line and the coin's own volume ratio), and non-repetition of today's proven failures. QUALITY OVER QUOTA: the two patterns that actually destroy sessions are visible in each candidate's OWN briefing — (a) the dead-thin-zero-fired cluster: zero strategies fired (the Strategies line) AND a dead regime AND volume ratio at or below __DEAD_THIN_VOL_RATIO__ (both on the Regime line), and (b) a heavy losing session: the coin has already been attempted __HEAVY_ATTEMPTS_COUNT__ or more times today with a negative net (the session attempts line, when shown). When a candidate shows either pattern, DECLINING that candidate is correct trading, not caution — a forced no-edge entry is a guaranteed bleed, not exploitation. A genuine RR conflict (neither side has both confirmation and reward room) remains a valid skip as before. Grades and interestingness are secondary context only: an X-RAY SKIP grade, interestingness below 0.30, or deep sub-confidence is supporting evidence, and a HIGH interestingness score does NOT redeem a dead-thin-zero-fired candidate. DEFAULT TO ACTION: take a play on EVERY candidate that does not hit one of the three decline patterns above — reach for 3 to 5 or more plays per cycle, including the smaller both-direction scalps. Returning fewer than 3 is correct ONLY when most candidates genuinely hit a decline pattern, not as a habit. Maximum exploitation of every GENUINE edge, best profit, every cycle — long or short, whatever the situation pays.

Aggressive exploitation. Maximum profit. Find the play.

DIRECTION BY REGIME (PER-COIN — there is NO global direction bias):
- Each coin has its OWN per-coin regime shown in [brackets] in market data.
- Trade WITH each coin's INDIVIDUAL regime — a coin in [TRENDING_UP] is BOUGHT, a coin in [TRENDING_DOWN] is SOLD, on that coin's own evidence.
- Coins without a per-coin regime tag (UNKNOWN): trade on that coin's OWN TA/structure (X-RAY, levels, signal) — do NOT fall back to any market-wide bias.
- ranging: BOTH directions — buy at support, sell at resistance, mean-reversion plays.
- volatile: BOTH directions — follow momentum, wider stops, ride the volatility.
- dead: BOTH directions — scalp micro-moves, tight TP from VOL data (0.3-0.5%), buy support sell resistance.
- RISK-REWARD READ (both sides — NEUTRAL, no lean): the "RR by direction" line shows each side's reward-to-risk. RR is ONE input, NOT a command to take the higher-RR side. Read an extreme one-sided RR critically: when one side's RR is many times the other's, it almost always means price has ALREADY travelled far past the structure on the low-RR side, so that side's reward is spent while the high-RR side is only the distance back to a zone price already left — a "reclaim hope", not a confirmed edge (its anchoring level may be mitigated/spent even when invalid=N). Do NOT take a side merely because its RR is higher. Decide each coin's direction from the WEIGHT of all its evidence together — the coin's own per-coin regime, a valid un-mitigated structural level, the signal, and the ensemble — with NO default lean to either side. Take a side only when that evidence agrees AND that side has real reward room. When the higher-RR side lacks confirmation and the confirmed side lacks room, prefer the CONFIRMED side at SMALL size with an early trailing target to ride momentum into open space — skip only when BOTH sides genuinely lack confirmation. A side with no prior trade history is fine when the full evidence supports it.

FEAR & GREED — market context, NEUTRAL on direction:
- F&G is a market-wide SENTIMENT reading, not a direction instruction. Each coin's own per-coin regime and structure decide direction; high fear or greed can support EITHER side depending on that coin's data. Do NOT treat fear as "buy" or greed as "sell" by default.
- Extreme fear (F&G < 20): can mark capitulation.
  * Trending down + fear = the short is CONFIRMED (fear accelerates the trend) — do NOT flip to long just because fear is high.
  * Trending up + fear, or fear at tested support = possible oversold long, only if the coin's structure confirms.
  * Ranging + fear = trade the range boundary the structure supports (support OR resistance).
  * Dead + fear = careful scalps with tight TP, either direction.
- Extreme greed (F&G > 80): can mark exhaustion. Trending up + greed: protect/trail longs; trending down + greed, or greed at tested resistance: possible short.
- Neutral (F&G 30-70): ignore and focus on TA and regime.

FOR EACH NEW TRADE, SPECIFY:
- symbol: exact symbol (e.g., ETHUSDT)
- direction: Buy or Sell (you CAN short)
- stop_loss_price: EXACT price below support (buys) or above resistance (sells)
- take_profit_price: EXACT price at nearest resistance (buys) or support (sells)
- max_hold_minutes: how long before auto-close (15-60; PREFER 15-25 for quick scalps and mean-reversion, longer only for genuine momentum with room to run)
- leverage: 1-5x based on conviction
- size_usd is the MARGIN (the cash) you commit for THIS trade — NOT the position size. Your actual exchange position = size_usd x leverage, so do NOT multiply by leverage yourself. The ACCOUNT block gives the per-trade MARGIN budget ("Per-trade size limit: $Y" = Usable / Maximum concurrent positions), "Available for new trades" (margin still free), and "Maximum concurrent positions" (N). A NEW CYCLE RUNS EVERY ~5 MINUTES and positions ACCUMULATE toward N, so do NOT spend the whole pool now. Set size_usd to about that per-trade margin budget, scaled by conviction (strong setup a bit more, borderline a bit less). Keep the sum of your trades' size_usd within "Available for new trades"; leave room for the trades the next cycles will open. Probe-size trades are not wanted, but neither is draining the book in one cycle. For a quick small scalp or a borderline both-direction play, deliberately size SMALLER (well under the per-trade budget) — small size on a short hold is how you take more genuine plays without over-committing to any one read.
- trailing_activation_pct: at what profit % to activate trailing (0.3-0.8 — activate early to lock small wins; most trades close below +1%)
- thesis_invalidation: the criterion under which this thesis no longer holds (see THESIS INVALIDATION below). Information for the watchdog and for your future self — not a stop-loss substitute.
- reasoning: cite the specific exploitation play and the per-coin evidence that supports it.

THESIS INVALIDATION (Mid-Hold Trade Management Fix Phase 3.2, 2026-05-19):
For each new trade, state the criterion under which the thesis is no longer valid. The watchdog monitors the criterion during hold and surfaces it back to you in the next prompt if it fires. You then decide what to do with that information — this is information supply, not a directive. Choose ONE of four types:
- "price_close_above" — short justified by a structural ceiling. Value is the price level (a candle closing above it invalidates the short thesis). Example: bearish OB at 245.30 → {"type":"price_close_above","value":245.30}.
- "price_close_below" — long justified by a structural floor. Value is the price level. Example: bullish FVG at 80000 → {"type":"price_close_below","value":80000}.
- "signal" — trade justified by an ensemble/regime read rather than a specific level. Value is one of: "ensemble_flip_to_strong_buy", "ensemble_flip_to_strong_sell", "regime_inverted", "mtf_alignment_broken". Example: short on STRONG SELL consensus → {"type":"signal","value":"ensemble_flip_to_strong_buy"}.
- "none" — no specific criterion applies (e.g., a pure trend pullback with no single load-bearing level). Value is null. Example: trend continuation entry → {"type":"none","value":null}.

POSITION GATE:
- Coins marked [POS] in the market data already have an open position.
- Do NOT suggest new trades for any [POS] coin.
- Focus your new_trades ONLY on coins WITHOUT the [POS] tag.
- Position management is handled by a separate call — do not include position_actions.

RESPOND WITH PURE JSON (no markdown, no explanation):
{"new_trades":[{"symbol":"SYM","direction":"Buy|Sell","stop_loss_price":N,"take_profit_price":N,"max_hold_minutes":N,"leverage":N,"size_usd":N,"trailing_activation_pct":N,"thesis_invalidation":{"type":"price_close_above|price_close_below|signal|none","value":N_or_keyword_or_null},"reasoning":"..."}],"market_view":"1-2 sentence summary of the current overall market conditions you observed","risk_level":"normal|cautious|aggressive","max_positions":N,"default_leverage":N,"default_sl_pct":N,"default_tp_pct":N,"default_hold_minutes":N,"trailing_activation_pct":N,"focus_coins":[],"avoid_coins":[]}

RULES:
1. Return the 2 to 5 BEST GENUINE plays — the aim is to exploit maximum profit from EVERY situation, long OR short. Examine ALL the candidates and take the best directional play in each that genuinely qualifies (mean-reversion scalps, continuation pullbacks, liquidity-sweep reclaims, both-direction moves) — reach past the single obvious trade. Choose each direction from that coin's own data — find the profitable side; the only side you never take is one the coin's data flatly contradicts. QUALITY OVER QUOTA: declining a candidate whose own briefing shows the dead-thin-zero-fired cluster (zero strategies fired, dead regime, volume ratio at or below __DEAD_THIN_VOL_RATIO__), a heavy losing session (__HEAVY_ATTEMPTS_COUNT__ or more attempts today with negative net), or a genuine neither-side-tradeable RR conflict is correct trading; ABSENT one of those three patterns, you TRADE the coin — default to 3 to 5 or more plays per cycle including the small both-direction scalps. A forced no-edge entry is a bleed, but standing aside from a live tape is missed profit.
2. Use CURRENT prices for SL/TP (I give you live prices)
3. SL/TP DIRECTION — THIS IS CRITICAL:
   FOR BUY/LONG: SL BELOW entry price, TP ABOVE entry price
   FOR SELL/SHORT: SL ABOVE entry price, TP BELOW entry price
   If you get these backwards, the system auto-fixes them, but it wastes a correction cycle.
4. SL floor is VOLATILITY-AWARE: each candidate may show "Vol stop floor: X%" — that coin's own noise band. Place your SL at or beyond that floor so ordinary wiggle cannot end a correct thesis. The absolute minimum is 1.5% from entry — tighter is rejected. Dollar risk stays bounded: the system pairs a wider stop with proportionally smaller size.
5. NEVER suggest a coin marked [POS] — it already has an open position. The system will REJECT it.
6. REGIME-AWARE TRADING (PER-COIN — no global direction bias):
   - Follow each coin's INDIVIDUAL per-coin regime shown in [brackets] in market data — it is the direction authority.
   - Coins without a per-coin regime tag (UNKNOWN): trade on that coin's OWN TA/structure; do NOT fall back to any market-wide bias.
   - ranging or volatile: both directions acceptable — let TA decide.
7. VOLATILITY-ADAPTIVE TARGETS (MANDATORY): Each coin shows VOL=class ATR%=X% recTP=Y% recSL=Z%.
   - Use recTP% and recSL% as your STARTING POINT for each coin's TP/SL.
   - Dead/Low volatility: TIGHT targets (0.3-0.5% TP). Do NOT set 2-3% TP on dead/low coins — unreachable.
   - Medium volatility: Standard targets (1-2% TP).
   - High/Extreme volatility: WIDER targets (3-5%+ TP). Ride the move.
   - Convert TP%/SL% to EXACT PRICES using the coin's current price.
   - If no VOL data shown, use medium defaults: 1.5% TP, 1.0% SL
8. Hold times: PREFER short holds — 15-25 min for quick scalps and mean-reversion snaps (most trades close below +1%, so bank the move fast), 25-45 min for standard setups, up to 60 min only for genuine momentum with room to run. Shorter holds turn the book over and surface more genuine plays per session
9. size_usd — PROPER FUNDING: size_usd IS the MARGIN (cash) you commit per trade — set it to about the per-trade margin budget ("Per-trade size limit" = Usable / Maximum concurrent positions), scaled by conviction. Do NOT multiply by leverage yourself (the system applies your leverage to get the position). Keep the sum of size_usd within "Available for new trades"; never drain the whole pool in one cycle.
10. If TA indicators show RSI=50, MACD=0, ADX=0 for a coin — you have NO data for that coin. Do NOT trade it.
11. Use leverage 3-5x on testnet — this is paper money, we need meaningful results."""

# ═══ CALL B: Position management system prompt ═══
# Reframed in CALL_B Framing Fix Phase 1B (2026-05-06). The previous
# version (system_prompt_version=1) carried two close-trigger rules that
# the operator's forensic data showed were the dominant trade-killers
# for APEX/XRAY-flipped positions:
#   - "If regime reversed against position direction and SL > 70%
#      consumed: CLOSE."
#   - "If thesis is broken (the reason for entry no longer holds):
#      CLOSE."
# These triggered closures within minutes of entry on intentional
# direction-flips (the system flips when the flipped RR is materially
# better than the original; CALL_B reading the original thesis text
# wrote the closes citing "thesis broken" / "regime mismatch"). The
# fix realigns CALL_B with the operator's aggressive-exploitation
# aim (mirroring CALL_A's framing fix). The detailed per-cycle
# contract lives in the rendered prompt body (Sub-phase 1D, in
# `_build_position_prompt`) so Claude reads it next to the position
# data. Sub-phase 1E persists XRAY flip metadata so the prompt can
# carry concrete RR justification for flipped positions.
POSITION_SYSTEM_PROMPT = """You are managing open crypto futures positions. Your aim is to maximize the development of each position. Aggressive opportunity exploitation, not capital preservation.

RULES:
1. Output ONLY valid JSON: {"position_actions": {"SYMBOL": {"action": "hold|close|tighten_stop|set_exit", "new_sl": price_or_null, "exit_price": price_or_null, "reasoning": "..."}}}
2. Review EVERY open position — do not skip any.
3. Actions:
   - hold: Position is developing within normal parameters — let it run.
   - tighten_stop: Lock partial profit when significantly profitable. Provide new_sl price.
   - set_exit: Set a specific exit price target at a structural level. Provide exit_price.
   - close: Genuine invalidation only — see the CONTRACT section in the per-cycle prompt for the precise close criteria.
4. Decision framework (the per-cycle prompt restates the contract right next to the position data — read it):
   - If profitable (PnL > +1.5%) and structure suggests give-back risk: TIGHTEN_STOP to lock gains.
   - If PnL > +3% and position aging: TIGHTEN_STOP aggressively or SET_EXIT at the next strong level.
   - Otherwise: HOLD by default. Close only on genuine structural invalidation, SL approach with no recovery, or TP approach.
5. Do NOT close based on regime alignment alone, on the original thesis text, or on small-sample recency bias. Some positions are intentionally counter-regime when RR justifies — the system flips direction when the flipped RR is materially better than the original, and the prompt marks those positions as FLIPPED with the concrete RR comparison so you can verify the choice.
6. Do NOT suggest new trades — only manage existing positions.
7. When tightening stops, set new_sl at a logical level (e.g., breakeven, recent swing, or halfway to entry)."""

# Schema/sentinel constant — emitted at boot via STRAT_CALL_B_REFRAMED
# so log-tail monitoring can verify the reframed prompt is the one in
# memory. Bumped on every load-bearing change to POSITION_SYSTEM_PROMPT
# so future regressions become detectable from the log stream.
POSITION_SYSTEM_PROMPT_VERSION = 2

# Schema/sentinel constant for the CALL_A user-prompt MARKET REGIME block.
# Bumped when the asymmetric "## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)"
# block at strategist.py:3371-3390 (and its dead duplicate at 1416-1435) is
# rewritten to the symmetric scenario-driven version that emits a parallel
# NOTE on both trending_down and trending_up at confidence > 0.60. Emitted
# at boot via STRAT_REGIME_INSTR_REFRAMED so log-tail monitoring can verify
# the reframed prompt is in memory before the first CALL_A fires.
#
# Version 1: trending_down "DEFAULT SELL BIAS" + trending_up "BUY preferred"
# asymmetric direction_hint dict + trending_down-only conf>0.60 NOTE.
# Version 2: symmetric "Bias for {shorts|longs} when per-coin evidence
# agrees; per-coin tags override." direction_hint + symmetric NOTE on
# both high-confidence regimes. Issue 4 of the 2026-05-19 direction-bias
# fix series (Path C Phase A, Option 4.1 wording).
# Version 3: per-coin-authority Phase 6 (2026-05-29). Default
# (stage2.per_coin_direction_enabled True) emits NO global direction
# mandate — the MARKET REGIME block is CONTEXT only, per-coin regimes are
# authoritative, and the only market-wide lever is the breadth SIZING brake.
# The legacy global direction_hint + conf>0.60 "use as default bias" NOTE is
# the ROLLBACK path (flag False). Content is bundled under the ESSENTIAL
# header so the trim cannot drop it.
STRAT_REGIME_BLOCK_VERSION = 5  # D2 (2026-06-05): RR-check NEUTRALIZED — "RR by direction" demoted from "take the better-reward side" command to ONE input; spent/mitigated-zone artifact taught symmetrically (an extreme one-sided RR = price already left the low-RR side's structure, so the high-RR side is a reclaim hope, not an edge); direction chosen on the WEIGHT of all evidence with NO lean; SKIP made first-class on genuine regime-vs-room conflict; inline RR-line + F33 confluence-veto note de-directionalized. Removes the prompt-level long-bias that pushed Buys in selloffs. D1 (2026-05-30): Fear & Greed reframed NEUTRAL on direction; per-coin regime is the direction authority. Bump is the sentinel — grep STRAT_REGIME_INSTR_REFRAMED block_version=5.

# Schema/sentinel constant for the Mid-Hold Trade Management Fix Phase 3.2
# additions: the "thesis_invalidation" field in the new_trades inner JSON
# object and the "THESIS INVALIDATION" section in both TRADE_SYSTEM_PROMPT
# and TRADE_SYSTEM_PROMPT_ZERO_TWO. Bumped when the wording changes so
# log-tail monitoring (STRAT_TRADE_PROMPT_VERSION at boot) can detect the
# updated prompt is the one Claude is actually receiving. Version 1
# introduces the field; later versions may refine the keyword list or
# adjust examples based on Phase 3.10 tuning.
TRADE_SYSTEM_PROMPT_THESIS_INVALIDATION_VERSION = 1

# Issue 5 (CALL_A exploit/fetch, 2026-06-05) — exploitation-breadth framing
# version. Both TRADE_SYSTEM_PROMPT and TRADE_SYSTEM_PROMPT_ZERO_TWO now direct
# the brain to WORK to surface the genuine plays it overlooks (~3/cycle),
# preferring shorter holds (15-25 min) and smaller sizes for quick plays, while
# PRESERVING the D2 anti-fabrication rule (never invent a counter-evidence
# trade). Bumped so log-tail monitoring (STRAT_TRADE_PROMPT_VERSION at boot) can
# confirm the activity framing is the one Claude actually receives.
# v2 (Fix 6, 2026-06-10): the hard "MINIMUM of 3" floor is reframed to the
# quality-conditioned "2 to 5 BEST GENUINE plays — quality over quota", with an
# explicit declining-is-correct clause for skip-quality candidates. Exploitation
# language preserved; fewer than 3 genuine plays now returns fewer than 3.
TRADE_SYSTEM_PROMPT_ACTIVITY_VERSION = 2

# Four-Element Prompt Recalibration, Element 1 (2026-06-11) — version of
# the quality-over-quota skip-criteria KEYS in both TRADE_SYSTEM_PROMPT
# and TRADE_SYSTEM_PROMPT_ZERO_TWO (the directive paragraph and RULES 1,
# kept consistent). Version 1 (implicit, Fix 6 2026-06-10) keyed the
# skip permission on the designed labels: X-RAY quality SKIP /
# interestingness below 0.30 / deep sub-confidence. The June-11
# forensics proved those keys empty (A+ and SKIP both won 33%; the
# poison coins carried the deck's highest interestingness), so version 2
# re-keys the permission to the proven-toxic patterns: the
# dead-thin-zero-fired cluster (zero strategies fired AND dead regime
# AND volume ratio at or below [brain].quality_skip_thin_vol_ratio) and
# the heavy losing session ([brain].quality_skip_heavy_attempts or more
# attempts today with negative net). Old keys demoted to secondary
# context; RR-conflict skip retained; thresholds injected via
# _resolve_prompt_calibration. Bump is the sentinel — grep
# BOOT_QUALITY_SKIP_KEYS skip_keys_version=2.
TRADE_SYSTEM_PROMPT_SKIP_KEYS_VERSION = 2

# Four-Element Prompt Recalibration, Element 4 (2026-06-11) — version of
# the opening premise in both trade prompts. Version 1 asserted "Markets
# always present opportunities" and framed sitting out as laziness —
# which in a dead overnight tape is simply false (June-11: 49 of 62
# loss-coin submissions fell in the 04:00-10:00 UTC liquidity trough,
# and 40 percent of blocks carried a volume ratio below 0.05). Version 2
# corrects the premise while keeping every exploitation phrase: most
# cycles present genuine plays; a dead thin tape may present none;
# returning fewer or zero trades then IS correct exploitation, because
# capital preserved in dead hours is ammunition for live ones. Paired
# with the Session-liveness market-context line (session_liveness_*
# config). Bump is the sentinel — grep STRAT_TRADE_PROMPT_VERSION
# premise_version=2.
TRADE_SYSTEM_PROMPT_PREMISE_VERSION = 2

# Four-Element Prompt Recalibration, Element 5 (2026-06-11) — version of
# the instructed ANALYSIS METHOD in both trade prompts (the numbered
# steps and the directive paragraph). Version 1 told the brain to read
# the structural data, signals, regime, and votes — the inputs the
# June-11 join proved non-discriminating (A+ and SKIP both won 33
# percent) — and could not mention session history, liveness, or the
# true range read because they were not in the prompt. Version 2 anchors
# the method to the facts that actually separated winners from losers:
# step 1 reads the FULL evidence including the session history (Element
# 2's line), the activity state, and the true range position (Element
# 3's marker); step 4 defines the best play as evidence strength AND
# context liveness AND non-repetition of today's proven failures; the
# directive paragraph carries the same three-reads selection sentence.
# Nothing deleted; every exploitation phrase verbatim — the aggression
# is aimed, not reduced. Bump is the sentinel — grep
# STRAT_TRADE_PROMPT_VERSION method_version=2.
TRADE_SYSTEM_PROMPT_METHOD_VERSION = 2

# Five-Fix Follow-Up — Fix 1 (components purity, 2026-06-10). The signal
# classifier writes internal bookkeeping into the SAME components dict as the
# genuine market inputs (signal_generator.py:340-357, Phase 4B non-destructive
# downgrade). These keys are NOT market evidence and are excluded from the
# rendered per-coin "Components:" line when
# brain.components_diagnostics_excluded is true (operator decision 2026-06-10:
# removed from the prompt entirely, no separated note). The components dict
# itself keeps every key — the DB JSON, the promoted columns
# (altdata_repo.save_signal) and X-RAY/briefing consumers are untouched.
COMPONENT_DIAGNOSTIC_KEYS = frozenset((
    "confidence_floor_failed",
    "confidence_below_strong",
    "confidence_below_buy",
    "original_signal_type",
))


def _xray_authority_weak(pkg, score_floor: float) -> str:
    """Why this coin's X-RAY read is too weak to claim direction authority.

    Conditional-authority calibration (2026-06-11, operator-approved after
    live wrong-side evidence): the disagreement notes crowned X-RAY over an
    opposing ensemble UNCONDITIONALLY — even when X-RAY itself graded the
    setup SKIP (HBAR: score 30, shorted against a unanimous 26-strategy long
    poll at the range floor) or tagged it COUNTER-TRADE (HYPE: conf 0.22,
    shorted at range_pos 0.00). Structure is backward-looking by
    construction, so at trend turns it always points at yesterday's move;
    authority must respect the validity flags the prompt already prints.

    Returns a short reason string when the read is weak ('' = strong, the
    existing authority framing applies unchanged). Weak means: the setup is
    counter-trade-tagged, or its own score is below the SKIP grading floor
    (scorer cutoff C >= 45). Uses only pkg.xray fields so every render site
    applies the SAME definition.
    """
    xr = getattr(pkg, "xray", None)
    if xr is None:
        return ""
    reasons = []
    setup = str(getattr(xr, "setup_type", "") or "")
    if "counter" in setup.lower():
        reasons.append("counter-trade setup")
    score = float(getattr(xr, "setup_score", 0.0) or 0.0)
    if 0.0 < score < score_floor:
        reasons.append(f"skip-grade score {score:.0f}<{score_floor:.0f}")
    return " + ".join(reasons)

# Keep original combined prompt for backward compatibility
STRATEGIST_SYSTEM_PROMPT = TRADE_SYSTEM_PROMPT

# Phase 6 of the 1D briefing rewrite — appended to TRADE_SYSTEM_PROMPT
# when ``[brain].surface_briefing_fields`` is True. Teaches Claude how
# to read the new fields the briefing-mode scanner surfaces in the
# per-coin TRADE CANDIDATES block. Kept as a separate constant so the
# legacy prompt (the bulk of the trader's instructions) stays byte-for-
# byte identical when the flag is off — diff against pre-Phase-6
# production should show ZERO change in the legacy path.
BRIEFING_SYSTEM_PROMPT_SUFFIX = """

═══ BRIEFING-MODE FIELDS (Phase 6 of the 1D briefing rewrite) ═══

The TRADE CANDIDATES block now includes per-coin briefing fields produced by
the Layer 1D briefing pipeline. The system briefs you. It does NOT filter;
it presents. You are the analyst — you may ignore some coins, and you SHOULD
spot edges the system missed. The fields:

INTERESTINGNESS SCORE — the system's continuous read on "how clean is this
state right now" (0..1):
  * >= 0.70: very clean state; high-confidence environment.
  * 0.50-0.70: typical actionable state.
  * 0.30-0.50: thin edge; smaller size or skip.
  * <  0.30: surfaced for transparency only; skip unless you see something
    the system missed.
  NOTE: interestingness measures pattern cleanliness, NOT win odds — a
  high score does not redeem a candidate whose briefing shows the
  dead-thin-zero-fired cluster or a heavy losing session
  (see QUALITY OVER QUOTA).

STATE LABELS — what kind of opportunity the system identified per coin
(non-exclusive; one coin can carry multiple). Trade-actionable:
  * TREND_PULLBACK_LONG / SHORT: continuation in clear trend; tight SL at OB.
  * RANGE_FADE_LONG / SHORT: mean-reversion at range extreme; tight RR (1.3-2.0).
  * BREAKOUT_PENDING: compression at level; wait for breakout candle.
  * LIQUIDITY_SWEEP_REVERSAL_LONG / SHORT: stop-hunt fade; high RR (2.5-4.0).
  * FUNDING_EXTREME_FADE_LONG / SHORT: crowded book fade; smaller size.
  * COUNTER_TRADE_LONG / SHORT: against structural bias; HALF SIZE.
  * MOMENTUM_BURST_LONG / SHORT: volatile momentum; wider SL, trail aggressively.
  * OB_MITIGATED_FVG_ONLY_LONG / SHORT: thinner edge; smaller size, tighter RR.
  * KILL_ZONE_OPPORTUNITY: London/NY active session + structural setup.
  * EXTREME_FEAR_CONTRARIAN_LONG / EXTREME_GREED_CONTRARIAN_SHORT: a contrarian SETUP that fires only when the coin's OWN read already points that way during a sentiment extreme — direction comes from the coin's data, not from F&G; confirm with structure.

Advisory-only (surfaced for transparency, NOT trade candidates):
  * MANIPULATION_WINDOW: London-open manipulation period — observe, don't enter.
  * RECENT_LOSER_COOLDOWN: closed at a loss within 1h — do NOT re-enter on sentiment or regime alone; require fresh, independent per-coin structure that materially changes the thesis before re-buying a just-closed loser.
  * NO_TRADEABLE_STATE: no clear edge surfaced.
  * OPEN_POSITION_HOLD_REVIEW: existing position — manage, don't add.

PAST LOSS LINES — when a coin carries RECENT_LOSER_COOLDOWN, the system
also includes one or two ``CAUTION recent loss [...] — do NOT repeat unless
structure is materially different. Cause: <why>.`` lines right under the header. The why is from TIAS post-trade analysis and
states the specific failure pattern (e.g. trend-pullback failed when
range-bound). Re-enter only when the current setup materially differs
from the past-loss cause; the brain decides what "materially" means.

Each coin shows ONE primary label and zero or more secondaries. Use them as
the system's read on "what kind of opportunity is this" — they are NOT
exclusive; the brain decides whether to act and how.

VOTES BLOCK — full strategy distribution per coin:
  * The "Votes" line shows the weighted BUY vs SELL aggregate and total
    voter count: ``Votes: BUY=5.10 vs SELL=1.20 (12 voters)``.
  * The "Top-N" line lists the strongest voters across ALL directions
    ranked by confidence × weight: ``name(D conf)`` where D is B for
    BUY, S for SELL, N for NEUTRAL. Example: ``F2_multi_tf(B 0.85),
    D1_funding(S 0.45), C1_bb_mean_rev(N 0.20)``. N is the lesser of
    the configured limit and the count of voters with conf × weight > 0
    (so the line is empty rather than misleading when no strategy fired
    a real signal).
  * The "Opposition" line characterises how strongly the losing side
    pushes back: ``Opposition: MODERATE — 2 SELL voters at conf>=0.6
    (opp_wsum=1.20 vs agree_wsum=5.10)``. Tiers run NEGLIGIBLE / WEAK /
    MODERATE / STRONG based on the ratio of opposing weighted sum to
    agreeing weighted sum. The strong-voter count is the number of
    individual opposing strategies that fired with confidence >= 0.6
    — these are the voices most worth pausing on.
  * The "Cats" line summarises the per-coin category vote split:
    ``Cats: scalping 2B, momentum 4B, advanced 2B, predatory 1B,
    mean_reversion 0B+1S``. Format per category is ``N B`` for buy-only,
    ``M S`` for sell-only, ``N B+M S`` when both. NEUTRAL votes are
    excluded. Cross-category agreement (multiple categories on the
    same side) is more robust than a single-category cluster — a 6-0
    vote that's all scalping is weaker evidence than a 6-0 vote that
    spans scalping + momentum + advanced.
  * Conviction is high when the leading direction has 3+ strong voters
    AND Opposition reads NEGLIGIBLE or WEAK AND the Cats line shows
    agreement across multiple categories.
  * Opposition STRONG or MODERATE with multiple strong opposing voters
    is real ambiguity — prefer smaller size or skipping.

ACTION HINT — one-line guidance from the labeller. The system's read on what
the state suggests doing. You may override with reasoning.

The legacy fields (Setup, Price, Strategies ensemble, Signal, Funding, Why,
SL/TP, RR) are unchanged — they continue to be the primary trade decision
inputs. The briefing fields are augmentation: a regime-aware second opinion
on each coin's state."""


# Stage 2 phase 3 — strict bounded-count trade contract.
# Replaces TRADE_SYSTEM_PROMPT verbatim when [stage2].enable_zero_two_contract
# is True. Default False so the legacy "always 2+, target 3-6, max 8"
# mandate ships untouched until the live trial flips the flag.
# BRIEFING_SYSTEM_PROMPT_SUFFIX continues to apply on top of either
# base prompt, so the brain still gets the briefing-field instructions
# regardless of contract.
#
# Range update (2026-05-05, operator preference): expanded from "1-2
# trades" to "2-4 trades" to match the wider top-10 candidate set fed
# by [stage2].top_n_to_brain. The setting + constant names are kept
# (``enable_zero_two_contract`` / ``TRADE_SYSTEM_PROMPT_ZERO_TWO``)
# for backward-compatibility; the contract is the same shape (strict
# bounded count) — only the numeric range changed.
TRADE_SYSTEM_PROMPT_ZERO_TWO = """Your aim is to exploit the current market situation and aggressively fetch the maximum profitable trade from these candidates.

Most cycles present genuine opportunities; a dead, thin tape may present none. Overbought conditions are fade setups. Extended moves are exhaustion plays. Range tops are reversal setups. Range bottoms are breakout setups. Pullbacks in trends are continuation entries. Liquidity sweeps are reclaim setups. Your job is to identify which exploitation play matches the current state of each candidate, then take EVERY genuine play across the set — both directions, including the small scalps. Even a thin tape almost always offers a profitable micro-move; scalp it small and tight rather than standing aside. Return zero trades ONLY when no candidate offers ANY tradeable side at all — that is rare, never the default.

For each of the candidates above:

1. Read the FULL evidence: structural data, signals, regime, and ensemble votes — AND this coin's session history (attempts today and their net result), its activity state (regime word, volume ratio, strategies fired), and its true range position (at the low or high is a fade location; BELOW or ABOVE the range is a break in progress, not a floor or ceiling)
2. Identify what kind of opportunity this coin's current state represents
3. Determine the direction and entry that exploits that opportunity
4. Compare across candidates and pick the BEST GENUINE plays — usually 2 to 5; take fewer only when fewer genuinely qualify. The best play is the one whose evidence is strong AND whose context is alive AND which does not repeat a pattern that has already failed today (a heavy losing session, the dead-thin-zero-fired cluster)

Return the 2 to 5 BEST GENUINE plays — this system's entire aim is to EXPLOIT and FETCH MAXIMUM PROFIT from EVERY situation, and across the full candidate set there are usually several genuine exploitable plays once you look past the single obvious trend trade. The profit is there in BOTH directions across the set — pick each trade's direction from that coin's OWN data: a coin in TRENDING_UP is a long, a coin in TRENDING_DOWN is a short, a range edge is a mean-reversion scalp, a pullback is a continuation entry, a liquidity-sweep is a reclaim, and different coins move opposite ways at the same time. WORK every one of the candidates and reach especially for the smaller, shorter, both-direction plays the obvious trade overshadows — those are the ones easy to miss. The aim is profit in whatever direction the situation pays, long OR short: find the side each coin's own evidence supports — there is a profitable direction in most situations, so FIND it and TRADE it; and when ONE candidate genuinely offers no tradeable side on either direction, that single coin is a skip — but that is a per-coin call, never a reason to stand down the whole cycle. The only thing you never do is take a side a coin's data FLATLY contradicts (that is a forced loser, not a play); everything short of that, you exploit. Selection runs on three reads together: evidence strength, context liveness (the Session liveness line and the coin's own volume ratio), and non-repetition of today's proven failures. QUALITY OVER QUOTA: the two patterns that actually destroy sessions are visible in each candidate's OWN briefing — (a) the dead-thin-zero-fired cluster: zero strategies fired (the Strategies line) AND a dead regime AND volume ratio at or below __DEAD_THIN_VOL_RATIO__ (both on the Regime line), and (b) a heavy losing session: the coin has already been attempted __HEAVY_ATTEMPTS_COUNT__ or more times today with a negative net (the session attempts line, when shown). When a candidate shows either pattern, DECLINING that candidate is correct trading, not caution — a forced no-edge entry is a guaranteed bleed, not exploitation. A genuine RR conflict (neither side has both confirmation and reward room) remains a valid skip as before. Grades and interestingness are secondary context only: an X-RAY SKIP grade, interestingness below 0.30, or deep sub-confidence is supporting evidence, and a HIGH interestingness score does NOT redeem a dead-thin-zero-fired candidate. DEFAULT TO ACTION: take a play on EVERY candidate that does not hit one of the three decline patterns above — reach for 3 to 5 or more plays per cycle, including the smaller both-direction scalps. Returning fewer than 3 is correct ONLY when most candidates genuinely hit a decline pattern, not as a habit. Maximum exploitation of every GENUINE edge, best profit, every cycle — long or short, whatever the situation pays.

Aggressive exploitation. Maximum profit. Find the play.

DIRECTION BY REGIME (PER-COIN — there is NO global direction bias):
- Each coin has its own per-coin regime in the Regime line — it is the direction authority.
- Trade WITH each coin's individual regime — a coin in [TRENDING_UP] is bought, a coin in [TRENDING_DOWN] is sold, on that coin's own evidence.
- Coins without a per-coin regime (UNKNOWN): trade on that coin's OWN TA/structure; do NOT fall back to any market-wide bias.
- ranging: BOTH directions allowed — buy at support, sell at resistance.
- volatile: BOTH directions — wider stops, follow momentum.
- dead: BOTH directions but TIGHT TP — scalp micro-moves only.
- RISK-REWARD READ (both sides — NEUTRAL, no lean): the "RR by direction" line shows each side's reward-to-risk. RR is ONE input, NOT a command to take the higher-RR side. Read an extreme one-sided RR critically: when one side's RR is many times the other's, it almost always means price has ALREADY travelled far past the structure on the low-RR side, so that side's reward is spent while the high-RR side is only the distance back to a zone price already left — a "reclaim hope", not a confirmed edge (its anchoring level may be mitigated/spent even when invalid=N). Do NOT take a side merely because its RR is higher. Decide each coin's direction from the WEIGHT of all its evidence together — the coin's own per-coin regime, a valid un-mitigated structural level, the signal, and the ensemble — with NO default lean to either side. Take a side only when that evidence agrees AND that side has real reward room. When the higher-RR side lacks confirmation and the confirmed side lacks room, prefer the CONFIRMED side at SMALL size with an early trailing target to ride momentum into open space — skip only when BOTH sides genuinely lack confirmation. A side with no prior trade history is fine when the full evidence supports it.

FEAR & GREED — market context, NEUTRAL on direction:
- F&G is a market-wide SENTIMENT reading, not a direction instruction. Direction is decided by each coin's OWN per-coin regime and structure; F&G can support EITHER side depending on that coin's data. Do NOT treat fear as "buy" or greed as "sell" by default.
- F&G < 20 (extreme fear): can mark capitulation. In a coin whose own regime is TRENDING_DOWN it CONFIRMS the short (do NOT flip to long merely because fear is high); in a TRENDING_UP coin, or one holding tested support, it can mark an oversold long. Require the coin's own structure to confirm either way.
- F&G > 80 (extreme greed): can mark exhaustion. In a TRENDING_UP coin, protect/trail rather than auto-sell; in a TRENDING_DOWN coin, or at tested resistance, it can mark a short.
- F&G 30-70: ignore; rely on TA + regime + structure.

FOR EACH NEW TRADE, SPECIFY:
- symbol: exact symbol (e.g., ETHUSDT)
- direction: "Buy" or "Sell" (you CAN short)
- stop_loss_price: EXACT price below support (buys) or above resistance (sells)
- take_profit_price: EXACT price at nearest resistance (buys) or support (sells)
- max_hold_minutes: 15-60 (PREFER 15-25 for quick scalps and mean-reversion, longer only for genuine momentum with room to run)
- leverage: 1-5x based on conviction
- size_usd is the MARGIN (the cash) you commit for THIS trade — NOT the position size. Your actual exchange position = size_usd x leverage, so do NOT multiply by leverage yourself. The ACCOUNT block gives the per-trade MARGIN budget ("Per-trade size limit: $Y" = Usable / Maximum concurrent positions), "Available for new trades" (margin still free), and "Maximum concurrent positions" (N). A NEW CYCLE RUNS EVERY ~5 MINUTES and positions ACCUMULATE toward N, so do NOT spend the whole pool now. Set size_usd to about that per-trade margin budget, scaled by conviction (strong setup a bit more, borderline a bit less). Keep the sum of your trades' size_usd within "Available for new trades"; leave room for the trades the next cycles will open. Probe-size trades are not wanted, but neither is draining the book in one cycle. For a quick small scalp or a borderline both-direction play, deliberately size SMALLER (well under the per-trade budget) — small size on a short hold is how you take more genuine plays without over-committing to any one read.
- trailing_activation_pct: 0.3-0.8
- thesis_invalidation: criterion under which the thesis no longer holds (see THESIS INVALIDATION below). Information for the watchdog and for your future self — not a stop-loss substitute.
- reasoning: cite the SPECIFIC per-coin evidence that pushed conviction. Generic reasoning ("good setup", "looks bullish") is rejected.

THESIS INVALIDATION (Mid-Hold Trade Management Fix Phase 3.2, 2026-05-19):
For each new trade, state the criterion under which the thesis is no longer valid. The watchdog monitors the criterion during hold and surfaces it back to you in the next prompt if it fires. You decide what to do with that information — this is information supply, not a directive. Choose ONE of four types:
- "price_close_above" — short justified by a structural ceiling. Value is the price level. Example: bearish OB at 245.30 → {"type":"price_close_above","value":245.30}.
- "price_close_below" — long justified by a structural floor. Value is the price level. Example: bullish FVG at 80000 → {"type":"price_close_below","value":80000}.
- "signal" — trade justified by an ensemble/regime read rather than a specific level. Value is one of: "ensemble_flip_to_strong_buy", "ensemble_flip_to_strong_sell", "regime_inverted", "mtf_alignment_broken".
- "none" — no specific criterion applies (e.g., a pure trend pullback). Value is null.

POSITION GATE:
- Coins marked [POS] in the market data already have an open position.
- Do NOT suggest new trades for any [POS] coin.
- Position management is handled by Call B; do not include position_actions in this response.

RESPOND WITH PURE JSON (no markdown, no explanation). When zero trades qualify, return new_trades: [] — the system handles empty lists correctly.

{"new_trades":[{"symbol":"SYM","direction":"Buy|Sell","stop_loss_price":N,"take_profit_price":N,"max_hold_minutes":N,"leverage":N,"size_usd":N,"trailing_activation_pct":N,"thesis_invalidation":{"type":"price_close_above|price_close_below|signal|none","value":N_or_keyword_or_null},"reasoning":"..."}],"market_view":"...","risk_level":"normal|cautious|aggressive","max_positions":N,"default_leverage":N,"default_sl_pct":N,"default_tp_pct":N,"default_hold_minutes":N,"trailing_activation_pct":N,"focus_coins":[],"avoid_coins":[]}

RULES:
1. Return the 2 to 5 BEST GENUINE plays — the aim is to exploit maximum profit from EVERY situation, long OR short. Examine ALL the candidates and take the best directional play in each that genuinely qualifies (mean-reversion scalps, continuation pullbacks, liquidity-sweep reclaims, both-direction moves) — reach past the single obvious trade. Choose each direction from that coin's own data — find the profitable side; the only side you never take is one the coin's data flatly contradicts. QUALITY OVER QUOTA: declining a candidate whose own briefing shows the dead-thin-zero-fired cluster (zero strategies fired, dead regime, volume ratio at or below __DEAD_THIN_VOL_RATIO__), a heavy losing session (__HEAVY_ATTEMPTS_COUNT__ or more attempts today with negative net), or a genuine neither-side-tradeable RR conflict is correct trading; ABSENT one of those three patterns, you TRADE the coin — default to 3 to 5 or more plays per cycle including the small both-direction scalps. A forced no-edge entry is a bleed, but standing aside from a live tape is missed profit.
2. Use CURRENT prices for SL/TP.
3. SL/TP DIRECTION:
   FOR BUY/LONG: SL BELOW entry, TP ABOVE entry.
   FOR SELL/SHORT: SL ABOVE entry, TP BELOW entry.
4. SL floor is VOLATILITY-AWARE: place SL at or beyond the candidate's "Vol stop floor" (that coin's own noise band, shown per candidate). Absolute minimum 1.5% from entry — tighter is rejected. Wider stop pairs with proportionally smaller size; dollar risk unchanged.
5. NEVER suggest a [POS] coin — it has an open position.
6. PER-COIN regime overrides global regime.
7. Cite the specific evidence block in reasoning.
8. size_usd — PROPER FUNDING: size_usd IS the MARGIN (cash) you commit per trade — set it to about the per-trade margin budget ("Per-trade size limit" = Usable / Maximum concurrent positions), scaled by conviction. Do NOT multiply by leverage yourself (the system applies your leverage to get the position). Keep the sum of size_usd within "Available for new trades"; never drain the whole pool in one cycle.
9. Two independent reads appear per coin and measure DIFFERENT things: "Signal" is the intelligence/sentiment read (news, funding, F&G, OI), and "Strategies/ensemble" is the technical strategy vote. They can disagree (e.g. Signal neutral while the ensemble is GOOD). Neither is automatically authoritative over the other — when they conflict, treat it as genuine uncertainty: let the per-coin regime and structure (X-RAY levels) break the tie, and size smaller. Do not discount a confident ensemble just because Signal is quiet, and do not follow either blindly.
"""


# Stage 2 phase 4 — priority classifier for the section-aware trim.
# Sections in _build_trade_prompt are appended as plain strings; the
# classifier infers priority from a leading-marker substring. Index 0
# is always essential (coaching is the first append). Anything that
# matches no marker defaults to OPTIONAL — better to trim an
# unrecognised section than risk discarding an essential one we
# forgot to enumerate. Used only when [stage2].enable_priority_trim
# is True; the legacy pop-from-end path is byte-identical when False.

_TRIM_PRIORITY_ESSENTIAL = 1
_TRIM_PRIORITY_IMPORTANT = 2
_TRIM_PRIORITY_OPTIONAL = 3

# Markers that indicate ESSENTIAL content (never trimmed).
_TRIM_ESSENTIAL_MARKERS = (
    "## MARKET DATA",
    "## ACCOUNT",
    "## CAPITAL POSITION",
    "## TRADE CANDIDATES",
    "## OPEN POSITIONS",
    "## CURRENT POSITIONS",
    "## BYBIT EXCHANGE POSITIONS",
    # Issue E22 (2026-05-28): the legacy ``_build_context_prompt`` appends the
    # held-symbols HARD CONSTRAINT as a plain "You ALREADY HOLD: ..." section
    # with NO "##" header, so it defaulted to OPTIONAL and could be trimmed.
    # NOTE on live scope (cross-check finding): the LIVE brain calls
    # create_trade_plan (Call-A -> _build_trade_prompt) and create_position_plan
    # (Call-B -> _build_position_prompt); it does NOT call create_strategic_plan
    # / _build_context_prompt (legacy, used only by scripts/run_30min_test.py).
    # The live held-constraint was already protected by the E16 fix in
    # _build_trade_prompt (its "## OPEN POSITIONS" header is ESSENTIAL; that
    # block's comment notes it "also addresses companion E22"), and the live
    # Call-B prompt is compact with no priority trim. So this marker reuses
    # #13's mechanism to HARDEN the legacy path for belt-and-suspenders; it is
    # correct and harmless but does not close a live gap (E16 already did).
    "ALREADY HOLD",
    "TRADEABLE COINS THIS CYCLE",
    # Issue A fix (2026-05-08): the live CALL_A urgent block is emitted by
    # ``src/core/urgent_queue.py:format_for_prompt`` whose header reads
    # ``"\n## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED\n"``.
    # The prior marker ``"OVERRIDE — URGENT WATCHDOG ALERTS"`` only ever
    # matched a system-prompt fragment appended at strategist.py:694
    # (which is system_prompt text, not a user-prompt section in the
    # ``sections`` list). It never matched the live URGENT block, so
    # ``_infer_section_priority`` classified it OPTIONAL and the trim
    # dropped it first — confirmed by 14 ``URGENT WATCHDOG`` occurrences
    # in ``CLAUDE_PROMPT_TRIMMED`` ``dropped_labels`` over the 13:00–16:00
    # 2026-05-08 window. Substring ``"## URGENT WATCHDOG ALERTS"`` matches
    # the live header and is short enough to also catch any future header
    # variant that keeps the tag.
    "## URGENT WATCHDOG ALERTS",
    "## REGIME-SPECIFIC TRADING INSTRUCTIONS",
    # Issue 4 of 2026-05-19 direction-bias fix: header text was
    # "## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)" — replaced with
    # "## MARKET REGIME (CONTEXT)" to remove directive-flavoured framing.
    # Both substrings retained so any in-flight prompts (e.g. legacy logs
    # being replayed) still classify the section as ESSENTIAL.
    "## MARKET REGIME (CONTEXT)",
    "## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)",
    # XRAY phase-5 fix — capital sizing contract emitted by
    # ``tiered_capital.FundLimits.to_prompt_text`` as
    # ``"FUND RULES (non-negotiable):"``. The header lacks the ``##``
    # prefix used elsewhere so pre-fix it fell through to OPTIONAL and
    # was dropped first when the 14k cap fired. Substring match is
    # sufficient because ``_infer_section_priority`` looks at the first
    # 200 characters of each section and the FUND RULES emitter places
    # this prefix at the top of its block. Dropping FUND RULES leaves
    # Claude without the max-single-trade / max-positions limits.
    "FUND RULES",
    # Aggressive-framing rewrite (2026-05-05) — _build_trade_prompt no
    # longer emits ``FUND RULES (non-negotiable):`` and the
    # ``FundLimits.to_prompt_text()`` block. The replacement is two
    # clean lines led by ``Per-trade size limit:`` (no header). Without
    # this marker the new block falls through to OPTIONAL and the
    # priority-aware trim drops it first — leaving Claude with no
    # numeric ceiling on size_usd. The legacy ``FUND RULES`` marker
    # above stays in place as defense-in-depth: the dead
    # ``_build_context_prompt:1356`` still calls ``to_prompt_text()``
    # and OBS-1 cleanup will retire that path separately.
    "Per-trade size limit",
    # XRAY phase-5 follow-up — promote TODAY'S PERFORMANCE to ESSENTIAL.
    # The Phase 0 baseline (2026-05-05) captured three real cycles where
    # ``CLAUDE_PROMPT_TRIMMED`` dropped both "Trades today: 0" and
    # "Daily PnL: +0.00%" together with the FUND RULES section header.
    # Both lines emit from the TODAY'S PERFORMANCE section rendered at
    # strategist.py:1352 and :2722 — they carry the daily-trade count
    # and PnL context Claude uses to calibrate sizing and risk per the
    # FUND RULES tier definition. Without them Claude has no view on
    # how many trades it has already taken today, which materially
    # affects the size_usd choice on a tier whose allocation depends
    # on cumulative daily activity. Promoting the marker matches the
    # FUND RULES protection contract: same Phase 4 priority-trim path,
    # same substring-match logic, same first-200-char window.
    "## TODAY'S PERFORMANCE",
    "## TODAY:",
    # Issue A fix (2026-05-08): three single-line metadata sections are
    # appended at ``_build_trade_prompt`` lines 2861/2862/2904 as their
    # own elements in the ``sections`` list, each without a leading
    # ``##`` header — so they fell through to OPTIONAL and were dropped
    # first by the priority-aware trim. All 21 priority-mode trim events
    # in the 13:00–16:00 2026-05-08 window dropped these three lines.
    # Adding the bare-line substrings here mirrors the protection already
    # granted to the sibling ``Per-trade size limit:`` line above (added
    # in commit b25148c0). The classifier scans only the first 200 chars
    # of each section, so substring match is sufficient regardless of the
    # interpolated dollar amount or count value.
    "Equity:",
    "Available:",
    "Maximum concurrent positions",
    # Fund-management enrichment (2026-05-31) — the brain sizes against these,
    # so they must survive token-trim (bare lines, no header).
    "Open trades:",
    # Brain-Awareness Prompt Additions — Addition 2 (2026-06-09): the book-tilt
    # awareness line sits in the ACCOUNT section; protect it from the priority
    # trim like its sibling ACCOUNT lines so the brain's directional-tilt context
    # cannot be silently dropped under token pressure.
    "Book tilt:",
    # Four-Element Prompt Recalibration, Element 4 (2026-06-11): the
    # session-liveness line sits directly under the market-context line
    # and tells the brain whether the tape is live, mixed, or thin —
    # protect it from the priority trim like its market-context sibling.
    # (Deliberately does NOT start with "## SESSION", which is an
    # OPTIONAL marker.)
    "Session liveness:",
    "Used funds:",
    "Usable funds:",
    "Available for new trades",
    "FUNDING SITUATION",
)

# Markers that indicate IMPORTANT content (trim only after optional).
_TRIM_IMPORTANT_MARKERS = (
    "## DIRECTION PERFORMANCE",
    "## REGIME DIVERGENCE",
    "## STRATEGY HINTS",
    "## DAILY",
    "Trading Mode:",
    "## SETUP",
)

# Markers that indicate OPTIONAL content (first to trim).
_TRIM_OPTIONAL_MARKERS = (
    "## SENTIMENT",
    "## SESSION",
    "## X-RAY STRUCTURAL SETUPS",
    "## RECENT LESSONS",
    "(market data error",
    "(... ",
)


def _summarize_kept_protections(
    sections: list[str],
) -> tuple[int, list[str]]:
    """Summarise which ESSENTIAL marker categories survive in ``sections``.

    Walks each surviving section, scans the first 200 characters for
    the FIRST matching ``_TRIM_ESSENTIAL_MARKERS`` substring (mirroring
    ``_infer_section_priority``'s contract), and accumulates:

    * ``kept_count``: how many sections carry an essential marker.
    * ``kept_categories``: sorted list of unique markers matched —
      operators inspecting ``CLAUDE_PROMPT_TRIMMED`` log lines can
      confirm the right essentials made it through trim without
      reading source.

    Used by ``_build_trade_prompt``'s priority-aware trim block to
    enrich the trim emit with ``protected_kept`` and
    ``protected_categories`` fields. Index-0 coaching is forced
    essential by ``_infer_section_priority`` but has no marker; it is
    NOT counted here (the contract is "category coverage", not
    "essentials including coaching"). Matches the pre-trim
    classifier's behaviour for index > 0.

    Args:
        sections: The surviving prompt sections (post-trim, pre-footer).

    Returns:
        ``(kept_count, kept_categories)`` — kept_categories is a sorted
        list so the log line is deterministic across runs.
    """
    kept_count = 0
    kept_categories: set[str] = set()
    for s in sections:
        if not s:
            continue
        head = s[:200]
        for marker in _TRIM_ESSENTIAL_MARKERS:
            if marker in head:
                kept_count += 1
                kept_categories.add(marker)
                break
    return kept_count, sorted(kept_categories)


def _detect_essential_drift(
    dropped_labels: list[str],
) -> list[tuple[str, str]]:
    """Return (marker, dropped_label) pairs for any dropped label that
    contains a substring from ``_TRIM_ESSENTIAL_MARKERS``.

    A non-empty result indicates classifier-vs-trim drift: the section
    was dropped, yet its label exposes an essential-marker substring
    that the priority-aware trim is contractually obliged to preserve.
    Used by the trim block in ``_build_trade_prompt`` to emit the
    ``STRAT_TRIM_ESSENTIAL_DROPPED`` warning so silent marker-vs-header
    drift surfaces at log-tail time instead of silently degrading
    Claude's context (the exact failure mode that drove the
    Issue A 2026-05-08 fix).

    Args:
        dropped_labels: The label strings recorded by the priority trim
            for each section it dropped (already truncated to 60 chars).

    Returns:
        A list of ``(marker, label)`` tuples — empty when no drift.
        Each label is paired with the FIRST essential marker it
        contains; downstream callers see one entry per drifted label.
    """
    drift: list[tuple[str, str]] = []
    for label in dropped_labels:
        for marker in _TRIM_ESSENTIAL_MARKERS:
            if marker in label:
                drift.append((marker, label))
                break
    return drift


def _infer_section_priority(content: str, index: int) -> int:
    """Classify a prompt section by content-prefix marker matching.

    Args:
        content: The section's full string (newline-prefixed in many cases).
        index: The section's position in the assembled list. Position 0
            is always the coaching block, which is essential.

    Returns:
        ``_TRIM_PRIORITY_ESSENTIAL`` (1), ``_TRIM_PRIORITY_IMPORTANT`` (2),
        or ``_TRIM_PRIORITY_OPTIONAL`` (3). Unmatched content defaults
        to OPTIONAL.
    """
    if index == 0:
        return _TRIM_PRIORITY_ESSENTIAL
    head = content[:200] if content else ""
    for marker in _TRIM_ESSENTIAL_MARKERS:
        if marker in head:
            return _TRIM_PRIORITY_ESSENTIAL
    for marker in _TRIM_IMPORTANT_MARKERS:
        if marker in head:
            return _TRIM_PRIORITY_IMPORTANT
    return _TRIM_PRIORITY_OPTIONAL


class ClaudeStrategist:
    """Builds market context, calls Claude, parses strategic plan."""

    def __init__(self, claude_client, services: dict, settings) -> None:
        self.claude = claude_client
        self.services = services
        self.settings = settings
        # Item 2 (entry-gaps investigation, 2026-05-26): boot sentinel for the
        # expected-winner-magnitude advisory. Confirms the flag state at boot.
        if getattr(getattr(settings, "brain", None),
                   "entry_magnitude_advisory_enabled", False):
            log.info(
                "MAGNITUDE_ADVISORY_SENTINEL | enabled=True | strategist appends "
                "MAG=HIGH/MED/LOW expected-winner-magnitude advisory to coin "
                "volatility lines (Item 2 entry-gaps 2026-05-26)"
            )
        # Cached regime/sentiment for Call B (set during Call A's _build_trade_prompt)
        self._last_regime_str: str = "unknown"
        self._last_regime_confidence: float = 0.5
        self._last_fg_value: int = 50
        # UrgentQueue: flag set during prompt building, read during response parsing
        self._has_urgent_concerns: bool = False
        # Mid-Hold Trade Management Fix Phase 3.7/3.8 (2026-05-19) — list
        # of thesis_event row IDs rendered into the most recent CALL_A
        # or CALL_B prompt. Populated by _build_context_prompt /
        # _build_position_prompt; consumed (mark_events_consumed) by
        # create_trade_plan / create_position_plan after the Claude
        # response returns successfully. Reset to [] before each prompt
        # build to avoid double-consume across cycles.
        self._last_callA_event_ids: list[int] = []
        self._last_callB_event_ids: list[int] = []
        # Phase 2 (P0-1) — symbols flagged invalid by the close-broadcast hub
        # since the last prompt build. The next prompt build clears the set
        # after using it to skip stale-position rendering.
        self._invalidated_positions: set[str] = set()
        # CALL_B Framing Fix Phase 1B (2026-05-06) — boot sentinel that the
        # reframed POSITION_SYSTEM_PROMPT (version 2) is the one loaded into
        # memory. Operators tail this once per service restart to confirm
        # the framing fix is live before relying on the per-cycle behaviour.
        log.info(
            f"STRAT_CALL_B_REFRAMED | system_prompt_version={POSITION_SYSTEM_PROMPT_VERSION} "
            f"close_rules_removed=2 contract=aggressive_management | {ctx()}"
        )
        # Issue 4 of 2026-05-19 direction-bias fix Phase A. Boot sentinel
        # mirrors the STRAT_CALL_B_REFRAMED precedent. Confirms the
        # symmetric scenario-driven MARKET REGIME block at
        # _build_trade_prompt:3371+ and its dead duplicate at 1416+ is in
        # memory. mode=symmetric_scenario distinguishes from the previous
        # asymmetric "DEFAULT SELL BIAS" wording (block_version=1).
        # Layer 4 (2026-05-22) — boot sentinel for the Consensus-Truth
        # framing. Mirrors the STRAT_CALL_B_REFRAMED / _REGIME_INSTR_REFRAMED
        # precedent: one line per service restart that confirms the L4
        # truthful-context note is wired into the CALL_A prompt. Operators
        # tail this once to confirm the framing is live before relying on
        # the per-cycle behaviour. _OFF when the operator has flipped the
        # flag for instant rollback.
        # Defensive nested attribute access: legacy callers and many
        # existing tests construct ClaudeStrategist with a stub
        # ``settings`` (e.g., types.SimpleNamespace) that lacks the
        # ``.strategy_engine`` sub-object. The boot log must not break
        # those callers — fall back to the True default if the path
        # isn't fully populated. Production Settings always has it.
        try:
            _l4_on = bool(
                settings.strategy_engine.brain_prompt_l4_consensus_context_enabled
            )
        except AttributeError:
            _l4_on = True
        log.info(
            f"BOOT_L4_CONSENSUS_CONTEXT_{'ON' if _l4_on else 'OFF'} | "
            f"flag=brain_prompt_l4_consensus_context_enabled={_l4_on} "
            f"truthful_framing={'live' if _l4_on else 'rolled_back'} | {ctx()}"
        )
        # Candidate-Block Data Integrity Fix — Issue 1 (2026-06-09) — boot
        # sentinel confirming the direction-disagreement labeling flag is
        # loaded, so operators can grep BOOT_DIR_DISAGREEMENT_NOTES at log-tail
        # time to confirm the labeled-conflict rendering is live. Defensive
        # getattr keeps stub-settings test callers working (default True).
        _dd_on = bool(
            getattr(
                getattr(self, "settings", None) or settings,
                "brain", None,
            ) and getattr(
                getattr(settings, "brain", None),
                "emit_direction_disagreement_notes", True,
            )
        ) if getattr(settings, "brain", None) is not None else True
        log.info(
            f"BOOT_DIR_DISAGREEMENT_NOTES_{'ON' if _dd_on else 'OFF'} | "
            f"flag=emit_direction_disagreement_notes={_dd_on} "
            f"scope=signal_vs_xray+ensemble_vs_xray+votes_poll_consistency | {ctx()}"
        )
        # Candidate-Block Data Integrity Fix — Issue 4 (2026-06-09) — boot
        # sentinel for the fear-greed components demotion flag.
        _fgd_on = bool(
            getattr(
                getattr(settings, "brain", None),
                "fear_greed_components_demote_enabled", True,
            )
        ) if getattr(settings, "brain", None) is not None else True
        log.info(
            f"BOOT_FG_COMPONENTS_DEMOTE_{'ON' if _fgd_on else 'OFF'} | "
            f"flag=fear_greed_components_demote_enabled={_fgd_on} "
            f"effect={'fear_greed_held_out_of_top5_and_tagged' if _fgd_on else 'prior_magnitude_ranking'} "
            f"| {ctx()}"
        )
        # Five-Fix Follow-Up — Fix 1 (components purity, 2026-06-10) — boot
        # sentinel for the diagnostics-exclusion flag (Rule 9/12: every new
        # config key gets a boot sentinel confirming load and state).
        _cdx_on = bool(
            getattr(
                getattr(settings, "brain", None),
                "components_diagnostics_excluded", True,
            )
        ) if getattr(settings, "brain", None) is not None else True
        log.info(
            f"BOOT_COMPONENTS_DIAGNOSTICS_{'EXCLUDED' if _cdx_on else 'INCLUDED'} | "
            f"flag=components_diagnostics_excluded={_cdx_on} "
            f"keys={sorted(COMPONENT_DIAGNOSTIC_KEYS)} "
            f"effect={'diagnostics_absent_from_components_line' if _cdx_on else 'numeric_diagnostics_may_rank'} "
            f"bool_render_guard=always_on | {ctx()}"
        )
        # Conditional X-RAY authority (2026-06-11) — boot sentinel (Rule 9/12).
        _xa_cfg = getattr(settings, "brain", None)
        _xa_on_boot = bool(getattr(
            _xa_cfg, "xray_authority_conditional_enabled", True,
        )) if _xa_cfg is not None else True
        log.info(
            f"BOOT_XRAY_AUTHORITY_{'CONDITIONAL' if _xa_on_boot else 'UNCONDITIONAL'} | "
            f"flag=xray_authority_conditional_enabled={_xa_on_boot} "
            f"min_score={float(getattr(_xa_cfg, 'xray_authority_min_score', 45.0)) if _xa_cfg is not None else 45.0} "
            f"weak_when=counter_trade_or_skip_grade "
            f"effect={'weak_xray_yields_no_authority_and_withholds_same_side_hint' if _xa_on_boot else 'prior_unconditional_authority'} "
            f"| {ctx()}"
        )
        # Candidate-Block Data Integrity Fix — Issue 3 follow-up (2026-06-09) —
        # boot sentinel confirming the Components-line decimal precision loaded
        # (Rule 12: every new config key gets a boot sentinel).
        _comp_prec_boot = int(
            getattr(
                getattr(settings, "stage2", None),
                "component_precision_decimals", 4,
            )
        ) if getattr(settings, "stage2", None) is not None else 4
        log.info(
            f"BOOT_COMPONENT_PRECISION | "
            f"component_precision_decimals={_comp_prec_boot} "
            f"(per-coin Components line decimal places) | {ctx()}"
        )
        # Brain-Awareness Prompt Additions — Addition 2 (2026-06-09) — boot
        # sentinel confirming the book-tilt awareness flag + thresholds loaded
        # (Rule 13: new config gets a boot sentinel).
        _bt_cfg = getattr(settings, "brain", None)
        _bt_on = bool(getattr(_bt_cfg, "book_tilt_enabled", True)) if _bt_cfg is not None else True
        log.info(
            f"BOOT_BOOK_TILT_{'ON' if _bt_on else 'OFF'} | "
            f"flag=book_tilt_enabled={_bt_on} "
            f"small_count={getattr(_bt_cfg, 'book_tilt_small_count', 2)} "
            f"one_sided_ratio={getattr(_bt_cfg, 'book_tilt_one_sided_ratio', 3.0)} "
            f"scope=account_section_long_vs_short_awareness | {ctx()}"
        )
        # Four-Element Prompt Recalibration, Element 1 (2026-06-11) — boot
        # sentinel confirming the re-keyed quality-over-quota thresholds
        # loaded (Rule 9/12: centralized config gets a boot sentinel).
        # skip_keys_version=2 = dead-thin-zero-fired cluster + heavy losing
        # session are the primary skip currency; grades/interestingness
        # demoted to secondary context.
        log.info(
            f"BOOT_QUALITY_SKIP_KEYS | "
            f"thin_vol_ratio={getattr(_bt_cfg, 'quality_skip_thin_vol_ratio', 0.25)} "
            f"heavy_attempts={getattr(_bt_cfg, 'quality_skip_heavy_attempts', 6)} "
            f"skip_keys_version={TRADE_SYSTEM_PROMPT_SKIP_KEYS_VERSION} "
            f"scope=system_prompt_quality_over_quota | {ctx()}"
        )
        # Four-Element Prompt Recalibration, Element 2 (2026-06-11) — boot
        # sentinel for the session-attempt memory line (Rule 12). The heavy
        # threshold is the SHARED quality_skip_heavy_attempts key.
        _sa_on = bool(getattr(
            _bt_cfg, "session_attempts_enabled", True,
        )) if _bt_cfg is not None else True
        log.info(
            f"BOOT_SESSION_ATTEMPTS_{'ON' if _sa_on else 'OFF'} | "
            f"flag=session_attempts_enabled={_sa_on} "
            f"heavy_min={getattr(_bt_cfg, 'quality_skip_heavy_attempts', 6)} "
            f"source=trade_log_read_only "
            f"scope=call_a_per_coin_awareness_line | {ctx()}"
        )
        # Four-Element Prompt Recalibration, Element 4 (2026-06-11) — boot
        # sentinel for the session-liveness market-context line (Rule 12).
        _lv_on = bool(getattr(
            _bt_cfg, "session_liveness_enabled", True,
        )) if _bt_cfg is not None else True
        log.info(
            f"BOOT_SESSION_LIVENESS_{'ON' if _lv_on else 'OFF'} | "
            f"flag=session_liveness_enabled={_lv_on} "
            f"thin_vol_ratio={getattr(_bt_cfg, 'session_liveness_thin_vol_ratio', 0.25)} "
            f"live_max_thin_share={getattr(_bt_cfg, 'session_liveness_live_max_thin_share', 0.20)} "
            f"thin_min_thin_share={getattr(_bt_cfg, 'session_liveness_thin_min_thin_share', 0.60)} "
            f"scope=market_context_awareness_line | {ctx()}"
        )
        # Four-Element Prompt Recalibration, Element 3 (2026-06-11) — boot
        # sentinel for the range-truth render gate (Rule 12). When ON, the
        # Structure line and the compact X-RAY pos= sites append the
        # pre-clamp BELOW/ABOVE RANGE marker with the overshoot percent.
        _rt_cfg = getattr(settings, "structure", None)
        _rt_on = bool(getattr(
            _rt_cfg, "range_truth_enabled", True,
        )) if _rt_cfg is not None else True
        log.info(
            f"BOOT_RANGE_TRUTH_{'ON' if _rt_on else 'OFF'} | "
            f"flag=range_truth_enabled={_rt_on} "
            f"overshoot_unit=pct_of_broken_boundary_price "
            f"scope=structure_line+xray_pos_sites | {ctx()}"
        )
        log.info(
            f"STRAT_REGIME_INSTR_REFRAMED | block_version={STRAT_REGIME_BLOCK_VERSION} "
            f"mode={'per_coin_authority' if bool(getattr(getattr(self.settings, 'stage2', None), 'per_coin_direction_enabled', True)) else 'global_direction_rollback'} "
            f"| {ctx()}"
        )
        # Mid-Hold Trade Management Fix Phase 3.2 (2026-05-19) — boot
        # sentinel that the THESIS INVALIDATION section + the
        # thesis_invalidation field in the new_trades JSON schema are
        # live in TRADE_SYSTEM_PROMPT and TRADE_SYSTEM_PROMPT_ZERO_TWO.
        # Mirrors the STRAT_CALL_B_REFRAMED and STRAT_REGIME_INSTR_REFRAMED
        # precedents so operators can grep STRAT_TRADE_PROMPT_VERSION at
        # log-tail time to confirm the version Claude is actually
        # receiving. Bumped by Phase 3.10 tuning passes when wording or
        # signal-keyword list changes.
        log.info(
            f"STRAT_TRADE_PROMPT_VERSION | "
            f"thesis_invalidation_version="
            f"{TRADE_SYSTEM_PROMPT_THESIS_INVALIDATION_VERSION} "
            f"activity_version={TRADE_SYSTEM_PROMPT_ACTIVITY_VERSION} "
            f"skip_keys_version={TRADE_SYSTEM_PROMPT_SKIP_KEYS_VERSION} "
            f"premise_version={TRADE_SYSTEM_PROMPT_PREMISE_VERSION} "
            f"method_version={TRADE_SYSTEM_PROMPT_METHOD_VERSION} "
            f"target_play_count="
            f"{getattr(getattr(getattr(self, 'settings', None), 'brain', None), 'brain_target_play_count', 3)} "
            f"pref_hold_max="
            f"{getattr(getattr(getattr(self, 'settings', None), 'brain', None), 'brain_preferred_hold_minutes_max', 25)} "
            f"section=thesis_invalidation+exploit_breadth "
            f"prompt_chars={len(TRADE_SYSTEM_PROMPT)} "
            f"zero_two_prompt_chars={len(TRADE_SYSTEM_PROMPT_ZERO_TWO)} "
            f"| {ctx()}"
        )
        # F33 (2026-06-05) boot sentinel — confirm the confluence-veto presentation
        # config loaded so the operator can see the note is armed and tunable.
        _sc = getattr(getattr(self, "settings", None), "structure", None)
        log.info(
            f"STRAT_CONFLUENCE_VETO_CONFIG | "
            f"enabled={getattr(_sc, 'confluence_veto_note_enabled', True)} "
            f"rr_floor={getattr(_sc, 'confluence_veto_rr_floor', 1.0)} "
            f"ratio={getattr(_sc, 'confluence_veto_ratio', 2.0)} | {ctx()}"
        )

    # ─── Phase 2 (P0-1) — close-broadcast hooks ──────────────────────────

    def invalidate_position(self, symbol: str) -> None:
        """Mark a symbol's position as closed since last prompt build.

        Called by the close-broadcast callback (manager.py) so that the
        very next ``_build_*`` does not render or reason about a position
        Shadow has already retired. Pairs with ``refresh_positions`` for
        the prompt-time fresh fetch.
        """
        self._invalidated_positions.add(symbol)
        # Phase 11 Gap F2 (output-quality obs): promote from DEBUG to
        # INFO with structured tag. Position invalidations are
        # operationally significant (the close-broadcast just retired
        # a position) and should appear at default INFO log level.
        log.info(
            f"POSITION_INVALIDATED | sym={symbol} reason=close_broadcast "
            f"invalidated_count={len(self._invalidated_positions)} | {ctx()}"
        )
        # Back-compat: keep the original DEBUG tag for any downstream
        # parser that depends on it.
        log.debug(
            f"STRAT_POS_INVALIDATE | sym={symbol} pending={len(self._invalidated_positions)} | {ctx()}"
        )

    def _has_blocking_price_divergence(self) -> bool:
        """Phase 3 (P0-2 Fix D) — pure check used by ``create_position_plan``.

        Returns True when the transformer's last enrichment observed a
        |local-vs-Shadow| divergence above
        ``settings.price.divergence_block_prompt_pct`` on any position.
        Pure / cheap: just reads the cached counter on the transformer
        instance — no DB or network calls.
        """
        tf = self.services.get("transformer")
        if tf is None:
            return False
        max_div = float(getattr(tf, "_last_enrichment_max_divergence_pct", 0.0) or 0.0)
        threshold = float(
            getattr(self.settings, "price", None) and
            getattr(self.settings.price, "divergence_block_prompt_pct", 1.0)
            or 1.0
        )
        return max_div > threshold

    async def refresh_positions(self) -> list:
        """Force-fetch live positions from the position service.

        Bypasses any caching layer in this class. Used at the top of
        prompt builders so Claude never reasons over a position the
        watchdog already reconciled as closed (P1-17).
        """
        position_service = self.services.get("position_service")
        if position_service is None:
            return []
        try:
            positions = await position_service.get_positions()
        except Exception as e:
            log.warning(
                f"STRAT_REFRESH_FAIL | err='{str(e)[:120]}' | {ctx()}"
            )
            return []
        # Drain the invalidation set on a successful fetch — its only purpose
        # was to flag stale state between close and next prompt; once we have
        # the live set, the flag is consumed.
        if self._invalidated_positions:
            # P2 of P1-P10: source=shadow_live mislabel (audit L2-G3).
            # The fetch is mode-routed through the position proxy — could
            # be Shadow, Bybit demo, or Bybit live. Use a mode-agnostic
            # label so log analysis is honest.
            log.info(
                f"STRAT_PROMPT_REFRESH | n_positions={len(positions)} "
                f"source=proxy_live cleared_invalidated={len(self._invalidated_positions)} | {ctx()}"
            )
            self._invalidated_positions.clear()
        return positions

    async def create_strategic_plan(self) -> StrategicPlan | None:
        """Build context, call Claude, parse plan."""
        _cycle_start = time.time()
        did = new_decision_id()
        log.info(f"STRAT_CYCLE_START | did={did} | {ctx()}")
        try:
            prompt = await self._build_context_prompt()
            log.info(f"STRAT_PROMPT | chars={len(prompt)} | {ctx()}")

            # Element 1 (2026-06-11) — the legacy combined prompt shares
            # the quality-over-quota passages, so the same threshold
            # resolution applies here (placeholder tokens must never
            # reach Claude on any path).
            _brain_cfg_legacy = getattr(self.settings, "brain", None)
            raw_response = await self.claude.send_message(
                prompt,
                _resolve_prompt_calibration(
                    STRATEGIST_SYSTEM_PROMPT,
                    thin_vol_ratio=float(getattr(
                        _brain_cfg_legacy, "quality_skip_thin_vol_ratio", 0.25,
                    )),
                    heavy_attempts=int(getattr(
                        _brain_cfg_legacy, "quality_skip_heavy_attempts", 6,
                    )),
                ),
            )

            if hasattr(self.claude, "extract_json"):
                plan_data = self.claude.extract_json(raw_response)
            else:
                plan_data = json.loads(raw_response)

            plan = self._parse_plan(plan_data)

            # Enterprise log: plan summary
            log.info(
                f"STRAT_PLAN | trades={len(plan.new_trades)} acts={len(plan.position_actions)} "
                f"risk={plan.risk_level} view='{str(plan.market_view)[:80]}' | {ctx()}"
            )

            # Enterprise log: each trade directive
            for i, t in enumerate(plan.new_trades):
                sym = t.get("symbol", "?") if isinstance(t, dict) else getattr(t, "symbol", "?")
                d = t.get("direction", "?") if isinstance(t, dict) else getattr(t, "direction", "?")
                lev = t.get("leverage", 1) if isinstance(t, dict) else getattr(t, "leverage", 1)
                sl = t.get("stop_loss_price", 0) if isinstance(t, dict) else getattr(t, "stop_loss_price", 0)
                tp = t.get("take_profit_price", 0) if isinstance(t, dict) else getattr(t, "take_profit_price", 0)
                rsn = t.get("reasoning", "") if isinstance(t, dict) else getattr(t, "reasoning", "")
                log.info(f"STRAT_DIRECTIVE | #{i+1} sym={sym} dir={d} lev={lev} sl={sl} tp={tp} rsn='{str(rsn)[:80]}' | {ctx()}")

            # Enterprise log: each position action
            for sym, act in plan.position_actions.items():
                log.info(f"STRAT_POS_ACT | sym={sym} act={act.action} rsn='{str(act.reason)[:80]}' | {ctx()}")

            # Enterprise log: no trades warning
            if not plan.new_trades and not plan.position_actions:
                log.warning(f"STRAT_NO_TRADES | view='{str(plan.market_view)[:100]}' risk={plan.risk_level} | {ctx()}")

            _elapsed = (time.time() - _cycle_start) * 1000
            log.info(f"STRAT_CYCLE_END | el={_elapsed:.0f}ms trades={len(plan.new_trades)} acts={len(plan.position_actions)} | {ctx()}")
            return plan

        except Exception as e:
            log.error(f"STRAT_PLAN_FAIL | err='{str(e)[:500]}' | {ctx()}")
            # Phase 12.2 (lifecycle-logging-audit Gap 2.10-G1): deleted prose
            # duplicate of STRAT_PLAN_FAIL above.
            _elapsed = (time.time() - _cycle_start) * 1000
            log.info(f"STRAT_CYCLE_END | el={_elapsed:.0f}ms trades=0 acts=0 failed=Y | {ctx()}")
            return None

    async def review_positions(self, positions) -> dict:
        """Quick position review — called every 30 seconds by watchdog."""
        if not positions:
            return {}

        try:
            prompt = await self._build_position_review_prompt(positions)

            system = (
                "You are reviewing open trading positions. For each position, "
                "decide: hold, close, tighten_stop (provide new_sl price), "
                "set_exit (provide exit_price), or take_profit. "
                "Include the original trade thesis context when deciding. "
                'Respond with JSON only: {"SYMBOL": {"action": "...", "reason": "..."}}'
            )

            raw_response = await self.claude.send_message(prompt, system)

            if hasattr(self.claude, "extract_json"):
                return self.claude.extract_json(raw_response)
            else:
                return json.loads(raw_response)

        except Exception as e:
            # Phase 12.2 (lifecycle-logging-audit Gap 2.10-G2): structured tag.
            log.error(
                f"STRAT_POS_REVIEW_FAIL | err='{str(e)[:500]}' | {ctx()}"
            )
            return {}

    # ═══ DUAL API METHODS ═══

    async def create_trade_plan(self) -> StrategicPlan | None:
        """CALL A: Find new trades. Focused prompt, no position data.

        Observability G1 (try/finally pairing): every entry into this
        function pairs with exactly one ``STRAT_CALL_A_END`` emission,
        regardless of exit path. Cancellation
        (``asyncio.CancelledError``, a ``BaseException``) is the gap
        the audited 12/10 START:END pairing exposed — the prior
        ``try/except Exception`` blocks did not catch
        ``BaseException``, leaving cancellation silent.
        """
        _cycle_start = time.time()
        did = new_decision_id()
        log.info(f"STRAT_CALL_A_START | did={did} | {ctx()}")

        _status = "success"
        _trades_count = 0
        _prompt_chars = 0
        _sys_prompt_chars = 0

        try:
            # Post-Execution Closure Fix Phase 3 (2026-05-05) — skip CALL_A
            # entirely when the scanner has produced zero packages. Without
            # packages there is nothing to exploit, and historically Claude
            # produced ghost trades from cached/held-position context. The
            # skip preserves the null contract (return None as on failure)
            # so callers do not need a new branch. layer_manager absence
            # OR ``brain.use_packages=False`` falls THROUGH to the existing
            # path; the skip applies only to the explicit cold-start
            # "scanner ready, zero qualified packages" case.
            try:
                _use_packages = bool(getattr(
                    self.settings.brain, "use_packages", True,
                ))
                if _use_packages:
                    _lm = self.services.get("layer_manager")
                    if _lm is not None and hasattr(_lm, "get_coin_packages"):
                        _pkgs = _lm.get_coin_packages()
                        if not _pkgs:
                            log.warning(
                                f"STRAT_CALL_A_SKIPPED | "
                                f"reason=no_packages_available count=0 "
                                f"did={did} | {ctx()}"
                            )
                            _status = "skipped"
                            return None
            except Exception as e:
                # Pre-check failure must NOT abort the cycle — fall through
                # to the normal path so degraded telemetry never blocks
                # trade discovery. Any real failure surfaces in the existing
                # STRATEGIST_PACKAGES_READ branch downstream.
                # Phase 12.2 (lifecycle-logging-audit Gap 2.1-G2): promoted from
                # DEBUG to WARNING. Pre-check exception is rare but operationally
                # meaningful (layer_manager.get_coin_packages threw).
                log.warning(
                    f"STRAT_CALL_A_PRECHECK_ERR | err='{str(e)[:150]}' | {ctx()}"
                )

            prompt = await self._build_trade_prompt()
            _prompt_chars = len(prompt)
            log.info(f"STRAT_CALL_A | chars={_prompt_chars} | {ctx()}")

            # System prompt — add urgent addendum if watchdog concerns present.
            # Stage 2 phase 3 — when [stage2].enable_zero_two_contract is True,
            # the strict 0-2 base prompt replaces TRADE_SYSTEM_PROMPT.
            # BRIEFING_SYSTEM_PROMPT_SUFFIX still applies on top either way
            # so briefing-mode field instructions reach Claude regardless of
            # contract. Default False keeps the legacy "always 2+" prompt
            # byte-identical until the trial flip.
            _stage2_cfg_a = getattr(self.settings, "stage2", None)
            _zero_two = bool(getattr(
                _stage2_cfg_a, "enable_zero_two_contract", False,
            )) if _stage2_cfg_a else False
            # Element 1 (2026-06-11) — resolve the centralized
            # quality-over-quota thresholds into the placeholder tokens
            # before anything is appended. Pure string substitution; the
            # selected constant itself is never mutated.
            _brain_cfg_cal = getattr(self.settings, "brain", None)
            # No `or default` coercion: the loader guarantees numeric
            # values and getattr covers a missing field, so a configured
            # 0 (e.g. thin_vol_ratio = 0 to disable the volume leg)
            # stays configurable (cross-check fix, 2026-06-11).
            system = _resolve_prompt_calibration(
                TRADE_SYSTEM_PROMPT_ZERO_TWO if _zero_two else TRADE_SYSTEM_PROMPT,
                thin_vol_ratio=float(getattr(
                    _brain_cfg_cal, "quality_skip_thin_vol_ratio", 0.25,
                )),
                heavy_attempts=int(getattr(
                    _brain_cfg_cal, "quality_skip_heavy_attempts", 6,
                )),
            )
            if any(t in system for t in _PROMPT_CALIBRATION_TOKENS):
                log.error(
                    f"STRAT_PROMPT_TOKEN_UNRESOLVED | "
                    f"tokens={[t for t in _PROMPT_CALIBRATION_TOKENS if t in system]} "
                    f"| {ctx()}"
                )
            # Phase 6 of the 1D briefing rewrite — when surface_briefing_fields
            # is True, append a new section to the system prompt that
            # teaches Claude how to read the briefing fields surfaced by
            # _format_packages_for_prompt (state_label primary/secondaries,
            # interestingness_score, votes block, action hint). Default
            # False so today's prompt is byte-identical; flips at Phase 9.
            if bool(getattr(
                self.settings.brain, "surface_briefing_fields", False,
            )):
                system += BRIEFING_SYSTEM_PROMPT_SUFFIX
            if self._has_urgent_concerns:
                system += (
                    '\n\nOVERRIDE — URGENT WATCHDOG ALERTS:\n'
                    'The data below contains URGENT position alerts from the watchdog.\n'
                    'For this call ONLY, you must ALSO include a "position_actions" field '
                    'in your JSON response for each alerted symbol, in addition to new_trades.\n'
                    'Response format: {"new_trades": [...], "position_actions": {"SYMBOL": '
                    '{"action": "hold|close|tighten_stop|set_exit", "new_sl": N, '
                    '"exit_price": N, "reasoning": "..."}}, "market_view": "...", ...}\n'
                    'This overrides the normal rule about not including position_actions in Call A.'
                )
            _sys_prompt_chars = len(system)

            # Aggressive-framing rewrite (2026-05-05) — single-line
            # observability sentinel emitted once per Call A. Captures
            # all six framing-removal switches in one breadcrumb so a
            # regression on any of them surfaces immediately at log-tail
            # time. Also reports which system-prompt branch served this
            # cycle (zero_two vs legacy) — both rewritten to emit the
            # aggressive framing, but operators may still want the flag
            # state for runbook correlation.
            # Issue 4 of 2026-05-19 direction-bias fix Phase A.
            # regime_instr field updated from "minimal" (false-advertised
            # the legacy asymmetric MARKET REGIME block as suppressed when
            # it was still emitted) to "symmetric" (the new scenario-driven
            # block at _build_trade_prompt:3371 honoring operator directive).
            log.info(
                f"STRAT_AGGRESSIVE_FRAMING | mode_line=skipped "
                f"coaching=skipped fund_rules=minimal "
                f"today_perf=skipped dir_perf=skipped "
                f"regime_instr=symmetric contract=aggressive_exploit "
                f"zero_two_flag={_zero_two} | {ctx()}"
            )

            raw_response = await self.claude.send_message(prompt, system, call_type="call_a")

            if hasattr(self.claude, "extract_json"):
                plan_data = self.claude.extract_json(raw_response)
            else:
                plan_data = json.loads(raw_response)

            plan = self._parse_trade_plan(plan_data)

            # Mid-Hold Trade Management Fix Phase 3.7 — Claude has now
            # seen any thesis_events that were rendered into the prompt.
            # Mark them consumed so the next cycle doesn't re-render.
            # Best-effort: failure logs but doesn't affect the plan.
            try:
                await self._consume_callA_events()
            except Exception as _e:
                log.warning(
                    f"CALLA_CONSUME_OUTER_FAIL | err='{str(_e)[:120]}' | {ctx()}"
                )

            # Parse position_actions if urgent concerns were injected
            if self._has_urgent_concerns and plan_data.get("position_actions"):
                for symbol, action in plan_data["position_actions"].items():
                    if isinstance(action, dict):
                        plan.position_actions[symbol] = PositionAction(
                            symbol=symbol,
                            action=action.get("action", "hold"),
                            reason=action.get("reason", action.get("reasoning", "")),
                            exit_price=_safe_float(action.get("exit_price")),
                            new_sl=_safe_float(action.get("new_sl")),
                        )
                log.info(
                    f"STRAT_CALL_A_URGENT_ACTS | acts={len(plan.position_actions)} | {ctx()}"
                )
                self._has_urgent_concerns = False

            # Enterprise log: each trade directive
            log.info(
                f"STRAT_CALL_A_PLAN | trades={len(plan.new_trades)} "
                f"risk={plan.risk_level} view='{str(plan.market_view)[:80]}' | {ctx()}"
            )
            for i, t in enumerate(plan.new_trades):
                sym = t.get("symbol", "?") if isinstance(t, dict) else "?"
                d = t.get("direction", "?") if isinstance(t, dict) else "?"
                lev = t.get("leverage", 1) if isinstance(t, dict) else 1
                rsn = t.get("reasoning", "") if isinstance(t, dict) else ""
                log.info(f"STRAT_DIRECTIVE | #{i+1} sym={sym} dir={d} lev={lev} rsn='{str(rsn)[:80]}' | {ctx()}")

            # Issue 5 (CALL_A exploit/fetch, 2026-06-05) — per-cycle exploitation
            # activity sentinel. Measures how many genuine plays the brain
            # produced and their size/hold/direction mix, against the configured
            # breadth targets, so the operator can confirm the prompt is driving
            # ~target plays per cycle with shorter holds and a mix of sizes —
            # WITHOUT a count quota (zero is still correct on a genuinely flat
            # tape). Pure read over plan.new_trades; never raises from the log.
            try:
                _bc = getattr(getattr(self.settings, "brain", None), "brain_target_play_count", 3)
                _ph = getattr(getattr(self.settings, "brain", None), "brain_preferred_hold_minutes_max", 25)
                _nt = [t for t in plan.new_trades if isinstance(t, dict)]
                _buys = sum(1 for t in _nt if str(t.get("direction", "")).lower() in ("buy", "long"))
                _sells = sum(1 for t in _nt if str(t.get("direction", "")).lower() in ("sell", "short"))
                _holds = [float(t.get("max_hold_minutes", 0) or 0) for t in _nt]
                _short_h = sum(1 for h in _holds if 0 < h <= _ph)
                _mid_h = sum(1 for h in _holds if _ph < h <= 45)
                _long_h = sum(1 for h in _holds if h > 45)
                _sizes = [float(t.get("size_usd", 0) or 0) for t in _nt]
                _avg_hold = round(sum(_holds) / len(_holds), 1) if _holds else 0.0
                _avg_size = round(sum(_sizes) / len(_sizes), 1) if _sizes else 0.0
                log.info(
                    f"STRAT_CALL_A_ACTIVITY | n_trades={len(_nt)} target={_bc} "
                    f"buys={_buys} sells={_sells} "
                    f"short_holds={_short_h} mid_holds={_mid_h} long_holds={_long_h} "
                    f"pref_hold_max={_ph} avg_hold_min={_avg_hold} "
                    f"avg_size_usd={_avg_size} | exploitation breadth this cycle "
                    f"(target is a reach, not a quota; 0 is correct on a flat tape) "
                    f"| {ctx()}"
                )
            except Exception as _ae:
                log.debug(f"STRAT_CALL_A_ACTIVITY_FAIL | err='{str(_ae)[:80]}'")

            if not plan.new_trades:
                log.warning(f"STRAT_CALL_A_NO_TRADES | view='{str(plan.market_view)[:100]}' | {ctx()}")
                # Stage 2 phase 3 — under the bounded-count contract
                # (range=2-4 since 2026-05-05; was 1-2), an empty
                # response is the system working as designed (Claude
                # judged the entire candidate set genuinely flat).
                # Surface this explicitly so operators distinguish
                # "intentional skip" from "parse failure / brain
                # regression".
                if _zero_two:
                    log.info(
                        f"STRAT_ZERO_TRADES_INTENTIONAL "
                        f"| view='{str(plan.market_view)[:120]}' "
                        f"contract=2_4 | {ctx()}"
                    )

            _trades_count = len(plan.new_trades)
            return plan

        except Exception as e:
            log.error(f"STRAT_CALL_A_FAIL | err='{str(e)[:500]}' | {ctx()}")
            _status = "failed"
            return None
        except BaseException:
            # CancelledError / KeyboardInterrupt / SystemExit. Mark the
            # status so the finally records it, then re-raise so the
            # caller (and the asyncio event loop) sees the same
            # propagation it always saw.
            _status = "cancelled"
            raise
        finally:
            _elapsed = (time.time() - _cycle_start) * 1000
            log.info(
                f"STRAT_CALL_A_END | el={_elapsed:.0f}ms status={_status} "
                f"trades={_trades_count} prompt_chars={_prompt_chars} "
                f"sys_prompt_chars={_sys_prompt_chars} | {ctx()}"
            )

    async def create_position_plan(self) -> StrategicPlan | None:
        """CALL B: Manage open positions. Compact prompt, no market scan.

        Observability G1 (try/finally pairing): same structural fix as
        ``create_trade_plan`` — every entry pairs with exactly one
        ``STRAT_CALL_B_END`` emission, including cancellation.
        """
        _cycle_start = time.time()
        did = new_decision_id()
        log.info(f"STRAT_CALL_B_START | did={did} | {ctx()}")

        _status = "success"
        _acts_count = 0
        _prompt_chars = 0
        _sys_prompt_chars = 0

        try:
            # Phase 3 (P0-2 Fix D): defer if any open position has price
            # divergence > divergence_block_prompt_pct (default 1.0%). The
            # transformer publishes the max |divergence| seen in its last
            # enrichment cycle. If above threshold, skip this prompt so
            # Claude doesn't reason on wrong prices; the next cycle will
            # try again after the WS re-syncs.
            if self._has_blocking_price_divergence():
                tf = self.services.get("transformer")
                div_pct = float(getattr(tf, "_last_enrichment_max_divergence_pct", 0.0) or 0.0)
                threshold = float(
                    getattr(self.settings, "price", None) and
                    getattr(self.settings.price, "divergence_block_prompt_pct", 1.0)
                    or 1.0
                )
                log.warning(
                    f"PROMPT_DEFERRED | rsn=price_divergence max_div={div_pct:.3f}% "
                    f"threshold={threshold:.2f}% | {ctx()}"
                )
                _status = "deferred"
                return None

            prompt = await self._build_position_prompt()
            _prompt_chars = len(prompt)
            _sys_prompt_chars = len(POSITION_SYSTEM_PROMPT)
            log.info(f"STRAT_CALL_B | chars={_prompt_chars} | {ctx()}")

            raw_response = await self.claude.send_message(prompt, POSITION_SYSTEM_PROMPT, call_type="call_b")

            if hasattr(self.claude, "extract_json"):
                plan_data = self.claude.extract_json(raw_response)
            else:
                plan_data = json.loads(raw_response)

            plan = self._parse_position_plan(plan_data)

            # Mid-Hold Trade Management Fix Phase 3.8 — mark queued
            # thesis_events as seen by CALL_B after the response succeeds.
            try:
                await self._consume_callB_events()
            except Exception as _e:
                log.warning(
                    f"CALLB_CONSUME_OUTER_FAIL | err='{str(_e)[:120]}' | {ctx()}"
                )

            # Enterprise log: each position action
            log.info(
                f"STRAT_CALL_B_PLAN | acts={len(plan.position_actions)} | {ctx()}"
            )
            for sym, act in plan.position_actions.items():
                log.info(
                    f"STRAT_POS_ACT | sym={sym} act={act.action} "
                    f"rsn='{str(act.reason)[:80]}' | {ctx()}"
                )

            _acts_count = len(plan.position_actions)
            return plan

        except Exception as e:
            log.error(f"STRAT_CALL_B_FAIL | err='{str(e)[:500]}' | {ctx()}")
            _status = "failed"
            return None
        except BaseException:
            _status = "cancelled"
            raise
        finally:
            _elapsed = (time.time() - _cycle_start) * 1000
            log.info(
                f"STRAT_CALL_B_END | el={_elapsed:.0f}ms status={_status} "
                f"acts={_acts_count} prompt_chars={_prompt_chars} "
                f"sys_prompt_chars={_sys_prompt_chars} | {ctx()}"
            )

    def _magnitude_advisory_tag(self, vp) -> str:
        """Item 2 (entry-gaps investigation, 2026-05-26): optional expected-
        winner-magnitude advisory derived from the entry M5 volatility class.

        Entry volatility predicts winner SIZE, not win/loss: in the bybit_demo
        data, big-winners (top-quartile winner PnL) separated from the rest by
        entry ATR% with AUC 0.633, while no feature separated win from loss.
        This surfaces that as advisory context only — it never gates a trade,
        changes a size, or alters direction. Default off (returns "").
        """
        if not getattr(getattr(self.settings, "brain", None),
                       "entry_magnitude_advisory_enabled", False):
            return ""
        if vp is None:
            return ""
        cls = (getattr(vp, "volatility_class", "") or "").lower()
        if cls in ("high", "extreme"):
            tag = " MAG=HIGH(larger-winner-potential)"
        elif cls in ("dead", "low"):
            tag = " MAG=LOW(small-winner-likely)"
        else:
            tag = " MAG=MED"
        # Per-decision observability (Rule 9): one INFO line per ~60s per process
        # confirms the advisory is acting, without per-coin spam.
        _now = time.monotonic()
        if _now - getattr(self, "_mag_advisory_last_log", 0.0) > 60.0:
            self._mag_advisory_last_log = _now
            log.info(
                "MAGNITUDE_ADVISORY_ACTIVE | appending MAG token to coin "
                "volatility lines (Item 2 entry-gaps 2026-05-26)"
            )
        return tag

    async def _build_context_prompt(self) -> str:
        """Build the full market context for strategic review."""
        _t_build = time.time()
        sections = []

        # === COACHING + RECENT TRADES (moved to TOP for prominence) ===
        enforcer = self.services.get("enforcer")
        if enforcer and hasattr(enforcer, "get_coaching_text"):
            try:
                _sc = self.services.get("structure_cache")
                coaching = enforcer.get_coaching_text(structure_cache=_sc)
                if coaching:
                    sections.append(f"## {coaching}")
            except Exception as e:
                log.debug("Coaching text fetch failed: {err}", err=str(e))

        # === EARLY FETCH: Regime + Fear & Greed (needed for regime instructions) ===
        _regime_str = "unknown"
        _regime_confidence = 0.5
        _fear_greed_value = 50
        _regime_state = None
        _fg_data = None

        try:
            regime_detector = self.services.get("regime_detector")
            if regime_detector:
                # Read RegimeWorker's cached detection (updated every ~600s).
                # Zero-cost in the happy path; avoids ~30s of H1 TA recompute.
                _regime_state = regime_detector.get_last_regime()
                if _regime_state is None:
                    # Boot race: RegimeWorker hasn't run a tick yet. Compute
                    # once so the first prompt has real data; subsequent
                    # strategist calls hit the cache.
                    _regime_state = await regime_detector.detect()
                if _regime_state:
                    _regime_str = _regime_state.regime.value
                    _regime_confidence = _regime_state.confidence
        except Exception as e:
            log.debug("Early regime detection failed: {err}", err=str(e))

        try:
            fg_service = self.services.get("fear_greed")
            if fg_service:
                _fg_data = await fg_service.get_latest()
                if _fg_data:
                    _fear_greed_value = _fg_data.value
        except Exception as e:
            log.debug("Early fear & greed fetch failed: {err}", err=str(e))

        # === REGIME INSTRUCTIONS (position 2: right after coaching) ===
        try:
            regime_instructions = self._build_regime_instructions(
                _regime_str, _regime_confidence, _fear_greed_value
            )
            if regime_instructions:
                sections.append(regime_instructions)
        except Exception as e:
            log.debug("Regime instructions build failed: {err}", err=str(e))

        # === DIRECTION PERFORMANCE (position 3: after regime instructions) ===
        try:
            dir_perf = self._build_direction_performance()
            if dir_perf:
                sections.append(dir_perf)
        except Exception as e:
            log.debug("Direction performance build failed: {err}", err=str(e))

        # Trading mode instruction (#6: testnet vs mainnet)
        trading_mode_mgr = self.services.get("trading_mode")
        if trading_mode_mgr:
            sections.append(trading_mode_mgr.mode.get_claude_mode_instruction())

        # Supported symbols instruction (Issue #1: filter unsupported)
        from src.config.constants import SUPPORTED_SYMBOLS
        is_testnet = getattr(self.settings, "bybit", None) and self.settings.bybit.testnet
        thesis_mgr_early = self.services.get("thesis_manager")
        symbols_line = ", ".join(sorted(SUPPORTED_SYMBOLS))
        sections.append(
            f"SUPPORTED SYMBOLS (you can ONLY trade these — all others will be rejected):\n"
            f"{symbols_line}\n"
            f"Do NOT suggest any symbol not in this list.\n"
        )

        # Minimum trade sizes per symbol (BUG 5: Claude must know BTC needs large size)
        from src.config.constants import TESTNET_QTY_STEPS
        market_service = self.services.get("market_service")
        min_size_lines = []
        if market_service:
            for sym in sorted(SUPPORTED_SYMBOLS):
                try:
                    _tk = await market_service.get_ticker(sym)
                    _price = _tk.last_price if _tk else 0
                    _step = TESTNET_QTY_STEPS.get(sym, 0.1)
                    if _price > 0:
                        _min_1x = _step * _price
                        _min_2x = _min_1x / 2
                        min_size_lines.append(
                            f"  {sym}: min ${_min_1x:.0f} at 1x, ${_min_2x:.0f} at 2x (step={_step})"
                        )
                except Exception as e:
                    log.debug("Ticker fetch for min trade size failed: {err}", err=str(e))
        if min_size_lines:
            sections.append(
                "MINIMUM TRADE SIZES (your size_usd must exceed these or qty rounds to 0):\n"
                + "\n".join(min_size_lines)
                + "\nIf size_usd is too small, INCREASE it or choose a different coin.\n"
            )

        # Market data — filtered to reduce prompt size (target <15K chars)
        sections.append("## MARKET DATA")
        try:
            scanner = self.services.get("scanner")
            market_service = self.services.get("market_service")
            ta_cache = self.services.get("ta") or self.services.get("ta_cache")
            volatility_profiler = self.services.get("volatility_profiler")

            universe = (
                await scanner.get_active_universe() if scanner else []
            )

            # Filter to supported symbols only (Issue #1)
            if is_testnet:
                universe = [s for s in universe if s in SUPPORTED_SYMBOLS]

            # Build set of symbols with open positions (always include these)
            open_position_symbols: set[str] = set()
            if thesis_mgr_early:
                try:
                    theses = await thesis_mgr_early.get_open_theses()
                    open_position_symbols = {t["symbol"] for t in (theses or [])}
                except Exception:
                    pass

            included_count = 0
            skipped_count = 0
            _rd = self.services.get("regime_detector")  # Fetch once for market data + divergence
            for symbol in universe:
                try:
                    ticker = await market_service.get_ticker(symbol)
                    ta = None
                    if ta_cache:
                        try:
                            ta = await ta_cache.analyze(
                                symbol=symbol, timeframe=TimeFrame.H1
                            )
                        except Exception as e:
                            log.debug("TA analysis failed: {err}", err=str(e))

                    price = ticker.last_price if ticker else 0
                    change = getattr(ticker, "change_24h_pct", 0) or 0
                    rsi = 50
                    macd_hist = 0
                    adx = 0
                    if ta:
                        rsi = (
                            ta.get("momentum", {}).get("rsi_14", 50)
                        )
                        macd_data = ta.get("trend", {}).get("macd", {})
                        if isinstance(macd_data, dict):
                            macd_hist = macd_data.get("histogram", 0)
                        adx_data = ta.get("trend", {}).get("adx", {})
                        if isinstance(adx_data, dict):
                            adx = adx_data.get("adx", 0)

                    # Filter: only include coins with open positions, extreme RSI,
                    # big 24h moves, or strong ADX trend
                    has_position = symbol in open_position_symbols
                    is_notable = (
                        abs(change) > 3.0       # Big 24h move
                        or rsi < 30 or rsi > 70  # Extreme RSI
                        or adx > 30              # Strong trend
                    )
                    # Always include BTC and ETH as market references
                    is_major = symbol in ("BTCUSDT", "ETHUSDT")

                    if has_position or is_notable or is_major:
                        tag = " [POS]" if has_position else ""
                        # Per-coin regime tag
                        _cr = _rd.get_coin_regime(symbol) if _rd else None
                        rgm_tag = (
                            f" [{_cr.regime.value.upper()} {_cr.confidence*100:.0f}%]"
                            if _cr else ""
                        )
                        # Per-coin volatility profile tag
                        vol_tag = ""
                        if volatility_profiler:
                            try:
                                _vp = await volatility_profiler.get_profile(symbol)
                                if _vp:
                                    vol_tag = (
                                        f" VOL={_vp.volatility_class.upper()}"
                                        f" ATR%={_vp.atr_pct_5m:.2f}%"
                                        f" recTP={_vp.recommended_tp_pct:.1f}%"
                                        f" recSL={_vp.recommended_sl_pct:.1f}%"
                                    )
                                    vol_tag += self._magnitude_advisory_tag(_vp)
                            except Exception as e:
                                log.debug(
                                    "VOL_PROFILE_LOOKUP_FAIL | sym={sym} err='{err}'",
                                    sym=symbol, err=str(e)[:80],
                                )
                        sections.append(
                            f"{symbol}{tag}{rgm_tag}{vol_tag}: ${format_price(price)} ({change:+.1f}% 24h) "
                            f"RSI={rsi:.0f} MACD_hist={macd_hist:.4f} ADX={adx:.0f}"
                        )
                        included_count += 1
                    else:
                        skipped_count += 1
                except Exception as e:
                    log.debug("Market data for symbol failed: {err}", err=str(e))

            if skipped_count > 0:
                sections.append(f"({skipped_count} neutral coins omitted for brevity)")

            # Regime Divergence section — highlight coins whose per-coin regime
            # DISAGREES with the global BTC regime (critical for direction decisions)
            if _rd:
                divergent_coins = []
                for _sym in universe:
                    _cr = _rd.get_coin_regime(_sym)
                    if _cr:
                        _cd = _cr.regime.value
                        if (("up" in _cd and "down" in _regime_str)
                                or ("down" in _cd and "up" in _regime_str)):
                            divergent_coins.append(
                                f"{_sym} ({_cd} {_cr.confidence*100:.0f}%)"
                            )
                if divergent_coins:
                    # Per-coin-authority Phase 6 (2026-05-29): dropped the
                    # "DISAGREE with global {regime}" framing — the prompt no
                    # longer states a global DIRECTION to disagree with. This is
                    # now a pure per-coin reinforcement (trade each coin WITH its
                    # OWN regime). Marker "## REGIME DIVERGENCE" kept so the trim
                    # protection on line 518 still matches.
                    sections.append(
                        f"\n## REGIME DIVERGENCE — coins whose per-coin regime is clearly directional:\n"
                        f"  {', '.join(divergent_coins)}\n"
                        f"  Trade these coins WITH their individual regime direction, NOT against it.\n"
                        f"  Do NOT short a coin that is individually in an uptrend.\n"
                        f"  Do NOT buy a coin that is individually in a downtrend."
                    )
        except Exception as e:
            sections.append(f"(market data error: {e})")

        # Data Lake: snapshot market state (#10)
        try:
            data_lake = self.services.get("data_lake")
            if data_lake and market_service:
                _btc = _eth = _sol = 0.0
                for _sym, _getter in [("BTCUSDT", None), ("ETHUSDT", None), ("SOLUSDT", None)]:
                    try:
                        _t = await market_service.get_ticker(_sym)
                        if _sym == "BTCUSDT":
                            _btc = _t.last_price
                        elif _sym == "ETHUSDT":
                            _eth = _t.last_price
                        else:
                            _sol = _t.last_price
                    except Exception as e:
                        log.debug("Ticker fetch for data lake failed: {err}", err=str(e))
                await data_lake.write_market_snapshot(btc_price=_btc, eth_price=_eth, sol_price=_sol)
        except Exception as e:
            log.debug("Data lake snapshot failed: {err}", err=str(e))

        # X-RAY Structural Intelligence
        try:
            structure_cache = self.services.get("structure_cache")
            if structure_cache:
                # Phase 4: Session header
                ranked_setups = structure_cache.get_ranked_setups()
                session_ctx = None
                if ranked_setups and ranked_setups[0].session_favorable is not None:
                    first = structure_cache.get(ranked_setups[0].symbol)
                    if first and first.session_context:
                        session_ctx = first.session_context
                if not session_ctx:
                    # Fallback: scanner hasn't run yet, get session from any cached analysis
                    all_cached = structure_cache.get_all()
                    for _sym, _analysis in all_cached.items():
                        if _analysis.session_context:
                            session_ctx = _analysis.session_context
                            break
                if session_ctx:
                    sc = session_ctx
                    sections.append(
                        f"\n## SESSION: {sc.current_session.upper()} ({sc.session_phase}) "
                        f"| {sc.session_elapsed_minutes}min elapsed, {sc.session_remaining_minutes}min remaining"
                        f"\n  {sc.trading_recommendation}"
                        + (f"\n  ⚠️ Manipulation likely" if sc.manipulation_likely else "")
                        + f"\n  Next: {sc.next_session} in {sc.next_session_starts_in_minutes}min"
                    )

                top_setups = structure_cache.get_top_setups(n=8)
                if top_setups:
                    xray_lines = ["\n## X-RAY STRUCTURAL SETUPS (ranked by confluence)"]
                    # Gap 2 fix (2026-05-19) — brief informational explainer
                    # for the new INVALID_LONG / INVALID_SHORT annotation on
                    # each per-coin RR_DIR row. Purely informational; the
                    # brain decides whether to factor it in. Adding
                    # restrictive guidance (e.g. "avoid INVALID setups")
                    # would violate the operator directive on hardcoded
                    # direction-asymmetry per Rule 4 anti-pattern.
                    xray_lines.append(
                        "  Field key: RR_DIR(L=long R:R, S=short R:R, best=DIR, Nx ratio) "
                        "INVALID_LONG / INVALID_SHORT = Y when that side's "
                        "structural_tp used the math-safety floor "
                        "(price at/past the relevant level); the corresponding "
                        "rr value reflects the floor distance, not real edge."
                    )
                    for a in top_setups:
                        line = f"  {a.symbol} (${format_price(a.current_price)}): "
                        ns = a.nearest_support
                        nr = a.nearest_resistance
                        ms = a.market_structure
                        sp = a.structural_placement
                        if ns:
                            line += f"S=${format_price(ns.price)}({ns.strength:.1f}/5,{ns.touches}t) "
                        if nr:
                            line += f"R=${format_price(nr.price)}({nr.strength:.1f}/5,{nr.touches}t) "
                        if ms and ms.structure != "unknown":
                            line += f"struct={ms.structure}({ms.strength}) "
                        line += f"pos={a.position_in_range:.0%} "
                        # Element 3 (2026-06-11) — pre-clamp range truth:
                        # a clamped 0%/100% can hide a live break; the
                        # compact marker surfaces it. Flag-gated; "" when
                        # in range so the legacy line is byte-identical.
                        if bool(getattr(
                            getattr(self.settings, "structure", None),
                            "range_truth_enabled", True,
                        )):
                            line += _range_breakout_marker(a, compact=True)
                        if sp:
                            line += f"RR=1:{sp.rr_ratio:.1f}({sp.rr_quality}) "
                            # Phase 10 (P1-9): expose BOTH directions' R:R
                            # so Claude can see when one side has a much
                            # better setup than the other. The strategy
                            # worker hard-blocks at >5x and reduces size
                            # at >3x; Claude seeing the comparison up-front
                            # avoids picking the losing side in the first
                            # place.
                            if sp.rr_long > 0 and sp.rr_short > 0:
                                if sp.rr_long >= sp.rr_short:
                                    _ratio = sp.rr_long / max(sp.rr_short, 0.01)
                                    _best = "LONG"
                                else:
                                    _ratio = sp.rr_short / max(sp.rr_long, 0.01)
                                    _best = "SHORT"
                                line += (
                                    f"RR_DIR(L={sp.rr_long:.1f},S={sp.rr_short:.1f},"
                                    f"best={_best},{_ratio:.1f}x) "
                                )
                                # Gap 2 fix (2026-05-19) — bidirectional
                                # clamp visibility. INVALID_LONG=Y means the
                                # long-side structural_tp was computed using
                                # the math-safety floor (Issue 1 Phase C
                                # clamp), not a real measure of edge. The
                                # corresponding rr_long value reflects the
                                # floor distance, not genuine structural
                                # opportunity. Symmetric for INVALID_SHORT.
                                # Informational only — brain decides.
                                _il = "Y" if getattr(sp, "is_long_invalid", False) else "N"
                                _is = "Y" if getattr(sp, "is_short_invalid", False) else "N"
                                line += f"INVALID_LONG={_il} INVALID_SHORT={_is} "
                        # Phase 2: Smart Money Concepts
                        if a.nearest_fvg:
                            nf = a.nearest_fvg
                            line += f"FVG={nf.direction}(${format_price(nf.bottom)}-${format_price(nf.top)}) "
                        if a.nearest_ob:
                            no = a.nearest_ob
                            fresh_tag = "FRESH" if no.fresh else f"{no.retests}r"
                            line += f"OB={no.direction}(${format_price(no.low)}-${format_price(no.high)},{fresh_tag},s={no.strength_score:.0f}) "
                        if a.active_sweep_signal:
                            sw = a.active_sweep_signal
                            line += f"SWEEP={sw.signal}(rev={sw.reversal_strength:.2f}) "
                        if a.smc_confluence > 0:
                            line += f"SMC={a.smc_confluence} "
                        # Phase 3: Confluence
                        if a.poc_price:
                            vp_pos = a.volume_profile.current_vs_poc if a.volume_profile else "?"
                            line += f"POC=${format_price(a.poc_price)}({vp_pos}) "
                        if a.fib_key_level:
                            confl = ""
                            if a.fibonacci and a.fibonacci.confluence_with:
                                confl = f",{a.fibonacci.confluence_with}"
                            line += f"FIB=${format_price(a.fib_key_level)}{confl} "
                        if a.mtf_confluence and a.mtf_confluence.score > 0:
                            line += f"MTF={a.mtf_confluence.score}/10({a.confluence_quality}) "
                        if a.total_confluence_factors > 0:
                            line += f"CONFL={a.total_confluence_factors} "
                        line += f"setup={a.setup_quality}({a.setup_score})"
                        xray_lines.append(line)

                    # Add coins with no structural edge
                    all_fresh = structure_cache.get_all()
                    skip_coins = [
                        sym for sym, a in all_fresh.items()
                        if a.setup_quality in ("SKIP", "C") and sym not in {a.symbol for a in top_setups}
                    ]
                    if skip_coins:
                        xray_lines.append(f"  {', '.join(skip_coins[:10])} — mid-range or weak structure, skip or wait.")

                    sections.append("\n".join(xray_lines))
                    log.debug(
                        f"XRAY_CONTEXT | setups_sent={len(top_setups)} "
                        f"skipped={len(skip_coins) if skip_coins else 0} "
                        f"top={top_setups[0].symbol}({top_setups[0].setup_score})"
                    )
        except Exception as e:
            # Phase 12.2 (lifecycle-logging-audit Gap 2.2-G1, HIGH):
            # promoted from DEBUG to WARNING with structured tag.
            # Silent X-RAY failure here corrupts every CALL_A prompt
            # with empty X-RAY context, directly affecting trade selection.
            log.warning(
                f"XRAY_CTX_BUILD_FAIL | call=CALL_A "
                f"err='{str(e)[:200]}' | {ctx()}"
            )

        # Fear & Greed, Sentiment (reuse early-fetched data)
        sections.append("\n## SENTIMENT")
        if _fg_data:
            sections.append(
                f"Fear & Greed: {_fg_data.value} ({getattr(_fg_data, 'classification', 'neutral')})"
            )

        # Regime (reuse early-fetched data)
        # Issue 4 of 2026-05-19 direction-bias fix Phase A — symmetric
        # scenario-driven MARKET REGIME block. The asymmetric "DEFAULT
        # SELL BIAS" / "BUY preferred" direction_hint dict + trending_down-
        # only conf>0.60 NOTE (block_version=1) is replaced with a
        # symmetric formulation honoring the operator directive: asymmetry
        # emerges from per-coin evidence, not hardcoded direction-specific
        # mandate strings. Both trending_down AND trending_up branches
        # receive a NOTE at confidence > 0.60 with parallel wording.
        # See STRAT_REGIME_BLOCK_VERSION (2) for sentinel correlation.
        # Per-coin-authority Phase 6f follow-up (2026-05-29): this is the DEAD
        # _build_context_prompt's own inline regime block (the live path is
        # _build_trade_prompt). It is neutralized IN LOCKSTEP with the live block
        # and _build_regime_instructions so a future revival of create_strategic_plan
        # can never reinstate the global short-bias: default mode (the flag) emits
        # CONTEXT-only with NO global direction mandate; the legacy direction_hint +
        # "use as default bias" NOTE lives only in the rollback branch.
        _regime_section = "\n## MARKET REGIME (CONTEXT)"
        if _regime_state:
            _pcd_dead = bool(getattr(
                getattr(self.settings, "stage2", None),
                "per_coin_direction_enabled", True,
            ))
            if _pcd_dead:
                _regime_section += (
                    "\nPer-coin regimes are AUTHORITATIVE: trade each coin on ITS OWN "
                    "regime; coins without a per-coin regime trade on their own "
                    "TA/structure. There is NO market-wide direction bias."
                )
            else:
                direction_hint = {
                    "trending_down": "Bias for shorts when per-coin evidence agrees; per-coin tags override.",
                    "trending_up": "Bias for longs when per-coin evidence agrees; per-coin tags override.",
                    "ranging": "both directions OK",
                    "volatile": "both directions with caution",
                    "dead": "scalp mode — both directions, tight TP",
                }.get(_regime_str, "neutral")
                _regime_section += (
                    f"\nGlobal: {_regime_str} "
                    f"(confidence={_regime_state.confidence:.0%}) "
                    f"→ {direction_hint}"
                )
                if _regime_state.confidence > 0.60:
                    if _regime_str == "trending_down":
                        _regime_section += (
                            "\nNOTE: High-confidence global downtrend. Use this as default bias "
                            "for coins without a per-coin tag; coins tagged [TRENDING_UP] are "
                            "valid long candidates on their own evidence."
                        )
                    elif _regime_str == "trending_up":
                        _regime_section += (
                            "\nNOTE: High-confidence global uptrend. Use this as default bias "
                            "for coins without a per-coin tag; coins tagged [TRENDING_DOWN] are "
                            "valid short candidates on their own evidence."
                        )
        sections.append(_regime_section)

        # YOUR OPEN POSITIONS with theses (Issue #2: thesis tracking)
        thesis_mgr = self.services.get("thesis_manager")
        # Mid-Hold Trade Management Fix Phase 3.7 — reset the per-call
        # event-id ledger before building. Whatever IDs we render below
        # will be marked consumed by create_trade_plan after Claude
        # responds successfully.
        self._last_callA_event_ids = []
        if thesis_mgr:
            try:
                open_theses = await thesis_mgr.get_open_theses()
                # Fetch unseen events once for all open symbols so each
                # per-thesis block can pull its events without N round-
                # trips. Mid-Hold Trade Management Fix Phase 3.7.
                _events_by_symbol: dict[str, list[dict]] = {}
                if open_theses:
                    try:
                        _open_syms = [t["symbol"] for t in open_theses]
                        _unseen = await thesis_mgr.get_unseen_events(_open_syms)
                        for ev in _unseen:
                            _events_by_symbol.setdefault(
                                ev["symbol"], [],
                            ).append(ev)
                    except Exception as _ee:
                        log.debug(
                            f"CALLA_EVENTS_FETCH_FAIL | err='{str(_ee)[:120]}' | "
                            f"{ctx()}"
                        )
                if open_theses:
                    sections.append("\n## YOUR OPEN POSITIONS (with your original thesis)")
                    sections.append("Cross-check each thesis against current data.")
                    sections.append("If thesis broken -> close. If thesis holds -> hold or add.\n")
                    for t in open_theses:
                        sections.append(
                            f"  {t['symbol']} {t['direction']} "
                            f"entry=${format_price(t['entry_price'])} "
                            f"SL=${format_price(t['stop_loss_price'])} TP=${format_price(t['take_profit_price'])} "
                            f"size=${t['size_usd']:.0f} {t['leverage']}x "
                            f"hold={t['max_hold_minutes']}min "
                            f"opened={t['opened_at']}"
                        )
                        sections.append(f"    THESIS: {t['thesis']}")
                        # Mid-Hold Trade Management Fix Phase 3.7 —
                        # render the entry-thesis invalidation criterion
                        # + current state, plus any queued events. This
                        # is information supply: the brain decides what
                        # to do (Rule 4 / Rule 16 of IMPLEMENT_MIDHOLD).
                        _thesis_inv = self._render_thesis_invalidation_block(t)
                        if _thesis_inv:
                            sections.append(_thesis_inv)
                        _sym_events = _events_by_symbol.get(t["symbol"], [])
                        if _sym_events:
                            _event_lines, _consumed_ids = (
                                self._render_thesis_events_block(_sym_events)
                            )
                            sections.append(_event_lines)
                            self._last_callA_event_ids.extend(_consumed_ids)
                        if t.get('apex_flipped'):
                            sections.append(
                                f"    APEX-OPTIMIZED: Flipped "
                                f"{t.get('apex_original_direction', '?')}->{t['direction']}: "
                                f"{str(t.get('apex_reason', ''))[:100]}"
                            )
                            sections.append(
                                "    NOTE: Evaluate on current PnL merit, "
                                "not original thesis direction."
                            )
                        sections.append("")
                else:
                    sections.append("\n## NO OPEN THESES — find the best opportunity now")

                # Recent lessons (T1-3 / F9 six-tier-fixes 2026-05-11):
                # apply anti-closed-loop guards. (a) `min_age_seconds=300`
                # keeps the recency-bias case (RENDERUSDT 3-min-after-loss
                # close-on-same-symbol) out of the prompt. (b) the open
                # position symbol set is excluded so a lesson for symbol
                # X is not shown while X is in the current decision set.
                try:
                    _open_syms_for_lessons: frozenset[str] = frozenset()
                    _pos_svc = self.services.get("position_service")
                    if _pos_svc is not None:
                        _open_positions = await _pos_svc.get_positions()
                        _open_syms_for_lessons = frozenset(
                            p.symbol for p in _open_positions
                        )
                except Exception:
                    _open_syms_for_lessons = frozenset()
                lessons = await thesis_mgr.get_recent_lessons(
                    limit=10,
                    min_age_seconds=300,
                    exclude_symbols=_open_syms_for_lessons,
                )
                # Phase 12.10 (lifecycle-logging-audit Gap 10.4-G1): per-cycle
                # CALL_B lesson injection visibility. Operators previously
                # couldn't tell whether lessons reached the prompt.
                log.info(
                    f"STRAT_CALL_B_LESSONS_INJECTED | count={len(lessons or [])} "
                    f"min_age_s=300 excluded_open={len(_open_syms_for_lessons)} | {ctx()}"
                )
                if lessons:
                    sections.append("\n## LESSONS FROM RECENT TRADES (learn from these)")
                    for l in lessons:
                        emoji = "W" if (l.get("actual_pnl_pct") or 0) > 0 else "L"
                        sections.append(
                            f"  [{emoji}] {l['symbol']} {l['direction']} "
                            f"PnL={l.get('actual_pnl_pct', 0):+.2f}% "
                            f"Reason: {l.get('close_reason', '?')}"
                        )
                        if l.get("lesson"):
                            sections.append(f"      Lesson: {l['lesson']}")
                    sections.append("")

                # T1-3 / F9 aggregated stats — closed-loop-immune block
                # rendered alongside per-trade lessons. Symbol-agnostic.
                try:
                    _stats = await thesis_mgr.get_aggregated_stats(limit_closes=50)
                    from src.core.thesis_manager import format_aggregated_stats_for_prompt
                    _stats_block = format_aggregated_stats_for_prompt(_stats)
                    if _stats_block:
                        sections.append(_stats_block)
                        sections.append("")
                except Exception as _se:
                    log.debug(
                        f"STRAT_CALL_A_STATS_FAIL | err='{str(_se)[:120]}' | {ctx()}"
                    )
            except Exception as e:
                # Phase 12.10 (Gap 10.4-G2): DEBUG -> WARNING + structured tag.
                log.warning(
                    f"STRAT_CALL_B_LESSONS_FETCH_FAIL | err='{str(e)[:120]}' | {ctx()}"
                )

        # Bybit exchange positions (ground truth)
        sections.append("\n## BYBIT EXCHANGE POSITIONS (ground truth)")
        try:
            position_service = self.services.get("position_service")
            if position_service:
                positions = await position_service.get_positions()
                if positions:
                    for pos in positions:
                        coordinator = self.services.get("trade_coordinator")
                        trade_info = (
                            coordinator.get_trade_info(pos.symbol)
                            if coordinator
                            else {}
                        )
                        plan = (
                            coordinator.get_trade_plan(pos.symbol)
                            if coordinator
                            else None
                        )

                        pnl_pct = (
                            (
                                (pos.mark_price - pos.entry_price)
                                / pos.entry_price
                                * 100
                            )
                            if pos.entry_price > 0
                            else 0
                        )
                        if hasattr(pos.side, "value") and pos.side.value in (
                            "Sell",
                            "Short",
                        ):
                            pnl_pct = -pnl_pct

                        age_info = ""
                        if plan:
                            age_info = (
                                f" age={plan.age_minutes:.0f}min "
                                f"remain={plan.remaining_minutes:.0f}min"
                            )

                        strategy = trade_info.get("strategy_name", "unknown")
                        thesis = (
                            plan.reasoning[:80]
                            if plan and plan.reasoning
                            else "no thesis recorded"
                        )

                        sections.append(
                            f"  {pos.symbol} {pos.side.value if hasattr(pos.side, 'value') else pos.side} "
                            f"entry=${format_price(pos.entry_price)} "
                            f"now=${format_price(pos.mark_price)} PnL={pnl_pct:+.2f}%{age_info} "
                            f'strategy={strategy} thesis="{thesis}"'
                        )
                    # BUG 6 fix: Make held symbols impossible to ignore
                    held_syms = [pos.symbol for pos in positions]
                    sections.append(
                        f"\nYou ALREADY HOLD: {', '.join(held_syms)}\n"
                        f"DO NOT suggest new trades for these symbols. "
                        f"The system will REJECT them."
                    )
                else:
                    sections.append("  No open positions — you can trade any supported symbol.")
        except Exception as e:
            sections.append(f"  (position error: {e})")

        # Recently closed positions — per-(symbol, direction) reentry cooldown
        # (Issue 3, 2026-05-18). Surfaces only directions currently blocked
        # by the 5-min cooldown so the brain plans around them instead of
        # proposing trades the gate will reject as reentry_cooldown_5min_*.
        coordinator = self.services.get("trade_coordinator")
        if coordinator and hasattr(coordinator, "get_active_reentry_cooldowns"):
            try:
                pairs = coordinator.get_active_reentry_cooldowns()
            except Exception:
                pairs = []
            if pairs:
                sections.append("\nRECENTLY CLOSED (wait for cooldown before re-entering):")
                for sym, direction, remaining in pairs:
                    sections.append(f"  {sym} {direction}: {remaining}s remaining")
                sections.append("")

        # Strategy signals summary
        # Issue #3: Strategy HINTS (from 40 automated strategies)
        # E23 (2026-05-28): collapsed to two joined sections, mirroring the live
        # Call-A block in _build_trade_prompt (this legacy copy in
        # _build_context_prompt is kept in lockstep to avoid divergent copies).
        _hint_header = (
            "\n## STRATEGY HINTS — additional automated strategy signals\n"
            "Outputs from ~40 automated strategies, ranked by score. These may "
            "include coins NOT in the TRADE CANDIDATES above; for any coin that "
            "IS a candidate, its full per-coin ensemble and votes appear in its "
            "candidate block and these one-line hints do not override it.\n"
            "Weigh strategies as ONE input alongside regime, structure and "
            "X-RAY — do not blindly follow them and do not blindly dismiss them. "
            "An ensemble that disagrees with the per-coin regime is worth "
            "investigating (a possible early reversal), not automatically ignoring."
        )
        layer_manager = self.services.get("layer_manager")
        if layer_manager and hasattr(layer_manager, "_strategy_hints"):
            hints = getattr(layer_manager, "_strategy_hints", []) or []
            _hint_lines = [
                f"  {h.get('strategy', '?')}: {h.get('symbol', '?')} "
                f"{h.get('direction', '?')} score={h.get('score', 0)} "
                f"{h.get('consensus', '?')}"
                for h in hints[:20]
            ]
            sections.append(
                _hint_header + ("\n" + "\n".join(_hint_lines) if _hint_lines else "")
            )
            # Layer 1 restructure Phase 3 — read the LEGACY summary shape
            # via the explicit ``_strategy_consensus_summary`` alias.
            # Phase 3 repurposed ``_strategy_consensus`` as a per-coin
            # categorical cache (consumed by ScannerWorker / Phase 6
            # package builder). The summary {buy, sell, total_score}
            # entries Claude expects here live in the alias.
            consensus = getattr(
                layer_manager, "_strategy_consensus_summary",
                getattr(layer_manager, "_strategy_consensus", {}),
            ) or {}
            # Defensive: skip rows lacking the summary keys (e.g. a
            # cache snapshot from before the alias was populated).
            summary_rows = {
                sym: data for sym, data in consensus.items()
                if isinstance(data, dict) and "total_score" in data
                and "buy" in data and "sell" in data
            }
            if summary_rows:
                _consensus_rows = [
                    f"    {sym}: {data['buy']} buy / {data['sell']} sell "
                    f"(total score: {data['total_score']:.0f})"
                    for sym, data in sorted(
                        summary_rows.items(),
                        key=lambda x: x[1]["total_score"], reverse=True,
                    )[:15]
                ]
                sections.append(
                    "\n  CONSENSUS PER COIN:\n" + "\n".join(_consensus_rows)
                )
        else:
            sections.append(_hint_header + "\n  (No strategy signals available yet)")

        # Account
        sections.append("\n## ACCOUNT")
        try:
            account_service = self.services.get("account_service")
            if account_service:
                account = await account_service.get_wallet_balance()
                sections.append(f"Equity: ${account.total_equity:,.2f}")
                sections.append(
                    f"Available: ${account.available_balance:,.2f}"
                )
        except Exception as e:
            # Phase 12.2 (lifecycle-logging-audit Gap 2.2-G2): promoted from
            # DEBUG to WARNING. Account balance affects equity in prompt
            # → affects sizing reasoning.
            log.warning(
                f"STRAT_CTX_BALANCE_FAIL | err='{str(e)[:120]}' | {ctx()}"
            )

        # Issue #4: Tiered capital fund limits
        tiered_capital = self.services.get("tiered_capital")
        if tiered_capital:
            try:
                equity = account.total_equity if 'account' in dir() else 168000.0
                deployed = 0.0
                position_service = self.services.get("position_service")
                if position_service:
                    try:
                        pos_list = await position_service.get_positions()
                        for pos in pos_list:
                            deployed += abs(pos.size * pos.entry_price / max(pos.leverage, 1))
                    except Exception as e:
                        log.debug("Position list fetch for deployed capital failed: {err}", err=str(e))
                limits = tiered_capital.get_limits(equity, deployed)
                sections.append(f"\n{limits.to_prompt_text()}")
            except Exception as e:
                # Phase 12.2 (lifecycle-logging-audit Gap 2.2-G3): promoted
                # from DEBUG to WARNING. Tiered capital limits affect
                # capital tier reasoning in the prompt.
                log.warning(
                    f"STRAT_CTX_TIERED_CAPITAL_FAIL | err='{str(e)[:120]}' | {ctx()}"
                )

        # Daily PnL — Phase 9c: expose components so an anomalous PnL %
        # (e.g. "96% drawdown" vs. actual 35%) is immediately diagnosable
        # from the prompt itself. Display value clamped to [-100, +1000]
        # so a corrupted starting_equity never sends a nonsensical number
        # to Claude and contaminates the reasoning.
        sections.append("\n## TODAY'S PERFORMANCE")
        try:
            pnl_manager = self.services.get("pnl_manager")
            if pnl_manager:
                _raw_pct = float(getattr(pnl_manager, "current_pnl_pct", 0.0) or 0.0)
                _display_pct = max(-100.0, min(_raw_pct, 1000.0))
                _real = float(getattr(pnl_manager, "realized_pnl", 0.0) or 0.0)
                _unreal = float(getattr(pnl_manager, "unrealized_pnl", 0.0) or 0.0)
                _start = float(getattr(pnl_manager, "starting_equity", 0.0) or 0.0)
                sections.append(
                    f"Daily PnL: {_display_pct:+.2f}% "
                    f"(real=${_real:+.2f} + unreal=${_unreal:+.2f} / "
                    f"base=${_start:,.0f})"
                )
                if abs(_raw_pct - _display_pct) > 0.01:
                    sections.append(
                        f"  (display clamped; raw={_raw_pct:+.2f}% — check "
                        f"starting_equity freshness)"
                    )
                # Phase 27 (Y-26): expose max-drawdown-today separately
                # from Daily PnL so Claude doesn't confuse a temporary
                # mid-session dip with a flat-day PnL number. Both
                # metrics agree on units (%); the labels are explicit.
                _mdd = float(
                    getattr(pnl_manager, "_max_drawdown_today", 0.0) or 0.0
                )
                if _mdd < 0:
                    sections.append(
                        f"Max drawdown today: {_mdd:.2f}% "
                        f"(deepest mid-session dip — current PnL above shows where we ended)"
                    )
                sections.append(
                    f"Trades today: {getattr(pnl_manager, '_trades_today', 0)}"
                )
        except Exception as e:
            # Phase 12.2 (lifecycle-logging-audit Gap 2.2-G4): promoted
            # from DEBUG to WARNING. Daily PnL affects performance
            # enforcer reasoning in the prompt.
            log.warning(
                f"STRAT_CTX_DAILY_PNL_FAIL | err='{str(e)[:120]}' | {ctx()}"
            )

        # Event buffer — watchdog events since last review (#8)
        # Phase 2 session-stability: same max_events cap as the Call A path
        # so both prompts are bounded during storms.
        event_buffer = self.services.get("event_buffer")
        if event_buffer:
            try:
                _evbuf_max = getattr(
                    getattr(self.settings, "brain", None),
                    "prompt_event_buffer_max_events",
                    20,
                )
                _pre_count = event_buffer.count
                events_text = event_buffer.get_prompt_text(max_events=_evbuf_max)
                if events_text:
                    sections.append(f"\n{events_text}")
                    _kept = min(_pre_count, _evbuf_max)
                    _dropped = max(0, _pre_count - _kept)
                    if _dropped > 0:
                        log.info(
                            f"CLAUDE_PROMPT_TRIMMED | site=context "
                            f"events_total={_pre_count} events_kept={_kept} "
                            f"events_dropped={_dropped} cap={_evbuf_max} | {ctx()}"
                        )
                    event_buffer.clear()  # Clear after feeding to Claude
            except Exception as e:
                log.debug("Event buffer fetch failed: {err}", err=str(e))

        # (Performance coaching moved to TOP of context for prominence)

        # Enterprise log: context summary
        _ctx_el_ms = (time.time() - _t_build) * 1000
        log.info(f"STRAT_CTX | sections={len(sections)} chars={sum(len(s) for s in sections)} el={_ctx_el_ms:.0f}ms | {ctx()}")
        if _ctx_el_ms > 5000:
            log.warning(f"STRAT_CTX_SLOW | el={_ctx_el_ms:.0f}ms sections={len(sections)} | {ctx()}")

        return "\n".join(sections)

    # ═══ CALL A: Trade-focused prompt builder ═══

    def _format_packages_for_prompt(
        self,
        packages: dict,
        lessons_by_sym: dict[str, list[dict]] | None = None,
        session_attempts_by_sym: dict[str, dict] | None = None,
    ) -> str:
        """Layer 1 restructure Phase 7 — render CoinPackage dict as a Claude-readable trade block.

        Emits the per-coin trade candidate block per blueprint Section 7.
        Packages are sorted by ``opportunity_score`` descending so Claude
        sees the strongest setups first. Force-included open positions
        get a marker line so Claude treats them as "manage existing"
        rather than "new entry".

        Phase 6 of the 1D briefing rewrite — when ``[brain].surface_briefing_fields``
        is True, additional lines render the state_label primary +
        secondaries, action_hint, interestingness_score, and the votes
        block (top buy / top sell summary). When False (default), the
        block is byte-identical to pre-Phase-6 production. The flag flips
        to True at Phase 9 cutover.

        Args:
            packages: Dict keyed by symbol → CoinPackage. Output of
                ``ScannerWorker._build_package`` for each selected coin.

        Returns:
            Multi-line string ready to ``sections.append`` into the
            CALL_A prompt. Empty string when ``packages`` is empty.
        """
        if not packages:
            return ""
        # Phase 6 flag — keep both code paths so the flip is purely
        # data-driven (no code change needed to roll back). Defensive
        # access: tests instantiate the strategist via ``__new__`` for
        # pure-function coverage and don't carry a settings attribute;
        # in that case we default to the legacy (flag-off) renderer.
        _settings = getattr(self, "settings", None)
        _brain_cfg = getattr(_settings, "brain", None) if _settings else None
        surface_briefing = bool(getattr(
            _brain_cfg, "surface_briefing_fields", False,
        )) if _brain_cfg is not None else False
        lines = [
            "## TRADE CANDIDATES (passed ScannerWorker qualitative gate; "
            "open-position coins included for HR-2 management)"
        ]
        # Phase 6 — when surfacing briefing fields, sort by interestingness
        # (the new continuous score from the briefing pipeline) so Claude
        # sees the cleanest states first. Legacy mode keeps opportunity_score
        # as the sort key for byte-identical output.
        if surface_briefing:
            sorted_packages = sorted(
                packages.values(),
                key=lambda p: (
                    getattr(p, "interestingness_score", 0.0),
                    getattr(p, "opportunity_score", 0.0),
                ),
                reverse=True,
            )
        else:
            sorted_packages = sorted(
                packages.values(),
                key=lambda p: getattr(p, "opportunity_score", 0.0),
                reverse=True,
            )
        # Phase 9 cutover — under surface_briefing_fields=True,
        # briefing-mode emits packages with qualified=False for
        # legitimate-but-low-interestingness or advisory-only states
        # (the labeller's NO_TRADEABLE_STATE / RECENT_LOSER_COOLDOWN /
        # MANIPULATION_WINDOW). The brain SHOULD see those — it's the
        # whole point of the briefing pipeline (transparency over
        # exclusion). The Q3b BTC/ETH hallucination fix is preserved
        # because briefing mode's selection step does NOT re-add BTC/ETH
        # (no unconditional ref-pair add); only legitimately-built
        # qualified=False packages reach this filter under briefing
        # mode. Skip-rule under briefing mode: only skip when both
        # primary label is NO_TRADEABLE_STATE AND interestingness <
        # the prompt floor AND no open position.
        try:
            from src.workers.scanner.state_labeler import (
                LABEL_NO_TRADEABLE_STATE,
            )
        except Exception:
            LABEL_NO_TRADEABLE_STATE = "NO_TRADEABLE_STATE"
        # Defensive read — same `__new__`-bypass case as `surface_briefing`
        # above; tests construct without settings and we default safely.
        _scanner_cfg = getattr(_settings, "scanner", None) if _settings else None
        _briefing_cfg = (
            getattr(_scanner_cfg, "briefing", None) if _scanner_cfg else None
        )
        prompt_floor = float(getattr(
            _briefing_cfg, "prompt_floor_interestingness", 0.20,
        )) if _briefing_cfg is not None else 0.20
        for pkg in sorted_packages:
            if surface_briefing:
                # Briefing-mode skip rule: only skip the per-coin block
                # when the coin is unambiguously dead — no edge, no
                # position, low interestingness. The brain still sees
                # the per-cycle SCANNER_BRIEFING_SUMMARY rollup.
                _primary = (
                    pkg.state_label.primary if pkg.state_label else ""
                )
                _interest = float(
                    getattr(pkg, "interestingness_score", 0.0) or 0.0
                )
                if (
                    _primary in {LABEL_NO_TRADEABLE_STATE, ""}
                    and pkg.open_position is None
                    and _interest < prompt_floor
                ):
                    continue
            else:
                # Legacy exclusion-mode filter (Q3b BTC/ETH guard).
                if not pkg.qualified and pkg.open_position is None:
                    continue
            try:
                # Phase 6 header includes interestingness + label. Legacy
                # header keeps opportunity_score only.
                if surface_briefing:
                    _label_block = getattr(pkg, "state_label", None)
                    _primary = (
                        _label_block.primary if _label_block else ""
                    ) or "—"
                    _secondary = (
                        list(_label_block.secondary) if _label_block else []
                    )
                    _label_str = (
                        f"[{_primary}" +
                        (f", {', '.join(_secondary[:2])}" if _secondary else "") +
                        "]"
                    )
                    _interest = float(
                        getattr(pkg, "interestingness_score", 0.0) or 0.0
                    )
                    lines.append(
                        f"\n### {pkg.symbol} — interestingness={_interest:.2f} "
                        f"score={pkg.opportunity_score:.2f} {_label_str}"
                        f"{' (open-position, manage)' if pkg.open_position else ''}"
                    )
                else:
                    lines.append(
                        f"\n### {pkg.symbol} - score={pkg.opportunity_score:.2f}"
                        f" {'(open-position, manage)' if pkg.open_position else ''}"
                    )
                # Brain-prompt-enrichment Phase 3.5 (E6) — TIAS lesson
                # lines for candidates flagged RECENT_LOSER_COOLDOWN.
                # Rendered right under the header so the brain reads
                # the past-loss cause before working through the rest
                # of the per-coin block. Dict is pre-fetched async in
                # _build_trade_prompt; here we just look up.
                if lessons_by_sym:
                    lines.extend(
                        self._format_recent_loss_lines(
                            lessons_by_sym.get(pkg.symbol),
                        )
                    )
                # Element 2 (2026-06-11) — session-attempt memory line,
                # same data the full formatter renders so flipping
                # [stage2].enable_full_layer_block can never silently
                # drop the fact. Dict pre-fetched async in
                # _build_trade_prompt; zero attempts renders nothing.
                if session_attempts_by_sym:
                    try:
                        _sa = session_attempts_by_sym.get(pkg.symbol) or {}
                        _sa_line = _session_attempts_line(
                            int(_sa.get("attempts", 0) or 0),
                            float(_sa.get("net_usd", 0.0) or 0.0),
                            int(getattr(
                                getattr(self.settings, "brain", None),
                                "quality_skip_heavy_attempts", 6,
                            )),
                        )
                        if _sa_line:
                            lines.append(_sa_line)
                    except Exception as e:
                        log.debug(
                            "session attempts line failed: {err}",
                            err=str(e),
                        )
                # XRAY counter-setup Phase 5d — surface counter-trade
                # context to the brain. *_FVG_OB_COUNTER setups represent
                # opposite-direction trades because in-direction zones
                # are missing but counter zones are present near price.
                # The brain needs to know this is a counter-trade against
                # the structural bias (lower conviction, but still
                # tradeable) so it can factor that into its decision.
                _setup_label = pkg.xray.setup_type
                _is_counter = "counter" in _setup_label
                if _is_counter:
                    _setup_label = (
                        f"{_setup_label} (COUNTER-TRADE — trade direction "
                        f"is OPPOSITE to market structure bias; lower conviction)"
                    )
                _trade_dir = pkg.xray.trade_direction or "n/a"
                lines.append(
                    f"  Setup: {_setup_label} "
                    f"(confidence {pkg.xray.setup_type_confidence:.2f}, "
                    f"trade_direction={_trade_dir})"
                )
                if pkg.price_data.current:
                    lines.append(
                        f"  Price: ${format_price(pkg.price_data.current)}"
                        f" ({pkg.price_data.change_24h_pct:+.1f}% 24h)"
                        f" regime={pkg.price_data.regime}"
                    )
                lvls = pkg.xray.structural_levels
                if lvls.suggested_sl and lvls.suggested_tp:
                    # Display-only honest RR (2026-05-31): match the SL/TP shown,
                    # not lvls.rr_ratio (=rr_best). Mirrors the live full formatter
                    # (_format_packages_for_prompt_full). rr_best stays the
                    # gate/ranking signal elsewhere.
                    _entry_c = float(getattr(pkg.price_data, "current", 0.0) or 0.0)
                    _sl_c = float(lvls.suggested_sl)
                    _tp_c = float(lvls.suggested_tp)
                    _rr_c = float(lvls.rr_ratio or 0.0)
                    if _entry_c > 0 and _sl_c > 0 and _tp_c > 0:
                        if _tp_c >= _entry_c:
                            _risk_c, _reward_c = _entry_c - _sl_c, _tp_c - _entry_c
                        else:
                            _risk_c, _reward_c = _sl_c - _entry_c, _entry_c - _tp_c
                        if _risk_c > 0:
                            _rr_c = _reward_c / _risk_c
                    lines.append(
                        f"  Suggested SL/TP: ${format_price(lvls.suggested_sl)}/${format_price(lvls.suggested_tp)}"
                        f" (RR {_rr_c:.2f})"
                    )
                lines.append(
                    # Issue 2.12 (2026-06-07) lockstep with the live full-layer
                    # formatter: the tier is the strategy-VOTE consensus; the
                    # score is the TradeScorer STRUCTURAL setup quality. Relabel
                    # the score to setup_quality_score and tag the tier as the
                    # vote consensus so the brain is not misled about consensus
                    # strength. Same field, label-only change.
                    f"  Strategies: {pkg.strategies.fired_count} fired,"
                    f" ensemble {pkg.strategies.ensemble_consensus} (vote consensus),"
                    f" setup_quality_score {pkg.strategies.total_score:.1f}"
                )
                # Layer 4 (2026-05-22) — Consensus-Truth fix.
                # See ``_format_consensus_context`` docstring. Gated by
                # ``brain_prompt_l4_consensus_context_enabled`` for
                # instant rollback. Helper handles its own failure.
                # Defensive getattr so test subclasses / legacy
                # _FakeStrategist mocks that don't override the helper
                # still render the prompt successfully (the helper is
                # additive — missing it = pre-Layer-4 behaviour).
                _l4_helper = getattr(self, "_format_consensus_context", None)
                if callable(_l4_helper):
                    _l4_helper(lines, pkg)
                # Phase 6 — full vote distribution surfacing. Brain-prompt-
                # enrichment Phase 3.1 (2026-05-16) replaced the original
                # top-3-each-side render with a single Top-N mixed line.
                if surface_briefing:
                    self._format_briefing_extras(lines, pkg)
                lines.append(
                    f"  Signal: confidence {pkg.signals.confidence:.2f}"
                    f" direction {pkg.signals.direction}"
                )
                lines.append(
                    f"  Funding: {pkg.alt_data.funding_rate:.4f} ({pkg.alt_data.funding_signal})"
                )
                if pkg.qualification_reasons:
                    lines.append(f"  Why: {', '.join(pkg.qualification_reasons[:5])}")
                # Phase 6 — action hint surfaced as the last advisory line
                # so Claude sees the system's read on what the state
                # suggests. Brain may override with reasoning.
                if surface_briefing:
                    self._format_action_hint(lines, pkg)
                if pkg.open_position:
                    side = pkg.open_position.get("side", "?")
                    entry = pkg.open_position.get("entry_price") or 0.0
                    lines.append(f"  ** OPEN POSITION: {side} from ${format_price(entry)}")
            except Exception as e:
                log.debug("package format failed: {err}", err=str(e))
        return "\n".join(lines)

    def _format_consensus_context(self, lines: list, pkg) -> None:
        """Layer 4 (2026-05-22) — surface the herding reality to the brain.

        Per ``IMPLEMENT_LAYER4_CONSENSUS_TRUTH.md`` A.2, the
        MASTER_SITUATION_REPORT verified that broad strategy agreement
        (5+ strategies) has historically tended to mark crowded/late
        entries with lower per-trade edge than narrower agreement
        (3-4 strategies). The brain reads the consensus LABEL
        (STRONG / GOOD / WEAK) to choose ``size_usd`` in CALL_A; without
        this context, ``STRONG`` on a crowded trade reads as a size-up
        signal — the system, in effect, instructs the brain to bet
        bigger on its worst trades.

        This helper appends a brief truthful-framing note to the prompt.
        The note INFORMS the brain — it does NOT block trades, hardcode
        a size, or mutate the consensus computation. The brain remains
        the sizer; asymmetry on crowded trades emerges from the brain's
        honest reading of the truth, not from a forced rule.

        Aim safeguard (Rule 5): this is a TRUTH-FIX, not a blocking
        fix. No code path elsewhere reads this text back; no sizing
        logic switches on it. The brain weighs the note and decides.

        Rollout safeguard (Rule 11): gated by
        ``brain_prompt_l4_consensus_context_enabled`` (default True).
        Flipping the flag False is an instant rollback that removes
        the note from the prompt without a code change.

        Failures are non-fatal (debug log; legacy lines still render).
        """
        try:
            cfg = self.settings.strategy_engine
            if not cfg.brain_prompt_l4_consensus_context_enabled:
                return
            fired = pkg.strategies.fired_count
            consensus = pkg.strategies.ensemble_consensus
            direction = (
                pkg.signals.direction if pkg.signals else "n/a"
            )
            # Issue #2 (2026-05-31): this line describes the strategies that
            # "fired in {regime} regime" — i.e. the SCORING event — so label it
            # with the regime the coin was SCORED under (consistent with the
            # candidate `Regime:` line and the votes), falling back to the
            # package's live-cache regime only when the coin was not scored.
            _score_reg_cc = (
                getattr(pkg.strategies, "scoring_regime", "") or ""
            ) if getattr(pkg, "strategies", None) else ""
            regime = (
                _score_reg_cc
                or (pkg.price_data.regime if pkg.price_data else "")
                or "unknown"
            )
            lines.append(
                f"  Consensus Context: {fired} strategies fired in "
                f"{regime} regime, ensemble {consensus} {direction}."
            )
            # Candidate-Block Data Integrity Fix — Issue 1b (2026-06-09).
            # The ensemble lean shown above can contradict the X-RAY structure
            # on the same coin (e.g. BSB "ensemble WEAK long" while the X-RAY
            # structure is a short downtrend); a one-sided strategy poll then
            # reads as clean conviction. Label it as a genuine disagreement so
            # the brain does not fade the structure on consensus alone.
            # Presentation only — no vote value is recomputed. Gated by
            # [brain].emit_direction_disagreement_notes.
            try:
                _dd_cfg_cc = getattr(self.settings, "brain", None)
                if getattr(
                    _dd_cfg_cc, "emit_direction_disagreement_notes", True
                ):
                    # NOTE: `direction` here is pkg.signals.direction, which
                    # carries the CONSENSUS/ENSEMBLE lean — scanner_worker.py:840
                    # sets SignalsBlock.direction from the consensus dict's
                    # 'direction', and the intelligence Signal object has no
                    # `.direction` attribute so the line-848 override never fires.
                    # So this correctly compares the ENSEMBLE lean (the BSB
                    # "ensemble WEAK long" the consensus line shows) against the
                    # X-RAY structural direction — not the intelligence signal.
                    _ens_dl = str(direction).lower()
                    _ens_side = (
                        "LONG" if _ens_dl in ("long", "buy")
                        else "SHORT" if _ens_dl in ("short", "sell")
                        else ""
                    )
                    _xr_cc = str(
                        getattr(
                            getattr(pkg, "xray", None), "trade_direction", ""
                        ) or ""
                    ).lower()
                    _xr_cc_side = (
                        "LONG" if _xr_cc in ("long", "buy")
                        else "SHORT" if _xr_cc in ("short", "sell")
                        else ""
                    )
                    if (
                        _ens_side and _xr_cc_side
                        and _ens_side != _xr_cc_side
                    ):
                        # Conditional authority (2026-06-11): X-RAY may claim
                        # direction authority ONLY when its own read is
                        # tradeable. A counter-trade or skip-grade structure
                        # read pointing against a unanimous ensemble is
                        # yesterday's move, not authority (live evidence:
                        # HBAR/HYPE wrong-side shorts at range floors).
                        _brain_cfg_xa = getattr(
                            getattr(self, "settings", None), "brain", None,
                        )
                        _xa_on = bool(getattr(
                            _brain_cfg_xa,
                            "xray_authority_conditional_enabled", True,
                        ))
                        _xa_floor = float(getattr(
                            _brain_cfg_xa, "xray_authority_min_score", 45.0,
                        ))
                        _weak = (
                            _xray_authority_weak(pkg, _xa_floor)
                            if _xa_on else ""
                        )
                        if _weak:
                            lines.append(
                                f"    DISAGREEMENT: the strategy ensemble leans "
                                f"{_ens_side} but the X-RAY structure is "
                                f"{_xr_cc_side} — and the X-RAY read is WEAK "
                                f"this cycle ({_weak}), so do NOT treat "
                                f"structure as authoritative here. Weigh the "
                                f"ensemble lean and the per-coin regime "
                                f"instead; if they do not back a side, "
                                f"declining this coin is correct."
                            )
                        else:
                            lines.append(
                                f"    DISAGREEMENT: the strategy ensemble leans "
                                f"{_ens_side} but the X-RAY structure is "
                                f"{_xr_cc_side} — inputs disagree; structure/X-RAY is "
                                f"authoritative for direction. Weigh the ensemble as "
                                f"one input, not clean conviction."
                            )
                        log.info(
                            f"STRAT_ENSEMBLE_DIR_CONFLICT | "
                            f"sym={getattr(pkg, 'symbol', '?')} "
                            f"ensemble_side={_ens_side} xray_side={_xr_cc_side} "
                            f"xray_weak='{_weak}' "
                            f"consensus={consensus} | {ctx()}"
                        )
            except Exception as _e_cc:
                log.debug(
                    f"STRAT_ENSEMBLE_DIR_CONFLICT_FAIL | "
                    f"sym={getattr(pkg, 'symbol', '?')} "
                    f"err='{str(_e_cc)[:80]}' | {ctx()}"
                )
            lines.append(
                f"    Note: in this project's historical data, broad "
                f"agreement (5+ strategies) has tended to mark "
                f"crowded/late entries with lower per-trade edge "
                f"than narrower-agreement setups (3-4 strategies). "
                f"Consider this when sizing — broad agreement is "
                f"not always strength."
            )
        except Exception as e:
            log.debug(
                f"L4_CONSENSUS_CONTEXT_FAIL | "
                f"sym={getattr(pkg, 'symbol', '?')} "
                f"err='{str(e)[:80]}' | {ctx()}"
            )

    def _format_briefing_extras(self, lines: list, pkg) -> None:
        """Phase 6 — render votes block + interestingness breakdown.

        Called only when ``[brain].surface_briefing_fields`` is True.
        Failures are non-fatal (debug log; legacy lines still render).

        Brain-prompt-enrichment Phase 3.1 (2026-05-16): the per-coin
        votes block now emits a single "Top-N" line ranked across all
        directions (BUY/SELL/NEUTRAL) by ``confidence × weight``. The
        legacy "Top BUY" + "Top SELL" sub-blocks of 3 each are replaced
        in-place; ``N`` defaults to 10 and is operator-tunable via
        ``[brain].surface_top_n_voters``. Setting N to 0 suppresses the
        line without affecting the rest of the votes block. Voters with
        ``confidence × weight == 0`` are filtered (no informational
        content); when no voter clears that bar, the line is omitted.
        """
        try:
            lm = self.services.get("layer_manager") if hasattr(self, "services") else None
            votes_entry = None
            if lm is not None and hasattr(lm, "get_strategy_votes"):
                try:
                    votes_entry = lm.get_strategy_votes(pkg.symbol)
                except Exception:
                    votes_entry = None
            if votes_entry and isinstance(votes_entry, dict):
                votes_dict = votes_entry.get("votes") or {}
                buy_w = votes_entry.get("buy_weighted", 0.0)
                sell_w = votes_entry.get("sell_weighted", 0.0)
                total_voters = len(votes_dict)
                # Brain-prompt-enrichment Phase 3.1 — read Top-N config
                # once, fall back to legacy 3-per-side budget (effective
                # N=6 mixed) when the field is absent.
                _settings = getattr(self, "settings", None)
                _brain_cfg = getattr(_settings, "brain", None) if _settings else None
                top_n = int(getattr(_brain_cfg, "surface_top_n_voters", 10) or 0)
                # Mixed-direction voters ranked by conf × weight. Zero
                # conf*weight contributes no signal — drop those before
                # truncating to N so the top-N is informative even when
                # many strategies abstained.
                ranked_voters = sorted(
                    (
                        (
                            name,
                            v.get("vote", "NEUTRAL"),
                            v.get("confidence", 0.0),
                            v.get("weight", 0.0),
                        )
                        for name, v in votes_dict.items()
                    ),
                    key=lambda t: t[2] * t[3],
                    reverse=True,
                )
                ranked_voters = [
                    t for t in ranked_voters if t[2] * t[3] > 0.0
                ]
                # Candidate-Block Data Integrity Fix — Issue 1b (2026-06-09).
                # The "Votes" line is the ONE-SIDED confirmed-direction tally
                # (strategies only confirm the originator's side), so label it as
                # such when direction-disagreement labeling is on — otherwise a
                # one-sided "BUY=3.10 vs SELL=0.00" reads as the full contest when
                # the Two-sided poll below carries the real opposing strength.
                _dd_on_v = bool(
                    getattr(
                        _brain_cfg, "emit_direction_disagreement_notes", True
                    )
                )
                _votes_label = (
                    "Votes (confirmed-direction tally)" if _dd_on_v else "Votes"
                )
                lines.append(
                    f"  {_votes_label}: BUY={buy_w:.2f} vs SELL={sell_w:.2f} "
                    f"({total_voters} voters)"
                )
                # P2 entry-direction fix (2026-06-04) — surface the honest
                # opposite-direction tally. The line above is one-sided by
                # construction (the strategies only confirm the originator's
                # direction); when two-sided polling is active, this line
                # reports the real strength of the OTHER side so direction is
                # weighed as an honest contest, not a one-sided count. Shown
                # only when the opposing poll found genuine opposing strength.
                _opp_w = float(votes_entry.get("opposing_weighted", 0.0) or 0.0)
                _two_sided_active = bool(votes_entry.get("two_sided"))
                # Issue 1b (2026-06-09): when direction-disagreement labeling is
                # on, render the two-sided poll whenever two-sided polling ran
                # (not only when the opposing weight is non-zero), so the Votes
                # line, the poll line and the Opposition tier all describe the
                # SAME contest — a genuinely-zero opposing side is shown as polled
                # rather than silently omitted (the BSB asymmetry, where the poll
                # line vanished and the one-sided tally looked like clean
                # conviction). Backward compatible when the flag is off.
                _show_poll = (
                    _two_sided_active if _dd_on_v
                    else (_two_sided_active and _opp_w > 0.0)
                )
                if _show_poll:
                    _orig_dir = "BUY" if buy_w >= sell_w else "SELL"
                    _opp_dir = "SELL" if _orig_dir == "BUY" else "BUY"
                    _agree_w = buy_w if _orig_dir == "BUY" else sell_w
                    if _opp_w > 0.0:
                        lines.append(
                            f"    Two-sided poll: {_orig_dir}={_agree_w:.2f} vs "
                            f"{_opp_dir}={_opp_w:.2f} — strategies asked the OTHER "
                            f"side DO back it; weigh both against structure/regime."
                        )
                    else:
                        lines.append(
                            f"    Two-sided poll: {_orig_dir}={_agree_w:.2f} vs "
                            f"{_opp_dir}=0.00 — the opposite side was polled and no "
                            f"strategy backed it; the lean is one-sided."
                        )
                if top_n > 0 and ranked_voters:
                    # Direction-tag the line as (B|S|N conf) so the
                    # brain reads name + side + confidence without a
                    # multi-line breakdown. Weight elided when uniform
                    # (production default 1.0) to keep the line compact.
                    top_slice = ranked_voters[:top_n]
                    s = ", ".join(
                        f"{n}({d[:1]} {c:.2f})"
                        for n, d, c, _w in top_slice
                    )
                    lines.append(f"    Top-{len(top_slice)}: {s}")
                # Brain-prompt-enrichment Phase 3.2 — opposition tier.
                # Brain reads agreement-vs-opposition asymmetry without
                # having to compare ``buy_w`` and ``sell_w`` mentally.
                # Skipped entirely when both weighted sums are zero
                # (no strategy fired — line would be meaningless).
                emit_opp = bool(
                    getattr(_brain_cfg, "emit_vote_opposition", True)
                )
                if emit_opp and (buy_w > 0.0 or sell_w > 0.0):
                    # Direction-reconcile fix (2026-06-04, Problem 4 / F20) — when
                    # the two-sided poll is active, classify the Opposition tier on
                    # the SAME honest opposing weight the Two-sided poll line shows
                    # (``opposing_weighted``), NOT the one-sided buy_w/sell_w. The
                    # one-sided opposing sum is ~0 by construction (strategies only
                    # confirm the originator's direction), which made a coin with
                    # real latent opposition read "SELL=0.00" + "Two-sided
                    # SELL=3.55" + "Opposition NEGLIGIBLE" — three numbers for the
                    # same thing, and the brain cited the wrong one. With this, the
                    # Two-sided poll line and the Opposition tier agree; the Votes
                    # line stays the one-sided confirmed tally (a distinct, labeled
                    # measure). Backward compatible when two-sided is inactive.
                    _two_sided = bool(votes_entry.get("two_sided"))
                    _opp_weighted = float(
                        votes_entry.get("opposing_weighted", 0.0) or 0.0
                    )
                    tier, opp_dir, opp_wsum, agree_wsum = _opposition_tier(
                        buy_w=buy_w, sell_w=sell_w,
                        opposing_weighted=_opp_weighted, two_sided=_two_sided,
                    )
                    if _two_sided:
                        # Two-sided mode: strategies confirm only their own side,
                        # so an opposing-VOTER count is structurally ~0 and would
                        # mislead; report the weighted two-sided contest instead.
                        lines.append(
                            f"    Opposition: {tier} — latent {opp_dir} strength "
                            f"opp_wsum={opp_wsum:.2f} vs "
                            f"agree_wsum={agree_wsum:.2f} (two-sided poll)"
                        )
                    else:
                        # Count opposing-side voters that fired a strong
                        # individual signal (conf >= 0.6) — these are the
                        # voices the brain should consider before trusting
                        # the leading side.
                        strong_opp_count = sum(
                            1
                            for v in votes_dict.values()
                            if v.get("vote") == opp_dir
                            and v.get("confidence", 0.0) >= 0.6
                        )
                        lines.append(
                            f"    Opposition: {tier} — "
                            f"{strong_opp_count} {opp_dir} voters at conf>=0.6 "
                            f"(opp_wsum={opp_wsum:.2f} vs "
                            f"agree_wsum={agree_wsum:.2f})"
                        )
                    log.debug(
                        f"OPPOSITION_TIER_RENDER | tier={tier} "
                        f"basis={'two_sided' if _two_sided else 'one_sided'} "
                        f"opp_wsum={opp_wsum:.2f} agree_wsum={agree_wsum:.2f}"
                    )
                # Brain-prompt-enrichment Phase 3.3 — category split.
                # Brain distinguishes cross-category agreement (more
                # robust) from one-category cluster (weaker). NEUTRAL
                # votes excluded — they carry no directional signal.
                emit_cats = bool(
                    getattr(_brain_cfg, "emit_category_split", True)
                )
                if emit_cats:
                    cat_map = self._strategy_category_map()
                    if cat_map:
                        # cat_counts[<category>] = (buy_count, sell_count).
                        cat_counts: dict[str, list[int]] = {}
                        for name, v in votes_dict.items():
                            vote = v.get("vote", "NEUTRAL")
                            if vote not in ("BUY", "SELL"):
                                continue
                            cat = cat_map.get(name)
                            if not cat:
                                continue
                            slot = cat_counts.setdefault(cat, [0, 0])
                            if vote == "BUY":
                                slot[0] += 1
                            else:
                                slot[1] += 1
                        if cat_counts:
                            # Sort by total directional count desc, then
                            # alphabetical for stable ordering.
                            sorted_cats = sorted(
                                cat_counts.items(),
                                key=lambda kv: (-(kv[1][0] + kv[1][1]), kv[0]),
                            )
                            parts = []
                            for cat, (b, sl) in sorted_cats:
                                if b and sl:
                                    parts.append(f"{cat} {b}B+{sl}S")
                                elif b:
                                    parts.append(f"{cat} {b}B")
                                else:
                                    parts.append(f"{cat} {sl}S")
                            lines.append(
                                f"    Cats: {', '.join(parts)}"
                            )
            # Interestingness component breakdown (one line, compact).
            bd = getattr(pkg, "interestingness_breakdown", None) or {}
            if bd:
                # Show top-3 contributing components.
                top_components = sorted(
                    bd.items(), key=lambda kv: kv[1], reverse=True,
                )[:3]
                comp_str = ", ".join(
                    f"{name}={val:.2f}" for name, val in top_components
                )
                lines.append(
                    f"  State: cleanness={getattr(pkg, 'state_cleanness', 0.0):.2f} "
                    f"confluence={getattr(pkg, 'confluence_count', 0)} "
                    f"top_components=[{comp_str}]"
                )
        except Exception as e:
            log.debug("briefing extras format failed: {err}", err=str(e))

    async def _prefetch_recent_loss_lessons(
        self, packages: dict,
    ) -> dict[str, list[dict]]:
        """Brain-prompt-enrichment Phase 3.5 (E6) — gather TIAS lessons
        for every candidate flagged RECENT_LOSER_COOLDOWN in one async
        wave so the sync formatter can read them without re-querying.

        Reads ``[brain].emit_recent_loss_context`` to short-circuit
        when the operator has the flag off. Returns ``{}`` on any
        failure path (missing db service, no flagged candidates, no
        matching past losses, query exception) so the caller can pass
        the result through unconditionally.

        Side-effect: emits a single ``TIAS_BRIDGE`` log event per
        CALL_A build summarising how many candidates were flagged
        and how many produced lesson rows. Lets operators trace
        which prompts received the enrichment.
        """
        out: dict[str, list[dict]] = {}
        try:
            _settings = getattr(self, "settings", None)
            _brain_cfg = getattr(_settings, "brain", None) if _settings else None
            emit = bool(getattr(_brain_cfg, "emit_recent_loss_context", True))
            if not emit:
                return out
            db = self.services.get("db") if hasattr(self, "services") else None
            if db is None:
                return out
            from src.workers.scanner.state_labeler import (
                LABEL_RECENT_LOSER_COOLDOWN,
            )
            from src.core.trade_recorder import recent_losses_for_setup

            lookback = int(getattr(_brain_cfg, "recent_loss_lookback_hours", 336))
            max_lessons = int(getattr(_brain_cfg, "recent_loss_max_lessons", 2))
            flagged: list = []
            for sym, pkg in packages.items():
                label = getattr(pkg, "state_label", None)
                primary = getattr(label, "primary", "") if label else ""
                secondary = list(getattr(label, "secondary", []) or []) if label else []
                if (
                    primary == LABEL_RECENT_LOSER_COOLDOWN
                    or LABEL_RECENT_LOSER_COOLDOWN in secondary
                ):
                    flagged.append(pkg)
            if not flagged:
                # No flagged candidates — quiet path, no log noise.
                return out
            coros = []
            for pkg in flagged:
                trade_direction = str(
                    getattr(getattr(pkg, "xray", None), "trade_direction", "") or ""
                )
                side = "Buy" if trade_direction.lower().startswith("long") else "Sell"
                regime = (
                    str(getattr(getattr(pkg, "price_data", None), "regime", "") or "")
                    or None
                )
                coros.append(
                    recent_losses_for_setup(
                        db,
                        symbol=pkg.symbol,
                        side=side,
                        regime=regime,
                        hours=lookback,
                        limit=max_lessons,
                    )
                )
            results = await asyncio.gather(*coros, return_exceptions=True)
            for pkg, res in zip(flagged, results):
                if isinstance(res, list) and res:
                    out[pkg.symbol] = res
            log.info(
                f"TIAS_BRIDGE | call=CALL_A flagged={len(flagged)} "
                f"with_lessons={len(out)} lookback_h={lookback} "
                f"max_lessons={max_lessons} | {ctx()}"
            )
        except Exception as e:
            log.debug("recent loss prefetch failed: {err}", err=str(e))
        return out

    async def _prefetch_session_attempts(
        self, packages: dict,
    ) -> dict[str, dict]:
        """Four-Element Prompt Recalibration, Element 2 (2026-06-11) —
        per-coin attempts-today + net result from trade_log for the
        Session-today awareness line.

        Mirrors the recent-loss prefetch contract: flag check, db from
        services, empty dict on ANY failure so the formatter degrades
        silently. The exchange mode is resolved from
        ``transformer.current_mode`` (never hardcoded) so the count
        always matches the ACTIVE exchange's ledger; when the mode is
        unresolvable the line renders NOTHING rather than risking a
        cross-mode count (Rule 4: a wrong awareness line is worse than
        none).
        """
        out: dict[str, dict] = {}
        try:
            _brain_cfg = getattr(
                getattr(self, "settings", None), "brain", None,
            )
            if not bool(getattr(
                _brain_cfg, "session_attempts_enabled", True,
            )):
                return out
            services = getattr(self, "services", None)
            db = (
                services.get("db")
                if services and hasattr(services, "get") else None
            )
            tf = (
                services.get("transformer")
                if services and hasattr(services, "get") else None
            )
            mode = (
                str(getattr(tf, "current_mode", "") or "")
                if tf is not None else ""
            )
            if db is None or not mode or not packages:
                return out
            from src.core.trade_recorder import session_attempts_today
            out = await session_attempts_today(
                db, symbols=list(packages.keys()), exchange_mode=mode,
            )
            log.info(
                f"SESSION_ATTEMPTS | call=CALL_A mode={mode} "
                f"candidates={len(packages)} with_attempts={len(out)} "
                f"| {ctx()}"
            )
        except Exception as e:
            log.debug("session attempts prefetch failed: {err}", err=str(e))
        return out

    def _format_recent_loss_lines(
        self, lessons: list[dict] | None,
    ) -> list[str]:
        """Brain-prompt-enrichment Phase 3.5 (E6) — render TIAS lesson
        lines for a candidate flagged RECENT_LOSER_COOLDOWN.

        Each lesson row sourced from ``trade_intelligence`` via the
        ``recent_losses_for_setup`` helper. One line per lesson, format
        (Issue 2.7 — prominent, actionable CAUTION line):

            CAUTION recent loss [<dir>, <regime>] <±pnl>% via <closed_by> <Nm>
            — do NOT repeat unless structure is materially different. Cause: <ds_why excerpt>.

        ``ds_why`` is kept up to ``brain.tias_cause_max_chars`` (centralized,
        default 120) and truncated at a clause/sentence boundary so the cause
        text — the most decision-relevant part — is no longer cut mid-sentence.
        Returns an empty list when ``lessons`` is empty or None so the
        caller can extend a lines list unconditionally.

        Pure renderer — no DB I/O. The async caller (``_build_trade_prompt``)
        pre-fetches the lessons dict and passes the per-symbol slice
        in here.
        """
        out: list[str] = []
        if not lessons:
            return out
        for lesson in lessons:
            try:
                direction = str(lesson.get("direction", "?") or "?")
                pnl_pct = float(lesson.get("pnl_pct", 0.0) or 0.0)
                closed_by_raw = str(lesson.get("closed_by", "") or "").strip()
                closed_by = closed_by_raw or "?"
                hold_seconds = float(lesson.get("hold_seconds", 0.0) or 0.0)
                hold_min = int(hold_seconds // 60)
                regime = str(lesson.get("regime", "") or "").strip()
                ds_why = str(lesson.get("ds_why", "") or "").strip()
                # Char budget — per-coin block is dense; keep each
                # F22 (c) — the cause is the most decision-relevant part of the
                # lesson (the failure pattern the re-entry guard depends on); the
                # old hard 57-char cut dropped it mid-sentence. Keep up to
                # ``tias_cause_max_chars`` (centralized, default 120) and prefer a
                # clause/sentence boundary so we never cut mid-word.
                _settings = getattr(self, "settings", None)
                _brain_cfg = (
                    getattr(_settings, "brain", None) if _settings else None
                )
                _cause_max = int(getattr(
                    _brain_cfg, "tias_cause_max_chars", 120,
                ) or 120) if _brain_cfg else 120
                if len(ds_why) > _cause_max:
                    _cut = ds_why[:_cause_max]
                    _b = max(_cut.rfind(". "), _cut.rfind("; "), _cut.rfind(", "),
                             _cut.rfind(" "))
                    if _b >= _cause_max // 2:
                        _cut = _cut[:_b]
                    ds_why = _cut.rstrip(" ;,.") + "..."
                regime_str = f", {regime}" if regime else ""
                # Issue 2.7 (2026-06-07): the lesson reaches the prompt and the
                # brain cites it, but it read as one terse advisory line among
                # dense detail and the pattern recurred. Make it PROMINENT and
                # ACTIONABLE — a CAUTION prefix that stands out and an explicit
                # re-entry instruction tying the decision to the failure cause —
                # without adding a gate (per-coin authority is preserved; the
                # brain still decides).
                out.append(
                    f"  CAUTION recent loss [{direction}{regime_str}] "
                    f"{pnl_pct:+.2f}% via {closed_by} {hold_min}m — do NOT repeat "
                    f"unless structure is materially different. Cause: {ds_why}"
                )
            except Exception as e:
                log.debug("lesson line format failed: {err}", err=str(e))
        return out

    def _strategy_category_map(self) -> dict[str, str]:
        """Brain-prompt-enrichment Phase 3.3 — strategy_name → category map.

        Built once per process from the live ``StrategyRegistry`` service
        via its ``get_all()`` public accessor and cached on ``self`` so
        per-cycle renders pay the lookup once. Returns an empty dict
        when the registry service is unavailable (cold start, harness
        without ``services``) so the caller can gracefully omit the
        Cats line rather than rendering garbage.

        The category strings are sourced from each strategy's own
        ``category`` attribute (e.g. ``scalping``, ``momentum``,
        ``mean_reversion``, ``funding_arb``, ``sentiment``, ``advanced``,
        ``predatory``, ``microstructure``, ``time_based``,
        ``cross_market``, ``ai_enhanced``). Sourced from the registry
        rather than hardcoded so a future strategy rename or new
        category surfaces automatically.
        """
        cached = getattr(self, "_strat_cat_map_cache", None)
        if cached is not None:
            return cached
        out: dict[str, str] = {}
        try:
            services = getattr(self, "services", None)
            registry = (
                services.get("registry")
                if services and hasattr(services, "get")
                else None
            )
            if registry is not None and hasattr(registry, "get_all"):
                for strat in registry.get_all() or []:
                    name = getattr(strat, "name", None)
                    cat = getattr(strat, "category", None)
                    if (
                        isinstance(name, str)
                        and name
                        and isinstance(cat, str)
                        and cat
                    ):
                        out[name] = cat
        except Exception as e:
            log.debug("strategy category map build failed: {err}", err=str(e))
        # Cache even on empty result so we don't retry on every coin
        # render — only retry on a clean process restart.
        self._strat_cat_map_cache = out
        return out

    def _format_action_hint(self, lines: list, pkg) -> None:
        """Phase 6 — surface the labeller's action hint for the primary label.

        Hint table lives in ``src.workers.scanner.state_labeler.ACTION_HINTS``;
        empty/missing primary → no hint line emitted (so the prompt
        stays compact for advisory-only or unlabeled coins).
        """
        try:
            from src.workers.scanner.state_labeler import ACTION_HINTS
            primary = getattr(getattr(pkg, "state_label", None), "primary", "")
            hint = ACTION_HINTS.get(primary, "") if primary else ""
            if hint:
                # Conditional authority (2026-06-11): a command-shaped hint
                # whose side matches a WEAK X-RAY read (counter-trade or
                # skip-grade) compounds the stale-structure push that drove
                # the live wrong-side shorts — withhold it with the reason
                # instead, so the label info survives without the command.
                _brain_cfg_ah = getattr(
                    getattr(self, "settings", None), "brain", None,
                )
                _xa_on_h = bool(getattr(
                    _brain_cfg_ah, "xray_authority_conditional_enabled", True,
                ))
                _hint_side = (
                    "long" if primary.endswith("_LONG")
                    else "short" if primary.endswith("_SHORT")
                    else ""
                )
                _xr_side_h = str(getattr(
                    getattr(pkg, "xray", None), "trade_direction", "",
                ) or "").lower()
                _weak_h = ""
                if _xa_on_h and _hint_side and _hint_side == _xr_side_h:
                    _xa_floor_h = float(getattr(
                        _brain_cfg_ah, "xray_authority_min_score", 45.0,
                    ))
                    _weak_h = _xray_authority_weak(pkg, _xa_floor_h)
                if _weak_h:
                    lines.append(
                        f"  Action hint withheld: the {_hint_side}-side hint "
                        f"aligns with a WEAK X-RAY read ({_weak_h}) — treat "
                        f"that side with reduced conviction, not as a plan."
                    )
                else:
                    lines.append(f"  Action hint: {hint}")
        except Exception as e:
            log.debug("action hint format failed: {err}", err=str(e))

    def _format_packages_for_prompt_full(
        self,
        packages: dict,
        lessons_by_sym: dict[str, list[dict]] | None = None,
        session_attempts_by_sym: dict[str, dict] | None = None,
        vol_floors: dict[str, float] | None = None,
    ) -> str:
        """Stage 2 phase 2 — rich per-coin Layer 1B/1C/1D block.

        Same coin selection + sort rules as ``_format_packages_for_prompt``
        (briefing-mode sort, NO_TRADEABLE_STATE skip when no position).
        Each per-coin block extends from ~1000-1400 chars (briefing) to
        ~2000-2500 chars by surfacing the underlying evidence:

            1. Header (interestingness + score + state label)
            2. XRAY 12 phases (full StructuralAnalysis fields)
            3. Signals (per-component breakdown)
            4. Regime (RegimeState detail)
            5. Strategy votes (Top-N mixed via _format_briefing_extras)
            6. TradeScorer 4-component breakdown (Base/Confluence/
               Context/Quality)
            7. RR setup + Position context

        Each sub-block runs in its own try/except. Missing/broken
        services log STRAT_RICH_BLOCK_FAIL at DEBUG and the block is
        omitted; other sub-blocks render normally. No band-aid
        defaulting — only what the system has, surfaced honestly.

        Gated by ``[stage2].enable_full_layer_block`` (default False).
        When False, ``_build_trade_prompt`` calls the legacy formatter
        and this method is dormant.
        """
        if not packages:
            return ""

        _settings = getattr(self, "settings", None)
        _brain_cfg = getattr(_settings, "brain", None) if _settings else None
        surface_briefing = bool(getattr(
            _brain_cfg, "surface_briefing_fields", False,
        )) if _brain_cfg is not None else False
        # Sniper-Latency-Size Fix Phase 2 (2026-05-07) — gated rollout
        # of per-coin verbosity compression. When True the formatter
        # uses tighter separators and lower float precision on
        # non-critical numeric fields. The compression is identity-
        # preserving (no field removed; no abbreviation requiring a
        # decoder key) so the legacy per-coin rendering shape is byte-
        # for-byte recoverable by flipping the flag back to False.
        _stage2_cfg_local = getattr(_settings, "stage2", None) if _settings else None
        _compress = bool(getattr(
            _stage2_cfg_local, "enable_prompt_compression", False,
        )) if _stage2_cfg_local is not None else False

        # Sort + skip-rule parity with _format_packages_for_prompt so
        # toggling enable_full_layer_block changes ONLY the per-coin
        # rendering depth, not which coins make it into the prompt.
        if surface_briefing:
            sorted_packages = sorted(
                packages.values(),
                key=lambda p: (
                    getattr(p, "interestingness_score", 0.0),
                    getattr(p, "opportunity_score", 0.0),
                ),
                reverse=True,
            )
        else:
            sorted_packages = sorted(
                packages.values(),
                key=lambda p: getattr(p, "opportunity_score", 0.0),
                reverse=True,
            )

        try:
            from src.workers.scanner.state_labeler import (
                LABEL_NO_TRADEABLE_STATE,
            )
        except Exception:
            LABEL_NO_TRADEABLE_STATE = "NO_TRADEABLE_STATE"
        _scanner_cfg = getattr(_settings, "scanner", None) if _settings else None
        _briefing_cfg = (
            getattr(_scanner_cfg, "briefing", None) if _scanner_cfg else None
        )
        prompt_floor = float(getattr(
            _briefing_cfg, "prompt_floor_interestingness", 0.20,
        )) if _briefing_cfg is not None else 0.20

        services = getattr(self, "services", {}) or {}
        structure_cache = services.get("structure_cache")
        signal_worker = services.get("signal_worker")
        regime_detector = services.get("regime_detector")
        layer_manager = services.get("layer_manager")

        out_lines: list[str] = [
            "## TRADE CANDIDATES (full Layer 1B/1C evidence; "
            "open-position coins included for HR-2 management)"
        ]

        # S1 observability (2026-05-30): count candidates whose strategy
        # evidence was empty so the operator can confirm every coin reaches the
        # brain with a truthful, non-ambiguous strategy line (never a silent
        # blank). Emitted as STRAT_EVIDENCE_SUMMARY after the loop.
        _n_zero_fired = 0
        # F19 (2026-06-05): count candidate blocks actually rendered so the user
        # prompt's TRADE CANDIDATES header states the true count the brain is
        # given. The system prompt is now count-neutral (no hardcoded "10"), so
        # this is the single authoritative count and the "10 vs 5" mismatch
        # (brain told to weigh more candidates than present) cannot recur.
        _n_candidates_rendered = 0
        # F31 (2026-06-05): regime-fingerprint provenance. Two coins rendering a
        # byte-identical regime line is a genuine COINCIDENCE — the regime cache
        # is keyed per symbol and each detection builds a fresh RegimeState, so no
        # aliasing is possible (proven). This map records each candidate's regime
        # fingerprint plus the identity of the source object it read, so the
        # post-loop sentinel can confirm duplicates come from DISTINCT sources
        # (coincidence) and would loudly flag a SHARED source if a real cache/copy
        # bug ever appeared. Observability only; the regime path is untouched.
        _regime_fp: dict[str, list[tuple[str, int]]] = {}

        for pkg in sorted_packages:
            if surface_briefing:
                _primary = (
                    pkg.state_label.primary if pkg.state_label else ""
                )
                _interest = float(
                    getattr(pkg, "interestingness_score", 0.0) or 0.0
                )
                if (
                    _primary in {LABEL_NO_TRADEABLE_STATE, ""}
                    and pkg.open_position is None
                    and _interest < prompt_floor
                ):
                    continue
            else:
                if not pkg.qualified and pkg.open_position is None:
                    continue

            coin_lines: list[str] = []
            sub_blocks_rendered = 0
            # F31: per-candidate regime fingerprint + source identity (set in the
            # live-detector regime branch below; empty when no regime rendered).
            _this_regime_fp = ""
            _this_regime_src = 0

            # ---- 1. Header ----
            try:
                if surface_briefing:
                    _label_block = getattr(pkg, "state_label", None)
                    _primary = (_label_block.primary if _label_block else "") or "—"
                    _secondary = (
                        list(_label_block.secondary) if _label_block else []
                    )
                    _label_str = (
                        f"[{_primary}"
                        + (
                            f", {', '.join(_secondary[:2])}"
                            if _secondary else ""
                        )
                        + "]"
                    )
                    _interest = float(
                        getattr(pkg, "interestingness_score", 0.0) or 0.0
                    )
                    coin_lines.append(
                        f"\n### {pkg.symbol} — interestingness={_interest:.2f} "
                        f"score={pkg.opportunity_score:.2f} {_label_str}"
                        f"{' (open-position, manage)' if pkg.open_position else ''}"
                    )
                else:
                    coin_lines.append(
                        f"\n### {pkg.symbol} - score={pkg.opportunity_score:.2f}"
                        f" {'(open-position, manage)' if pkg.open_position else ''}"
                    )
                sub_blocks_rendered += 1
            except Exception as e:
                log.debug(
                    f"STRAT_RICH_BLOCK_FAIL | sym={pkg.symbol} block=header "
                    f"err='{str(e)[:80]}' | {ctx()}"
                )

            # ---- 1b. Brain-prompt-enrichment Phase 3.5 (E6) ----
            # TIAS lesson lines for candidates flagged
            # RECENT_LOSER_COOLDOWN. Rendered right under the header so
            # the brain sees the past-loss cause before the structural
            # detail. Dict pre-fetched async in _build_trade_prompt.
            if lessons_by_sym:
                try:
                    _lesson_lines = self._format_recent_loss_lines(
                        lessons_by_sym.get(pkg.symbol),
                    )
                    if _lesson_lines:
                        coin_lines.extend(_lesson_lines)
                        sub_blocks_rendered += 1
                except Exception as e:
                    log.debug(
                        f"STRAT_RICH_BLOCK_FAIL | sym={pkg.symbol} "
                        f"block=recent_loss err='{str(e)[:80]}' | {ctx()}"
                    )

            # ---- 1c. Element 2 (2026-06-11) ----
            # Session-attempt memory: attempts today + net so far,
            # computed READ-ONLY from trade_log (the truthful ledger)
            # and pre-fetched async in _build_trade_prompt. The
            # twenty-fourth attempt at a grinding coin must be
            # distinguishable from the first. Awareness only — no gate;
            # zero attempts renders nothing.
            if session_attempts_by_sym:
                try:
                    _sa = session_attempts_by_sym.get(pkg.symbol) or {}
                    _sa_line = _session_attempts_line(
                        int(_sa.get("attempts", 0) or 0),
                        float(_sa.get("net_usd", 0.0) or 0.0),
                        int(getattr(
                            getattr(self.settings, "brain", None),
                            "quality_skip_heavy_attempts", 6,
                        )),
                    )
                    if _sa_line:
                        coin_lines.append(_sa_line)
                        sub_blocks_rendered += 1
                except Exception as e:
                    log.debug(
                        f"STRAT_RICH_BLOCK_FAIL | sym={pkg.symbol} "
                        f"block=session_attempts err='{str(e)[:80]}' | {ctx()}"
                    )

            # ---- 2. XRAY (12 phases) ----
            try:
                analysis = (
                    structure_cache.get(pkg.symbol)
                    if structure_cache and hasattr(structure_cache, "get")
                    else None
                )
                if analysis is not None:
                    _setup_label = pkg.xray.setup_type
                    _is_counter = "counter" in _setup_label.lower()
                    _setup_suffix = " (COUNTER-TRADE — opposite to structural bias)" if _is_counter else ""
                    _trade_dir = pkg.xray.trade_direction or "n/a"
                    coin_lines.append(
                        f"  XRAY: setup={_setup_label}{_setup_suffix} "
                        f"conf={pkg.xray.setup_type_confidence:.2f} "
                        f"dir={_trade_dir} score={pkg.xray.setup_score:.0f} "
                        f"quality={getattr(analysis, 'setup_quality', 'n/a')}"
                    )
                    _ms = getattr(analysis, "market_structure", None)
                    _ms_label = getattr(_ms, "structure", None) or getattr(_ms, "label", "n/a")
                    # Element 3 (2026-06-11) — append the pre-clamp range
                    # truth so a breakdown reads as a breakdown. Flag-gated
                    # ([analysis.structure].range_truth_enabled); flag off
                    # restores the prior line byte-identically.
                    _rt_note = ""
                    if bool(getattr(
                        getattr(self.settings, "structure", None),
                        "range_truth_enabled", True,
                    )):
                        _rt_note = _range_breakout_marker(
                            analysis, compact=False,
                        )
                    coin_lines.append(
                        f"  Structure: market_structure={_ms_label} "
                        f"range_pos={getattr(analysis, 'position_in_range', 0.0):.2f}{_rt_note} "
                        f"smc_confluence={getattr(analysis, 'smc_confluence', 0)}"
                    )
                    _fvg = getattr(analysis, "nearest_fvg", None)
                    _ob = getattr(analysis, "nearest_ob", None)
                    _sweep = getattr(analysis, "active_sweep_signal", None)
                    if _fvg or _ob or _sweep:
                        _bits = []
                        if _fvg is not None:
                            # Issue #4 (2026-05-31): FairValueGap/OrderBlock carry
                            # the bullish/bearish polarity on `.direction` — there is
                            # NO `.kind` attribute, so the old getattr(...,'kind')
                            # silently rendered 'n/a' for every coin every cycle.
                            # `.direction` defaults to "" (not absent), so fall the
                            # empty string through to 'n/a' too. Mirrors the canonical
                            # X-RAY SETUPS renderer (see ~line 1594/3893).
                            _bits.append(
                                f"fvg={getattr(_fvg, 'direction', None) or 'n/a'}@"
                                f"{format_price(getattr(_fvg, 'midpoint', 0.0))}"
                            )
                        if _ob is not None:
                            _bits.append(
                                f"ob={getattr(_ob, 'direction', None) or 'n/a'}@"
                                f"{format_price(getattr(_ob, 'midpoint', 0.0))}"
                            )
                        if _sweep is not None:
                            _bits.append(
                                f"sweep={getattr(_sweep, 'signal', 'n/a')}"
                            )
                        coin_lines.append(f"  SMC: {', '.join(_bits)}")
                    _mtf = getattr(analysis, "mtf_confluence", None)
                    if _mtf is not None:
                        coin_lines.append(
                            f"  MTF: quality={getattr(_mtf, 'quality', 'n/a')} "
                            f"score={getattr(analysis, 'mtf_confluence_score', 0)} "
                            f"factors={getattr(analysis, 'total_confluence_factors', 0)}"
                        )
                    _vp = getattr(analysis, "volume_profile", None)
                    if _vp is not None:
                        coin_lines.append(
                            f"  Volume profile: poc="
                            f"{format_price(getattr(analysis, 'poc_price', 0.0) or 0.0)} "
                            f"fib_key={format_price(getattr(analysis, 'fib_key_level', 0.0) or 0.0)}"
                        )
                    _sess = getattr(analysis, "session_context", None)
                    if _sess is not None:
                        coin_lines.append(
                            f"  Session: {getattr(_sess, 'current_session', 'n/a')} "
                            f"{getattr(_sess, 'session_phase', 'n/a')} "
                            f"manipulation_likely="
                            f"{getattr(_sess, 'manipulation_likely', False)}"
                        )
                    lvls = pkg.xray.structural_levels
                    if lvls.suggested_sl and lvls.suggested_tp:
                        # Issue (2026-05-31): the per-candidate Levels RR must
                        # match the SL/TP printed on THIS line. lvls.rr_ratio
                        # carries rr_best (the BETTER of long/short, see
                        # structure_engine.py:409) and was mislabeling a
                        # worse-direction setup with the OPPOSITE side's inflated
                        # RR (e.g. a SHORT setup shown RR=3.62 when its own RR is
                        # 0.23). Recompute from the displayed entry/SL/TP so the
                        # number is self-consistent with the levels shown. This is
                        # DISPLAY-ONLY: rr_best stays the gate/ranking signal
                        # everywhere else (performance_enforcer, scanner qualitative
                        # filter, interestingness, gate.py). The honest both-sides
                        # split is on the "RR by direction" line below.
                        _entry = float(getattr(analysis, "current_price", 0.0) or 0.0)
                        _sl_d = float(lvls.suggested_sl)
                        _tp_d = float(lvls.suggested_tp)
                        _disp_rr = float(lvls.rr_ratio or 0.0)  # fallback if no entry
                        _lvl_side = ""  # F36: direction the SL/TP geometry implies
                        if _entry > 0 and _sl_d > 0 and _tp_d > 0:
                            if _tp_d >= _entry:   # long-oriented levels
                                _risk, _reward = _entry - _sl_d, _tp_d - _entry
                                _lvl_side = "LONG"
                            else:                 # short-oriented levels
                                _risk, _reward = _sl_d - _entry, _entry - _tp_d
                                _lvl_side = "SHORT"
                            if _risk > 0:
                                _disp_rr = _reward / _risk
                        # F36 (2026-06-05): label which direction these structural
                        # SL/TP belong to so the geometry can no longer read as the
                        # opposite of the candidate's X-RAY direction without saying
                        # so (MON showed short-side SL/TP under a dir=long label).
                        _lvl_tag = f" ({_lvl_side}-setup)" if _lvl_side else ""
                        coin_lines.append(
                            f"  Levels{_lvl_tag}: SL=${format_price(lvls.suggested_sl)} "
                            f"TP=${format_price(lvls.suggested_tp)} "
                            f"RR={_disp_rr:.2f}"
                        )
                        # Flag (clarity + observability) when the levels geometry
                        # direction differs from the X-RAY suggested direction.
                        _xr_lv = str(
                            getattr(pkg.xray, "trade_direction", "") or ""
                        ).lower()
                        _xr_lv_side = (
                            "LONG" if _xr_lv in ("long", "buy")
                            else "SHORT" if _xr_lv in ("short", "sell")
                            else ""
                        )
                        if _lvl_side and _xr_lv_side and _lvl_side != _xr_lv_side:
                            coin_lines.append(
                                f"    NOTE: these structural levels are "
                                f"{_lvl_side}-side geometry while X-RAY's suggested "
                                f"direction is {_xr_lv_side} — confirm the SL/TP "
                                f"match the direction you choose."
                            )
                            log.info(
                                f"STRAT_LEVELS_DIR_MISMATCH | sym={pkg.symbol} "
                                f"levels_side={_lvl_side} xray_dir={_xr_lv_side} "
                                f"| {ctx()}"
                            )
                    # H2 (2026-05-30): surface BOTH directions' risk-reward so
                    # the brain weighs each side neutrally. D2 (2026-06-05): the
                    # line is now framed as ONE input (not a "take the better side"
                    # command) — an extreme one-sided RR signals price already left
                    # the low-RR side's structure (spent zone), so the high-RR side
                    # is a reclaim hope, not an edge; direction is decided on the
                    # weight of regime+structure+signal, SKIP on conflict.
                    # analysis.structural_placement carries
                    # rr_long/rr_short (set in structure_engine); defensive
                    # getattr keeps the line optional when absent. This is the
                    # live-path equivalent of the legacy RR_DIR line; direction
                    # is the brain's call at decision time (no auto-flip).
                    _sp_h2 = getattr(analysis, "structural_placement", None)
                    _rr_l = float(getattr(_sp_h2, "rr_long", 0.0) or 0.0) if _sp_h2 else 0.0
                    _rr_s = float(getattr(_sp_h2, "rr_short", 0.0) or 0.0) if _sp_h2 else 0.0
                    if _rr_l > 0 and _rr_s > 0:
                        if _rr_l >= _rr_s:
                            _best_dir, _rr_ratio = "LONG", _rr_l / max(_rr_s, 0.01)
                        else:
                            _best_dir, _rr_ratio = "SHORT", _rr_s / max(_rr_l, 0.01)
                        _il = "Y" if getattr(_sp_h2, "is_long_invalid", False) else "N"
                        _ish = "Y" if getattr(_sp_h2, "is_short_invalid", False) else "N"
                        coin_lines.append(
                            f"  RR by direction: long={_rr_l:.2f} short={_rr_s:.2f} "
                            f"better={_best_dir} ({_rr_ratio:.1f}x) "
                            f"long_invalid={_il} short_invalid={_ish} — RR is ONE "
                            f"input, not a command: an extreme skew usually means "
                            f"price already left the low-RR side's structure (its "
                            f"reward is spent), so weigh BOTH sides against this "
                            f"coin's regime/structure/signal with no lean and SKIP "
                            f"if they conflict; lack of trade history on a side is "
                            f"NOT a reason to avoid it."
                        )
                        # H2 sentinel: a materially-better opposite side exists,
                        # so the operator can confirm the brain takes it or skips
                        # rather than entering the worse-reward direction.
                        if _rr_ratio >= 2.0:
                            log.info(
                                f"STRAT_RR_ASYMMETRY | sym={pkg.symbol} "
                                f"rr_long={_rr_l:.2f} rr_short={_rr_s:.2f} "
                                f"better={_best_dir} ratio={_rr_ratio:.1f}x "
                                f"long_invalid={_il} short_invalid={_ish} | {ctx()}"
                            )
                        # F33 (2026-06-05): confluence-paradox signal. When the
                        # X-RAY structurally-implied side is the WORSE-reward,
                        # roomless side, say so explicitly so the brain understands
                        # a strong structural read (regime + X-RAY + ensemble
                        # agreeing) is correctly unavailable for lack of room and
                        # does not waste reasoning re-deriving it. Clarity only —
                        # the RR veto decision is unchanged. Thresholds centralized
                        # in [analysis.structure] (confluence_veto_*).
                        _struct_cfg = getattr(
                            getattr(self, "settings", None), "structure", None
                        )
                        if getattr(_struct_cfg, "confluence_veto_note_enabled", True):
                            _veto_floor = float(
                                getattr(_struct_cfg, "confluence_veto_rr_floor", 1.0)
                            )
                            _veto_ratio = float(
                                getattr(_struct_cfg, "confluence_veto_ratio", 2.0)
                            )
                            _xray_raw = str(
                                getattr(pkg.xray, "trade_direction", "") or ""
                            ).lower()
                            _xray_side = (
                                "LONG" if _xray_raw in ("long", "buy")
                                else "SHORT" if _xray_raw in ("short", "sell")
                                else ""
                            )
                            _worse_dir = "SHORT" if _best_dir == "LONG" else "LONG"
                            _worse_rr = _rr_s if _worse_dir == "SHORT" else _rr_l
                            if (
                                _xray_side
                                and _xray_side == _worse_dir
                                and _worse_rr < _veto_floor
                                and _rr_ratio >= _veto_ratio
                            ):
                                coin_lines.append(
                                    f"    NOTE: the structurally-implied {_worse_dir} "
                                    f"(X-RAY's read) has insufficient reward-to-risk "
                                    f"room — its RR {_worse_rr:.2f} is far below the "
                                    f"{_best_dir} side ({_rr_ratio:.1f}x). But that high "
                                    f"{_best_dir} RR is the mirror image of the SAME "
                                    f"exhausted move (price already ran past structure), "
                                    f"so it is a reclaim hope, not a confirmed edge. "
                                    f"Neither side may be tradeable here: take {_best_dir} "
                                    f"ONLY if its OWN regime, valid structure and signal "
                                    f"confirm AND it has room; otherwise SKIP — do not "
                                    f"default into {_best_dir} just because its RR is higher."
                                )
                                log.info(
                                    f"STRAT_CONFLUENCE_VETO | sym={pkg.symbol} "
                                    f"confluent_dir={_worse_dir} "
                                    f"worse_rr={_worse_rr:.2f} better={_best_dir} "
                                    f"ratio={_rr_ratio:.1f}x | {ctx()}"
                                )
                    sub_blocks_rendered += 1
            except Exception as e:
                log.debug(
                    f"STRAT_RICH_BLOCK_FAIL | sym={pkg.symbol} block=xray "
                    f"err='{str(e)[:80]}' | {ctx()}"
                )

            # ---- 2b. Volatility-aware stop floor (Five-Fix Follow-Up Fix 3,
            # 2026-06-10) ----
            # The flat 1.5% minimum sat INSIDE volatile coins' noise band, so
            # ordinary wiggle ended correct theses (65% of the losing window
            # closed by stop-hit). The caller prefetches each candidate's
            # volatility-derived floor (the SAME clamp the entry path enforces:
            # recommended_sl_pct x scalar, floored at the reference, capped)
            # ONLY when [risk.volatility_stop_scaling] is enabled — flag off
            # restores the prior prompt byte-identical. Shown so the brain
            # places survivable stops itself instead of being silently widened.
            try:
                if vol_floors:
                    _vf = vol_floors.get(pkg.symbol)
                    if _vf is not None and _vf > 0.0:
                        coin_lines.append(
                            f"  Vol stop floor: {_vf:.2f}% "
                            f"(this coin's noise band — place SL at or beyond "
                            f"it; absolute min 1.5%)"
                        )
            except Exception as e:
                log.debug(
                    f"STRAT_RICH_BLOCK_FAIL | sym={pkg.symbol} block=vol_floor "
                    f"err='{str(e)[:80]}' | {ctx()}"
                )

            # ---- 3. Signals (per-component) ----
            try:
                sig = (
                    signal_worker.get_signal(pkg.symbol)
                    if signal_worker and hasattr(signal_worker, "get_signal")
                    else None
                )
                if sig is not None:
                    _stype = getattr(sig, "signal_type", None)
                    _stype_str = (
                        _stype.value if _stype is not None and hasattr(_stype, "value")
                        else str(_stype) if _stype is not None else "n/a"
                    )
                    coin_lines.append(
                        f"  Signal: type={_stype_str} "
                        f"conf={getattr(sig, 'confidence', 0.0):.2f} "
                        f"source={getattr(sig, 'source', 'n/a')}"
                    )
                    comps = getattr(sig, "components", None) or {}
                    if comps:
                        # Candidate-Block Data Integrity Fix — Issue 4
                        # (2026-06-09): fear_greed is a GLOBAL, direction-inactive
                        # market index on a 0-100 scale; left in the magnitude
                        # ranking it always crowds the top-5 and reads like a live
                        # per-coin directional input (identical on every coin).
                        # When demote is enabled, exclude it from the ranking and
                        # append it ONCE, tagged, as the integer index — visible
                        # but unambiguous, never displacing a live per-coin
                        # component. Presentation only; the fear-greed-inactive-
                        # for-direction fix is unchanged. Flip the flag false to
                        # restore the prior ranking.
                        _brain_cfg_fg = getattr(
                            getattr(self, "settings", None), "brain", None
                        )
                        _fg_demote = bool(
                            getattr(
                                _brain_cfg_fg,
                                "fear_greed_components_demote_enabled", True,
                            )
                        )
                        _fg_raw = comps.get("fear_greed") if _fg_demote else None
                        # Five-Fix Follow-Up — Fix 1 (components purity,
                        # 2026-06-10): hold the internal classifier diagnostics
                        # out of the rendered line (flag-gated, default on).
                        _diag_excluded = bool(
                            getattr(
                                _brain_cfg_fg,
                                "components_diagnostics_excluded", True,
                            )
                        )
                        # Top-N components by absolute weight (so negatives
                        # surface), with fear_greed held out when demoted.
                        # NOTE: the isinstance(int|float) test also drops any
                        # component stored as None — that is intentional and is
                        # how Issue 3's true-absence handling works: when the
                        # sentiment level is UNKNOWN, signal_generator stores
                        # overall_sentiment/news_count/reddit_count as None so a
                        # genuinely-absent input is OMITTED here rather than
                        # rendered as a misleading "0.000" live value.
                        # Fix 1: the bool exclusion is UNCONDITIONAL — bool
                        # subclasses int, so without it a True flag renders as
                        # 1.0000 and outranks small real inputs like funding.
                        top5 = sorted(
                            (
                                (k, float(v))
                                for k, v in comps.items()
                                if isinstance(v, (int, float))
                                and not isinstance(v, bool)
                                and not (_fg_demote and k == "fear_greed")
                                and not (
                                    _diag_excluded
                                    and k in COMPONENT_DIAGNOSTIC_KEYS
                                )
                            ),
                            key=lambda kv: abs(kv[1]),
                            reverse=True,
                        )[:5]
                        if top5 or (
                            _fg_raw is not None
                            and isinstance(_fg_raw, (int, float))
                        ):
                            # Sniper-Latency-Size Fix Phase 2 (2026-05-07) —
                            # under enable_prompt_compression, drop one
                            # decimal of precision and use space-separated
                            # k=v pairs (Claude reads both forms identically;
                            # the comma+space adds ~30% overhead per pair
                            # for no information gain).
                            if _compress:
                                _sep = " "
                                _comp_str = _sep.join(
                                    f"{k}={v:.2f}" for k, v in top5
                                )
                            else:
                                # Candidate-Block Data Integrity Fix — Issue 3
                                # (2026-06-09): precision is config-driven
                                # (default 4) so a real small funding rate like
                                # -0.0002 is visible instead of rounding to
                                # -0.000 and looking dead — matches the Funding:
                                # line. Was a hardcoded .3f.
                                _stage2_cfg = getattr(
                                    getattr(self, "settings", None),
                                    "stage2", None,
                                )
                                _comp_prec = int(
                                    getattr(
                                        _stage2_cfg,
                                        "component_precision_decimals", 4,
                                    )
                                )
                                _sep = ", "
                                _comp_str = _sep.join(
                                    f"{k}={v:.{_comp_prec}f}" for k, v in top5
                                )
                            # Issue 4: append the tagged global fear-greed index
                            # (an integer 0-100 reading) once, after the ranked
                            # per-coin components.
                            if (
                                _fg_demote
                                and _fg_raw is not None
                                and isinstance(_fg_raw, (int, float))
                            ):
                                _fg_entry = (
                                    f"fear_greed={int(_fg_raw)} "
                                    f"(global, direction-inactive)"
                                )
                                _comp_str = (
                                    f"{_comp_str}{_sep}{_fg_entry}"
                                    if _comp_str else _fg_entry
                                )
                            coin_lines.append(f"  Components: {_comp_str}")
                    # Candidate-Block Data Integrity Fix — Issue 1a (2026-06-09).
                    # The Signal is an independent intelligence/OI read; when its
                    # direction contradicts the X-RAY structural direction on the
                    # SAME coin (e.g. SKR strong_buy on a short-structure coin), say
                    # so as a labeled disagreement rather than letting "strong_buy"
                    # read as authoritative. Presentation only — no signal value is
                    # changed; structure/regime stay authoritative for direction.
                    # Mirrors the existing STRAT_LEVELS_DIR_MISMATCH idiom.
                    try:
                        _dd_cfg = getattr(
                            getattr(self, "settings", None), "brain", None
                        )
                        if getattr(
                            _dd_cfg, "emit_direction_disagreement_notes", True
                        ):
                            _sig_side = (
                                "LONG" if _stype_str in ("buy", "strong_buy")
                                else "SHORT" if _stype_str in ("sell", "strong_sell")
                                else ""
                            )
                            _xr_obj = getattr(pkg, "xray", None)
                            _xr_sig = str(
                                getattr(_xr_obj, "trade_direction", "") or ""
                            ).lower()
                            _xr_sig_side = (
                                "LONG" if _xr_sig in ("long", "buy")
                                else "SHORT" if _xr_sig in ("short", "sell")
                                else ""
                            )
                            if (
                                _sig_side and _xr_sig_side
                                and _sig_side != _xr_sig_side
                            ):
                                # Conditional authority (2026-06-11): same
                                # validity condition as the ensemble-vs-xray
                                # note — a weak structure read may not claim
                                # direction authority over the Signal.
                                _xa_on_s = bool(getattr(
                                    _dd_cfg,
                                    "xray_authority_conditional_enabled", True,
                                ))
                                _xa_floor_s = float(getattr(
                                    _dd_cfg, "xray_authority_min_score", 45.0,
                                ))
                                _weak_s = (
                                    _xray_authority_weak(pkg, _xa_floor_s)
                                    if _xa_on_s else ""
                                )
                                if _weak_s:
                                    coin_lines.append(
                                        f"    NOTE: this Signal is an independent "
                                        f"intelligence/OI read; its {_stype_str} "
                                        f"({_sig_side}) direction CONFLICTS with "
                                        f"the X-RAY structure ({_xr_sig_side}) — "
                                        f"and the X-RAY read is WEAK this cycle "
                                        f"({_weak_s}), so structure is NOT "
                                        f"authoritative here. Weigh the Signal, "
                                        f"the ensemble lean and the per-coin "
                                        f"regime on their own merits."
                                    )
                                else:
                                    coin_lines.append(
                                        f"    NOTE: this Signal is an independent "
                                        f"intelligence/OI read; its {_stype_str} "
                                        f"({_sig_side}) direction CONFLICTS with the "
                                        f"X-RAY structure ({_xr_sig_side}). Inputs "
                                        f"disagree — structure and regime are "
                                        f"authoritative for direction; weigh the Signal "
                                        f"as one input, not a command."
                                    )
                                log.info(
                                    f"STRAT_SIGNAL_DIR_CONFLICT | sym={pkg.symbol} "
                                    f"signal={_stype_str} signal_side={_sig_side} "
                                    f"xray_side={_xr_sig_side} "
                                    f"xray_weak='{_weak_s}' | {ctx()}"
                                )
                    except Exception as _e_dd:
                        log.debug(
                            f"STRAT_SIGNAL_DIR_CONFLICT_FAIL | sym={pkg.symbol} "
                            f"err='{str(_e_dd)[:80]}' | {ctx()}"
                        )
                    sub_blocks_rendered += 1
                else:
                    # Fallback to package summary so the sub-block isn't
                    # silently dropped when signal_worker hasn't ticked.
                    coin_lines.append(
                        f"  Signal: confidence {pkg.signals.confidence:.2f} "
                        f"direction {pkg.signals.direction}"
                    )
                    sub_blocks_rendered += 1
            except Exception as e:
                log.debug(
                    f"STRAT_RICH_BLOCK_FAIL | sym={pkg.symbol} block=signals "
                    f"err='{str(e)[:80]}' | {ctx()}"
                )

            # ---- 4. Regime (per-coin RegimeState) ----
            try:
                # Issue E25 (2026-05-28): the regime the strategy worker SCORED
                # this coin under, carried on the package beside the votes. When
                # present it is the authoritative LABEL — it matches the scores
                # the brain reads. The detector cache (get_coin_regime) supplies
                # the live metric fields (ADX/atr_pct/chop/...) but its regime
                # WORD can have drifted from the scoring regime; the scoring
                # regime wins for the displayed label so the label and the votes
                # are consistent. Empty -> fall back to the cache (pre-E25).
                _score_reg = (getattr(pkg.strategies, "scoring_regime", "") or "")
                rs = (
                    regime_detector.get_coin_regime(pkg.symbol)
                    if regime_detector and hasattr(regime_detector, "get_coin_regime")
                    else None
                )
                if rs is not None:
                    _reg = getattr(rs, "regime", None)
                    _cache_reg_str = (
                        _reg.value if _reg is not None and hasattr(_reg, "value")
                        else str(_reg) if _reg is not None else "n/a"
                    )
                    # E25 contract preserved: the SCORING word wins the displayed
                    # LABEL so it matches the votes shown below it; fall back to the
                    # live-cache word only when the coin was not scored this cycle.
                    _reg_str = _score_reg or _cache_reg_str
                    # E25 sentinel: prove the label now follows the scores. When
                    # source=scoring and match=False, E25 actively corrected a
                    # drift this cycle (label realigned to the scoring regime).
                    if _score_reg and _score_reg != _cache_reg_str:
                        log.info(
                            f"E25_REGIME_SNAPSHOT | sym={pkg.symbol} "
                            f"pkg_regime={_score_reg} cache_regime={_cache_reg_str} "
                            f"consensus={pkg.strategies.ensemble_consensus} "
                            f"source=scoring match=False | {ctx()}"
                        )
                    # Issue #2 (2026-05-31): when the coin WAS scored, render the
                    # metrics from the SAME scored snapshot carried on the package
                    # (StrategiesBlock.scoring_regime_*), so the word and its own
                    # numbers describe ONE regime — instead of gluing the scoring
                    # word onto the live-cache metrics of a possibly-drifted regime.
                    # Unscored -> fall back to the live-cache metrics (pre-#2/E25).
                    if _score_reg:
                        _m_conf = float(getattr(pkg.strategies, "scoring_regime_confidence", 0.0) or 0.0)
                        _m_adx = float(getattr(pkg.strategies, "scoring_regime_adx", 0.0) or 0.0)
                        _m_atr = float(getattr(pkg.strategies, "scoring_regime_atr_percentile", 0.0) or 0.0)
                        _m_chop = float(getattr(pkg.strategies, "scoring_regime_choppiness", 0.0) or 0.0)
                        _m_volr = float(getattr(pkg.strategies, "scoring_regime_volume_ratio", 0.0) or 0.0)
                        _m_volr_known = bool(getattr(pkg.strategies, "scoring_regime_volume_ratio_known", True))
                        _m_trend = int(getattr(pkg.strategies, "scoring_regime_trend_direction", 0) or 0)
                    else:
                        _m_conf = float(getattr(rs, "confidence", 0.0) or 0.0)
                        _m_adx = float(getattr(rs, "adx", 0.0) or 0.0)
                        _m_atr = float(getattr(rs, "atr_percentile", 0.0) or 0.0)
                        _m_chop = float(getattr(rs, "choppiness", 0.0) or 0.0)
                        _m_volr = float(getattr(rs, "volume_ratio", 0.0) or 0.0)
                        _m_volr_known = bool(getattr(rs, "volume_ratio_known", True))
                        _m_trend = int(getattr(rs, "trend_direction", 0) or 0)
                    # Issue #2: when the live detector has since drifted off the
                    # scored regime, surface the current-conditions word EXPLICITLY
                    # so the brain reads it instead of inferring a silent
                    # contradiction from the MARKET-DATA [TAG] / Consensus lines.
                    _drift_note = ""
                    if _score_reg and _score_reg != _cache_reg_str and _cache_reg_str != "n/a":
                        _drift_note = f" (live conditions now read {_cache_reg_str})"
                    # Issue #3A (2026-05-31): honest vol_ratio — render `n/a` when
                    # the ratio is not a real measurement (rather than a fake 1.00),
                    # else 3dp so a genuinely-low ~0.06 is not floored to 0.00 by
                    # the old :.2f.
                    _vr_disp = "n/a" if not _m_volr_known else f"{_m_volr:.3f}"
                    coin_lines.append(
                        f"  Regime: {_reg_str} "
                        f"conf={_m_conf:.2f} "
                        f"ADX={_m_adx:.1f} "
                        f"atr_percentile={_m_atr:.0f} "
                        f"chop={_m_chop:.0f} "
                        f"vol_ratio={_vr_disp} "
                        f"trend_dir={_m_trend:+d}"
                        f"{_drift_note}"
                    )
                    # F31: record this coin's regime fingerprint and the identity
                    # of the source object it read (the live RegimeState, or the
                    # scored package when the detector cache missed). Distinct ids
                    # on a shared fingerprint prove coincidence, not aliasing.
                    _this_regime_fp = (
                        f"{_reg_str}|conf={_m_conf:.2f}|ADX={_m_adx:.1f}"
                        f"|atrp={_m_atr:.0f}|chop={_m_chop:.0f}|trend={_m_trend:+d}"
                    )
                    _this_regime_src = id(pkg.strategies) if _score_reg else id(rs)
                    # S5 (2026-05-30): flag the strong-trend-on-thin-volume
                    # oddity (e.g. INJ ADX=36 on vol_ratio=0.03) so the brain
                    # does not read a high-ADX "trend" on near-zero relative
                    # volume as a confirmed directional regime. Issue #3A: only
                    # when volume is a REAL measurement (a missing ratio is not
                    # "thin volume"), and on the SAME numbers shown above.
                    if _m_volr_known and _m_adx >= 25.0 and 0.0 < _m_volr <= 0.10:
                        coin_lines.append(
                            f"    Caveat: strong ADX ({_m_adx:.1f}) on thin "
                            f"relative volume ({_m_volr:.3f}) — trend reading is "
                            f"on low participation; treat regime conf with caution."
                        )
                    _cats = list(getattr(rs, "active_strategy_categories", []) or [])
                    if _cats:
                        # Sniper-Latency-Size Fix Phase 2 (2026-05-07) —
                        # under enable_prompt_compression use a single
                        # space delimiter (categories are short tokens
                        # like ``scalping``, ``momentum`` — the comma+
                        # space adds redundant width).
                        _cat_sep = " " if _compress else ", "
                        coin_lines.append(
                            f"  Active categories: {_cat_sep.join(_cats)}"
                        )
                    sub_blocks_rendered += 1
                elif _score_reg:
                    # Issue #2 (2026-05-31): the live detector cache missed for
                    # this coin, but it WAS scored this cycle — so render the
                    # scored word WITH the scored metrics carried on the package
                    # (instead of the bare "detail not yet cached" word-only line
                    # that discarded the metrics we now have). No live-drift note
                    # here: with no live cache read there is nothing to compare.
                    _ms_volr = float(getattr(pkg.strategies, "scoring_regime_volume_ratio", 0.0) or 0.0)
                    _ms_volr_known = bool(getattr(pkg.strategies, "scoring_regime_volume_ratio_known", True))
                    _ms_vr_disp = "n/a" if not _ms_volr_known else f"{_ms_volr:.3f}"
                    coin_lines.append(
                        f"  Regime: {_score_reg} "
                        f"conf={float(getattr(pkg.strategies, 'scoring_regime_confidence', 0.0) or 0.0):.2f} "
                        f"ADX={float(getattr(pkg.strategies, 'scoring_regime_adx', 0.0) or 0.0):.1f} "
                        f"atr_percentile={float(getattr(pkg.strategies, 'scoring_regime_atr_percentile', 0.0) or 0.0):.0f} "
                        f"chop={float(getattr(pkg.strategies, 'scoring_regime_choppiness', 0.0) or 0.0):.0f} "
                        f"vol_ratio={_ms_vr_disp} "
                        f"trend_dir={int(getattr(pkg.strategies, 'scoring_regime_trend_direction', 0) or 0):+d}"
                    )
                    sub_blocks_rendered += 1
                elif pkg.price_data.regime:
                    coin_lines.append(
                        f"  Regime: {pkg.price_data.regime} "
                        f"(per-coin detail not yet cached)"
                    )
                    sub_blocks_rendered += 1
            except Exception as e:
                log.debug(
                    f"STRAT_RICH_BLOCK_FAIL | sym={pkg.symbol} block=regime "
                    f"err='{str(e)[:80]}' | {ctx()}"
                )

            # ---- 5. Strategy ensemble line + votes ----
            try:
                _fired_n = pkg.strategies.fired_count
                _ens = pkg.strategies.ensemble_consensus
                # Issue 2.12 (2026-06-07): the tier (ensemble_consensus) is the
                # strategy-VOTE consensus (agree-vs-oppose weight; the numeric
                # weights are on the Votes line below). total_score is the
                # TradeScorer STRUCTURAL setup quality — an orthogonal measure.
                # F30 had labeled the structural score "ensemble_score", which
                # read like a consensus magnitude sitting next to the tier and
                # could mislead the brain about consensus strength (e.g. a
                # GOOD-tier coin shown with a high score whose vote weight was
                # actually WEAK). Relabel it setup_quality_score and tag the tier
                # as the vote consensus so the two are unambiguous. Label-only;
                # no numeric or logic change.
                coin_lines.append(
                    f"  Strategies: {_fired_n} fired, "
                    f"ensemble {_ens} (vote consensus), "
                    f"setup_quality_score {pkg.strategies.total_score:.1f}"
                )
                # S1 (2026-05-30): never leave the strategy evidence empty and
                # ambiguous. When no strategy fired, say so plainly and tell the
                # brain what to decide on instead, and distinguish a genuine
                # no-signal from a data gap using the package provenance (the
                # Data quality line below carries the source-failed / stale
                # detail). Operator rule: every coin carries correct, non-empty
                # strategy evidence — a truthful "no signal" beats a blank.
                if _fired_n == 0 or _ens in ("NONE", "", None):
                    _blockers_s1 = list(getattr(pkg, "blockers_observed", []) or [])
                    _missing_s1 = list(getattr(pkg, "missing_fields", []) or [])
                    # F22 (a) — fire the "inputs were incomplete" note ONLY on a
                    # genuine strategy/structure-input failure (the same markers
                    # that reduce completeness) or a missing field, so the note no
                    # longer contradicts completeness=1.00 on a coin whose only
                    # blocker is an advisory flag (e.g. recent_loss_within_1h).
                    _fail_s1 = [
                        b for b in _blockers_s1
                        if b in STRATEGY_INPUT_FAILURE_MARKERS
                    ]
                    # Issue 4 (CALL_A exploit/fetch, 2026-06-05) — poll-aware
                    # honesty. "0 fired" means no SCORED setup reached the
                    # ensemble this cycle; it does NOT mean no strategy expressed
                    # a view. The two-sided poll (the same Votes block rendered
                    # below) often carries a real directional lean across the full
                    # voter roster (e.g. SELL=0.80, 27 voters) even at 0-fired.
                    # Surface that lean so the brain does not read "0 fired" as
                    # "no edge" and over-skip a coin that has signal. Stale reads
                    # are flagged so a drifted lean is weighed cautiously.
                    _poll_lean = None
                    try:
                        _lm_v = (
                            self.services.get("layer_manager")
                            if hasattr(self, "services") else None
                        )
                        _ve = (
                            _lm_v.get_strategy_votes(pkg.symbol)
                            if _lm_v is not None
                            and hasattr(_lm_v, "get_strategy_votes")
                            else None
                        )
                        if _ve and isinstance(_ve, dict):
                            _bw = float(_ve.get("buy_weighted", 0.0) or 0.0)
                            _sw = float(_ve.get("sell_weighted", 0.0) or 0.0)
                            _nv = len(_ve.get("votes") or {})
                            if max(_bw, _sw) > 0.0 and _nv > 0:
                                _lean_dir = "BUY" if _bw >= _sw else "SELL"
                                _lean_w = max(_bw, _sw)
                                _fresh_s = float(getattr(
                                    getattr(self.settings, "brain", None),
                                    "consensus_freshness_seconds", 360,
                                ) or 360)
                                _lu = float(_ve.get("last_updated", 0.0) or 0.0)
                                _age_s = (time.time() - _lu) if _lu > 0 else 0.0
                                _stale = _age_s > _fresh_s
                                _poll_lean = (
                                    _lean_dir, _lean_w, _nv, _age_s, _stale,
                                )
                    except Exception:
                        _poll_lean = None
                    if _poll_lean is not None:
                        _ld, _lw, _nv, _age_s, _stale = _poll_lean
                        _stale_note = (
                            f" (last strategy read {int(_age_s)}s old — weigh "
                            f"cautiously)" if _stale else ""
                        )
                        coin_lines.append(
                            f"    No firm strategy consensus this cycle, but the "
                            f"full two-sided strategy poll DID lean {_ld}="
                            f"{_lw:.2f} ({_nv} voters){_stale_note} — this coin "
                            f"carries directional signal, NOT no-edge; weigh it "
                            f"with regime, structure (X-RAY levels) and the "
                            f"Signal read."
                        )
                        try:
                            log.info(
                                f"STRAT_ZERO_FIRED_NONZERO_POLL | "
                                f"sym={pkg.symbol} fired={_fired_n} "
                                f"ensemble={_ens} lean={_ld} weight={_lw:.2f} "
                                f"voters={_nv} age_s={int(_age_s)} "
                                f"stale={_stale} | {ctx()}"
                            )
                        except Exception:
                            pass
                    elif _fail_s1 or _missing_s1:
                        coin_lines.append(
                            "    No strategy signal this cycle AND strategy inputs "
                            "were incomplete (see Data quality below) — treat as a "
                            "data gap, not a confirmed flat; decide on regime, "
                            "structure (X-RAY levels) and the Signal read."
                        )
                    else:
                        coin_lines.append(
                            "    No strategy fired an entry on this coin this cycle "
                            "(no scored setup AND no directional poll lean) — decide "
                            "on regime, structure (X-RAY levels) and the Signal read."
                        )
                    _n_zero_fired += 1
                # Layer 4 (2026-05-22) — Consensus-Truth fix (full-block
                # render path; see ``_format_consensus_context`` docstring).
                # Defensive getattr mirror of the legacy path above.
                _l4_helper_fb = getattr(self, "_format_consensus_context", None)
                if callable(_l4_helper_fb):
                    _l4_helper_fb(coin_lines, pkg)
                if surface_briefing:
                    self._format_briefing_extras(coin_lines, pkg)
                sub_blocks_rendered += 1
            except Exception as e:
                log.debug(
                    f"STRAT_RICH_BLOCK_FAIL | sym={pkg.symbol} block=votes "
                    f"err='{str(e)[:80]}' | {ctx()}"
                )

            # ---- 6. TradeScorer 4-component breakdown ----
            try:
                comps = (
                    layer_manager.get_scorer_components(pkg.symbol)
                    if layer_manager and hasattr(layer_manager, "get_scorer_components")
                    else None
                )
                if comps:
                    # Candidate-Block Data Integrity Fix — Issue 5 (2026-06-09):
                    # when the quality sub-score is below the floor, the high
                    # grade is carried by base/confluence/context while the
                    # setup's own quality is weak (e.g. BSB grade A+ on
                    # quality=7/20). Surface that so a top grade on a low-quality
                    # setup is not read as strength. Always-on rendering; the
                    # grade-capping lever is separate (scorer, config-gated). A
                    # quality floor of 0 disables the annotation.
                    _q_val = float(comps.get("quality", 0.0) or 0.0)
                    _se_cfg = getattr(
                        getattr(self, "settings", None), "strategy_engine", None
                    )
                    _q_floor = float(
                        getattr(_se_cfg, "grade_quality_floor", 10.0)
                    )
                    _q_note = (
                        " [driven by base/confluence/context; quality LOW]"
                        if _q_floor > 0 and _q_val < _q_floor else ""
                    )
                    coin_lines.append(
                        f"  Score: total={comps.get('total', 0.0):.1f} "
                        f"grade={comps.get('grade', 'n/a')} | "
                        f"base={comps.get('base', 0.0):.1f}/40 "
                        f"confluence={comps.get('confluence', 0.0):.1f}/25 "
                        f"context={comps.get('context', 0.0):.1f}/20 "
                        f"quality={comps.get('quality', 0.0):.1f}/20"
                        f"{_q_note}"
                    )
                    sub_blocks_rendered += 1
            except Exception as e:
                log.debug(
                    f"STRAT_RICH_BLOCK_FAIL | sym={pkg.symbol} block=scorer "
                    f"err='{str(e)[:80]}' | {ctx()}"
                )

            # ---- 7. Funding + alt-data line (kept compact) ----
            try:
                coin_lines.append(
                    f"  Funding: {pkg.alt_data.funding_rate:.4f} "
                    f"({pkg.alt_data.funding_signal}) "
                    f"OI_24h={pkg.alt_data.oi_change_24h_pct:+.2f}% "
                    f"F&G={pkg.alt_data.fear_greed}"
                )
                # Issue #12 fix (2026-05-27): data-provenance line so the brain
                # can DISCOUNT a package whose fields are source-failed defaults
                # (blank regime, NONE consensus, neutral direction) rather than
                # treating them as real market neutrality. completeness and the
                # missing/blocked sources were computed upstream and previously
                # discarded; surface them here. Rendered only when the package
                # is not fully clean, to keep the prompt compact.
                _missing = list(getattr(pkg, "missing_fields", []) or [])
                _blockers = list(getattr(pkg, "blockers_observed", []) or [])
                # F22 (b) — split blockers into genuine data/compute SOURCE
                # failures and ADVISORY state/gating flags. Only real source
                # failures render as ``source_failed``; advisory flags
                # (recent_loss_within_*, manipulation_likely_session) are shown
                # separately so the prompt no longer stamps a "source failure" on
                # a coin whose source did not fail (the F22 contradiction). The
                # blockers list itself is unchanged (it still drives the scanner's
                # gating); only the prompt's labelling is corrected.
                _source_failed = [b for b in _blockers if b in SOURCE_FAILURE_MARKERS]
                _advisory = [b for b in _blockers if b not in SOURCE_FAILURE_MARKERS]
                # Issue E10 (2026-05-27): stale_fields was the one provenance
                # field the validator computed but #12 never rendered; surface
                # it on the SAME data-quality line so the brain sees fields
                # that are populated-but-past-freshness, not just absent ones.
                _stale = list(getattr(pkg, "stale_fields", []) or [])
                _completeness = float(getattr(pkg, "completeness", 1.0) or 1.0)
                if _completeness < 1.0 or _missing or _source_failed or _stale:
                    _prov = f"  Data quality: completeness={_completeness:.2f}"
                    if _missing:
                        _prov += f" missing={_missing}"
                    if _source_failed:
                        _prov += f" source_failed={_source_failed}"
                    if _stale:
                        _prov += f" stale={_stale}"
                    coin_lines.append(_prov)
                if _advisory:
                    coin_lines.append(
                        f"  Advisory: {_advisory} "
                        "(state/gating flags, not data-source failures)"
                    )
                if pkg.qualification_reasons:
                    coin_lines.append(
                        f"  Why: {', '.join(pkg.qualification_reasons[:5])}"
                    )
                if surface_briefing:
                    self._format_action_hint(coin_lines, pkg)
                sub_blocks_rendered += 1
            except Exception as e:
                log.debug(
                    f"STRAT_RICH_BLOCK_FAIL | sym={pkg.symbol} block=funding "
                    f"err='{str(e)[:80]}' | {ctx()}"
                )

            # ---- 8. Position context (only when open) ----
            if pkg.open_position:
                try:
                    side = pkg.open_position.get("side", "?")
                    entry = float(pkg.open_position.get("entry_price") or 0.0)
                    coin_lines.append(
                        f"  ** OPEN POSITION: {side} from ${format_price(entry)} "
                        f"(see Call B for full management context)"
                    )
                    sub_blocks_rendered += 1
                except Exception as e:
                    log.debug(
                        f"STRAT_RICH_BLOCK_FAIL | sym={pkg.symbol} block=position "
                        f"err='{str(e)[:80]}' | {ctx()}"
                    )

            _coin_block = "\n".join(coin_lines)
            log.debug(
                f"STRAT_RICH_BLOCK_RENDER | sym={pkg.symbol} "
                f"chars={len(_coin_block)} sub_blocks={sub_blocks_rendered} "
                f"| {ctx()}"
            )
            out_lines.extend(coin_lines)
            _n_candidates_rendered += 1
            # F31: accumulate this candidate's regime fingerprint + source id.
            if _this_regime_fp:
                _regime_fp.setdefault(_this_regime_fp, []).append(
                    (pkg.symbol, _this_regime_src)
                )

        # F19 (2026-06-05): stamp the true rendered candidate count into the
        # header so the user prompt agrees with the count-neutral system prompt.
        # out_lines[0] is the header initialized above; nothing is inserted
        # before it, so this rewrite is safe.
        out_lines[0] = (
            f"## TRADE CANDIDATES ({_n_candidates_rendered} candidates; "
            "full Layer 1B/1C evidence; open-position coins included for "
            "HR-2 management)"
        )
        # S1 observability — confirm every candidate carried a truthful strategy
        # line (empty cases labeled, never silently blank). F19: rendered= is the
        # count the brain actually receives (candidates= is the pre-skip total).
        log.info(
            f"STRAT_EVIDENCE_SUMMARY | candidates={len(sorted_packages)} "
            f"rendered={_n_candidates_rendered} "
            f"zero_fired_labeled={_n_zero_fired} | {ctx()}"
        )
        # F31 — surface any duplicate regime fingerprint with provenance so the
        # operator can confirm it is coincidental (distinct source objects) rather
        # than a per-coin cache/copy bug (a shared source object). verdict=
        # coincidence_distinct_sources is the expected, healthy case.
        for _fp, _members in _regime_fp.items():
            if len(_members) > 1:
                _syms = [m[0] for m in _members]
                _ids = {m[1] for m in _members}
                _verdict = (
                    "coincidence_distinct_sources"
                    if len(_ids) == len(_members)
                    else "SHARED_SOURCE_INVESTIGATE"
                )
                log.info(
                    f"STRAT_REGIME_FINGERPRINT_DUP | symbols={_syms} "
                    f"distinct_source_ids={len(_ids)} verdict={_verdict} "
                    f"fp='{_fp}' | {ctx()}"
                )

        return "\n".join(out_lines)

    async def _build_trade_prompt(self) -> str:
        """Build trade-finding prompt for Call A. Target: ~12-14K chars.

        Contains: market data, regime, X-RAY, strategy hints, account.
        Does NOT contain: position details, theses, recently closed, lessons.

        Layer 1 restructure Phase 7 — when ``settings.brain.use_packages``
        is True (default) AND ``layer_manager._coin_packages`` is non-empty,
        a TRADE CANDIDATES block is prepended summarizing the qualified
        packages. The legacy per-coin sections still run; Phase 9
        observation drives any subsequent reduction. Set
        ``[brain].use_packages = false`` to fall back to the legacy path
        only.
        """
        _t_build = time.time()
        sections = []
        # Per-section wall-clock timings for STRAT_PROMPT_BUILD observability.
        # Preserves the existing STRAT_CALL_A_CTX total at the end of this
        # method; adds granular visibility into which section dominates.
        _timings: dict[str, float] = {}
        _t_sec = time.time()

        # Performance Enforcer coaching no longer injected (aggressive-
        # framing rewrite 2026-05-05). The "PERFORMANCE COACH (your stats
        # today):", "CAPITAL PRESERVATION MODE", "RISK MANAGEMENT MODE",
        # win rate / loss streak / per-coin best-worst breakdown text was
        # training defensive bias against the operator's stated aim of
        # aggressive market exploitation.
        #
        # The PerformanceEnforcer module itself is untouched. Its role
        # outside the prompt continues uninterrupted: enforcement levels
        # (consumed by strategy_worker via should_allow_trade /
        # get_size_multiplier / qualify_survival_trade), state diagnostics
        # (consumed by telegram bot.py and handlers/system.py via
        # get_status), check_and_enforce called by enforcer_worker every
        # 60s, on_trade_closed called by manager.py. The get_coaching_text
        # method itself stays defined — the dead _build_context_prompt at
        # line 759 also calls it and we leave that path alone per FIX
        # Rule 5. Reset _t_sec so STRAT_PROMPT_BUILD timing stays correct.
        _t_sec = time.time()

        # === EARLY FETCH: Regime + Fear & Greed (needed for regime instructions) ===
        _regime_str = "unknown"
        _regime_confidence = 0.5
        _fear_greed_value = 50
        _regime_state = None
        _fg_data = None

        try:
            regime_detector = self.services.get("regime_detector")
            if regime_detector:
                # Read RegimeWorker's cached detection (updated every ~600s).
                # Zero-cost in the happy path; avoids ~30s of H1 TA recompute.
                _regime_state = regime_detector.get_last_regime()
                if _regime_state is None:
                    # Boot race: RegimeWorker hasn't run a tick yet. Compute
                    # once so the first prompt has real data; subsequent
                    # strategist calls hit the cache.
                    _regime_state = await regime_detector.detect()
                if _regime_state:
                    _regime_str = _regime_state.regime.value
                    _regime_confidence = _regime_state.confidence
        except Exception as e:
            log.debug("Early regime detection failed: {err}", err=str(e))

        try:
            fg_service = self.services.get("fear_greed")
            if fg_service:
                _fg_data = await fg_service.get_latest()
                if _fg_data:
                    _fear_greed_value = _fg_data.value
        except Exception as e:
            log.debug("Early fear & greed fetch failed: {err}", err=str(e))

        # Cache for Call B
        self._last_regime_str = _regime_str
        self._last_regime_confidence = _regime_confidence
        self._last_fg_value = _fear_greed_value
        _timings["regime_fetch"] = (time.time() - _t_sec) * 1000
        _t_sec = time.time()

        # Regime block — single factual line (aggressive-framing rewrite
        # 2026-05-05, scope-extended by operator). The full
        # _build_regime_instructions() block contained prescriptive
        # avoidance language: "DEFAULT BIAS: SHORT — for coins without
        # per-coin regime: SELL/SHORT bias — 70% shorts, 30% longs only
        # on extreme oversold bounces", "Oversold RSI in a downtrend
        # means trend is STRONG. Short the bounces", "Fear & Greed
        # extreme fear in a downtrend means the trend is accelerating —
        # NOT a buy signal", and similar regime-specific bias rules. The
        # operator's aim is exploitation matched to per-coin structure;
        # prescriptive global-regime bias trains avoidance instead.
        # The brief factual MARKET REGIME section later in this prompt
        # (still emitted at strategist.py:2616-2639 with global regime
        # name + confidence + per-coin override hint) carries the
        # operational regime context Claude needs. The
        # _build_regime_instructions() helper stays defined; OBS-3 in
        # the plan tracks the garbage-collection pass.
        try:
            # Per-coin-authority Phase 6d follow-up (2026-05-29): this bare
            # factual line previously rendered the GLOBAL directional regime word
            # (e.g. "Global regime: trending_down") unconditionally in the live
            # Call-A prompt — a market-wide direction signal the model could
            # anchor on, contradicting the per-coin-authority block below. In
            # default mode (per_coin_direction_enabled) drop the regime word and
            # state per-coin authority + the breadth sizing brake instead; keep
            # the legacy global line only in the rollback path.
            _pcd_line = bool(getattr(
                getattr(self.settings, "stage2", None),
                "per_coin_direction_enabled", True,
            ))
            if _pcd_line:
                sections.append(
                    f"Market context: Fear & Greed={_fear_greed_value} "
                    f"(per-coin regimes are authoritative for direction; "
                    f"position size auto-throttles when market breadth is one-sided)"
                )
            else:
                sections.append(
                    f"Global regime: {_regime_str} "
                    f"(confidence={_regime_confidence:.0%}, "
                    f"Fear & Greed={_fear_greed_value})"
                )
            # Element 4 (2026-06-11) — remember where the market-context
            # section landed so the session-liveness line (computed later,
            # once the candidate packages are finalized) can be inserted
            # directly after it. Index capture, not string search, so a
            # future prepended section cannot break the pairing.
            _mkt_ctx_idx = len(sections) - 1
        except Exception as e:
            log.debug("Regime line build failed: {err}", err=str(e))
            _mkt_ctx_idx = len(sections) - 1 if sections else 0

        _t_sec = time.time()

        # Direction Performance block removed (aggressive-framing rewrite
        # 2026-05-05, scope-extended by operator). The recent-window
        # win/loss split per direction was training Claude to avoid the
        # currently-losing direction — recency bias, not edge. Two losing
        # shorts in a row don't make the next short a worse setup; the
        # operator wants exploitation matched to per-coin structure, not
        # to the last 20 trades' direction tally. The
        # _build_direction_performance() method stays defined (no callers
        # outside this site after the deletion); OBS-4 garbage-collection
        # pass will retire it. Reset _t_sec for the next section's timer.
        _t_sec = time.time()

        # Trading mode line removed (aggressive-framing rewrite 2026-05-05).
        # The mode header text — "MODE: SHADOW", "MODE: TESTNET", or
        # "MODE: MAINNET (real money)\nYou are trading with REAL capital.
        # Maximum caution required." — was teaching defensive caution that
        # conflicts with the operator's stated aim of aggressive market
        # exploitation. Exchange routing is a plumbing concern owned by
        # OrderService and the trading_mode_mgr remains live for
        # SL/TP sanity thresholds, Telegram label/indicator, headspace_pct,
        # and max_trade_pct — all driven off TradingMode object attributes,
        # never off prompt text. Reset _t_sec here so the next section's
        # wall-clock measurement starts from the correct baseline.
        _t_sec = time.time()

        # === TRADEABLE COINS (dynamic from scanner, replaces hardcoded SUPPORTED_SYMBOLS) ===
        from src.config.constants import SUPPORTED_SYMBOLS, TESTNET_QTY_STEPS
        is_testnet = getattr(self.settings, "bybit", None) and self.settings.bybit.testnet
        thesis_mgr_early = self.services.get("thesis_manager")

        scanner = self.services.get("scanner")
        market_service = self.services.get("market_service")
        ta_cache = self.services.get("ta") or self.services.get("ta_cache")
        volatility_profiler = self.services.get("volatility_profiler")

        universe = await scanner.get_active_universe() if scanner else []
        if is_testnet:
            universe = [s for s in universe if s in SUPPORTED_SYMBOLS]

        universe_line = ", ".join(sorted(universe))
        sections.append(
            f"TRADEABLE COINS THIS CYCLE ({len(universe)} coins):\n"
            f"{universe_line}\n"
            f"Trade ONLY from this list. It updates every 5 minutes.\n"
        )

        # Layer 1 restructure Phase 7 — prepend the CoinPackages summary
        # when use_packages is on (default). Concise per-coin block helps
        # Claude focus on the qualified setups before reading the
        # detailed market data sections below. Phase 9 will measure
        # whether the legacy per-coin queries can be removed.
        _packages_count = 0
        # Direction-reconcile fix (2026-06-04, Problem 5 / F21) — the actual
        # candidate symbols this CALL_A, captured at function scope so the X-RAY
        # structural block below shows exactly these coins (not the top-confluence
        # universe). Empty until the candidate set is finalized; the X-RAY block
        # falls back to the universe-top when empty (cold-start race).
        _candidate_symbols: set[str] = set()
        try:
            use_packages = bool(getattr(self.settings.brain, "use_packages", True))
            if use_packages:
                lm = self.services.get("layer_manager")
                if lm is not None and hasattr(lm, "get_coin_packages"):
                    packages = lm.get_coin_packages()
                    _packages_count = len(packages)
                    # Phase 13 Gap I1 (output-quality obs): emit
                    # STRATEGIST_PACKAGES_READ tracing event so operators
                    # can correlate the SCANNER_PACKAGE_BUILD_DONE write
                    # with the brain's read at the next CALL_A. Captures
                    # ages so a stale packages cache surfaces visibly.
                    try:
                        import time as _t
                        _now = _t.time()
                        _ages = [
                            int(_now - p.built_at)
                            for p in packages.values()
                            if hasattr(p, "built_at") and p.built_at
                        ]
                        _age_min = min(_ages) if _ages else 0
                        _age_max = max(_ages) if _ages else 0
                    except Exception:
                        _age_min, _age_max = -1, -1
                    log.info(
                        f"STRATEGIST_PACKAGES_READ | call=CALL_A "
                        f"count={_packages_count} "
                        f"age_min_s={_age_min} age_max_s={_age_max} "
                        f"reader=brain_call_a | {ctx()}"
                    )
                    # Issue E16 fix (2026-05-27): open positions no longer
                    # consume the brain's NEW-entry candidate budget. The
                    # candidate budget (top_n_to_brain) is now reserved ENTIRELY
                    # for fresh, non-position candidates; open positions are
                    # surfaced separately in the dedicated "## OPEN POSITIONS"
                    # manage-block below and reviewed by the position-management
                    # call (Call B). Previously positions were pinned into the
                    # budget (slots_left = top_n - len(pinned)), so a crowded book
                    # left few or zero slots for new opportunities and starved
                    # the first-batch ranker (#2). The #2 reserve-slots ranker
                    # now applies over the FULL candidate budget, not leftovers.
                    _stage2_cfg = getattr(self.settings, "stage2", None)
                    _top_n = int(getattr(_stage2_cfg, "top_n_to_brain", 6)) if _stage2_cfg else 6
                    if packages:
                        from src.core.ranking import reserve_slots_union
                        _position_count = sum(
                            1 for p in packages.values() if p.open_position is not None
                        )
                        _candidate_pool = [
                            p for p in packages.values() if p.open_position is None
                        ]
                        if _top_n > 0 and len(_candidate_pool) > _top_n:
                            _picked, _from_opp, _from_int = reserve_slots_union(
                                _candidate_pool, _top_n,
                                opp_key=lambda p: getattr(p, "opportunity_score", 0.0),
                                int_key=lambda p: getattr(p, "interestingness_score", 0.0),
                            )
                            capped = {p.symbol: p for p in _picked}
                        else:
                            capped = {p.symbol: p for p in _candidate_pool}
                            _from_opp = _from_int = 0
                        log.info(
                            f"STRAT_TOP_N_APPLIED | call=CALL_A "
                            f"input_count={len(packages)} cap={_top_n} "
                            f"candidates={len(capped)} "
                            f"positions_in_manage_block={_position_count} "
                            f"from_opportunity={_from_opp} "
                            f"from_interestingness={_from_int} | {ctx()}"
                        )
                        packages = capped
                        # F21 — record the finalized candidate symbols for the
                        # X-RAY structural block (so it shows these coins, not the
                        # universe top-confluence set).
                        _candidate_symbols = set(packages.keys())
                    if packages:
                        # Brain-prompt-enrichment Phase 3.5 (E6) —
                        # pre-fetch TIAS lessons for RECENT_LOSER_COOLDOWN
                        # candidates before the (sync) formatter runs.
                        # The per-coin renderer then reads from this dict
                        # without making any DB calls of its own. Empty
                        # dict when the flag is off / db unavailable /
                        # no flagged candidates / no matching past
                        # losses — the formatter degrades silently.
                        lessons_by_sym = await self._prefetch_recent_loss_lessons(
                            packages,
                        )
                        # Element 2 (2026-06-11) — pre-fetch each
                        # candidate's session-attempt memory (attempts
                        # today + net from trade_log, READ ONLY) before
                        # the sync formatter runs. Same prefetch-pass-in
                        # pattern as the TIAS lessons above.
                        session_attempts_by_sym = (
                            await self._prefetch_session_attempts(packages)
                        )
                        # Element 4 (2026-06-11) — session-liveness line,
                        # inserted directly after the market-context
                        # section. Aggregated from the FINALIZED candidate
                        # set's measured volume ratios (zero new I/O);
                        # unknown-ratio coins are excluded from the
                        # denominator, and with zero measured ratios no
                        # line renders (never fabricate — Rule 4). Context
                        # only, NOT a clock gate: the brain remains free
                        # to take a genuine play at any hour.
                        try:
                            _lv_cfg = getattr(self.settings, "brain", None)
                            if bool(getattr(
                                _lv_cfg, "session_liveness_enabled", True,
                            )):
                                _lv_thr = float(getattr(
                                    _lv_cfg,
                                    "session_liveness_thin_vol_ratio", 0.25,
                                ))
                                _lv_live = float(getattr(
                                    _lv_cfg,
                                    "session_liveness_live_max_thin_share",
                                    0.20,
                                ))
                                _lv_thinmin = float(getattr(
                                    _lv_cfg,
                                    "session_liveness_thin_min_thin_share",
                                    0.60,
                                ))
                                # Cross-check fix (2026-06-11): read each
                                # coin's ratio through the SAME two-source
                                # contract the Regime line renders
                                # (scored snapshot, else live cache) via
                                # _candidate_vol_ratio — an unscored
                                # coin's scoring-field 0.0 is a dataclass
                                # default, not a measurement, and must
                                # not count as thin (Rule 4).
                                _lv_det = self.services.get(
                                    "regime_detector",
                                ) if hasattr(self, "services") else None
                                _lv_ratios = []
                                for _lv_pkg in packages.values():
                                    _lv_v, _lv_known = _candidate_vol_ratio(
                                        _lv_pkg, _lv_det,
                                    )
                                    if _lv_known:
                                        _lv_ratios.append(_lv_v)
                                _lv_label, _lv_thin = _session_liveness(
                                    _lv_ratios, _lv_thr, _lv_live, _lv_thinmin,
                                )
                                if _lv_label != "unknown":
                                    sections.insert(
                                        _mkt_ctx_idx + 1,
                                        f"Session liveness: {_lv_label} — "
                                        f"{_lv_thin} of {len(_lv_ratios)} "
                                        f"measured candidates at or below "
                                        f"volume ratio {_lv_thr:.2f}.",
                                    )
                                log.info(
                                    f"STRAT_SESSION_LIVENESS | "
                                    f"label={_lv_label} thin={_lv_thin} "
                                    f"known={len(_lv_ratios)} "
                                    f"total={len(packages)} thr={_lv_thr} "
                                    f"live_max={_lv_live} "
                                    f"thin_min={_lv_thinmin} | {ctx()}"
                                )
                        except Exception as e:
                            log.debug(
                                "session liveness line failed: {err}",
                                err=str(e),
                            )
                        # Brain-prompt-enrichment observability — one
                        # log event per CALL_A summarising which of the
                        # five enrichment flags are active for this
                        # prompt. Operators correlate this with the
                        # per-cycle STRAT_PROMPT_SIZE / STRAT_CALL_A_END
                        # events to attribute size/latency shifts to
                        # specific enrichments. Cf. doc Part C Rule 6.
                        _bc = getattr(self.settings, "brain", None) if self.settings else None
                        log.info(
                            f"PROMPT_ENRICHMENT_INCLUDED | call=CALL_A "
                            f"top_n_voters={int(getattr(_bc, 'surface_top_n_voters', 0))} "
                            f"vote_opposition={bool(getattr(_bc, 'emit_vote_opposition', False))} "
                            f"category_split={bool(getattr(_bc, 'emit_category_split', False))} "
                            f"recent_loss_context={bool(getattr(_bc, 'emit_recent_loss_context', False))} "
                            f"flagged_coins={len(lessons_by_sym)} "
                            f"packages={len(packages)} | {ctx()}"
                        )
                        # Stage 2 phase 2 — gate the rich Layer 1B/1C
                        # renderer behind [stage2].enable_full_layer_block.
                        # Default False keeps the legacy briefing-mode
                        # output byte-identical; flag-on extends each
                        # per-coin block from ~1.0-1.4K chars to
                        # ~2.0-2.5K chars by surfacing XRAY 12 phases,
                        # signal components, regime detail, and the
                        # scorer 4-component breakdown.
                        _use_full = bool(getattr(
                            _stage2_cfg, "enable_full_layer_block", False,
                        )) if _stage2_cfg else False
                        if _use_full:
                            # Five-Fix Follow-Up — Fix 3 (2026-06-10): prefetch
                            # each candidate's volatility-aware stop floor for
                            # the per-coin "Vol stop floor" line, mirroring the
                            # EXACT clamp the entry path enforces
                            # (strategy_worker Fix-7 block: recommended_sl_pct
                            # x recommended_sl_scalar, floored at
                            # reference_stop_pct, capped at max_cap_pct).
                            # Prefetch-pass-in pattern: the formatter is sync,
                            # so the async profiler reads happen here. Built
                            # ONLY when the scaling flag is on — flag off
                            # restores the prior prompt byte-identical.
                            _vol_floors: dict[str, float] = {}
                            _vss_cfg = getattr(
                                getattr(self.settings, "risk", None),
                                "volatility_stop_scaling", None,
                            )
                            if _vss_cfg is not None and getattr(
                                _vss_cfg, "enabled", False,
                            ):
                                _vp_svc = self.services.get("volatility_profiler")
                                if _vp_svc is not None:
                                    _ref_p = float(getattr(
                                        _vss_cfg, "reference_stop_pct", 1.5,
                                    ))
                                    _cap_p = float(getattr(
                                        _vss_cfg, "max_cap_pct", 5.0,
                                    ))
                                    _scl = float(getattr(
                                        _vss_cfg, "recommended_sl_scalar", 1.0,
                                    ))
                                    _use_prof = bool(getattr(
                                        _vss_cfg, "use_profiler_recommended_sl",
                                        True,
                                    ))
                                    for _vf_sym in packages:
                                        _rec = 0.0
                                        if _use_prof:
                                            try:
                                                _vf_prof = await _vp_svc.get_profile(
                                                    _vf_sym
                                                )
                                                if _vf_prof is not None:
                                                    _rec = float(getattr(
                                                        _vf_prof,
                                                        "recommended_sl_pct", 0.0,
                                                    )) * _scl
                                            except Exception:
                                                _rec = 0.0
                                        _vol_floors[_vf_sym] = max(
                                            _ref_p, min(_rec, _cap_p),
                                        ) if _rec > 0.0 else _ref_p
                            sections.append(
                                self._format_packages_for_prompt_full(
                                    packages,
                                    lessons_by_sym=lessons_by_sym,
                                    session_attempts_by_sym=session_attempts_by_sym,
                                    vol_floors=_vol_floors or None,
                                )
                            )
                        else:
                            sections.append(
                                self._format_packages_for_prompt(
                                    packages,
                                    lessons_by_sym=lessons_by_sym,
                                    session_attempts_by_sym=session_attempts_by_sym,
                                )
                            )
                    else:
                        log.debug(
                            f"PROMPT_PACKAGES_EMPTY | call=CALL_A "
                            f"reason=no_qualified_coins | {ctx()}"
                        )
        except Exception as e:
            log.debug("Phase 7 package prepend failed: {err}", err=str(e))

        _timings["universe"] = (time.time() - _t_sec) * 1000
        _t_sec = time.time()

        # === MARKET DATA with [POS] tags and min merged ===
        # Declare ticker_map at method scope (BEFORE the outer try) so the
        # data-lake section below can always reference it, even if the
        # market_data try block raises an unexpected exception.
        ticker_map: dict[str, Any] = {}
        # Issue #13 fix (2026-05-27): accumulate the per-coin market-data lines
        # into ONE section bound to the "## MARKET DATA" header. Previously the
        # header and each per-coin price/RSI/MACD/ADX line were appended as
        # SEPARATE sections, so the per-coin lines (unmarked) classified as
        # OPTIONAL and were the first dropped under prompt-size trim — the brain
        # could silently lose half its core price/indicator data. Binding them
        # under the ESSENTIAL "## MARKET DATA" marker (matched in the section's
        # first 200 chars) guarantees the core decision data survives trim.
        _md_lines: list[str] = ["## MARKET DATA"]
        try:
            # Build set of symbols with open positions (for [POS] tag)
            open_position_symbols: set[str] = set()
            if thesis_mgr_early:
                try:
                    theses = await thesis_mgr_early.get_open_theses()
                    open_position_symbols = {t["symbol"] for t in (theses or [])}
                except Exception:
                    pass

            included_count = 0
            skipped_count = 0
            _rd = self.services.get("regime_detector")

            # Bulk-fetch every USDT linear ticker in ONE HTTP call. The
            # market_service already caches the bulk result for 30s, so
            # subsequent prompt builds within the cache window are free. On
            # bulk-fetch failure, ticker_map stays empty and the per-symbol
            # fallback inside the loop preserves pre-fix behaviour.
            try:
                _bulk_t = await market_service.get_all_linear_tickers()
                ticker_map = {t.symbol: t for t in (_bulk_t or [])}
            except Exception as e:
                log.debug(
                    "STRAT_BULK_TICKER_FAIL | err='{err}'",
                    err=str(e)[:120],
                )

            # F36 (2026-06-05): guarantee MARKET-DATA parity with the candidate
            # blocks — every candidate must carry a price row. A candidate can be
            # in `packages` but absent from / filtered out of `universe` (e.g. MON
            # after its position closed mid-cycle), which left it with a candidate
            # block but no MARKET DATA row. Iterate the union of universe and the
            # finalized candidate set, and force-include candidates in the filter.
            _md_universe = list(universe)
            for _cand in _candidate_symbols:
                if _cand not in _md_universe:
                    _md_universe.append(_cand)
            _md_rendered: set[str] = set()
            for symbol in _md_universe:
                try:
                    # Dict lookup replaces 30 serial HTTP calls; the per-
                    # symbol fallback (or-branch) only fires when the bulk
                    # result omits a symbol (e.g. a freshly listed coin).
                    ticker = ticker_map.get(symbol) or await market_service.get_ticker(symbol)
                    ta = None
                    if ta_cache:
                        try:
                            ta = await ta_cache.analyze(
                                symbol=symbol, timeframe=TimeFrame.H1
                            )
                        except Exception as e:
                            log.debug("TA analysis failed: {err}", err=str(e))

                    price = ticker.last_price if ticker else 0
                    change = getattr(ticker, "change_24h_pct", 0) or 0
                    rsi = 50
                    macd_hist = 0
                    adx = 0
                    if ta:
                        rsi = ta.get("momentum", {}).get("rsi_14", 50)
                        macd_data = ta.get("trend", {}).get("macd", {})
                        if isinstance(macd_data, dict):
                            macd_hist = macd_data.get("histogram", 0)
                        adx_data = ta.get("trend", {}).get("adx", {})
                        if isinstance(adx_data, dict):
                            adx = adx_data.get("adx", 0)

                    has_position = symbol in open_position_symbols
                    is_notable = (
                        abs(change) > 3.0
                        or rsi < 30 or rsi > 70
                        or adx > 30
                    )
                    is_major = symbol in ("BTCUSDT", "ETHUSDT")

                    if has_position or is_notable or is_major or symbol in _candidate_symbols:
                        tag = " [POS]" if has_position else ""
                        _cr = _rd.get_coin_regime(symbol) if _rd else None
                        rgm_tag = (
                            f" [{_cr.regime.value.upper()} {_cr.confidence*100:.0f}%]"
                            if _cr else ""
                        )
                        vol_tag = ""
                        if volatility_profiler:
                            try:
                                _vp = await volatility_profiler.get_profile(symbol)
                                if _vp:
                                    vol_tag = (
                                        f" VOL={_vp.volatility_class.upper()}"
                                        f" ATR%={_vp.atr_pct_5m:.2f}%"
                                        f" recTP={_vp.recommended_tp_pct:.1f}%"
                                        f" recSL={_vp.recommended_sl_pct:.1f}%"
                                    )
                                    vol_tag += self._magnitude_advisory_tag(_vp)
                            except Exception as e:
                                log.debug(
                                    "VOL_PROFILE_LOOKUP_FAIL | sym={sym} err='{err}'",
                                    sym=symbol, err=str(e)[:80],
                                )
                        # Merge min trade size into coin line
                        _step = TESTNET_QTY_STEPS.get(symbol, 0.1)
                        _min_usd = _step * price if price > 0 else 0
                        min_tag = f" min=${_min_usd:.0f}" if _min_usd > 5 else ""
                        _md_lines.append(
                            f"{symbol}{tag}{rgm_tag}{vol_tag}: ${format_price(price)} ({change:+.1f}% 24h) "
                            f"RSI={rsi:.0f} MACD_hist={macd_hist:.4f} ADX={adx:.0f}{min_tag}"
                        )
                        included_count += 1
                        _md_rendered.add(symbol)
                    else:
                        skipped_count += 1
                except Exception as e:
                    log.debug("Market data for symbol failed: {err}", err=str(e))

            # F36 parity observability — confirm every candidate carries a row;
            # `missing` should always be empty now that candidates are force-
            # included (a non-empty list would flag a regression to investigate).
            _md_missing = sorted(_candidate_symbols - _md_rendered)
            log.info(
                f"STRAT_MARKETDATA_PARITY | candidates={len(_candidate_symbols)} "
                f"rendered={len(_candidate_symbols & _md_rendered)} "
                f"missing={_md_missing} | {ctx()}"
            )

            if skipped_count > 0:
                _md_lines.append(f"({skipped_count} neutral coins omitted for brevity)")

            # Issue #13: emit the bound MARKET DATA block as ONE essential
            # section so the core per-coin price/indicator data survives trim.
            sections.append("\n".join(_md_lines))

            # Regime Divergence (separate section; has its own ## REGIME DIVERGENCE marker)
            if _rd:
                divergent_coins = []
                for _sym in universe:
                    _cr = _rd.get_coin_regime(_sym)
                    if _cr:
                        _cd = _cr.regime.value
                        if (("up" in _cd and "down" in _regime_str)
                                or ("down" in _cd and "up" in _regime_str)):
                            divergent_coins.append(
                                f"{_sym} ({_cd} {_cr.confidence*100:.0f}%)"
                            )
                if divergent_coins:
                    # Per-coin-authority Phase 6 (2026-05-29): dropped the
                    # "DISAGREE with global {regime}" framing — the prompt no
                    # longer states a global DIRECTION to disagree with. This is
                    # now a pure per-coin reinforcement (trade each coin WITH its
                    # OWN regime). Marker "## REGIME DIVERGENCE" kept so the trim
                    # protection on line 518 still matches.
                    sections.append(
                        f"\n## REGIME DIVERGENCE — coins whose per-coin regime is clearly directional:\n"
                        f"  {', '.join(divergent_coins)}\n"
                        f"  Trade these coins WITH their individual regime direction, NOT against it.\n"
                        f"  Do NOT short a coin that is individually in an uptrend.\n"
                        f"  Do NOT buy a coin that is individually in a downtrend."
                    )
        except Exception as e:
            # Issue #13 defensive: still emit the market-data block accumulated
            # so far (so a late failure never silently drops core price data),
            # then the error note.
            if not any(isinstance(s, str) and s.startswith("## MARKET DATA") for s in sections):
                sections.append("\n".join(_md_lines))
            sections.append(f"(market data error: {e})")

        _timings["market_data"] = (time.time() - _t_sec) * 1000
        _t_sec = time.time()

        # Data Lake: snapshot market state. Reuse the bulk ticker_map from
        # the market_data section (dict lookup, zero HTTP) and only fall
        # back to per-symbol get_ticker() on miss (e.g. when bulk failed).
        try:
            data_lake = self.services.get("data_lake")
            if data_lake and market_service:
                _btc = _eth = _sol = 0.0
                for _sym, _getter in [("BTCUSDT", None), ("ETHUSDT", None), ("SOLUSDT", None)]:
                    try:
                        _t = ticker_map.get(_sym) if ticker_map else None
                        if _t is None:
                            _t = await market_service.get_ticker(_sym)
                        if _sym == "BTCUSDT":
                            _btc = _t.last_price
                        elif _sym == "ETHUSDT":
                            _eth = _t.last_price
                        else:
                            _sol = _t.last_price
                    except Exception as e:
                        log.debug("Ticker fetch for data lake failed: {err}", err=str(e))
                await data_lake.write_market_snapshot(btc_price=_btc, eth_price=_eth, sol_price=_sol)
        except Exception as e:
            log.debug("Data lake snapshot failed: {err}", err=str(e))

        _timings["data_lake"] = (time.time() - _t_sec) * 1000
        _t_sec = time.time()

        # X-RAY Structural Intelligence
        try:
            structure_cache = self.services.get("structure_cache")
            if structure_cache:
                ranked_setups = structure_cache.get_ranked_setups()
                session_ctx = None
                if ranked_setups and ranked_setups[0].session_favorable is not None:
                    first = structure_cache.get(ranked_setups[0].symbol)
                    if first and first.session_context:
                        session_ctx = first.session_context
                if not session_ctx:
                    all_cached = structure_cache.get_all()
                    for _sym, _analysis in all_cached.items():
                        if _analysis.session_context:
                            session_ctx = _analysis.session_context
                            break
                if session_ctx:
                    sc = session_ctx
                    sections.append(
                        f"\n## SESSION: {sc.current_session.upper()} ({sc.session_phase}) "
                        f"| {sc.session_elapsed_minutes}min elapsed, {sc.session_remaining_minutes}min remaining"
                        f"\n  {sc.trading_recommendation}"
                        + (f"\n  Warning: Manipulation likely" if sc.manipulation_likely else "")
                        + f"\n  Next: {sc.next_session} in {sc.next_session_starts_in_minutes}min"
                    )

                # Direction-reconcile fix (2026-06-04, Problem 5 / F21) — show the
                # ACTUAL candidates' structural rows, not the top-confluence
                # universe coins. The pre-fix get_top_setups(n=8) leaked ~10
                # untradeable universe coins (LINK/AERO/ICP/AXS...) while dropping
                # 4 of 5 candidates, so the brain reasoned on the wrong coins'
                # X-RAY. Fetch each candidate's cached analysis directly (ranked
                # by score for readability); fall back to the universe-top only
                # when there are no candidates yet (cold-start race).
                if _candidate_symbols:
                    top_setups = [
                        _a for _a in (
                            structure_cache.get(_s) for _s in _candidate_symbols
                        )
                        if _a is not None
                    ]
                    top_setups.sort(key=lambda _a: _a.setup_score, reverse=True)
                else:
                    top_setups = structure_cache.get_top_setups(n=8)
                log.info(
                    f"XRAY_FILTERED | candidates={len(_candidate_symbols)} "
                    f"rows={len(top_setups)} "
                    f"total_cache={len(structure_cache.get_all())} | {ctx()}"
                )
                if top_setups:
                    xray_lines = ["\n## X-RAY STRUCTURAL SETUPS (ranked by confluence)"]
                    for a in top_setups:
                        line = f"  {a.symbol} (${format_price(a.current_price)}): "
                        ns = a.nearest_support
                        nr = a.nearest_resistance
                        ms = a.market_structure
                        sp = a.structural_placement
                        if ns:
                            line += f"S=${format_price(ns.price)}({ns.strength:.1f}/5,{ns.touches}t) "
                        if nr:
                            line += f"R=${format_price(nr.price)}({nr.strength:.1f}/5,{nr.touches}t) "
                        if ms and ms.structure != "unknown":
                            line += f"struct={ms.structure}({ms.strength}) "
                        line += f"pos={a.position_in_range:.0%} "
                        # Element 3 (2026-06-11) — pre-clamp range truth:
                        # a clamped 0%/100% can hide a live break; the
                        # compact marker surfaces it. Flag-gated; "" when
                        # in range so the legacy line is byte-identical.
                        if bool(getattr(
                            getattr(self.settings, "structure", None),
                            "range_truth_enabled", True,
                        )):
                            line += _range_breakout_marker(a, compact=True)
                        if sp:
                            line += f"RR=1:{sp.rr_ratio:.1f}({sp.rr_quality}) "
                        if a.nearest_fvg:
                            nf = a.nearest_fvg
                            line += f"FVG={nf.direction}(${format_price(nf.bottom)}-${format_price(nf.top)}) "
                        if a.nearest_ob:
                            no = a.nearest_ob
                            fresh_tag = "FRESH" if no.fresh else f"{no.retests}r"
                            line += f"OB={no.direction}(${format_price(no.low)}-${format_price(no.high)},{fresh_tag},s={no.strength_score:.0f}) "
                        if a.active_sweep_signal:
                            sw = a.active_sweep_signal
                            line += f"SWEEP={sw.signal}(rev={sw.reversal_strength:.2f}) "
                        if a.smc_confluence > 0:
                            line += f"SMC={a.smc_confluence} "
                        if a.poc_price:
                            vp_pos = a.volume_profile.current_vs_poc if a.volume_profile else "?"
                            line += f"POC=${format_price(a.poc_price)}({vp_pos}) "
                        if a.fib_key_level:
                            confl = ""
                            if a.fibonacci and a.fibonacci.confluence_with:
                                confl = f",{a.fibonacci.confluence_with}"
                            line += f"FIB=${format_price(a.fib_key_level)}{confl} "
                        if a.mtf_confluence and a.mtf_confluence.score > 0:
                            line += f"MTF={a.mtf_confluence.score}/10({a.confluence_quality}) "
                        if a.total_confluence_factors > 0:
                            line += f"CONFL={a.total_confluence_factors} "
                        line += f"setup={a.setup_quality}({a.setup_score})"
                        xray_lines.append(line)

                    # F21 — the skip-coins tail was removed: it appended up to 10
                    # non-candidate universe coins ("... mid-range or weak
                    # structure, skip or wait"), exactly the untradeable leak this
                    # fix eliminates. The block now shows only the actual
                    # candidates' structural rows.
                    sections.append("\n".join(xray_lines))
                    log.debug(
                        f"XRAY_CONTEXT | setups_sent={len(top_setups)} "
                        f"top={top_setups[0].symbol}({top_setups[0].setup_score})"
                    )
        except Exception as e:
            # Phase 12.2 (lifecycle-logging-audit Gap 2.2-G1, HIGH):
            # promoted from DEBUG to WARNING with structured tag (CALL_A path).
            log.warning(
                f"XRAY_CTX_BUILD_FAIL | call=CALL_A "
                f"err='{str(e)[:200]}' | {ctx()}"
            )

        _timings["xray"] = (time.time() - _t_sec) * 1000
        _t_sec = time.time()

        # Sentiment
        sections.append("\n## SENTIMENT")
        if _fg_data:
            sections.append(
                f"Fear & Greed: {_fg_data.value} ({getattr(_fg_data, 'classification', 'neutral')})"
            )

        _timings["sentiment"] = (time.time() - _t_sec) * 1000
        _t_sec = time.time()

        # Regime — Per-coin-authority Phase 6 (2026-05-29).
        # The global (BTC) regime is no longer a DIRECTION mandate. Default
        # (stage2.per_coin_direction_enabled True): render this block as factual
        # CONTEXT only — each coin's OWN per-coin regime (shown per candidate) is
        # the direction authority, and the only market-wide lever is the breadth
        # SIZING brake (Phase 5). ROLLBACK (flag False): restore the legacy global
        # direction_hint + high-confidence "use as default bias" NOTE (the
        # profitable short-bias safety net) if a trial shows the structurally-
        # losing long side bleeding once the global short-bias is removed.
        # The content is BUNDLED into the ESSENTIAL "## MARKET REGIME (CONTEXT)"
        # header string (not appended as separate marker-less sections) so the
        # priority trim can never drop the guidance while keeping an empty header.
        _regime_section = "\n## MARKET REGIME (CONTEXT)"
        if _regime_state:
            _pcd = bool(getattr(
                getattr(self.settings, "stage2", None),
                "per_coin_direction_enabled", True,
            ))
            if _pcd:
                # No global direction word the model could read as a market-wide
                # long/short mandate. Per-coin is authority; breadth handles risk.
                _regime_section += (
                    "\nPer-coin regimes are AUTHORITATIVE: trade each coin on ITS OWN "
                    "regime (shown per candidate); coins without a per-coin regime trade "
                    "on their own TA/structure. There is NO market-wide direction bias — "
                    "position size is automatically throttled when overall market breadth "
                    "is one-sided."
                )
            else:
                # ROLLBACK PATH: legacy global direction lead.
                direction_hint = {
                    "trending_down": "Bias for shorts when per-coin evidence agrees; per-coin tags override.",
                    "trending_up": "Bias for longs when per-coin evidence agrees; per-coin tags override.",
                    "ranging": "both directions OK",
                    "volatile": "both directions with caution",
                    "dead": "scalp mode — both directions, tight TP",
                }.get(_regime_str, "neutral")
                _regime_section += (
                    f"\nGlobal: {_regime_str} "
                    f"(confidence={_regime_state.confidence:.0%}) "
                    f"→ {direction_hint}"
                )
                if _regime_state.confidence > 0.60:
                    if _regime_str == "trending_down":
                        _regime_section += (
                            "\nNOTE: High-confidence global downtrend. Use this as default bias "
                            "for coins without a per-coin tag; coins tagged [TRENDING_UP] are "
                            "valid long candidates on their own evidence."
                        )
                    elif _regime_str == "trending_up":
                        _regime_section += (
                            "\nNOTE: High-confidence global uptrend. Use this as default bias "
                            "for coins without a per-coin tag; coins tagged [TRENDING_DOWN] are "
                            "valid short candidates on their own evidence."
                        )
        sections.append(_regime_section)

        _timings["regime_global"] = (time.time() - _t_sec) * 1000
        _t_sec = time.time()

        # Issue E16 fix (2026-05-27): dedicated OPEN POSITIONS manage-block.
        # Open positions are surfaced HERE (a separate block) instead of
        # consuming the new-entry candidate budget above, so the brain keeps
        # full awareness of the book while its candidate slots stay reserved
        # for fresh opportunities. The "## OPEN POSITIONS" header is an
        # ESSENTIAL trim marker, so this constraint is never silently trimmed
        # under prompt pressure (also addresses companion E22). Full per-position
        # MANAGEMENT (hold/close/tighten) remains the job of the position-review
        # call (Call B); this block is awareness + the do-not-re-enter rule.
        try:
            position_service = self.services.get("position_service")
            if position_service:
                positions = await position_service.get_positions()
                if positions:
                    _pos_lines = [
                        "\n## OPEN POSITIONS (already held — do NOT open new trades "
                        "on these; reviewed in the position-management call):"
                    ]
                    for pos in positions:
                        _side = getattr(getattr(pos, "side", None), "value", None) \
                            or getattr(pos, "side", "?")
                        try:
                            _entry = float(getattr(pos, "entry_price", 0.0) or 0.0)
                        except (TypeError, ValueError):
                            _entry = 0.0
                        _pos_lines.append(
                            f"  {pos.symbol} {_side} @ ${format_price(_entry)}"
                        )
                    _pos_lines.append(
                        "The system will REJECT new trades on these symbols."
                    )
                    sections.append("\n".join(_pos_lines))
                else:
                    sections.append("\nNo open positions — you can trade any coin from the list.")
        except Exception as e:
            log.debug("Position list fetch failed: {err}", err=str(e))

        _timings["held_symbols"] = (time.time() - _t_sec) * 1000
        _t_sec = time.time()

        # Strategy signals summary
        # E23 (2026-05-28): emit the hints + per-coin consensus as TWO joined
        # sections instead of ~39 separate sections.append() calls. Each append
        # was its own trim-unit, so this block was the dominant prompt-size
        # pressure that triggered the trimmer (which #13 then had to protect the
        # core data from). Folding the "## STRATEGY HINTS" header into the joined
        # hints string keeps the whole block classified IMPORTANT and protected
        # as one unit. Every field the brain reads is preserved verbatim — only
        # the structural bloat (per-line appends) is removed.
        _hint_header = (
            "\n## STRATEGY HINTS — additional automated strategy signals\n"
            "Outputs from ~40 automated strategies, ranked by score. These may "
            "include coins NOT in the TRADE CANDIDATES above; for any coin that "
            "IS a candidate, its full per-coin ensemble and votes appear in its "
            "candidate block and these one-line hints do not override it.\n"
            "Weigh strategies as ONE input alongside regime, structure and "
            "X-RAY — do not blindly follow them and do not blindly dismiss them. "
            "An ensemble that disagrees with the per-coin regime is worth "
            "investigating (a possible early reversal), not automatically ignoring."
        )
        layer_manager = self.services.get("layer_manager")
        if layer_manager and hasattr(layer_manager, "_strategy_hints"):
            hints = getattr(layer_manager, "_strategy_hints", []) or []
            _hint_lines = [
                f"  {h.get('strategy', '?')}: {h.get('symbol', '?')} "
                f"{h.get('direction', '?')} score={h.get('score', 0)} "
                f"{h.get('consensus', '?')}"
                for h in hints[:20]
            ]
            sections.append(
                _hint_header + ("\n" + "\n".join(_hint_lines) if _hint_lines else "")
            )
            # Layer 1 restructure Phase 3 — read the LEGACY summary shape
            # via the explicit ``_strategy_consensus_summary`` alias.
            # Phase 3 repurposed ``_strategy_consensus`` as a per-coin
            # categorical cache (consumed by ScannerWorker / Phase 6
            # package builder). The summary {buy, sell, total_score}
            # entries Claude expects here live in the alias.
            consensus = getattr(
                layer_manager, "_strategy_consensus_summary",
                getattr(layer_manager, "_strategy_consensus", {}),
            ) or {}
            # Defensive: skip rows lacking the summary keys (e.g. a
            # cache snapshot from before the alias was populated).
            summary_rows = {
                sym: data for sym, data in consensus.items()
                if isinstance(data, dict) and "total_score" in data
                and "buy" in data and "sell" in data
            }
            if summary_rows:
                _consensus_rows = [
                    f"    {sym}: {data['buy']} buy / {data['sell']} sell "
                    f"(total score: {data['total_score']:.0f})"
                    for sym, data in sorted(
                        summary_rows.items(),
                        key=lambda x: x[1]["total_score"], reverse=True,
                    )[:15]
                ]
                sections.append(
                    "\n  CONSENSUS PER COIN:\n" + "\n".join(_consensus_rows)
                )
        else:
            sections.append(_hint_header + "\n  (No strategy signals available yet)")

        _timings["hints"] = (time.time() - _t_sec) * 1000
        _t_sec = time.time()

        # Account — equity + available balance only.
        # Aggressive-framing rewrite 2026-05-05: ``account`` is now bound
        # to None up-front and consulted via truthy check below, replacing
        # the broken ``'account' in dir()`` antipattern that read True on
        # every call (the previous-iteration assignment is in scope) and
        # silently fell through to a 168000.0 default whenever wallet-fetch
        # raised. Promoting log.debug to log.warning makes wallet failures
        # visible so the operator no longer flies blind on a sizing-block
        # degradation.
        sections.append("\n## ACCOUNT")
        account = None
        try:
            account_service = self.services.get("account_service")
            if account_service:
                account = await account_service.get_wallet_balance()
                sections.append(f"Equity: ${account.total_equity:,.2f}")
                sections.append(f"Available: ${account.available_balance:,.2f}")
        except Exception as e:
            log.warning(
                f"STRAT_ACCOUNT_FETCH_FAIL | err='{str(e)[:200]}' | {ctx()}"
            )

        # Per-trade sizing — minimal framing.
        # Aggressive-framing rewrite 2026-05-05: the full
        # FundLimits.to_prompt_text() block (header "FUND RULES (non-
        # negotiable):", total equity, starting equity, growth %, "Tier:
        # N — CONSERVATIVE/MODERATE/AGGRESSIVE (label)", capital allocation
        # %, usable capital, currently deployed, available for new trades,
        # max single trade, max positions, "Size your trades within
        # available capital.") was teaching avoidance bias via tier-label
        # ("Tier 1 — CONSERVATIVE (unproven)") and growth-percentage
        # framing on a losing account. Tier classification still drives
        # capital allocation algorithmically inside TieredCapitalManager
        # (get_tier returns ratio-based percentages 20/30/40 and max
        # positions 4/6/8); validate_trade_size still enforces the size
        # ceiling at order-submission time. The prompt only needs the
        # numeric ceiling Claude must respect to size new trades coherently.
        # Two lines — no header — flagged ESSENTIAL via the new
        # "Per-trade size limit" marker so the priority-aware trim still
        # protects the block.
        tiered_capital = self.services.get("tiered_capital")
        if tiered_capital:
            try:
                equity = account.total_equity if account else 168000.0
                deployed = 0.0
                # Fund-management enrichment (2026-05-31): track the open-trade
                # count alongside deployed capital so the brain can size as a
                # share of the REMAINING pool across the trades it opens. Init
                # to 0 here (not inside the inner try) so the degrade path can
                # never NameError on len(pos_list).
                open_trade_count = 0
                # Brain-Awareness Addition 2 (2026-06-09): count the open book's
                # long vs short directions from the SAME pos_list already fetched
                # for deployed capital — zero new data dependency. Position.side
                # is a Side enum (BUY/SELL).
                _long_count = 0
                _short_count = 0
                position_service = self.services.get("position_service")
                if position_service:
                    try:
                        pos_list = await position_service.get_positions()
                        open_trade_count = len(pos_list)
                        for pos in pos_list:
                            deployed += abs(pos.size * pos.entry_price / max(pos.leverage, 1))
                            _side_val = str(
                                getattr(getattr(pos, "side", None), "value",
                                        getattr(pos, "side", ""))
                            ).lower()
                            if _side_val in ("buy", "long"):
                                _long_count += 1
                            elif _side_val in ("sell", "short"):
                                _short_count += 1
                    except Exception as e:
                        log.debug("Position list fetch for deployed capital failed: {err}", err=str(e))
                limits = tiered_capital.get_limits(equity, deployed)
                _max_pos = max(int(limits.max_positions or 1), 1)
                # Fund-management fix (2026-05-31): the per-trade budget is
                # MARGIN = usable / max_positions, so the WHOLE book of
                # max_positions trades fits usable capital (NOT tiered's 25%
                # max_single_trade, which over-allocates when max_positions>4 and
                # drained the pool in one cycle). size_usd IS the MARGIN (the cash
                # committed); the exchange position = size_usd x leverage. The
                # brain sets size_usd ~= this per-trade margin; the gate enforces
                # the SAME budget. (Earlier framing wrongly called size_usd
                # NOTIONAL, which made the brain output margin x leverage and the
                # executor then applied leverage AGAIN -> 3x oversized trades.)
                _per_trade_margin = limits.usable_capital / _max_pos
                sections.append(
                    f"\nPer-trade size limit: ${_per_trade_margin:,.0f} "
                    f"(MARGIN per trade = usable / {_max_pos} max positions; set size_usd "
                    f"to about this — it is the cash you commit, NOT notional)"
                )
                sections.append(
                    f"Maximum concurrent positions: {limits.max_positions}"
                )
                # Open-trade count + used/usable/remaining (all MARGIN). The brain
                # sizes against these AND the gate enforces the SAME numbers (one
                # source of truth = tiered_capital). Each is its own section
                # element so the priority-trim classifier whitelists each prefix.
                sections.append(f"Open trades: {open_trade_count} of {limits.max_positions} max")
                # Brain-Awareness Prompt Additions — Addition 2 (2026-06-09) —
                # book-tilt awareness. Show the open book's directional
                # composition (long vs short) and a compact tilt label so the
                # brain can SEE when a new same-direction trade piles onto an
                # already one-sided book. AWARENESS ONLY — no block, no cap, no
                # suppression (the enforcement layer / portfolio breaker is
                # separate). Gated by [brain].book_tilt_enabled; the label
                # boundaries are centralized; the note is NEUTRAL. The note rides
                # in the SAME "Book tilt:" section element so the trim-priority
                # marker protects it too. Only renders when at least one position
                # is open (no tilt to read on a flat book).
                _bt_cfg = getattr(self.settings, "brain", None)
                if (
                    getattr(_bt_cfg, "book_tilt_enabled", True)
                    and (_long_count + _short_count) > 0
                ):
                    _tilt_label = _book_tilt_label(
                        _long_count, _short_count,
                        int(getattr(_bt_cfg, "book_tilt_small_count", 2)),
                        float(getattr(_bt_cfg, "book_tilt_one_sided_ratio", 3.0)),
                    )
                    _tilt_block = (
                        f"Book tilt: {_long_count} long / {_short_count} short "
                        f"— {_tilt_label}"
                    )
                    if _tilt_label != "balanced":
                        _tilt_block += (
                            "\n  Consider whether a new same-direction position "
                            "adds balance or concentrates an already one-sided "
                            "book (awareness only — your call)."
                        )
                    sections.append(_tilt_block)
                    log.info(
                        f"STRAT_BOOK_TILT | long={_long_count} short={_short_count} "
                        f"label={_tilt_label} | {ctx()}"
                    )
                sections.append(
                    f"Used funds: ${limits.currently_deployed:,.0f} (margin in open positions)"
                )
                sections.append(
                    f"Usable funds: ${limits.usable_capital:,.0f} (total margin budget)"
                )
                sections.append(
                    f"Available for new trades: ${limits.available_for_trades:,.0f} (margin left = usable - open)"
                )
                # THE SITUATION (per operator): cycles run every ~5 minutes and
                # positions ACCUMULATE up to max_positions — so a single cycle
                # must NOT consume the whole pool.
                sections.append(
                    f"FUNDING SITUATION: a new trade cycle runs every ~5 minutes and positions "
                    f"ACCUMULATE — over time you may hold up to {limits.max_positions} at once. "
                    f"Set size_usd (the MARGIN you commit) to about the per-trade limit above "
                    f"(~${_per_trade_margin:,.0f}); do NOT multiply by leverage — the system does that "
                    f"to build the position. That way all {limits.max_positions} positions can coexist "
                    f"within usable capital. Do NOT deploy the whole pool in one cycle — leave margin "
                    f"for the trades the next cycles will open."
                )
            except Exception as e:
                log.warning(
                    f"STRAT_FUND_LIMITS_FAIL | err='{str(e)[:200]}' | {ctx()}"
                )

        # TODAY'S PERFORMANCE block removed (aggressive-framing rewrite
        # 2026-05-05). The "Daily PnL: +/-N%" + "Trades today: N" pair
        # was teaching recency bias on outcome state — a negative day's
        # PnL pushed Claude toward defensive abstention exactly when the
        # operator wants exploitation. The operator's aim is decoupled
        # from intra-day P&L history.
        #
        # DailyPnLManager continues to drive everything off-prompt:
        #   - halt logic (current_pnl_pct <= halt_threshold_pct)
        #   - position_watchdog exit decisions
        #   - profit_sniper position exits
        #   - time_decay_sl recovery multiplier
        #   - Telegram dashboard / portfolio / control handlers
        #   - PNL_DAILY observability log every recalculate
        # Only the prompt-side reads at strategist.py:2739-2740 are
        # removed. Call B (_build_position_prompt:2963) still emits a
        # "## TODAY: PnL=" line — flagged as OBS-2 for follow-up.

        # Event buffer — watchdog events since last review
        # Phase 2 session-stability: cap events injected into the URGENT
        # prompt tail at ``BrainSettings.prompt_event_buffer_max_events``
        # (default 20). HIGH/MED are preserved ahead of LOW so the cap
        # never drops critical events. Emits CLAUDE_PROMPT_TRIMMED so
        # operators can see trimming happen in anger.
        event_buffer = self.services.get("event_buffer")
        if event_buffer:
            try:
                _evbuf_max = getattr(
                    getattr(self.settings, "brain", None),
                    "prompt_event_buffer_max_events",
                    20,
                )
                _pre_count = event_buffer.count
                events_text = event_buffer.get_prompt_text(max_events=_evbuf_max)
                _pre_chars = len(events_text) if events_text else 0
                if events_text:
                    sections.append(f"\n{events_text}")
                    _kept = min(_pre_count, _evbuf_max)
                    _dropped = max(0, _pre_count - _kept)
                    if _dropped > 0:
                        log.info(
                            f"CLAUDE_PROMPT_TRIMMED | "
                            f"events_total={_pre_count} events_kept={_kept} "
                            f"events_dropped={_dropped} "
                            f"cap={_evbuf_max} chars={_pre_chars} | {ctx()}"
                        )
                    event_buffer.clear()
            except Exception as e:
                log.debug("Event buffer fetch failed: {err}", err=str(e))

        # UrgentQueue — inject watchdog concerns into Call A
        self._has_urgent_concerns = False
        urgent_queue = self.services.get("urgent_queue")
        if urgent_queue and urgent_queue.has_concerns:
            concerns = urgent_queue.drain_concerns()
            if concerns:
                urgent_text = urgent_queue.format_for_prompt(concerns)
                sections.append(urgent_text)
                self._has_urgent_concerns = True
                log.info(
                    f"STRAT_CALL_A_URGENT | injected={len(concerns)} "
                    f"symbols=[{','.join(c.symbol for c in concerns)}] | {ctx()}"
                )
        # "account" bundles the five tail sub-sections (account + tiered_capital
        # + daily_pnl + event_buffer + urgent_queue). Keep them together so the
        # _has_urgent_concerns block structure above is not disturbed.
        _timings["account"] = (time.time() - _t_sec) * 1000

        # STRAT_PROMPT_BUILD — per-section breakdown emitted BEFORE the existing
        # STRAT_CALL_A_CTX total, so both lines share the same did context.
        _timings_str = " ".join(f"{k}={v:.0f}ms" for k, v in _timings.items())
        log.info(
            f"STRAT_PROMPT_BUILD | sections={len(sections)} | {_timings_str} | {ctx()}"
        )
        # Phase 4 (Stage-1/2 fix): when any sub-phase exceeds 10 s, emit the
        # TOP-3 slowest sub-phases so the operator sees the full cost picture
        # in one line (was just the single worst before). Important after
        # Phase 1's TACache fix: ``market_data`` should now drop from 33-38 s
        # to <3 s, exposing the next bottleneck behind it (e.g. xray,
        # sentiment). Surfacing 3 at a time compresses 2-3 tuning iterations
        # into the next restart cycle.
        if _timings and max(_timings.values()) > 10000:
            _sorted = sorted(_timings.items(), key=lambda kv: -kv[1])
            _top3 = ",".join(f"{k}={v:.0f}ms" for k, v in _sorted[:3])
            log.warning(
                f"STRAT_PROMPT_BUILD_SLOW | top3=[{_top3}] | {ctx()}"
            )

        # Phase 7 (Stage-1/2 fix): prompt-size gate. The pre-fix strategist
        # emitted ``CLAUDE_PROMPT_TRIMMED`` only when the event buffer
        # overflowed (cap = 20 events). It never tracked total section
        # count or total character count, which let the prompt grow 57 →
        # 81 sections over 10 minutes with zero trim events in the
        # 2026-04-24 observability window. We now:
        #   1. Always emit ``STRAT_PROMPT_SIZE`` with the final count + chars
        #      so operators can watch drift from baseline.
        #   2. If sections > 80 OR chars > _CHAR_CAP, drop the lowest-priority
        #      optional sections and emit a ``CLAUDE_PROMPT_TRIMMED`` event
        #      tagged ``site=size`` so trim is observable per occurrence.
        # Section-count cap at 80 picked from the brief's Phase-7 guidance.
        #
        # Issue A fix (2026-05-08) — char cap raised from 14,000 to 30,000.
        # Pre-fix: 21 priority-mode trim events in the 13:00–16:00 UTC
        # window on 2026-05-08, with raw prompts 16,910–19,919 chars.
        # 14k was set 2026-04-27 (commit fbd13dea) against a smaller
        # baseline; the prompt has grown ~3–6k since via additive fixes
        # (FUND RULES rewrite, XRAY phase-5 capital-sizing contract,
        # CALL_B framing carryover, tradeable-coins listing). 17/21
        # events were also dropping IMPORTANT-tagged sections (Direction
        # Performance, Trading Mode, Strategy Hints, Setup) — meaning
        # even after marker hardening (commit issueA/3a) the cap pressure
        # would still cascade past OPTIONAL into IMPORTANT.
        #
        # 30k chars ≈ 7,500 tokens — still ~4% of the Claude model's 200k
        # context window (~800k chars). Cost delta per CALL_A: roughly
        # +$0.005 (5k → 20k char prompts at Sonnet rates). Latency delta:
        # ≤100 ms. Trim becomes a backstop for runaway prompts (e.g.
        # 50+ open positions) rather than a per-cycle behaviour.
        _final_chars = sum(len(s) for s in sections)
        _final_count = len(sections)
        log.info(
            f"STRAT_PROMPT_SIZE | sections={_final_count} chars={_final_chars} "
            f"| {ctx()}"
        )
        _SECTION_CAP = 80
        _CHAR_CAP = 30000
        # Stage 2 phase 4 — priority-aware trim opt-in. Default False
        # keeps the legacy pop-from-end + 30-floor algorithm
        # byte-identical. When True, sections are classified by
        # leading-marker; OPTIONAL sections drop first, IMPORTANT
        # sections drop second, ESSENTIAL sections (system directive,
        # market data, account, regime, packages) NEVER drop.
        _stage2_cfg_t = getattr(self.settings, "stage2", None)
        _priority_trim = bool(getattr(
            _stage2_cfg_t, "enable_priority_trim", False,
        )) if _stage2_cfg_t else False
        if _final_count > _SECTION_CAP or _final_chars > _CHAR_CAP:
            _original_count = _final_count
            _original_chars = _final_chars
            if _priority_trim:
                # Classify each section once; drop in priority order.
                _priorities = [
                    _infer_section_priority(s, i)
                    for i, s in enumerate(sections)
                ]
                _dropped_labels: list[str] = []
                _dropped_optional = 0
                _dropped_important = 0
                # First pass: drop OPTIONAL (3) from the end. Second
                # pass: drop IMPORTANT (2) from the end. ESSENTIAL (1)
                # never drops.
                for _target_pri in (
                    _TRIM_PRIORITY_OPTIONAL,
                    _TRIM_PRIORITY_IMPORTANT,
                ):
                    i = len(sections) - 1
                    while i >= 0 and (
                        len(sections) > _SECTION_CAP
                        or sum(len(s) for s in sections) > _CHAR_CAP
                    ):
                        if _priorities[i] == _target_pri:
                            _label = (
                                sections[i].split("\n", 2)[1]
                                if "\n" in sections[i] else sections[i]
                            )[:60].strip()
                            _dropped_labels.append(_label)
                            if _target_pri == _TRIM_PRIORITY_OPTIONAL:
                                _dropped_optional += 1
                            else:
                                _dropped_important += 1
                            sections.pop(i)
                            _priorities.pop(i)
                        i -= 1
                _trimmed_count = _original_count - len(sections)
                _trimmed_chars = _original_chars - sum(len(s) for s in sections)
                # Issue A drift-detection guardrail (2026-05-08) — if any
                # dropped label contains a substring listed in
                # ``_TRIM_ESSENTIAL_MARKERS``, the classifier should have
                # tagged that section ESSENTIAL and the trim should never
                # have dropped it. This branch fires loudly when the
                # marker tuple drifts away from live emit-site headers
                # (the exact failure mode that caused issueA: marker
                # ``OVERRIDE — URGENT WATCHDOG ALERTS`` matched no
                # section, so URGENT got dropped 14 times in the 3-hour
                # 2026-05-08 audit window). Defends against future
                # silent regressions.
                _essential_drift = _detect_essential_drift(_dropped_labels)
                if _essential_drift:
                    log.warning(
                        f"STRAT_TRIM_ESSENTIAL_DROPPED | "
                        f"count={len(_essential_drift)} "
                        f"first={_essential_drift[:3]} "
                        f"| {ctx()}"
                    )
                # Issue A observability enrichment (2026-05-08) — count
                # how many ESSENTIAL sections survived the trim and which
                # marker categories they covered. Operators inspecting
                # ``CLAUDE_PROMPT_TRIMMED`` log entries can now confirm
                # the protections fired without reading source: a healthy
                # post-fix line carries protected_kept ≥ N for the
                # always-on essentials (market data, account, regime,
                # urgent block when present, etc.). Index-0 coaching is
                # forced essential by the classifier but has no marker
                # and is intentionally excluded from this category-coverage
                # summary — see ``_summarize_kept_protections`` docstring.
                _protected_kept, _kept_categories = (
                    _summarize_kept_protections(sections)
                )
                sections.append(
                    f"(... {_trimmed_count} trailing sections trimmed "
                    f"({_trimmed_chars} chars) to keep prompt bounded — "
                    f"see server logs STRAT_PROMPT_SIZE)"
                )
                log.warning(
                    f"CLAUDE_PROMPT_TRIMMED | site=size mode=priority "
                    f"reason={'sections' if _original_count > _SECTION_CAP else 'chars'} "
                    f"sections_before={_original_count} sections_after={len(sections)} "
                    f"chars_before={_original_chars} chars_after={sum(len(s) for s in sections)} "
                    f"dropped_optional={_dropped_optional} "
                    f"dropped_important={_dropped_important} "
                    f"dropped_count={len(_dropped_labels)} dropped_labels={_dropped_labels} "
                    f"protected_kept={_protected_kept} "
                    f"protected_categories={_kept_categories} "
                    f"cap_sections={_SECTION_CAP} cap_chars={_CHAR_CAP} | {ctx()}"
                )
            else:
                # Legacy path — pop from end with a 30-section floor.
                # Protects the system prompt (first section) + the
                # MARKET DATA, ACCOUNT, and primary BYBIT EXCHANGE
                # POSITIONS headers + per-position lines. Trimming
                # targets are appended tail sections: TIAS lessons,
                # URGENT queue overflow, and the long X-RAY structural
                # setups. Order-dependent — the critical directive +
                # header + market snapshot live near the top.
                while (
                    (len(sections) > _SECTION_CAP
                     or sum(len(s) for s in sections) > _CHAR_CAP)
                    and len(sections) > 30
                ):
                    sections.pop()
                _trimmed_count = _original_count - len(sections)
                _trimmed_chars = _original_chars - sum(len(s) for s in sections)
                sections.append(
                    f"(... {_trimmed_count} trailing sections trimmed "
                    f"({_trimmed_chars} chars) to keep prompt bounded — "
                    f"see server logs STRAT_PROMPT_SIZE)"
                )
                log.warning(
                    f"CLAUDE_PROMPT_TRIMMED | site=size reason={'sections' if _original_count > _SECTION_CAP else 'chars'} "
                    f"sections_before={_original_count} sections_after={len(sections)} "
                    f"chars_before={_original_chars} chars_after={sum(len(s) for s in sections)} "
                    f"cap_sections={_SECTION_CAP} cap_chars={_CHAR_CAP} | {ctx()}"
                )

        _a_el_ms = (time.time() - _t_build) * 1000
        _prompt = "\n".join(sections)
        log.info(f"STRAT_CALL_A_CTX | sections={len(sections)} chars={sum(len(s) for s in sections)} el={_a_el_ms:.0f}ms | {ctx()}")
        # Layer 1 restructure Phase 7 — observability for prompt size
        # tracking. Distinguishes this commit's "prepend packages"
        # behavior from a future "replace per-coin queries" reduction.
        log.info(
            f"PROMPT_BUILD_DONE | call=CALL_A coins={len(universe)} "
            f"size_bytes={len(_prompt)} sections={len(sections)} "
            f"packages={_packages_count} elapsed_ms={_a_el_ms:.0f} | {ctx()}"
        )
        # Sniper-Latency-Size Fix Phase 2 (2026-05-07) — surface the
        # active compression flag so post-deploy log analysis can
        # bucket latencies by compression-on/off and quantify the
        # actual saving achieved in production. The size_bytes above is
        # the post-compression size when the flag is on, so the trial
        # report can compare CALL_A latency distributions before/after
        # the flag flip in config.toml.
        try:
            _stage2_obs = getattr(self.settings, "stage2", None)
            _compress_flag = bool(getattr(
                _stage2_obs, "enable_prompt_compression", False,
            )) if _stage2_obs is not None else False
            log.info(
                f"PROMPT_COMPRESS | call=CALL_A coins={len(universe)} "
                f"size_bytes={len(_prompt)} compression={_compress_flag} "
                f"full_layer={bool(getattr(_stage2_obs, 'enable_full_layer_block', False))} "
                f"elapsed_ms={_a_el_ms:.0f} | {ctx()}"
            )
        except Exception:
            # Best-effort observability; never crash the prompt build.
            pass
        if _a_el_ms > 5000:
            log.warning(f"STRAT_CALL_A_CTX_SLOW | el={_a_el_ms:.0f}ms sections={len(sections)} | {ctx()}")
        return _prompt

    # ═══ CALL B: Position management prompt builder ═══

    async def _build_position_prompt(self) -> str:
        """Build position management prompt for Call B. Target: 5-8K chars.

        Contains: open positions with PnL, thesis, per-coin regime, lessons.
        Does NOT contain: full market scan, strategy hints, X-RAY.
        """
        _t_build = time.time()
        sections = []

        # 1. Brief regime context.
        # Issue #11 fix (2026-05-27): re-read the latest regime at Call-B build
        # time instead of reusing the value cached by the previous Call A. That
        # cache could be ~150-300s stale (one A/B alternation) ON TOP of the
        # RegimeWorker cadence, so position management could close-on-inversion
        # against a several-minute-old regime. get_last_regime() is a zero-cost
        # cached attribute read (no re-detection), so this bounds staleness to
        # one RegimeWorker detection cycle at no extra cost. Falls back to the
        # Call-A cache if the detector is momentarily unavailable.
        _rb_regime_str = self._last_regime_str
        _rb_regime_conf = self._last_regime_confidence
        try:
            _rd = self.services.get("regime_detector")
            if _rd is not None:
                _rs = _rd.get_last_regime()
                if _rs is not None:
                    # Only adopt the fresh read if it coerces to real types.
                    # A malformed (or, in tests, mocked) RegimeState must never
                    # crash the prompt build at the percentage-format step
                    # below — coerce first, and reassign only on success so a
                    # bad read falls back to the Call-A cache.
                    _fresh_str = str(_rs.regime.value)
                    _fresh_conf = float(_rs.confidence)
                    _rb_regime_str = _fresh_str
                    _rb_regime_conf = _fresh_conf
                    _age_s = -1.0
                    try:
                        from datetime import datetime, timezone
                        _da = getattr(_rs, "detected_at", None)
                        if _da is not None:
                            _age_s = (datetime.now(timezone.utc) - _da).total_seconds()
                    except Exception:
                        _age_s = -1.0
                    log.info(
                        f"CALLB_REGIME_FRESH | rgm={_rb_regime_str} "
                        f"conf={_rb_regime_conf:.2f} age_s={_age_s:.0f} "
                        f"source=get_last_regime | {ctx()}"
                    )
        except Exception as _e:
            log.debug(f"CALLB_REGIME_REFRESH_FAIL | err={str(_e)[:80]} | {ctx()}")
        # Per-coin-authority Phase 6 (2026-05-29): default = BTC regime is
        # informational CONTEXT only; each position is managed by ITS OWN
        # per-coin regime (shown in its row), per the CONTRACT below. The
        # "(CONTEXT)" header also matches the ESSENTIAL trim marker so it is
        # protected. Rollback (flag False) restores the bare global headline.
        _pcd_b = bool(getattr(
            getattr(self.settings, "stage2", None),
            "per_coin_direction_enabled", True,
        ))
        if _pcd_b:
            sections.append(
                f"## MARKET REGIME (CONTEXT): BTC={_rb_regime_str} ({_rb_regime_conf:.0%}) "
                f"— informational only. Manage each position by ITS OWN per-coin regime "
                f"(shown in its row), not this market-wide reading."
            )
        else:
            sections.append(f"## MARKET REGIME: {_rb_regime_str} ({_rb_regime_conf:.0%})")

        # 2. Brief sentiment
        sections.append(f"## SENTIMENT: Fear & Greed = {self._last_fg_value}")

        # 3. Brief account / daily PnL
        try:
            pnl_manager = self.services.get("pnl_manager")
            if pnl_manager:
                sections.append(f"## TODAY: PnL={pnl_manager.current_pnl_pct:+.2f}%")
        except Exception:
            pass

        # 3b. Brain-prompt-enrichment Phase 3.4 (E5) — direction-specific
        # performance for the current day. Reads ``PerformanceEnforcer``'s
        # per-direction wins/losses (day-bounded; reset on day rollover
        # at ``performance_enforcer.py:660-665``). Skipped when:
        #   - The flag is False,
        #   - The enforcer service is unregistered (cold start, harness
        #     without enforcer),
        #   - Zero trades have closed today on either side (line would
        #     carry no signal and might be misread as "no activity").
        # Intentionally fact-framed (absolute counts shown so the brain
        # can judge small-sample noise) and CALL_B-only.
        try:
            _settings = getattr(self, "settings", None)
            _brain_cfg = getattr(_settings, "brain", None) if _settings else None
            _emit_dp = bool(getattr(_brain_cfg, "emit_direction_perf_in_callb", True))
            enforcer = self.services.get("enforcer") if _emit_dp else None
            if enforcer is not None:
                pd = getattr(enforcer, "_per_direction", None)
                if isinstance(pd, dict):
                    buy = pd.get("Buy") or {"wins": 0, "losses": 0}
                    sell = pd.get("Sell") or {"wins": 0, "losses": 0}
                    bw, bl = int(buy.get("wins", 0)), int(buy.get("losses", 0))
                    sw, sl_ = int(sell.get("wins", 0)), int(sell.get("losses", 0))
                    total = bw + bl + sw + sl_
                    if total > 0:
                        buy_total = bw + bl
                        sell_total = sw + sl_
                        buy_wr = (bw / buy_total * 100) if buy_total > 0 else 0.0
                        sell_wr = (sw / sell_total * 100) if sell_total > 0 else 0.0
                        buy_lbl = (
                            f"Longs {bw}W/{bl}L ({buy_wr:.0f}% WR)"
                            if buy_total > 0
                            else "Longs 0W/0L (no data)"
                        )
                        sell_lbl = (
                            f"Shorts {sw}W/{sl_}L ({sell_wr:.0f}% WR)"
                            if sell_total > 0
                            else "Shorts 0W/0L (no data)"
                        )
                        sections.append(
                            f"## TODAY DIRECTION PERF: {buy_lbl} | {sell_lbl}"
                        )
                        log.info(
                            f"DIR_PERF_COMPUTED | longs_n={buy_total} longs_w={bw} "
                            f"shorts_n={sell_total} shorts_w={sw} | {ctx()}"
                        )
        except Exception as e:
            log.debug("direction perf line failed: {err}", err=str(e))

        # 4. Each open position with full context
        sections.append("\n## YOUR OPEN POSITIONS — Review each and decide: hold, close, tighten_stop, set_exit")

        # 4a. Per-cycle restatement of the close-criteria contract,
        # placed directly above the position data so Claude reads it
        # next to the rows it's reasoning about. Coherent with
        # POSITION_SYSTEM_PROMPT (Sub-phase 1B) which now leads with
        # the aggressive-exploitation aim and forbids regime-alignment /
        # original-thesis / recency-bias closes. Added in CALL_B
        # Framing Fix Sub-phase 1D (2026-05-06). Mirrors the structure
        # of CALL_A's framing fix: aim first, then mechanical rules.
        sections.append(
            "\n## CONTRACT — POSITION MANAGEMENT\n"
            "\n"
            "Manage these open positions to maximize their development.\n"
            "\n"
            "For each position:\n"
            "- HOLD if the position is developing within normal parameters.\n"
            "- TIGHTEN_STOP to lock partial profit when significantly profitable (PnL > +1.5%).\n"
            "- SET_EXIT or take_profit at strong structural levels.\n"
            "\n"
            "CLOSE only when:\n"
            "- The setup that triggered entry is genuinely invalidated by structural change (XRAY confidence drop, setup-type drift, or the POSITION'S OWN per-coin regime inverting against its direction at >=60% confidence — NOT a market-wide/global regime move).\n"
            "- SL is approaching and recovery looks unlikely.\n"
            "- TP is approaching and you want to lock the win.\n"
            "\n"
            "Do NOT close based on:\n"
            "- Regime alignment alone — some positions are intentionally counter-regime when RR justifies.\n"
            "- The original thesis text — the system may have flipped direction; trust the current state shown above.\n"
            "- Recency-bias from past similar trades — small samples don't define what works.\n"
            "\n"
            "For positions marked FLIPPED below: the flip was made because the flipped direction had materially better RR. Manage based on the CURRENT direction, not the original."
        )

        thesis_mgr = self.services.get("thesis_manager")
        position_service = self.services.get("position_service")
        coordinator = self.services.get("trade_coordinator")
        _rd = self.services.get("regime_detector")

        # Phase 18 (P1-17): explicit live fetch via the strategist hook
        # so a position the watchdog reconciled as closed never appears
        # in this prompt. ``refresh_positions`` clears any
        # invalidate_position flags accumulated since the last build,
        # ensuring the close-broadcast and the prompt builder agree on
        # what's open this cycle. Falls back to the previous direct
        # call if position_service is missing.
        positions = await self.refresh_positions()
        if not positions and position_service:
            positions = await position_service.get_positions()
        open_theses: dict = {}
        if thesis_mgr:
            try:
                theses_list = await thesis_mgr.get_open_theses()
                open_theses = {t["symbol"]: t for t in (theses_list or [])}
            except Exception:
                pass

        # Mid-Hold Trade Management Fix Phase 3.8 (2026-05-19) — reset
        # the per-call event-id ledger and batch-fetch unseen events for
        # every open symbol so each per-position block can pull its
        # events without an extra round-trip. The ledger is consumed
        # (mark_events_consumed) by create_position_plan after Claude
        # responds.
        self._last_callB_event_ids = []
        _callB_events_by_symbol: dict[str, list[dict]] = {}
        if thesis_mgr and positions:
            try:
                _open_syms = [p.symbol for p in positions]
                _unseen = await thesis_mgr.get_unseen_events(_open_syms)
                for ev in _unseen:
                    _callB_events_by_symbol.setdefault(
                        ev["symbol"], [],
                    ).append(ev)
            except Exception as _ee:
                log.debug(
                    f"CALLB_EVENTS_FETCH_FAIL | err='{str(_ee)[:120]}' | {ctx()}"
                )

        for pos in positions:
            symbol = pos.symbol
            side_val = pos.side.value if hasattr(pos.side, "value") else str(pos.side)

            # PnL calculation
            pnl_pct = (
                ((pos.mark_price - pos.entry_price) / pos.entry_price * 100)
                if pos.entry_price > 0 else 0
            )
            if side_val in ("Sell", "Short"):
                pnl_pct = -pnl_pct

            # Per-coin regime
            _cr = _rd.get_coin_regime(symbol) if _rd else None
            rgm_str = f"{_cr.regime.value.upper()} {_cr.confidence*100:.0f}%" if _cr else "unknown"

            # Trade plan metadata
            plan = coordinator.get_trade_plan(symbol) if coordinator else None
            trade_info = coordinator.get_trade_info(symbol) if coordinator else {}
            age = plan.age_minutes if plan else 0
            remaining = plan.remaining_minutes if plan else 999

            # Thesis row from thesis_manager — used for SL/TP/leverage and
            # APEX/XRAY flip metadata. The free-text `thesis` column is
            # intentionally NOT read here as of CALL_B Framing Fix Phase 1C
            # (2026-05-06). The original thesis text was written for the
            # pre-flip direction; on a flipped position it contradicts the
            # current state shown in the same block, which Claude was
            # reading as "thesis broken" and using to drive premature
            # closes. The position state (entry, mark, PnL, SL/TP, regime,
            # age, FLIPPED notice when applicable) is the source of truth
            # CALL_B reasons over. thesis_manager itself is unchanged —
            # the `thesis` column is still saved on entry and used by
            # trade-history queries / observability.
            thesis_data = open_theses.get(symbol, {})

            # SL consumed % — C1 Phase 1.4b (2026-05-21): both call sites
            # (brain CALL_B prompt and watchdog scoring intercept) now
            # share the same arithmetic via ``compute_sl_consumption_pct``.
            # Phase 9d direction-aware clamping is preserved inside the
            # helper. The brain prompt renders TWO numbers when the SL
            # has been trailed from the entry value:
            #   * entry-budget — "how much of the risk Claude originally
            #     signed up for has been consumed?" (uses thesis_data
            #     ``stop_loss_price``)
            #   * current-stop — "how close is price to the live trailed
            #     stop?" (uses ``pos.stop_loss``)
            # When no trailing has occurred (both SL prices equal within
            # 1 bp) only the single number is shown to keep the prompt
            # tight. The watchdog scoring intercept uses the current-stop
            # number; the operator can correlate the prompt's current-
            # stop reading against ``WATCHDOG_CLOSE_SCORE_COMPUTED`` for
            # any vote.
            sl_price = thesis_data.get("stop_loss_price", 0)
            sl_price_current = float(getattr(pos, "stop_loss", 0.0) or 0.0)
            _sl_entry_pct = compute_sl_consumption_pct(
                side=side_val,
                entry_price=pos.entry_price,
                stop_loss=sl_price,
                current_price=pos.mark_price,
            )
            _sl_current_pct = compute_sl_consumption_pct(
                side=side_val,
                entry_price=pos.entry_price,
                stop_loss=sl_price_current,
                current_price=pos.mark_price,
            )
            sl_consumed_entry = (
                _sl_entry_pct if _sl_entry_pct is not None else 0.0
            )
            sl_consumed_current = (
                _sl_current_pct if _sl_current_pct is not None else 0.0
            )
            _sl_trailed = (
                sl_price_current > 0
                and sl_price > 0
                and abs(sl_price_current - sl_price) / max(sl_price, 1e-9) > 1e-4
            )

            if _sl_trailed:
                sl_line = (
                    f"  SL: ${format_price(sl_price)} (entry) / ${format_price(sl_price_current)} "
                    f"(trailed) | TP: ${format_price(thesis_data.get('take_profit_price', 0))} | "
                    f"Lev: {thesis_data.get('leverage', '?')}x"
                )
                sl_consumed_line = (
                    f"  SL consumed: {sl_consumed_entry:.0f}% (entry-budget) "
                    f"/ {sl_consumed_current:.0f}% (current-stop)"
                )
            else:
                sl_line = (
                    f"  SL: ${format_price(sl_price)} | "
                    f"TP: ${format_price(thesis_data.get('take_profit_price', 0))} | "
                    f"Lev: {thesis_data.get('leverage', '?')}x"
                )
                sl_consumed_line = f"  SL consumed: {sl_consumed_entry:.0f}%"

            sections.append(
                f"\n### {symbol} [{side_val}]\n"
                f"  Entry: ${format_price(pos.entry_price)} | Now: ${format_price(pos.mark_price)} | PnL: {pnl_pct:+.2f}%\n"
                + sl_line + "\n"
                + f"  Age: {age:.0f}min | Remaining: {remaining:.0f}min | Regime: {rgm_str}\n"
                + sl_consumed_line
            )
            # CALL_B Framing Fix Phase 1E (2026-05-06) — unified flip
            # notice. Reads `xray_flip_source` first (v28 column); falls
            # back to `apex_flipped` for legacy rows pre-dating v28. For
            # XRAY-driven flips, renders concrete RR justification using
            # the at-flip rr_long / rr_short values so Claude has direct
            # evidence the flipped direction was the better-RR choice.
            # APEX-driven flips render with the existing apex_reason
            # free-text (rr_long / rr_short are 0.0 for those rows).
            _flip_source_str = str(thesis_data.get("xray_flip_source", "") or "")
            _legacy_apex_flipped = bool(thesis_data.get("apex_flipped"))
            if _flip_source_str or _legacy_apex_flipped:
                _orig_dir_str = str(thesis_data.get("apex_original_direction", "?") or "?")
                if _flip_source_str == "xray":
                    _rr_long = float(thesis_data.get("xray_flip_rr_long", 0.0) or 0.0)
                    _rr_short = float(thesis_data.get("xray_flip_rr_short", 0.0) or 0.0)
                    _ratio = float(thesis_data.get("xray_flip_ratio", 0.0) or 0.0)
                    _is_sell = side_val in ("Sell", "Short")
                    _chosen_rr = _rr_short if _is_sell else _rr_long
                    _rejected_rr = _rr_long if _is_sell else _rr_short
                    sections.append(
                        f"  FLIPPED via XRAY from {_orig_dir_str} to {side_val}: "
                        f"RR_chosen={_chosen_rr:.2f} vs RR_rejected={_rejected_rr:.2f} "
                        f"({_ratio:.1f}x better)"
                    )
                    log.info(
                        f"STRAT_CALL_B_FLIP_NOTICE | sym={symbol} source=xray "
                        f"ratio={_ratio:.2f} rr_chosen={_chosen_rr:.2f} "
                        f"rr_rejected={_rejected_rr:.2f} | {ctx()}"
                    )
                else:
                    # source == "apex" OR legacy apex_flipped=1 row.
                    _src_label = "APEX" if (_flip_source_str == "apex" or _legacy_apex_flipped) else _flip_source_str.upper()
                    sections.append(
                        f"  FLIPPED via {_src_label} from {_orig_dir_str} to {side_val}: "
                        f"{str(thesis_data.get('apex_reason', ''))[:100]}"
                    )
                    log.info(
                        f"STRAT_CALL_B_FLIP_NOTICE | sym={symbol} source={_src_label.lower()} "
                        f"| {ctx()}"
                    )

            # Mid-Hold Trade Management Fix Phase 3.8 (2026-05-19) —
            # render the entry-thesis invalidation criterion + current
            # state, plus any queued thesis_events for this position.
            # For APEX/XRAY-flipped positions, use the
            # _PRE_FLIP_INFORMATIONAL prefix (operator decision) so the
            # brain treats the criterion as situational context, NOT as
            # a thesis-broken close trigger — this defends against the
            # CALL_B Framing Fix Phase 1C regression. The free-text
            # ``thesis`` column is still NOT read (Phase 1C is intact);
            # only the structured invalidation criterion is surfaced.
            if thesis_data:
                _is_flipped = bool(_flip_source_str or _legacy_apex_flipped)
                _inv_line = self._render_thesis_invalidation_block(
                    thesis_data, flip_annotation=_is_flipped,
                )
                if _inv_line:
                    sections.append(_inv_line)
            _sym_events_b = _callB_events_by_symbol.get(symbol, [])
            if _sym_events_b:
                _event_text, _consumed_ids = (
                    self._render_thesis_events_block(_sym_events_b)
                )
                if _event_text:
                    sections.append(_event_text)
                self._last_callB_event_ids.extend(_consumed_ids)

        if not positions:
            sections.append("  No open positions.")

        # 5. RECENT LESSONS section intentionally removed (Post-Execution
        # Closure Fix Phase 1A, 2026-05-05). The recency-bias coaching
        # produced a closed-loop failure: CALL_B read "X just lost -0.23%
        # on time_decay" -> Claude requested close on a 3-min-old position
        # -> that loss became the next "lesson" feeding the next cycle.
        # TIAS itself is unchanged (still written/read by
        # thesis_manager.get_recent_lessons; CALL_A still injects its own
        # "## LESSONS FROM RECENT TRADES" section at _build_trade_prompt
        # ~line 1199 which is OUT OF SCOPE per operator decision and
        # flagged for follow-up in the Phase 5 verification report).
        # _tias_lessons_removed is the regression sentinel surfaced in
        # STRAT_CALL_B_CTX below so a future merge that re-adds the
        # section becomes immediately visible at log-tail time.
        _tias_lessons_removed = True

        # T1-3 / F9 aggregated stats block (six-tier-fixes 2026-05-11).
        # Closed-loop-immune by construction: contains no symbol-specific
        # narratives, only aggregate WR / close-reason distribution. Safe
        # for CALL_B even though per-trade lessons remain disabled.
        try:
            thesis_mgr_for_stats = self.services.get("thesis_manager")
            if thesis_mgr_for_stats is not None and hasattr(
                thesis_mgr_for_stats, "get_aggregated_stats"
            ):
                _stats_cb = await thesis_mgr_for_stats.get_aggregated_stats(
                    limit_closes=50
                )
                from src.core.thesis_manager import format_aggregated_stats_for_prompt
                _stats_block_cb = format_aggregated_stats_for_prompt(_stats_cb)
                if _stats_block_cb:
                    sections.append(_stats_block_cb)
        except Exception as _se:
            log.debug(
                f"STRAT_CALL_B_STATS_FAIL | err='{str(_se)[:120]}' | {ctx()}"
            )

        # 6. Recently closed — per-(symbol, direction) reentry cooldown
        #    (Issue 3, 2026-05-18). Same data source as CALL_A.
        if coordinator and hasattr(coordinator, "get_active_reentry_cooldowns"):
            try:
                pairs_cb = coordinator.get_active_reentry_cooldowns()
            except Exception:
                pairs_cb = []
            if pairs_cb:
                sections.append("\nRECENTLY CLOSED (wait for cooldown before re-entering):")
                for sym, direction, remaining_cd in pairs_cb:
                    sections.append(f"  {sym} {direction}: {remaining_cd}s remaining")

        # UrgentQueue — include any remaining concerns
        urgent_queue = self.services.get("urgent_queue")
        if urgent_queue and urgent_queue.has_concerns:
            concerns = urgent_queue.drain_concerns()
            if concerns:
                urgent_text = urgent_queue.format_for_prompt(concerns)
                sections.append(urgent_text)
                log.info(
                    f"STRAT_CALL_B_URGENT | injected={len(concerns)} | {ctx()}"
                )

        _b_el_ms = (time.time() - _t_build) * 1000
        _prompt_b = "\n".join(sections)

        # Observability G9 — TIAS bridge closure verification. The
        # audit (2026-05-13) noted "TIAS_BRIDGE" zero events. The
        # write side (TIAS_LESSON_BRIDGED at thesis_manager.py:456)
        # fires 8x per 8 closes. The read side has been intentionally
        # disabled in CALL_B as a closed-loop-immunity measure
        # (recency_lessons_count is hardcoded 0). G9 surfaces the
        # actual DB-side lesson count alongside the hardcoded 0 so
        # operators can see "TIAS writes N lessons; CALL_B
        # intentionally reads 0 of them" — the audit's specific
        # learning-loop concern, addressed by visibility not by
        # injection.
        _lessons_in_db: int = 0
        try:
            _thesis_mgr_g9 = self.services.get("thesis_manager")
            if (
                _thesis_mgr_g9 is not None
                and hasattr(_thesis_mgr_g9, "get_recent_lessons")
            ):
                _lessons_avail = await _thesis_mgr_g9.get_recent_lessons(
                    limit=10, min_age_seconds=300, exclude_symbols=frozenset(),
                )
                _lessons_in_db = len(_lessons_avail or [])
        except Exception:
            # Best-effort observability; the hardcoded 0 is preserved
            # if the query fails.
            _lessons_in_db = 0

        log.info(
            f"STRAT_CALL_B_CTX | positions={len(positions)} "
            f"chars={sum(len(s) for s in sections)} el={_b_el_ms:.0f}ms "
            f"tias_coaching_removed={_tias_lessons_removed} "
            f"recency_lessons_count=0 lessons_in_db={_lessons_in_db} | {ctx()}"
        )
        # Layer 1 restructure Phase 7 — CALL_B prompt size observability.
        log.info(
            f"PROMPT_BUILD_DONE | call=CALL_B positions={len(positions)} "
            f"size_bytes={len(_prompt_b)} sections={len(sections)} "
            f"elapsed_ms={_b_el_ms:.0f} | {ctx()}"
        )
        if _b_el_ms > 5000:
            log.warning(f"STRAT_CALL_B_CTX_SLOW | el={_b_el_ms:.0f}ms positions={len(positions)} | {ctx()}")
        return _prompt_b

    def _build_regime_instructions(self, regime: str, confidence: float, fear_greed: int) -> str:
        """Build dynamic regime-specific trading instructions.

        Placed early in context so Claude reads these constraints BEFORE seeing
        market data. Each regime is an OPPORTUNITY with the right approach.

        NOTE: this builder is part of the DEAD legacy path (create_strategic_plan
        / _build_context_prompt), used only by scripts/run_30min_test.py — the
        live brain uses create_trade_plan / _build_trade_prompt. It is NOT
        deleted (the project's NameError precedent makes deleting blocks in this
        file risky), but its global-direction-mandate body (the "DEFAULT BIAS:
        SHORT — 70% shorts" / "DEFAULT BIAS: LONG" language) is NEUTRALIZED in
        lockstep with Phase 6: under per_coin_direction_enabled (default) it
        returns a per-coin-authority stub with NO market-wide direction mandate,
        so a future revival of this path can never reinstate the global short-bias.
        Flip the flag False to restore the legacy body (rollback parity).
        """
        if bool(getattr(
            getattr(self.settings, "stage2", None),
            "per_coin_direction_enabled", True,
        )):
            return (
                "## REGIME-SPECIFIC TRADING INSTRUCTIONS (READ BEFORE LOOKING AT DATA)\n"
                f"GLOBAL regime (BTC-based): {regime} (confidence: {confidence:.0%}) "
                "— informational CONTEXT only; there is NO market-wide direction bias.\n"
                f"Fear & Greed Index: {fear_greed}\n\n"
                "PER-COIN REGIME IS AUTHORITATIVE:\n"
                "  Each coin has its OWN regime shown in [brackets] in market data below.\n"
                "  - A coin in [TRENDING_UP] is BOUGHT; a coin in [TRENDING_DOWN] is SOLD — on its own evidence.\n"
                "  - A coin in [RANGING]/[VOLATILE] — use TA signals to decide direction.\n"
                "  - A coin with NO per-coin regime (UNKNOWN) — trade on its OWN TA/structure; do NOT default to global.\n"
                "  Position size is automatically throttled when overall market breadth is one-sided."
            )
        lines = [
            "## REGIME-SPECIFIC TRADING INSTRUCTIONS (READ BEFORE LOOKING AT DATA)",
            f"GLOBAL regime (BTC-based): {regime} (confidence: {confidence:.0%})",
            f"Fear & Greed Index: {fear_greed}",
            "",
            "PER-COIN REGIME OVERRIDE (CRITICAL):",
            "  Each coin has its OWN regime shown in [brackets] in market data below.",
            "  Trade WITH each coin's INDIVIDUAL regime direction:",
            "  - A coin in [TRENDING_UP] should be BOUGHT, even if global is trending_down.",
            "  - A coin in [TRENDING_DOWN] should be SOLD, even if global is trending_up.",
            "  - A coin in [RANGING] — use TA signals to decide direction.",
            "  - If a coin has NO individual regime tag, use the global regime as default.",
            "  Global regime is CONTEXT for the overall market, NOT a blanket rule for every coin.",
            "",
            "GLOBAL REGIME GUIDANCE (use as DEFAULT when coin has no per-coin regime):",
            "",
        ]

        high_confidence = confidence > 0.60

        if regime == "trending_down":
            if high_confidence:
                lines.append("DEFAULT BIAS: SHORT — for coins without per-coin regime:")
                lines.append("  - SELL/SHORT bias — 70% shorts, 30% longs only on extreme oversold bounces.")
                lines.append("  - Oversold RSI in a downtrend means trend is STRONG. Short the bounces.")
                lines.append("  - This is where you make the money — ride the trend with conviction.")
                if fear_greed < 20:
                    lines.append(f"  - F&G={fear_greed} (extreme fear) confirms downtrend. SHORT with full conviction.")
                elif fear_greed < 40:
                    lines.append(f"  - F&G={fear_greed} (fear) confirms bearish sentiment. Favor shorts.")
            else:
                lines.append("MODERATE SHORT BIAS — 3-5 trades:")
                lines.append("  - Downtrend detected but confidence is moderate. Prefer SELL positions.")
                lines.append("  - BUY only the strongest coins with clear technical reversal signals.")
                lines.append("  - Target 70% SHORT / 30% LONG allocation.")

        elif regime == "trending_up":
            if high_confidence:
                lines.append("DEFAULT BIAS: LONG — for coins without per-coin regime:")
                lines.append("  - BUY/LONG bias — 70% longs, 30% shorts only on extreme overbought.")
                lines.append("  - Buy every pullback, ride the trend with conviction.")
                if fear_greed > 80:
                    lines.append(f"  - F&G={fear_greed} (extreme greed) — uptrend may be overextended.")
                    lines.append("    Tighten stops on existing longs. Still trade but reduce new long size.")
                elif fear_greed < 20:
                    lines.append(f"  - F&G={fear_greed} (extreme fear) + uptrend = MAXIMUM buy opportunity.")
                    lines.append("    Smart money buys when others panic during an uptrend. Go heavy long.")
            else:
                lines.append("MODERATE LONG BIAS — 3-5 trades:")
                lines.append("  - Uptrend detected but confidence is moderate. Favor BUY positions.")
                lines.append("  - Target 70% LONG / 30% SHORT allocation.")

        elif regime == "ranging":
            lines.append("RANGE PLAY — 3-5 trades, mean-reversion is your edge:")
            lines.append("  - Market is range-bound. Both directions acceptable.")
            lines.append("  - BUY near support levels, SELL near resistance levels.")
            lines.append("  - Use tighter stops and targets (the range is defined, use it).")
            lines.append("  - Mean-reversion strategies work best here. Trade the range boundaries.")
            if fear_greed < 20:
                lines.append(f"  - F&G={fear_greed} (extreme fear) + ranging = oversold near support.")
                lines.append("    BUY coins showing clear support holds with TA confirmation.")
            elif fear_greed > 80:
                lines.append(f"  - F&G={fear_greed} (extreme greed) + ranging = potential rejection at resistance.")
                lines.append("    Favor SHORT entries near range highs.")

        elif regime == "volatile":
            lines.append("MOMENTUM TRADING — 3-5 trades, ride the volatility:")
            lines.append("  - Both directions acceptable. Follow momentum.")
            lines.append("  - Use wider stops (2-3%) to avoid noise — volatility is in ATR.")
            lines.append("  - Use wider TPs (3-5%) — volatility means bigger moves to capture.")
            lines.append("  - Volatility = opportunity. Trade the momentum, not the chop.")
            if fear_greed < 20:
                lines.append(f"  - F&G={fear_greed} (extreme fear) + volatile = potential capitulation.")
                lines.append("    Watch for bounce setups after sharp drops. Be ready to flip direction.")

        elif regime == "dead":
            lines.append("SCALP MODE — 2-4 tight trades, exploit predictable levels:")
            lines.append("  - Dead markets have low volatility = predictable S/R levels.")
            lines.append("  - BUY at support, SELL at resistance with tight TP (1-1.5%).")
            lines.append("  - Use moderate leverage (3-4x), tight stops (1.5%).")
            lines.append("  - The market IS moving — just in a small range. Trade that range.")
            if fear_greed < 20:
                lines.append(f"  - F&G={fear_greed} (extreme fear) + dead = low liquidity but clean levels.")
                lines.append("    Scalp obvious S/R bounces with tight TP. Smaller size, quick exits.")

        else:
            lines.append("MODERATE TRADING — 3-5 trades with caution:")
            lines.append("  - Regime unknown. Trade with moderate size and clear setups only.")

        lines.append("")
        return "\n".join(lines)

    def _build_direction_performance(self) -> str:
        """Build direction-specific performance analysis from recent closed trades.

        Complements the enforcer's coaching text (which shows overall stats)
        by providing raw directional trade data with explicit warnings.
        """
        coordinator = self.services.get("trade_coordinator")
        if not coordinator or not hasattr(coordinator, "_closed_trades"):
            return ""

        closed = coordinator._closed_trades
        if not closed:
            return ""

        # Take last 20 trades for recent performance signal
        recent = closed[-20:]

        buy_trades = [t for t in recent if t.get("direction") == "Buy"]
        sell_trades = [t for t in recent if t.get("direction") == "Sell"]

        if not buy_trades and not sell_trades:
            return ""

        lines = [
            "## DIRECTION PERFORMANCE (last 20 trades — read carefully)",
        ]

        warnings = []

        # Buy direction analysis
        if buy_trades:
            buy_wins = sum(1 for t in buy_trades if t.get("was_win"))
            buy_total = len(buy_trades)
            buy_wr = buy_wins / buy_total
            buy_pnl = sum(t.get("pnl_usd", 0) for t in buy_trades)
            lines.append(
                f"  BUY/LONG: {buy_wins}W/{buy_total - buy_wins}L "
                f"(WR={buy_wr:.0%}) PnL=${buy_pnl:+.2f}"
            )
            if buy_total >= 5 and buy_wr < 0.40:
                warnings.append(
                    f"BUY DIRECTION FAILING: {buy_wr:.0%} win rate over {buy_total} trades "
                    f"(${buy_pnl:+.2f}). BUY underperforming — lean SHORT this cycle. Reduce BUY size by 50%."
                )
        else:
            lines.append("  BUY/LONG: no recent trades")

        # Sell direction analysis
        if sell_trades:
            sell_wins = sum(1 for t in sell_trades if t.get("was_win"))
            sell_total = len(sell_trades)
            sell_wr = sell_wins / sell_total
            sell_pnl = sum(t.get("pnl_usd", 0) for t in sell_trades)
            lines.append(
                f"  SELL/SHORT: {sell_wins}W/{sell_total - sell_wins}L "
                f"(WR={sell_wr:.0%}) PnL=${sell_pnl:+.2f}"
            )
            if sell_total >= 5 and sell_wr < 0.40:
                warnings.append(
                    f"SELL DIRECTION FAILING: {sell_wr:.0%} win rate over {sell_total} trades "
                    f"(${sell_pnl:+.2f}). SELL underperforming — lean LONG this cycle. Reduce SELL size by 50%."
                )
        else:
            lines.append("  SELL/SHORT: no recent trades")

        # Emit warnings
        if warnings:
            lines.append("")
            for w in warnings:
                lines.append(f"  WARNING: {w}")

        # Summary recommendation
        if buy_trades and sell_trades:
            buy_wr_val = sum(1 for t in buy_trades if t.get("was_win")) / len(buy_trades)
            sell_wr_val = sum(1 for t in sell_trades if t.get("was_win")) / len(sell_trades)
            if buy_wr_val > sell_wr_val + 0.15 and len(buy_trades) >= 3:
                lines.append("  RECOMMENDATION: BUY is outperforming SELL. Favor LONG setups.")
            elif sell_wr_val > buy_wr_val + 0.15 and len(sell_trades) >= 3:
                lines.append("  RECOMMENDATION: SELL is outperforming BUY. Favor SHORT setups.")

        lines.append("")

        log.debug(
            f"STRAT_DIR_PERF | buy_n={len(buy_trades)} sell_n={len(sell_trades)} "
            f"warnings={len(warnings)} | {ctx()}"
        )

        return "\n".join(lines)

    async def _build_position_review_prompt(self, positions) -> str:
        """Build prompt for 30-second position review."""
        lines = [
            "Review these open positions. For each: hold, close, tighten_stop, set_exit, or take_profit.\n"
        ]

        market_service = self.services.get("market_service")
        ta_cache = self.services.get("ta") or self.services.get("ta_cache")
        coordinator = self.services.get("trade_coordinator")

        for pos in positions:
            try:
                ticker = (
                    await market_service.get_ticker(pos.symbol)
                    if market_service
                    else None
                )
                ta = None
                if ta_cache:
                    try:
                        ta = await ta_cache.analyze(
                            symbol=pos.symbol, timeframe=TimeFrame.H1
                        )
                    except Exception as e:
                        log.debug("TA analysis for position review failed: {err}", err=str(e))

                plan = (
                    coordinator.get_trade_plan(pos.symbol)
                    if coordinator
                    else None
                )
                trade_info = (
                    coordinator.get_trade_info(pos.symbol)
                    if coordinator
                    else {}
                )

                current_price = (
                    ticker.last_price if ticker else pos.mark_price
                )
                pnl_pct = (
                    (
                        (current_price - pos.entry_price)
                        / pos.entry_price
                        * 100
                    )
                    if pos.entry_price > 0
                    else 0
                )
                side_val = (
                    pos.side.value if hasattr(pos.side, "value") else str(pos.side)
                )
                if side_val in ("Sell", "Short"):
                    pnl_pct = -pnl_pct

                rsi = 50
                macd = 0
                if ta:
                    rsi = ta.get("momentum", {}).get("rsi_14", 50)
                    macd_data = ta.get("trend", {}).get("macd", {})
                    if isinstance(macd_data, dict):
                        macd = macd_data.get("histogram", 0)

                age = plan.age_minutes if plan else 0
                remaining = plan.remaining_minutes if plan else 999
                strategy = trade_info.get("strategy_name", "unknown")
                thesis = (
                    plan.reasoning[:100]
                    if plan and plan.reasoning
                    else "unknown"
                )
                trailing = (
                    "active" if plan and plan.trailing_active else "off"
                )

                lines.append(
                    f"{pos.symbol} {side_val}: entry=${format_price(pos.entry_price)} "
                    f"now=${format_price(current_price)} PnL={pnl_pct:+.2f}% "
                    f"age={age:.0f}min remain={remaining:.0f}min "
                    f"RSI={rsi:.0f} MACD={macd:.4f} trailing={trailing} "
                    f'strategy={strategy} thesis="{thesis}"'
                )
            except Exception as e:
                lines.append(f"{pos.symbol}: error getting data -- {e}")

        return "\n".join(lines)

    def _parse_plan(self, data: dict) -> StrategicPlan:
        """Parse Claude's JSON response into a StrategicPlan."""
        plan = StrategicPlan(
            market_view=data.get("market_view", ""),
            risk_level=data.get("risk_level", "normal"),
            max_positions=_safe_int(data.get("max_positions"), 4),
            max_per_coin=_safe_int(data.get("max_per_coin"), 1),
            default_sl_pct=_safe_float(data.get("default_sl_pct"), 2.0),
            default_tp_pct=_safe_float(data.get("default_tp_pct"), 2.5),
            default_hold_minutes=_safe_int(data.get("default_hold_minutes"), 30),
            default_leverage=_safe_int(data.get("default_leverage"), 2),
            trailing_activation_pct=_safe_float(
                data.get("trailing_activation_pct"), 0.5
            ),
            focus_coins=data.get("focus_coins", []),
            avoid_coins=data.get("avoid_coins", []),
            raw_reasoning=data.get("market_view", ""),
        )

        # Parse new trades (Claude's direct trade commands)
        plan.new_trades = data.get("new_trades", [])

        # Parse coin directives
        for symbol, directive in data.get("coin_directives", {}).items():
            if isinstance(directive, dict):
                plan.coin_directives[symbol] = CoinDirective(
                    symbol=symbol,
                    direction=directive.get("direction", "both"),
                    reason=directive.get("reason", ""),
                    leverage=_safe_int(
                        directive.get("leverage"), plan.default_leverage
                    ),
                    sl_pct=_safe_float(
                        directive.get("sl_pct"), plan.default_sl_pct
                    ),
                    tp_pct=_safe_float(
                        directive.get("tp_pct"), plan.default_tp_pct
                    ),
                    max_hold_minutes=_safe_int(
                        directive.get("max_hold_minutes"),
                        plan.default_hold_minutes,
                    ),
                )

        # Parse position actions
        for symbol, action in data.get("position_actions", {}).items():
            if isinstance(action, dict):
                plan.position_actions[symbol] = PositionAction(
                    symbol=symbol,
                    action=action.get("action", "hold"),
                    reason=action.get("reason", ""),
                    exit_price=_safe_float(action.get("exit_price")),
                    new_sl=_safe_float(action.get("new_sl")),
                )

        return plan

    # ────────────────────────────────────────────────────────────────
    # Mid-Hold Trade Management Fix Phase 3.7/3.8 (2026-05-19) — Helpers
    # ────────────────────────────────────────────────────────────────

    @staticmethod
    def _render_thesis_invalidation_block(
        thesis_row: dict, flip_annotation: bool = False,
    ) -> str:
        """Render the per-position THESIS_INVALIDATION line for CALL_A/B.

        Args:
            thesis_row: A row dict from ``ThesisManager.get_open_theses``.
            flip_annotation: When True, prefix with the CALL_B Framing
                Fix Phase 1C-safe annotation so the brain treats the
                criterion as situational context for APEX/XRAY-flipped
                positions (Phase 3.8 only).

        Returns:
            One-line string ready to append to ``sections``. Empty
            string when the row has neither a brain criterion nor a
            heuristic snapshot worth rendering.
        """
        import json as _json

        source = str(thesis_row.get("thesis_source", "brain_stated") or "brain_stated")
        state = str(thesis_row.get("thesis_state", "VALID") or "VALID")
        prefix = "THESIS_INVALIDATION"
        if flip_annotation:
            # CALL_B Framing Fix Phase 1C defense: explicit framing so
            # the brain treats this as situational context for a flipped
            # position rather than as an instruction to close.
            prefix = "THESIS_INVALIDATION_PRE_FLIP_INFORMATIONAL"

        # Brain-stated path.
        if source == "brain_stated":
            raw = thesis_row.get("thesis_invalidation") or ""
            if not raw:
                # Brain explicitly said 'none' OR the column is empty.
                return f"    {prefix}: type=none state={state} source=brain_stated"
            try:
                crit = _json.loads(raw)
                t = crit.get("type")
                v = crit.get("value")
                return (
                    f"    {prefix}: type={t} value={v} state={state} "
                    f"source=brain_stated"
                )
            except Exception:
                return f"    {prefix}: type=unparsed state={state} source=brain_stated"

        # Heuristic fallback path.
        snap_raw = thesis_row.get("thesis_snapshot") or "{}"
        try:
            snap = _json.loads(snap_raw)
        except Exception:
            snap = {}
        anchor = snap.get("nearest_aligned_level") or {}
        anchor_type = anchor.get("type")
        if not anchor_type or anchor_type == "none":
            return (
                f"    {prefix}: source=heuristic_fallback no_anchor "
                f"state={state}"
            )
        # Choose the relevant price field by direction.
        direction = str(thesis_row.get("direction", "") or "").upper()
        if direction == "SELL":
            level = (
                anchor.get("high")
                or anchor.get("top")
                or anchor.get("midpoint")
                or 0.0
            )
        else:
            level = (
                anchor.get("low")
                or anchor.get("bottom")
                or anchor.get("midpoint")
                or 0.0
            )
        return (
            f"    {prefix}: source=heuristic_fallback "
            f"anchor={anchor_type}@{level} state={state}"
        )

    @staticmethod
    def _render_thesis_events_block(events: list[dict]) -> tuple[str, list[int]]:
        """Render the QUEUED_EVENTS line for one symbol.

        Args:
            events: List of unseen event dicts (from
                ``ThesisManager.get_unseen_events``), most-recent-first
                already due to ORDER BY clause.

        Returns:
            ``(rendered_text, event_ids_rendered)``. The caller is
            expected to track the event IDs and call
            ``mark_events_consumed`` after the Claude response returns.
        """
        if not events:
            return "", []
        ids: list[int] = []
        descriptors: list[str] = []
        for ev in events:
            ids.append(int(ev["id"]))
            etype = ev.get("event_type", "")
            ts = ev.get("created_at", "")
            payload = ev.get("payload") or "{}"
            # Compact payload to one line in the prompt.
            payload_short = payload.replace("\n", " ")[:140]
            descriptors.append(f"{ts}: {etype} {payload_short}")
        joined = " | ".join(descriptors)
        rendered = f"    QUEUED_EVENTS: [{joined}]"
        return rendered, ids

    async def _consume_callA_events(self) -> None:
        """Mark events rendered into the last CALL_A prompt as consumed.

        Called by create_trade_plan after Claude responds successfully.
        Failure to mark must not fail the trade plan — log warning and
        continue.
        """
        if not self._last_callA_event_ids:
            return
        thesis_mgr = self.services.get("thesis_manager")
        if thesis_mgr is None:
            self._last_callA_event_ids = []
            return
        try:
            n = await thesis_mgr.mark_events_consumed(
                self._last_callA_event_ids, "CALL_A",
            )
            log.info(
                f"THESIS_SURFACED_IN_PROMPT | consumer=CALL_A "
                f"events={n} | {ctx()}"
            )
        except Exception as _e:
            log.warning(
                f"CALLA_EVENTS_CONSUME_FAIL | err='{str(_e)[:120]}' | {ctx()}"
            )
        finally:
            self._last_callA_event_ids = []

    async def _consume_callB_events(self) -> None:
        """Mark events rendered into the last CALL_B prompt as consumed."""
        if not self._last_callB_event_ids:
            return
        thesis_mgr = self.services.get("thesis_manager")
        if thesis_mgr is None:
            self._last_callB_event_ids = []
            return
        try:
            n = await thesis_mgr.mark_events_consumed(
                self._last_callB_event_ids, "CALL_B",
            )
            log.info(
                f"THESIS_SURFACED_IN_PROMPT | consumer=CALL_B "
                f"events={n} | {ctx()}"
            )
        except Exception as _e:
            log.warning(
                f"CALLB_EVENTS_CONSUME_FAIL | err='{str(_e)[:120]}' | {ctx()}"
            )
        finally:
            self._last_callB_event_ids = []

    def _parse_trade_plan(self, data: dict) -> StrategicPlan:
        """Parse Call A response — new_trades only, no position_actions."""
        plan = StrategicPlan(
            market_view=data.get("market_view", ""),
            risk_level=data.get("risk_level", "normal"),
            max_positions=_safe_int(data.get("max_positions"), 4),
            max_per_coin=_safe_int(data.get("max_per_coin"), 1),
            default_sl_pct=_safe_float(data.get("default_sl_pct"), 2.0),
            default_tp_pct=_safe_float(data.get("default_tp_pct"), 2.5),
            default_hold_minutes=_safe_int(data.get("default_hold_minutes"), 30),
            default_leverage=_safe_int(data.get("default_leverage"), 2),
            trailing_activation_pct=_safe_float(
                data.get("trailing_activation_pct"), 0.5
            ),
            focus_coins=data.get("focus_coins", []),
            avoid_coins=data.get("avoid_coins", []),
            raw_reasoning=data.get("market_view", ""),
        )
        plan.new_trades = data.get("new_trades", [])
        # Parse coin directives if present
        for symbol, directive in data.get("coin_directives", {}).items():
            if isinstance(directive, dict):
                plan.coin_directives[symbol] = CoinDirective(
                    symbol=symbol,
                    direction=directive.get("direction", "both"),
                    reason=directive.get("reason", ""),
                    leverage=_safe_int(
                        directive.get("leverage"), plan.default_leverage
                    ),
                    sl_pct=_safe_float(
                        directive.get("sl_pct"), plan.default_sl_pct
                    ),
                    tp_pct=_safe_float(
                        directive.get("tp_pct"), plan.default_tp_pct
                    ),
                    max_hold_minutes=_safe_int(
                        directive.get("max_hold_minutes"),
                        plan.default_hold_minutes,
                    ),
                )
        return plan

    def _parse_position_plan(self, data: dict) -> StrategicPlan:
        """Parse Call B response — position_actions only, no new_trades.

        Tolerates: null fields (per POSITION_SYSTEM_PROMPT 'price_or_null'
        contract), missing fields, malformed types, unknown action strings.
        Downgrades ``tighten_stop`` without a valid new_sl (and ``set_exit``
        without a valid exit_price) to ``hold`` with a warning log so the
        intent is observable rather than silently dropped by the watchdog.
        """
        plan = StrategicPlan()

        if not isinstance(data, dict):
            log.warning(
                f"STRAT_CALL_B_BAD_SHAPE | type={type(data).__name__} | {ctx()}"
            )
            return plan

        raw_actions = data.get("position_actions") or {}
        if not isinstance(raw_actions, dict):
            log.warning(
                f"STRAT_CALL_B_BAD_ACTIONS | type={type(raw_actions).__name__} | {ctx()}"
            )
            return plan

        valid_actions = {
            "hold", "close", "tighten_stop", "set_exit", "take_profit",
        }
        counts = {
            "hold": 0, "close": 0, "tighten_stop": 0,
            "set_exit": 0, "take_profit": 0,
        }

        for symbol, action_data in raw_actions.items():
            if not isinstance(action_data, dict):
                log.warning(
                    f"STRAT_CALL_B_BAD_ACTION | sym={symbol} "
                    f"type={type(action_data).__name__} | {ctx()}"
                )
                continue

            action = str(action_data.get("action", "hold")).strip().lower()
            if action not in valid_actions:
                log.warning(
                    f"STRAT_CALL_B_BAD_ACTION_TYPE | sym={symbol} "
                    f"act='{action}' -> hold | {ctx()}"
                )
                action = "hold"

            new_sl = _safe_float(action_data.get("new_sl"))
            exit_price = _safe_float(action_data.get("exit_price"))

            if action == "tighten_stop" and new_sl <= 0:
                log.warning(
                    f"STRAT_CALL_B_DOWNGRADE | sym={symbol} "
                    f"act=tighten_stop new_sl=invalid -> hold | {ctx()}"
                )
                action = "hold"
            elif action == "set_exit" and exit_price <= 0:
                log.warning(
                    f"STRAT_CALL_B_DOWNGRADE | sym={symbol} "
                    f"act=set_exit exit_price=invalid -> hold | {ctx()}"
                )
                action = "hold"

            reason = str(
                action_data.get("reason", action_data.get("reasoning", ""))
            )[:500]

            plan.position_actions[symbol] = PositionAction(
                symbol=symbol,
                action=action,
                reason=reason,
                exit_price=exit_price,
                new_sl=new_sl,
            )
            counts[action] = counts.get(action, 0) + 1

        log.info(
            f"STRAT_CALL_B_PARSED | total={len(plan.position_actions)} "
            f"hold={counts['hold']} close={counts['close']} "
            f"tighten={counts['tighten_stop']} set_exit={counts['set_exit']} "
            f"take_profit={counts['take_profit']} | {ctx()}"
        )

        return plan
