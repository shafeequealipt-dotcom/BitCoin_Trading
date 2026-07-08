# Phase 1 Step 1.4 - Validate Issue 4 Claims

Date: 2026-05-19.
Branch: `fix/wd-scoring-brain-vote`.
Validator: read-only review of `src/brain/strategist.py` against the Issue 4 claims in `/home/inshadaliqbal786/DIRECTION_BIAS_ROOT_CAUSE_AND_FIX_OPTIONS_2026-05-19.md` Section 5 (lines 559-702 of the prior report).

## Scope of validation

The prior report's Issue 4 ("Strategist 70/30 mandate") raised four primary claims:

1. The live asymmetric MARKET REGIME block sits at `strategist.py:3371-3390` inside `_build_trade_prompt` (the CALL_A user-prompt builder).
2. A near-twin block at `strategist.py:1416-1435` inside `_build_context_prompt` is dead in production.
3. The original "70% shorts, 30% longs" mandate method `_build_regime_instructions` at `strategist.py:4155-4251` is dead in production.
4. The boot sentinel `STRAT_AGGRESSIVE_FRAMING | ... regime_instr=minimal contract=aggressive_exploit` (at `strategist.py:870`) is emitted on every CALL_A and falsely advertises the asymmetric block has been suppressed.

This validation reads end-to-end the relevant call paths, exhaustively grep-audits every direction-mentioning line in `strategist.py`, traces caller chains, and quotes the live text verbatim against the prior report. The output also enumerates other asymmetric sites the prior report did not call out, plus the CALL_B builder's direction-related text.

The read scope inside `strategist.py` (4612 total lines): file head (1-450), TRADE_SYSTEM_PROMPT (66-142), POSITION_SYSTEM_PROMPT (163-179), TRADE_SYSTEM_PROMPT_ZERO_TWO (298-357), trim-marker tuple (374-449), `create_strategic_plan` (677-731), `create_trade_plan` (765-952, with sentinel at 870), `create_position_plan` (953+), `_build_context_prompt` (1033-1700+), `_build_trade_prompt` (2808-3811), `_build_position_prompt` (3815-4153), `_build_regime_instructions` (4155-4251), `_build_direction_performance` (4253-4350). Direction-mentioning lines were grep-audited file-wide.

## Files read

Implementation code (read-only):
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/brain/strategist.py` lines 1-450, 677-952, 1033-1170, 1416-1470, 2808-3220, 3360-3470, 3490-3811, 3815-4153, 4155-4350.
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/core/layer_manager.py` lines 710-790 (production caller of `create_trade_plan`).
- `/home/inshadaliqbal786/trading-intelligence-mcp/scripts/run_30min_test.py` lines 60-130 (test-harness caller of `create_strategic_plan`).

Log evidence (read-only):
- `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/brain.log` (grepped for `STRAT_AGGRESSIVE_FRAMING` on 2026-05-18).

Prior reports referenced:
- `/home/inshadaliqbal786/DIRECTION_BIAS_ROOT_CAUSE_AND_FIX_OPTIONS_2026-05-19.md` lines 559-702 (Section 5).
- `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/dirbias_validation/phase0_baseline.md` for funnel cross-check.

No source files were modified.

## Live asymmetric block verification (line-by-line at 3371-3390)

The exact source at `strategist.py:3370-3390` reads:

```
3370        # Regime
3371        sections.append("\n## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)")
3372        if _regime_state:
3373            direction_hint = {
3374                "trending_down": "DEFAULT SELL BIAS - check per-coin regime before deciding",
3375                "trending_up": "BUY preferred",
3376                "ranging": "both directions OK",
3377                "volatile": "both directions with caution",
3378                "dead": "scalp mode - both directions, tight TP",
3379            }.get(_regime_str, "neutral")
3380            sections.append(
3381                f"Global: {_regime_str} "
3382                f"(confidence={_regime_state.confidence:.0%}) "
3383                f"→ {direction_hint}"
3384            )
3385            if _regime_state.confidence > 0.60 and _regime_str == "trending_down":
3386                sections.append(
3387                    "NOTE: High-confidence global downtrend. DEFAULT to SELL for coins "
3388                    "without per-coin regime data. Coins with [TRENDING_UP] per-coin "
3389                    "regime should still be BOUGHT - they are diverging from the market."
3390                )
```

Line-by-line verification against the prior report Section 5.2 claims:

| Element | Source location | Verified content | Matches prior report? |
|---|---|---|---|
| Header text | line 3371 | `"\n## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)"` | Yes - exact match |
| `direction_hint` dict opens | line 3373 | `direction_hint = {` | Yes |
| `trending_down` entry | line 3374 | `"DEFAULT SELL BIAS - check per-coin regime before deciding"` | Yes - exact match |
| `trending_up` entry | line 3375 | `"BUY preferred"` | Yes - exact match |
| `ranging` entry | line 3376 | `"both directions OK"` | Yes |
| `volatile` entry | line 3377 | `"both directions with caution"` | Yes |
| `dead` entry | line 3378 | `"scalp mode - both directions, tight TP"` | Yes |
| Global render line | lines 3380-3384 | `f"Global: {_regime_str} (confidence={_regime_state.confidence:.0%}) → {direction_hint}"` | Yes - matches verbatim |
| Conf-gated NOTE conditional | line 3385 | `if _regime_state.confidence > 0.60 and _regime_str == "trending_down":` | Yes - confirms NOTE fires for trending_down only |
| NOTE body | lines 3386-3390 | `"NOTE: High-confidence global downtrend. DEFAULT to SELL for coins without per-coin regime data. Coins with [TRENDING_UP] per-coin regime should still be BOUGHT - they are diverging from the market."` | Yes - matches verbatim |

Quote audit completed:
- Header text quoted verbatim: `## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)`. The word "CONTROLS" is directive-flavoured as the prior report flagged.
- All five `direction_hint` dict entries quoted above.
- The high-confidence NOTE fires only for `_regime_str == "trending_down"`. There is no sibling branch for `trending_up + conf > 0.60`. No other ` elif _regime_str == "trending_up":` exists in the surrounding 50 lines.
- The full block (header + global line + optional NOTE) occupies lines 3371-3390 - confirms the prior report's 3371-3390 boundary.
- The block executes inside `_build_trade_prompt` (the CALL_A live builder defined at `strategist.py:2808`).

The block IS reached on every CALL_A cycle in production - log evidence in the boot sentinel section below confirms this.

## Dead code verification (1416-1435 and 4155-4251)

### Dead duplicate at `strategist.py:1416-1435` (inside `_build_context_prompt`)

Read at the source location:

```
1415        # Regime (reuse early-fetched data)
1416        sections.append("\n## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)")
1417        if _regime_state:
1418            direction_hint = {
1419                "trending_down": "DEFAULT SELL BIAS - check per-coin regime before deciding",
1420                "trending_up": "BUY preferred",
1421                "ranging": "both directions OK",
1422                "volatile": "both directions with caution",
1423                "dead": "scalp mode - both directions, tight TP",
1424            }.get(_regime_str, "neutral")
1425            sections.append(
1426                f"Global: {_regime_str} "
1427                f"(confidence={_regime_state.confidence:.0%}) "
1428                f"→ {direction_hint}"
1429            )
1430            if _regime_state.confidence > 0.60 and _regime_str == "trending_down":
1431                sections.append(
1432                    "NOTE: High-confidence global downtrend. DEFAULT to SELL for coins "
1433                    "without per-coin regime data. Coins with [TRENDING_UP] per-coin "
1434                    "regime should still be BOUGHT - they are diverging from the market."
1435                )
```

This is a verbatim duplicate of the live block at 3371-3390. The only character difference is the surrounding indentation context (different parent function). The header text, the dict, and the NOTE all match the live block byte-for-byte.

Caller-chain analysis (see next section) proves this duplicate is unreachable in production.

### Dead method at `strategist.py:4155-4251`

The method definition at line 4155:

```
4155    def _build_regime_instructions(self, regime: str, confidence: float, fear_greed: int) -> str:
4156        """Build dynamic regime-specific trading instructions.
4157
4158        Placed early in context so Claude reads these constraints BEFORE seeing
4159        market data. Each regime is an OPPORTUNITY with the right approach.
4160        """
```

Body summary (sampled exact lines):
- Header `"## REGIME-SPECIFIC TRADING INSTRUCTIONS (READ BEFORE LOOKING AT DATA)"` at line 4162.
- `"  - A coin in [TRENDING_UP] should be BOUGHT, even if global is trending_down."` at line 4169.
- `"  - A coin in [TRENDING_DOWN] should be SOLD, even if global is trending_up."` at line 4170.
- `trending_down + high_confidence` branch at lines 4181-4190 includes:
  - line 4183: `"DEFAULT BIAS: SHORT - for coins without per-coin regime:"`
  - line 4184: `"  - SELL/SHORT bias - 70% shorts, 30% longs only on extreme oversold bounces."` (the literal "70% shorts, 30% longs" the prior report flagged).
  - line 4185: `"  - Oversold RSI in a downtrend means trend is STRONG. Short the bounces."`
  - line 4186: `"  - This is where you make the money - ride the trend with conviction."`
  - line 4188: F&G < 20: `"  - F&G={fear_greed} (extreme fear) confirms downtrend. SHORT with full conviction."`
  - line 4190: F&G < 40: `"  - F&G={fear_greed} (fear) confirms bearish sentiment. Favor shorts."`
- `trending_down + moderate` branch at lines 4192-4195 includes line 4195: `"  - Target 70% SHORT / 30% LONG allocation."`
- `trending_up + high_confidence` branch at lines 4197-4207 includes:
  - line 4199: `"DEFAULT BIAS: LONG - for coins without per-coin regime:"`
  - line 4200: `"  - BUY/LONG bias - 70% longs, 30% shorts only on extreme overbought."` (symmetric counterpart "70% longs, 30% shorts").
  - line 4201: `"  - Buy every pullback, ride the trend with conviction."`
  - line 4203: F&G > 80: `"  - F&G={fear_greed} (extreme greed) - uptrend may be overextended."`
  - line 4204: `"    Tighten stops on existing longs. Still trade but reduce new long size."`
  - line 4206: F&G < 20: `"  - F&G={fear_greed} (extreme fear) + uptrend = MAXIMUM buy opportunity."`
- `trending_up + moderate` branch at lines 4208-4211 includes line 4211: `"  - Target 70% LONG / 30% SHORT allocation."`
- `ranging` branch at lines 4213-4224.
- `volatile` branch at lines 4226-4234.
- `dead` branch at lines 4236-4244.
- Fallback at lines 4246-4248: `"MODERATE TRADING - 3-5 trades with caution:"`.

Block ends at line 4251 with `return "\n".join(lines)`. The method occupies lines 4155-4251 inclusive - confirms the prior report's boundary exactly.

Two observations the prior report glossed but worth noting:
- The "70% shorts / 30% longs" literal appears at line 4184 (high-confidence trending_down) and line 4195 (moderate trending_down).
- The "70% longs / 30% shorts" mirror appears at line 4200 (high-confidence trending_up) and line 4211 (moderate trending_up).
- So inside the dead method, the 70/30 mandate text is symmetric per regime. The asymmetric F&G rule is what the prior report calls out: `trending_down` branches into F&G<20 AND F&G<40 (line 4187, 4189), while `trending_up` branches into F&G>80 AND F&G<20 (line 4202, 4205) - the "F&G 20-40 + trending_up" branch is absent. Low operational impact since the method is dead.

## Caller chain trace (proving deadness)

### `_build_regime_instructions` callers

Grep result file-wide: `_build_regime_instructions` appears at exactly two locations in any `.py` file across the whole repo:
- `src/brain/strategist.py:1084` - the call.
- `src/brain/strategist.py:4155` - the method definition.

No test imports it. No external module imports it. Only caller is line 1084 inside `_build_context_prompt`:

```
1082        # === REGIME INSTRUCTIONS (position 2: right after coaching) ===
1083        try:
1084            regime_instructions = self._build_regime_instructions(
1085                _regime_str, _regime_confidence, _fear_greed_value
1086            )
1087            if regime_instructions:
1088                sections.append(regime_instructions)
```

This confirms the dead method has exactly one caller, which itself is dead (proved next).

### `_build_context_prompt` callers

Grep result: `_build_context_prompt` appears at three references across all `.py` files:
- `src/brain/strategist.py:683` - the call inside `create_strategic_plan`.
- `src/brain/strategist.py:1033` - the method definition.
- `src/brain/strategist.py:2844` - a comment ("the dead `_build_context_prompt` at line 759 also calls it"). The line 759 reference in this comment is stale - the actual call now sits at line 683. Not a behaviour issue; just an out-of-date comment pointer.

Plus test-file mentions in:
- `tests/test_stage2_phase4/test_priority_classifier.py:65` - comment string referencing the legacy header.
- `tests/test_callb_lessons_injected_fields.py:10` - comment string referencing the legacy path.

No production call from anywhere else. The lone production caller is line 683 inside `create_strategic_plan`:

```
677    async def create_strategic_plan(self) -> StrategicPlan | None:
678        """Build context, call Claude, parse plan."""
679        _cycle_start = time.time()
680        did = new_decision_id()
681        log.info(f"STRAT_CYCLE_START | did={did} | {ctx()}")
682        try:
683            prompt = await self._build_context_prompt()
684            log.info(f"STRAT_PROMPT | chars={len(prompt)} | {ctx()}")
```

### `create_strategic_plan` callers

Grep across the entire repo (excluding bytecode):
- `src/brain/strategist.py:677` - the method definition.
- `scripts/run_30min_test.py:76` - reads `original_create = strategist.create_strategic_plan`.
- `scripts/run_30min_test.py:106` - reassigns `strategist.create_strategic_plan = logged_create`.

No other reference exists in production code:
- `src/core/layer_manager.py` (the live brain loop dispatcher) does NOT call `create_strategic_plan`. It dispatches `create_trade_plan` (line 770) and `create_position_plan` (line 938).
- `src/workers/*` does not invoke it.
- No tests assert on it.

`scripts/run_30min_test.py:60-106` confirms it is a monkey-patch logging wrapper used solely by a manual 30-minute test harness (called only when an operator runs `python scripts/run_30min_test.py`). Reading lines 76-106:

```
76        original_create = strategist.create_strategic_plan
77        async def logged_create():
78            print(f"\n  [{ts()}] CLAUDE BRAIN REVIEW starting...", flush=True)
79            plan = await original_create()
...
106       strategist.create_strategic_plan = logged_create
```

The harness simply wraps the method for verbose console logging. It is NOT exercised by the live brain loop. The live brain loop in `layer_manager._run_brain_cycle` (lines 736-940) goes:

```
770            plan = await strategist.create_trade_plan()
...
938            plan = await strategist.create_position_plan()
```

Conclusion: `create_strategic_plan` is dead in the production runtime path. Therefore `_build_context_prompt` is dead, therefore `_build_regime_instructions` is dead, therefore the 70/30 mandate text in lines 4184/4195/4200/4211 never reaches a live Claude call. The duplicate header at `strategist.py:1416-1435` is also unreachable.

### `_build_trade_prompt` caller chain (live CALL_A confirmed)

Grep result (production paths only):
- `src/brain/strategist.py:823` - the call site inside `create_trade_plan`.
- `src/brain/strategist.py:2808` - the method definition.
- Multiple comments and tests reference it for monkey-patching.

`create_trade_plan` callers:
- `src/core/layer_manager.py:770` - the live brain loop dispatcher.
- `tests/test_strategist_calla_skip.py` and `tests/test_strat_call_pairing.py` - test-side calls.

`layer_manager.py:736-790` confirms the live dispatch path. Quoting lines 753-770:

```
753        if self._call_type == "A":
754            # ═══ CALL A: Find New Trades ═══
755            log.info(f"BRAIN_CYCLE_A | Finding new trades | {ctx()}")
...
768            try:
769                try:
770                    plan = await strategist.create_trade_plan()
```

`create_trade_plan` at `strategist.py:765` directly invokes `prompt = await self._build_trade_prompt()` at line 823, then sends `prompt` plus the system prompt to Claude at line 878.

Conclusion: The live CALL_A builder is `_build_trade_prompt`. The asymmetric block at 3371-3390 is therefore the production user-prompt source for direction-related regime context.

## Complete direction-string table (every direction-mentioning line in strategist.py)

Below is the exhaustive list of every direction-mentioning string in `strategist.py`. The grep base covered: "long", "short", "buy", "sell", "direction", "bias", "bullish", "bearish". Lines reporting purely non-trading direction language (e.g. "for backward compatibility", "short enough", "longer emits") are excluded. Builder column says where the line emits to.

Notation:
- `TRADE_SYSTEM_PROMPT` = constant at lines 66-142 (system prompt for legacy contract).
- `TRADE_SYSTEM_PROMPT_ZERO_TWO` = constant at lines 298-357 (system prompt for zero_two_contract; `_zero_two=True` in live boot sentinel - active in production).
- `POSITION_SYSTEM_PROMPT` = constant at lines 163-179 (CALL_B system prompt).
- `BRIEFING_SYSTEM_PROMPT_SUFFIX` = constant at lines 197-281 (suffix appended to system prompt when `surface_briefing_fields` is True).
- `_build_trade_prompt` = live CALL_A user-prompt builder, lines 2808-3811.
- `_build_context_prompt` = dead `create_strategic_plan` helper, lines 1033-1700.
- `_build_position_prompt` = live CALL_B user-prompt builder, lines 3815-4153.
- `_build_regime_instructions` = dead helper invoked only by `_build_context_prompt`, lines 4155-4251.
- `_build_direction_performance` = legacy helper invoked only by `_build_context_prompt`, lines 4253-4350.
- `_format_packages_for_prompt` and `_format_packages_for_prompt_full` = per-coin TRADE CANDIDATES renderers invoked by `_build_trade_prompt`, lines roughly 1900-2700 region.

### TRADE_SYSTEM_PROMPT (live, system-side - applied to every CALL_A by `create_trade_plan` line 685 or 829 branch)

| Line | Quote (literal) | Trigger | Mirror in opposite regime? | Symmetric? |
|---|---|---|---|---|
| 81 | `DIRECTION BY REGIME (mandatory - PER-COIN, not global):` | Always emitted | Header only | Yes |
| 83 | `- Trade WITH each coin's INDIVIDUAL regime - a coin in [TRENDING_UP] is BOUGHT even if global is trending_down.` | Always emitted | Implicit (per-coin override applies both ways) | Asymmetric example: only TRENDING_UP coin-override is exemplified; no mirror line "a coin in [TRENDING_DOWN] is SHORTED even if global is trending_up" |
| 84 | `- Coins without a per-coin regime tag: use the GLOBAL regime as a directional DEFAULT BIAS (not an absolute rule).` | Always emitted | Both directions | Yes |
| 85 | `- ranging: BOTH directions - buy at support, sell at resistance, mean-reversion plays.` | Always emitted | Both | Yes |
| 86 | `- volatile: BOTH directions - follow momentum, wider stops, ride the volatility.` | Always emitted | Both | Yes |
| 87 | `- dead: BOTH directions - scalp micro-moves, tight TP from VOL data (0.3-0.5%), buy support sell resistance.` | Always emitted | Both | Yes |
| 91 | `* Trending up + fear = strong buy (smart money buys panic).` | Always emitted | Asymmetric pair with 92 | Soft-symmetric |
| 92 | `* Trending down + fear = short with conviction (fear confirms trend).` | Always emitted | Pair with 91 | "strong buy" vs "short with conviction" - slightly different intensity wording |
| 93 | `* Ranging + fear = buy near support levels (mean-reversion).` | Always emitted | One direction quoted | No - no "Ranging + greed = sell near resistance" mirror line in F&G section |
| 94 | `* Dead + fear = careful scalps with tight TP.` | Always emitted | No directional bias | N/A |
| 95 | `- Extreme greed (F&G > 80): take profits on longs, look for short entries.` | Always emitted | One direction | No mirror "Extreme fear: take profits on shorts, look for long entries" |
| 100 | `- direction: Buy or Sell (you CAN short)` | Always emitted | Both | Yes - "you CAN short" is symmetric encouragement |
| 101 | `- stop_loss_price: EXACT price below support (buys) or above resistance (sells)` | Always emitted | Both | Yes |
| 102 | `- take_profit_price: EXACT price at nearest resistance (buys) or support (sells)` | Always emitted | Both | Yes |
| 122 | `   FOR BUY/LONG: SL BELOW entry price, TP ABOVE entry price` | Always emitted | Pair with 123 | Yes |
| 123 | `   FOR SELL/SHORT: SL ABOVE entry price, TP BELOW entry price` | Always emitted | Pair with 122 | Yes |
| 130 | `   - Global regime is the DEFAULT BIAS only for coins without a per-coin regime tag.` | Always emitted | Both directions | Yes |
| 131 | `   - ranging or volatile: both directions acceptable - let TA decide.` | Always emitted | Both | Yes |

System-prompt verdict: Mostly symmetric prose. The Fear & Greed sub-block (lines 89-96) carries minor asymmetry (lines 91/92, 93, 95) - more directional examples on the short side than the long side. Low severity; the system prompt is not the dominant signal.

### TRADE_SYSTEM_PROMPT_ZERO_TWO (live, system-side - active when `[stage2].enable_zero_two_contract=True` which the boot sentinel confirms is the production state)

| Line | Quote (literal) | Trigger | Mirror? | Symmetric? |
|---|---|---|---|---|
| 313 | `DIRECTION BY REGIME (per-coin, not global; guidance not absolute):` | Always emitted | Header | Yes |
| 315 | `- Trade WITH each coin's individual regime when possible - a coin in [TRENDING_UP] is bought even if global is trending_down.` | Always emitted | Implicit pair with line 316 | Asymmetric example: only TRENDING_UP example given; no TRENDING_DOWN mirror |
| 316 | `- Coins without a per-coin regime: global regime as default bias, not a hard rule.` | Always emitted | Both | Yes |
| 317 | `- ranging: BOTH directions allowed - buy at support, sell at resistance.` | Always emitted | Both | Yes |
| 318 | `- volatile: BOTH directions - wider stops, follow momentum.` | Always emitted | Both | Yes |
| 319 | `- dead: BOTH directions but TIGHT TP - scalp micro-moves only.` | Always emitted | Both | Yes |
| 322 | `- F&G < 20: extreme fear creates strong contrarian-buy windows.` | Always emitted | Pair with 323 | Yes - symmetric phrasing |
| 323 | `- F&G > 80: extreme greed creates strong short windows.` | Always emitted | Pair with 322 | Yes |
| 328 | `- direction: "Buy" or "Sell" (you CAN short)` | Always emitted | Both | Yes |
| 329 | `- stop_loss_price: EXACT price below support (buys) or above resistance (sells)` | Always emitted | Both | Yes |
| 330 | `- take_profit_price: EXACT price at nearest resistance (buys) or support (sells)` | Always emitted | Both | Yes |
| 335 | `- reasoning: cite the SPECIFIC per-coin evidence that pushed conviction. Generic reasoning ("good setup", "looks bullish") is rejected.` | Always emitted | One direction example | No mirror "looks bearish" example - low severity |
| 350 | `   FOR BUY/LONG: SL BELOW entry, TP ABOVE entry.` | Always emitted | Pair with 351 | Yes |
| 351 | `   FOR SELL/SHORT: SL ABOVE entry, TP BELOW entry.` | Always emitted | Pair with 350 | Yes |
| 354 | `6. PER-COIN regime overrides global regime.` | Always emitted | Both | Yes |

ZERO_TWO verdict: More symmetric than TRADE_SYSTEM_PROMPT. The asymmetric example at line 315 (only TRENDING_UP override example) is the same kind of asymmetry that exists in line 83.

### POSITION_SYSTEM_PROMPT (CALL_B system prompt)

| Line | Quote (literal) | Trigger | Mirror? | Symmetric? |
|---|---|---|---|---|
| 177 | Comment-style "...some positions are intentionally counter-regime when RR justifies - the system flips direction when the flipped RR is materially better than the original..." | Always | Both | Yes - direction-agnostic |

POSITION_SYSTEM_PROMPT verdict: No direction-asymmetric guidance. Direction language is purely mechanical (Buy/Sell, regime).

### BRIEFING_SYSTEM_PROMPT_SUFFIX (system prompt suffix, lines 197-281)

| Line | Quote | Trigger | Mirror? | Symmetric? |
|---|---|---|---|---|
| 216 | `* TREND_PULLBACK_LONG / SHORT: continuation in clear trend; tight SL at OB.` | Always | Both | Yes |
| 217 | `* RANGE_FADE_LONG / SHORT: mean-reversion at range extreme; tight RR (1.3-2.0).` | Always | Both | Yes |
| 219 | `* LIQUIDITY_SWEEP_REVERSAL_LONG / SHORT: stop-hunt fade; high RR (2.5-4.0).` | Always | Both | Yes |
| 220 | `* FUNDING_EXTREME_FADE_LONG / SHORT: crowded book fade; smaller size.` | Always | Both | Yes |
| 221 | `* COUNTER_TRADE_LONG / SHORT: against structural bias; HALF SIZE.` | Always | Both | Yes |
| 222 | `* MOMENTUM_BURST_LONG / SHORT: volatile momentum; wider SL, trail aggressively.` | Always | Both | Yes |
| 223 | `* OB_MITIGATED_FVG_ONLY_LONG / SHORT: thinner edge; smaller size, tighter RR.` | Always | Both | Yes |
| 225 | `* EXTREME_FEAR_LONG_BIAS / EXTREME_GREED_SHORT_BIAS: contrarian, F&G-driven.` | Always | Both | Yes - LONG_BIAS paired with SHORT_BIAS |
| 246 | `voter count: ``Votes: BUY=5.10 vs SELL=1.20 (12 voters)``.` | Always | Both | Yes |
| 256 | `pushes back: ``Opposition: MODERATE - 2 SELL voters at conf>=0.6` | Example | Asymmetric example | Could read "2 BUY voters" but cosmetic |

BRIEFING verdict: Symmetric. All trade-actionable state labels carry an explicit `_LONG / _SHORT` pair.

### `_build_trade_prompt` (live CALL_A user-prompt builder)

| Line | Quote (literal text in prompt or call) | Builder | Trigger condition | Mirror? | Symmetric? |
|---|---|---|---|---|---|
| 3225-3229 | `f"\n## REGIME DIVERGENCE - These coins DISAGREE with global {_regime_str}:\n  {', '.join(divergent_coins)}\n  Trade these coins WITH their individual regime direction, NOT against it.\n  Do NOT short a coin that is individually in an uptrend.\n  Do NOT buy a coin that is individually in a downtrend."` | `_build_trade_prompt` | When at least one coin's per-coin regime disagrees with global regime | Both directions explicitly mirrored within the same block | Yes - symmetric ("Do NOT short a coin that is individually in an uptrend" pairs with "Do NOT buy a coin that is individually in a downtrend") |
| 3310 | `line += f"FVG={nf.direction}(${nf.bottom:.0f}-${nf.top:.0f}) "` | `_build_trade_prompt` X-RAY block | Always when FVG exists | Both | Yes - direction is read from data |
| 3314 | `line += f"OB={no.direction}(${no.low:.0f}-${no.high:.0f},{fresh_tag},s={no.strength_score:.0f}) "` | `_build_trade_prompt` X-RAY block | Always when OB exists | Both | Yes - data-driven |
| 3371 | `sections.append("\n## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)")` | `_build_trade_prompt` | Always emitted (every CALL_A) | Header text | Header word "CONTROLS" is directive-flavoured. Same header text regardless of regime, but the framing elevates global signal above per-coin evidence |
| 3374 | `"trending_down": "DEFAULT SELL BIAS - check per-coin regime before deciding"` | `_build_trade_prompt` | When regime==trending_down (97% of 2026-05-18 cycles) | Yes - line 3375 | NO - "DEFAULT SELL BIAS" is mandate-flavoured; line 3375 "BUY preferred" is preference-flavoured |
| 3375 | `"trending_up": "BUY preferred"` | `_build_trade_prompt` | When regime==trending_up (5% of 2026-05-18 cycles) | Pair with 3374 | NO - asymmetric strength |
| 3376 | `"ranging": "both directions OK"` | `_build_trade_prompt` | When regime==ranging | Both | Yes |
| 3377 | `"volatile": "both directions with caution"` | `_build_trade_prompt` | When regime==volatile | Both | Yes |
| 3378 | `"dead": "scalp mode - both directions, tight TP"` | `_build_trade_prompt` | When regime==dead | Both | Yes |
| 3380-3384 | `sections.append(f"Global: {_regime_str} (confidence={_regime_state.confidence:.0%}) → {direction_hint}")` | `_build_trade_prompt` | Always when `_regime_state` truthy | Both | Yes - data-driven render |
| 3385-3390 | `if _regime_state.confidence > 0.60 and _regime_str == "trending_down": sections.append("NOTE: High-confidence global downtrend. DEFAULT to SELL for coins without per-coin regime data. Coins with [TRENDING_UP] per-coin regime should still be BOUGHT - they are diverging from the market.")` | `_build_trade_prompt` | trending_down AND conf > 0.60 (97% of cycles per 2026-05-18 brain.log) | NO mirror branch for trending_up + conf > 0.60 | NO - the NOTE fires ONLY for trending_down. No `elif _regime_str == "trending_up":` block. |
| 3389 | `regime should still be BOUGHT - they are diverging from the market.` | `_build_trade_prompt` | trending_down NOTE only | No mirror | NO - the phrasing "diverging from the market" implies trending_down is "the market". No symmetric framing for trending_up regimes |
| 3425 | `f"{h.get('direction', '?')} score={h.get('score', 0)} "` | `_build_trade_prompt` STRATEGY HINTS block | Per-hint render | Data-driven | Yes |
| 3443 | `and "buy" in data and "sell" in data` | `_build_trade_prompt` | Validation guard | Both | Yes |
| 3449 | `f"    {sym}: {data['buy']} buy / {data['sell']} sell (total score: {data['total_score']:.0f})"` | `_build_trade_prompt` CONSENSUS PER COIN block | Per-coin render | Both - shows buy AND sell counts | Yes |

Per-coin TRADE CANDIDATES block (rendered by `_format_packages_for_prompt` and `_format_packages_for_prompt_full` - called from `_build_trade_prompt` at lines 3077 and 3084):

| Line | Quote | Trigger | Mirror? | Symmetric? |
|---|---|---|---|---|
| 1947-1948 | `f"{_setup_label} (COUNTER-TRADE - trade direction is OPPOSITE to market structure bias; lower conviction)"` | Per coin when XRAY counter | Both | Yes - data-driven |
| 1950 | `_trade_dir = pkg.xray.trade_direction or "n/a"` | Per coin | Both | Yes |
| 1954 | `f"trade_direction={_trade_dir})"` | Per coin | Both | Yes |
| 1980 | `f" direction {pkg.signals.direction}"` | Per coin signals block | Both | Yes |
| 2080-2084 | `if buy_w >= sell_w: agree_wsum, opp_wsum = buy_w, sell_w; opp_dir = "SELL"; else: agree_wsum, opp_wsum = sell_w, buy_w; opp_dir = "BUY"` | Per coin vote opposition | Both | Yes - symmetric branch |
| 2123 | `if vote not in ("BUY", "SELL"):` | Per coin category split | Both | Yes |
| 2222 | `side = "Buy" if trade_direction.lower().startswith("long") else "Sell"` | Per coin past-loss line | Both | Yes |
| 2276-2293 | `direction = str(lesson.get("direction", "?") or "?")` then `f"  Past loss [{direction}{regime_str}]: ..."` | Per coin loss summary | Both | Yes - direction is read from past-loss row |
| 2544-2545 | `_setup_suffix = " (COUNTER-TRADE - opposite to structural bias)" if _is_counter else ""` and `_trade_dir = pkg.xray.trade_direction or "n/a"` | Per coin compact render | Both | Yes |
| 2667 | `f"direction {pkg.signals.direction}"` | Per coin compact signal | Both | Yes |
| 2696 | `f"trend_dir={getattr(rs, 'trend_direction', 0):+d}"` | Per coin compact regime line | Both | Yes |

Per-coin block verdict: Direction-symmetric. All trade direction context per-coin is rendered from upstream data.

### `_build_context_prompt` (dead helper, lines 1033-1700)

Contains the dead twin block plus a similar REGIME DIVERGENCE block (1262-1265) plus a `_build_regime_instructions` invocation at line 1084. All unreachable in production.

| Line | Quote | Status |
|---|---|---|
| 1084-1086 | `regime_instructions = self._build_regime_instructions(_regime_str, _regime_confidence, _fear_greed_value)` | Dead - method itself is dead |
| 1263-1265 | `f"  Trade these coins WITH their individual regime direction, NOT against it.\n  Do NOT short a coin that is individually in an uptrend.\n  Do NOT buy a coin that is individually in a downtrend."` | Dead - symmetric, same as 3227-3229 in the live builder |
| 1336-1351 | RR_DIR LONG/SHORT comparator | Dead duplicate of `_build_trade_prompt`'s equivalent at 3308-3320 region |
| 1357 | `line += f"FVG={nf.direction}..."` | Dead |
| 1361 | `line += f"OB={no.direction}..."` | Dead |
| 1416-1435 | Asymmetric MARKET REGIME block (DEAD duplicate, byte-for-byte match of 3371-3390) | Dead |
| 1459-1465 | `f"{t.get('apex_original_direction', '?')}->{t['direction']}"` then `"not original thesis direction."` | Dead |
| 1560-1561 | `"Sell"`, `"Short"` strings | Dead - inside dead-helper block |
| 1609-1610 | Recently-closed render `f"  {sym} {direction}: {remaining}s remaining"` | Dead |
| 1624 | `f"{h.get('direction', '?')} score=..."` | Dead - duplicate of 3425 |
| 1648 | `f"    {sym}: {data['buy']} buy / {data['sell']} sell"` | Dead - duplicate of 3449 |

### `_build_position_prompt` (live CALL_B user-prompt builder)

| Line | Quote | Trigger | Mirror? | Symmetric? |
|---|---|---|---|---|
| 3825 | `sections.append(f"## MARKET REGIME: {self._last_regime_str} ({self._last_regime_confidence:.0%})")` | Always | Single factual line | Yes - direction-agnostic |
| 3828 | `sections.append(f"## SENTIMENT: Fear & Greed = {self._last_fg_value}")` | Always | Yes | Yes |
| 3857-3866 | `buy = pd.get("Buy") or ...; sell = pd.get("Sell") or ...; bw, bl = ...; sw, sl_ = ...; buy_wr = ...; sell_wr = ...` | When per-direction data exists | Both | Yes |
| 3867-3876 | `buy_lbl = f"Longs {bw}W/{bl}L ({buy_wr:.0f}% WR)" ...; sell_lbl = f"Shorts {sw}W/{sl_}L ({sell_wr:.0f}% WR)" ...` | Both directions | Yes | Yes |
| 3877-3879 | `sections.append(f"## TODAY DIRECTION PERF: {buy_lbl} | {sell_lbl}")` | When emit_direction_perf_in_callb (default True) AND total > 0 | Both | Yes - shows BOTH Longs and Shorts side-by-side |
| 3899-3919 | CONTRACT - POSITION MANAGEMENT block (lines 3899-3919): aim, hold rules, close criteria | Always | Direction-agnostic | Yes |
| 3915 | `"- The original thesis text - the system may have flipped direction; trust the current state shown above.\n"` | Always | Both | Yes |
| 3946 | `side_val = pos.side.value ...` | Per position | Both | Yes |
| 3953 | `if side_val in ("Sell", "Short"): pnl_pct = -pnl_pct` | Per position | Both | Yes - mechanical sign flip |
| 3991-3994 | `if side_val in ("Buy", "Long"): moved = max(0.0, pos.entry_price - pos.mark_price) else: moved = max(0.0, pos.mark_price - pos.entry_price)` | Per position | Both | Yes |
| 3999 | `f"\n### {symbol} [{side_val}]\n..."` | Per position | Both | Yes - data-driven render |
| 4018-4029 | XRAY FLIP block: `_chosen_rr = _rr_short if _is_sell else _rr_long; _rejected_rr = _rr_long if _is_sell else _rr_short` | When position flipped via XRAY | Both | Yes |
| 4035-4041 | APEX/legacy flip block | When position flipped via APEX | Both | Yes |
| 4095-4096 | RECENTLY CLOSED `f"  {sym} {direction}: {remaining}s remaining"` | When reentry cooldowns active | Both | Yes |

CALL_B verdict: Symmetric. No directive language. No asymmetric framing. The block at 3825 ("## MARKET REGIME:" then regime name + confidence) is purely factual, with no per-direction guidance attached.

### `_build_regime_instructions` (dead method, lines 4155-4251)

Lines already enumerated in the dead-code verification section above. All references unreachable in production.

### `_build_direction_performance` (dead helper, lines 4253-4350)

Defined at line 4253. Only caller is `_build_context_prompt:1094` (also dead). The body computes BUY/SELL WR over the last 20 trades and emits:
- Line 4294-4295: `f"BUY DIRECTION FAILING: {buy_wr:.0%} win rate over {buy_total} trades (${buy_pnl:+.2f}). BUY underperforming - lean SHORT this cycle. Reduce BUY size by 50%."`
- Line 4312-4313: symmetric `"SELL DIRECTION FAILING: ... lean LONG this cycle. Reduce SELL size by 50%."`
- Line 4329: `"  RECOMMENDATION: BUY is outperforming SELL. Favor LONG setups."`
- Line 4331: symmetric `"  RECOMMENDATION: SELL is outperforming BUY. Favor SHORT setups."`

The dead helper IS direction-symmetric. It is also a recency-bias instrument the operator already stripped via the aggressive-framing rewrite of 2026-05-05 (see comment at lines 2917-2926).

### Other direction-mentioning sites (mechanical only - listed for completeness)

| Line | Purpose | Symmetric? |
|---|---|---|
| 706, 910 | Log STRAT_DIRECTIVE direction rendering | Yes |
| 1247 | Comment | N/A |
| 4394 | `if side_val in ("Sell", "Short")` | Yes |
| 4456, 4510 | `direction=directive.get("direction", "both")` | Yes |

## Other asymmetric sites in live CALL_A path (if any)

A scan of every direction-mentioning line in `_build_trade_prompt` confirms the only asymmetric direction language in the live CALL_A user prompt is the 3371-3390 block.

Specifically:
- The `## SENTIMENT` block at lines 3360-3365 only emits the F&G value and classification. No direction-related text.
- The `## REGIME DIVERGENCE` block at lines 3224-3229 is symmetric: it explicitly mirrors "Do NOT short a coin that is individually in an uptrend" with "Do NOT buy a coin that is individually in a downtrend".
- The `## STRATEGY HINTS` block at lines 3416-3453 renders strategy outputs directly from the strategy module. Direction-agnostic.
- The per-coin TRADE CANDIDATES block (rendered by `_format_packages_for_prompt` ~lines 1900-2700) renders XRAY direction, signal direction, and vote distribution directly from upstream data. No asymmetric framing.
- The Global regime factual line at lines 2907-2911 is symmetric: `f"Global regime: {_regime_str} (confidence={_regime_confidence:.0%}, Fear & Greed={_fear_greed_value})"`.
- The MARKET DATA block at lines 3105-3210 emits per-coin price, RSI, MACD, ADX, and the optional per-coin regime tag `[{_cr.regime.value.upper()} {_cr.confidence*100:.0f}%]`. Direction-agnostic.
- The CONSENSUS PER COIN block at lines 3445-3451 shows both `data['buy']` and `data['sell']` counts.
- The ACCOUNT, Per-trade size limit, FUND RULES, URGENT WATCHDOG ALERTS, X-RAY STRUCTURAL SETUPS blocks: no direction-asymmetric text.

The system-prompt side (TRADE_SYSTEM_PROMPT / TRADE_SYSTEM_PROMPT_ZERO_TWO) carries minor F&G-section asymmetry (lines 91-95 cited in the table above), but is symmetric on regime-by-direction guidance.

## CALL_B audit (any asymmetry there)

Reading `_build_position_prompt` lines 3815-4153 in full, no asymmetric direction text is present.

The MARKET REGIME line at `strategist.py:3825` is a single factual emit:

```
3825        sections.append(f"## MARKET REGIME: {self._last_regime_str} ({self._last_regime_confidence:.0%})")
```

There is no `direction_hint` dict, no conditional NOTE, no "DEFAULT SELL BIAS" framing. The header text is "## MARKET REGIME:" (with a colon) - NOT the directive-flavoured "## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)".

The TODAY DIRECTION PERF line at lines 3877-3879 emits BOTH Longs and Shorts side-by-side with WR and counts. Direction-symmetric by construction.

The CONTRACT - POSITION MANAGEMENT block at lines 3899-3919 is direction-agnostic.

The per-position render at lines 3998-4005 emits side, entry, mark, PnL, SL, TP, Lev, Age, Regime, SL consumed - all direction-symmetric data fields with no directive language.

The FLIPPED notice at lines 4014-4045 renders `_chosen_rr` and `_rejected_rr` symmetrically depending on `_is_sell`.

POSITION_SYSTEM_PROMPT at lines 163-179 (constant applied to every CALL_B):
- Aim text: "maximize the development of each position" - direction-agnostic.
- Rules 1-7 are direction-agnostic.
- Rule 5 explicitly forbids closing on regime-alignment alone.

CALL_B verdict: Clean. No asymmetric direction text in either the user-prompt or the system-prompt.

## Boot sentinel verification (STRAT_AGGRESSIVE_FRAMING false advertising)

The boot sentinel is emitted at `strategist.py:870-876`:

```
870            log.info(
871                f"STRAT_AGGRESSIVE_FRAMING | mode_line=skipped "
872                f"coaching=skipped fund_rules=minimal "
873                f"today_perf=skipped dir_perf=skipped "
874                f"regime_instr=minimal contract=aggressive_exploit "
875                f"zero_two_flag={_zero_two} | {ctx()}"
876            )
```

Live-log evidence from `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/brain.log`:
- 37 emissions on 2026-05-18 between 10:18:03 and 15:30 UTC (matches the prior report's audited window).
- All 37 emissions contain `regime_instr=minimal contract=aggressive_exploit zero_two_flag=True`.
- The prior report cited 36; actual count is 37 (one extra emission). Minor count discrepancy, not material.
- Sample line (timestamp 10:18:03): `2026-05-18 10:18:03.640 | INFO | src.brain.strategist:create_trade_plan:870 | STRAT_AGGRESSIVE_FRAMING | mode_line=skipped coaching=skipped fund_rules=minimal today_perf=skipped dir_perf=skipped regime_instr=minimal contract=aggressive_exploit zero_two_flag=True | did=d-1779099483160`

The `create_trade_plan:870` source-line reference in every emission confirms the sentinel fires inside `create_trade_plan` (the live CALL_A entry point), AFTER the prompt build at line 823 and BEFORE the Claude send at line 878.

Live behaviour cross-check:
- The sentinel emits once per CALL_A cycle.
- `_build_trade_prompt` (line 823) runs inside the same try block, so the asymmetric block at 3371-3390 is emitted into the same prompt for which the sentinel reports `regime_instr=minimal`.
- The sentinel value `regime_instr=minimal` refers semantically to: "the `_build_regime_instructions()` helper is no longer called" (commit 29f5e31, 2026-05-05). That part is TRUE: the dead method is in fact not called in `_build_trade_prompt`. See `strategist.py:2889-2905` comment block confirming the intent.
- BUT the sentinel does NOT reflect that the asymmetric MARKET REGIME block at 3371-3390 inside `_build_trade_prompt` is still emitted on every CALL_A. An operator reading the sentinel would reasonably interpret `regime_instr=minimal` as "the regime-related directive instructions have been minimised in the prompt", which is FALSE - the asymmetric directive block at 3371-3390 carries the operative directive (header text "CONTROLS YOUR TRADE DIRECTION", asymmetric `direction_hint` dict, trending_down-only NOTE).

Boot sentinel verdict:
- The sentinel fires on every CALL_A as the prior report claimed. Verified 37/37 emissions on 2026-05-18.
- The value `regime_instr=minimal` is technically accurate (the dead method is not called) but functionally misleading: a reader assumes the asymmetric regime directives are absent from the prompt when in fact they are present via the live block at 3371-3390. The prior report's "false advertising" characterisation is fair.

## Discrepancies vs prior report

| Item | Prior report claim | Actual finding | Resolution |
|---|---|---|---|
| STRAT_AGGRESSIVE_FRAMING count on 2026-05-18 | 36 emissions | 37 emissions in brain.log | Minor; same order of magnitude. Either the prior report rounded down or had a slightly narrower audit window. Functionally identical. |
| DL_DECISION type=call_a count | 36 (matches STRAT_AGGRESSIVE_FRAMING) | 34 in `workers.2026-05-18_*.log`, 0 in `brain.log` | The DL_DECISION counts come from a different log stream (workers). 34 vs 37 might reflect 3 CALL_A cycles that failed before reaching the DL_DECISION emission point. Not contradictory; just a different log surface. |
| 3371-3390 location | Asymmetric block at lines 3371-3390 | Block occupies exactly 3371-3390 inclusive | Confirmed exactly |
| 1416-1435 location | Dead duplicate at lines 1416-1435 | Duplicate occupies exactly 1416-1435 inclusive | Confirmed exactly |
| 4155-4251 location | Dead method at lines 4155-4251 | Method definition at 4155, return at 4251 | Confirmed exactly |
| `_build_regime_instructions` callers | Only `_build_context_prompt:1084` (test harness only) | Same | Confirmed |
| `create_strategic_plan` callers | Only test harness (`scripts/run_30min_test.py:76, 106`) | Same | Confirmed - production layer_manager dispatches `create_trade_plan` not `create_strategic_plan` |
| Header text | `"## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)"` | Same verbatim, three sites in strategist.py (line 398 marker, 1416 dead, 3371 live), seven sites in test files | Confirmed |
| direction_hint dict entries | Five entries: trending_down/up/ranging/volatile/dead | Same | Confirmed |
| trending_down-only NOTE | Fires only when conf > 0.60 AND _regime_str == "trending_down" | Same | Confirmed - no `elif _regime_str == "trending_up":` branch |
| Regime distribution on 2026-05-18 | 1,882 trending_down vs 85 trending_up (22x) | 1,789 trending_down vs 85 trending_up (21x) in `workers.*` log lines | Order-of-magnitude match. The prior report's count likely came from a slightly different log filter scope. |
| Asymmetric F&G branches in dead method | Prior report's RC-4.5 notes only one missing branch (trending_up + F&G 20-40) | Confirmed: trending_down branches into F&G<20 AND F&G<40; trending_up branches into F&G>80 AND F&G<20; no trending_up + F&G 20-40 mirror | Confirmed |

No claim in the prior report's Section 5 is contradicted by this validation.

## New findings

Findings that the prior report did not explicitly call out but this validation surfaced:

1. The dead duplicate at `strategist.py:1416-1435` is BYTE-FOR-BYTE identical to the live block at `strategist.py:3371-3390`. Any fix to the live block must also fix the dead duplicate for code hygiene (already noted in Option 4.1 of the prior report; this validation confirms there are no behavioural differences between the two blocks).

2. The trim-marker tuple at `strategist.py:398` includes `"## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)"`. This means any header rename in the fix (e.g. to `"## MARKET REGIME (CONTEXT)"` per Option 4.1) MUST also update the marker tuple, otherwise the priority-aware trim will drop the renamed block as OPTIONAL when the 30K-char cap fires. This validates the prior report's instruction at line 657 ("strategist.py:397-398 - update `_TRIM_ESSENTIAL_MARKERS` to match new header text"). 

3. The same header text is asserted by SEVEN test files in `tests/test_stage2_phase4/test_priority_trim_inline.py` (lines 121, 208, 349, 438, 517) and `tests/test_stage2_phase4/test_priority_classifier.py` (lines 46, 373, 383). Any header rename will need to update those tests. The prior report's Trade-offs note ("Test markers in `tests/test_stage2_phase4/test_priority_trim_inline.py` and `:test_priority_classifier.py` need updating") is correctly scoped at "5 lines + 3 lines"; the actual count is 5 trim_inline mentions + 3 priority_classifier mentions = 8 total. Matches.

4. The system-prompt side TRADE_SYSTEM_PROMPT carries minor F&G-section asymmetry at lines 91-95 - "Ranging + fear = buy near support levels" example is given without a mirror "Ranging + greed = sell near resistance" line; "Extreme greed (F&G > 80): take profits on longs, look for short entries" lacks a mirror "Extreme fear: take profits on shorts, look for long entries". Low-severity textual asymmetry the prior report did not catch. ZERO_TWO contract (lines 322-323) is cleaner.

5. `_build_direction_performance` (the dead helper at `strategist.py:4253-4350`) is direction-symmetric: it emits balanced "BUY DIRECTION FAILING" and "SELL DIRECTION FAILING" lines based on data. Even if revived, this dead helper would not introduce direction-asymmetric framing.

6. The 37 STRAT_AGGRESSIVE_FRAMING emissions on 2026-05-18 all have `zero_two_flag=True`. This confirms the production runtime uses TRADE_SYSTEM_PROMPT_ZERO_TWO (lines 298-357), NOT the legacy TRADE_SYSTEM_PROMPT (lines 66-142). Any fix that touches the system prompt must edit the ZERO_TWO variant; the legacy variant is dead behaviourally.

7. The CALL_B builder `_build_position_prompt` is clean of asymmetric direction text. No fix work needed on the CALL_B path for direction symmetry; the only direction asymmetry in the live runtime is in `_build_trade_prompt:3371-3390`.

8. The dead `_build_context_prompt` (lines 1033-1700) also contains a REGIME DIVERGENCE symmetric block at lines 1262-1265 - identical structure to the live one at 3225-3229. Confirming no behaviour difference between dead and live paths beyond the asymmetric block.

9. The comment at `strategist.py:2844` ("the dead `_build_context_prompt` at line 759 also calls it") has a stale line pointer. The actual call site for `_build_context_prompt` is line 683 inside `create_strategic_plan`, not line 759. Cosmetic; not a behaviour issue.

10. The CLAUDE.md project rule (mandatory grep-before-touch) is fully consistent with the recommended fix approach: any edit to the live block at 3371-3390 must be cross-checked against the duplicate at 1416-1435, the trim marker at line 398, the test files (8 lines), and the boot sentinel at line 870 to avoid creating a false-advertising mismatch.

## Verdict per claim

| Claim | Verdict | Evidence |
|---|---|---|
| Asymmetric MARKET REGIME block at 3371-3390 inside `_build_trade_prompt` | CONFIRMED | Direct read of strategist.py:3370-3390. Block emits header + dict + conditional trending_down NOTE on every CALL_A. |
| `_build_regime_instructions` at 4155-4251 is dead (never called from CALL_A) | CONFIRMED | Grep across entire repo shows only caller is `_build_context_prompt:1084`. `_build_context_prompt` itself is only called from `create_strategic_plan:683`. `create_strategic_plan` is only invoked by `scripts/run_30min_test.py:76, 106` (manual test harness). Production `layer_manager._run_brain_cycle` dispatches `create_trade_plan` (line 770) and `create_position_plan` (line 938), NOT `create_strategic_plan`. |
| Duplicate at 1416-1435 is dead | CONFIRMED | Same caller-chain proof. The duplicate lives inside `_build_context_prompt`, which is dead. |
| `STRAT_AGGRESSIVE_FRAMING | regime_instr=minimal` log emitted on every CALL_A | CONFIRMED | brain.log shows 37 emissions on 2026-05-18; all carry `regime_instr=minimal contract=aggressive_exploit zero_two_flag=True`. Source-line reference in every emission is `strategist.py:870`. Sentinel fires inside `create_trade_plan`. |
| Sentinel is "false advertising" with respect to the operative regime block | CONFIRMED with nuance | The sentinel is technically accurate (the dead `_build_regime_instructions()` helper is in fact not invoked) but functionally misleading (the asymmetric regime block at 3371-3390 IS emitted into the same prompt the sentinel describes as `regime_instr=minimal`). Either the sentinel value should be updated to truthfully describe state, or the asymmetric block at 3371-3390 should be reframed. The prior report's Option 4.5 wraps both. |

All four main claims of the prior report's Issue 4 are validated. The implementation-fact summary in Sections 5.1, 5.2, and 5.4 of the prior report is accurate.

## Implications for fix-path decision

This Phase 1.4 validation confirms that the fix surface for Issue 4 is small and well-scoped:

1. The live edit surface is `strategist.py:3371-3390` (the only asymmetric direction text in the live CALL_A user prompt). Approximately 10 lines of textual change per Option 4.1.

2. The dead duplicate at `strategist.py:1416-1435` should be edited identically for code hygiene (no production behaviour change since the path is dead). Same approximately 10-line textual change.

3. The trim marker at `strategist.py:398` must be updated in lock-step with any header rename.

4. The boot sentinel at `strategist.py:870-876` should be updated under Option 4.5 to truthfully describe the new state (e.g. swap `regime_instr=minimal` for an explicit `regime_block_mode=symmetric_scenario` or similar). This closes the false-advertising observability gap.

5. The dead method at `strategist.py:4155-4251` could either be edited symmetrically (same operator-directive intent, code hygiene) OR be deleted entirely as part of a follow-up garbage-collection pass. The prior report's Option 4.1 implies "Apply the same trio of edits at the dead-path duplicate `strategist.py:1416-1435`" but does not explicitly mandate touching the 4155-4251 method - this validator agrees the dead method can be left for a separate hygiene pass since it carries no live behaviour.

6. Tests to update: 5 lines in `test_priority_trim_inline.py` + 3 lines in `test_priority_classifier.py` = 8 lines for header rename. Plus any test-side direction-asymmetry assertions if added by the fix.

7. CALL_B path (`_build_position_prompt`) requires NO fix-side changes for direction symmetry. The CALL_B contract block, regime line, sentiment line, direction-perf line, and position render are all symmetric.

8. System-prompt side: TRADE_SYSTEM_PROMPT_ZERO_TWO (lines 298-357) is the live constant. TRADE_SYSTEM_PROMPT (lines 66-142) is the legacy variant - not in the production runtime (confirmed by `zero_two_flag=True` in 37/37 boot sentinels). Any fix to system-prompt directional asymmetry (the minor F&G example asymmetries flagged in the table above) should focus on ZERO_TWO; the legacy variant can be left for hygiene.

9. Risk assessment supports the prior report's LOW risk rating for Option 4.1 + 4.5:
   - Edit surface is approximately 30 lines across 4-5 sites.
   - Behaviour change is text-only (no logic, no settings).
   - Reversible via git revert.
   - No new failure mode introduced.
   - Test impact is bounded (8 marker-assertion lines).

10. Cross-issue dependency: As the prior report notes at line 700-701, Option 4.1 alone does not eliminate the upstream direction bias coming from Issue 1 (XRAY rr_long collapse near resistance), Issue 2 (counter ×0.7 multiplier), and Issue 3 (scanner regime AND-gate / labeller). The four fixes are independently shippable; Issue 4 is the cheapest and lowest-risk but addresses only the prompt-side asymmetry. The other three carry the structural-bias load.

The validation supports proceeding to Phase 2 implementation with the prior report's Issue 4 recommendation (Option 4.1 symmetric scenario-driven direction hints + Option 4.5 versioned boot sentinel) as the planned fix shape. No fact-base correction is required before implementation.
