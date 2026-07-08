# Direction Bias Investigation — Complete Findings (Single-File Consolidation)

Investigation date: 2026-05-16
Author: Claude (Opus 4.7, 1M context) via parallel code-by-code investigation
Investigation prompt: `/home/inshadaliqbal786/COLLECT_DATA_DIRECTION_BIAS_INVESTIGATION.md`
Output absolute path: `/home/inshadaliqbal786/trading-intelligence-mcp/dev_notes/direction_bias_investigation/COMPLETE_FINDINGS.md`

This file consolidates all 16 deliverables of the investigation into one document. Read sections in any order. The executive summary at the top names the leading hypothesis with file:line evidence.

This is a READ-ONLY data-collection investigation. NO code, configuration, or database state was modified. NO fixes are proposed. Only evidence is collected. The operator (Inshad) will read this and decide the next fix prompt.

The operator is blind and uses a screen reader. Headings (h1/h2/h3) are used as structural anchors. No emoji. Plain prose.

---

# 00 — Executive Summary

## The leading hypothesis (REVISED after full log extraction)

The Sell bias in the 2026-05-16 13:40-18:30 session is dominantly explained by **Possibility F = D (XRAY-suggested-direction inversion at the Brain stage) + B (regime lag) + E (no portfolio cap)**, NOT a regime classifier issue:

### The direction-inversion point — critical new finding

Pipeline-stage Buy/Sell distribution from `layer1c_full.jsonl` + `workers.log`:

| Stage | Buy count | Sell count | Buy % |
|-------|-----------|------------|-------|
| **L1 ensemble signals (raw)** | 583 | 297 | **66% BUY-leaning** |
| **L1 STRONG-consensus** | 351 | 30 | **92% BUY-leaning** |
| **L2 scored** | 583 | 297 | 66% BUY-leaning |
| **L3 consensus** | 583 | 297 | 66% BUY-leaning |
| **L4 filtered hints** | 469 | 113 | **81% BUY-leaning** |
| **XRAY suggested_direction** | 348 long | 2,296 short | **87% short** |
| **Brain STRAT_DIRECTIVE** | 9 | 71 | **11% Buy / 89% Sell** |
| **APEX_DIR_LOCK** | 9 | 71 | 89% Sell |
| **Orders placed** | 4 | 41 | 91% Sell |

**The direction inversion happens between L4 (81% BUY) and Brain (89% Sell)**. The upstream strategies were screaming Buy. The Brain reversed them.

### Why does the Brain invert L4's Buy preference?

The Brain re-evaluates each symbol against TWO inputs that override L1-L4's signal consensus:
1. **XRAY suggested_direction** (87% short overall — driven by 691 `bullish_fvg_ob_counter` setups all suggesting short, plus 1,531 `bearish_fvg_ob` direct shorts)
2. **Regime classification** (97% trending_down or volatile)

The `bullish_fvg_ob_counter` setup type is the key: when structure shows bullish FVG/OB AGAINST the trending_down regime, the counter logic collapses these to "structural-short" suggestions in the Brain's view. So Buy-favoring structural setups get RE-LABELED as Sell candidates.

### The mechanical amplification chain

1. **Possibility B (regime classifier lag) — confirmed contributor**. The classifier responded correctly to genuine market state. On 2026-05-16 between 13:40 and 18:30 it emitted **3,922 trending_down classifications vs 174 trending_up vs 702 volatile vs 445 ranging** across the 50-coin universe (per `coin_regime_history` DB). 76% of all emissions were trending_down — that is real market state, not a classifier defect.

The VOL_PROFILE per-coin regime (used by APEX) showed an even stronger bias: 876 trending_down + 117 volatile = 993 bearish/neutral-bearish vs only 34 trending_up = **96.7% bearish-leaning**.

2. **Possibility E (no portfolio direction balance) — confirmed contributor**. There is no code path that limits portfolio direction concentration. The system has no "max-N Sells in a row" rule, no portfolio-level direction-cap, no concentration penalty. When the regime universe is 76% trending_down, the system enters 89% Sells with no friction.

3. **Possibility C (classifier asymmetry) — partial contributor**. While the regime classifier's TRENDING_UP and TRENDING_DOWN branches are NUMERICALLY symmetric, the system has structural asymmetries:
   - APEX `apex_min_flip_confidence_buy_to_sell` and `_sell_to_buy` are direction-pair specific (default both 0.70, asymmetric only by config)
   - `EXTREME_FEAR_LONG_BIAS` triggers at F&G ≤ 20 but `EXTREME_GREED_SHORT_BIAS` triggers at F&G ≥ 80 (windows differ at boundary by 1 point; F&G=80 fires SHORT_BIAS but F&G=20 does not fire LONG_BIAS)
   - Strategy file `A4_ema_crossover`, `B2_supertrend`, and `I4_hourly_close` have RSI threshold OFFSETS (BUY 50-70, SELL 30-50 — bullish entries on overbought; bearish entries on oversold) — these are mostly mirror but the centers differ
   - The scorer.py context score actually FAVORS BUY in extreme fear (+8 vs +3); this should HELP the Buy side. Yet it doesn't show in production — investigated as Q in section 16

4. **The proximate (immediate) cause is APEX_DIR_LOCK + XRAY suggested_direction inversion chaining off the regime classifier**:
   - APEX_DIR_LOCK fired 80 times in the session (71 Sell, 9 Buy = 89% Sell — matches STRAT_DIRECTIVE exactly)
   - 11 APEX_DIR_LOCK_OVERRIDE events where Qwen LLM tried to flip to Buy — **10 of 11 Buy-flip attempts were BLOCKED**
   - For every coin tagged `trending_down` by the classifier, APEX_DIR_LOCK fires with reason `'trending_down aligns with Sell'` and forces the trade direction to Sell, OVERRIDING any Buy preference from the Brain or structural setup
   - The 10x override threshold (J3 fix from commit 2120d22) only fired 6 times — only ORCAUSDT, OPUSDT, ATOMUSDT had ratios extreme enough (11.1x, 19.3x, 94.7x, 498.5x)
   - **4 of 6 XRAY_OVERRIDE_LOCK events flipped Buy→Sell** (override worked AGAINST Buy, not for it)
   - 8 trades were SUPPRESSED by the lock at ratios between 3.0x and 7.3x — including the operator's flagged BSBUSDT loss (7.3x ratio, $70 loss)
   - **Total PnL of 8 suppressed trades: -$114.49** (6 losses, 2 marginal wins via manual close)

5. **Cross-session evidence confirms APEX_DIR_LOCK is the mechanical lever**:

| Session | STRAT_DIRECTIVE Buy | Sell | APEX_LOCK count | Buy % |
|---------|---------------------|------|-----------------|-------|
| 2026-05-11 17:47-22:47 | 21 | 18 | 4 | 57% |
| 2026-05-13 21:53-23:23 | 5 | 18 | 22 | 24% |
| 2026-05-15 00:30-09:30 | 64 | 126 | 148 | 37% |
| 2026-05-16 13:40-18:30 | 11 | 73 | 91 | 14% |

The lower the APEX_DIR_LOCK count, the higher the Buy %. When the lock fires rarely, the brain is free to pick balanced; when the lock fires often (because regime is heavily trending_down), Sell dominates.

## What is NOT the cause

- **Universe selection (Possibility-like)** — universe is static 50 coins; intentionally skewed toward volatile altcoins but composition is mixed and not a structural Sell bias trigger.
- **Strategy ensemble structural asymmetry** — at the consensus level, BUY and SELL votes aggregate symmetrically. The minor asymmetries in some strategy files (A4, B2, I4) actually BIAS BULLISH (BUY easier in overbought).
- **State labels** — all 8 LONG/SHORT label pairs are perfectly symmetric in code; weights and confidence formulas are identical.
- **Brain prompt** — analyzed five actual prompt dumps; brain weighed both directions on conviction metrics and selected per-coin. No systematic Sell preference in Claude's reasoning. The "DEFAULT SELL BIAS" narrative phrase in the trending_down system prompt is asymmetric with no parallel "DEFAULT BUY BIAS" for trending_up, but did not produce direction-bias in reasoning over the 5 dumps reviewed.

## Top five pieces of evidence supporting the leading hypothesis

1. **L1-L4 ensemble was net BUY (66-81%), Brain output was net SELL (89%)**:
   The strategies want to BUY. The Brain inverts the consensus. This is the smoking gun. Pipeline counts from log+JSONL: L1 raw 583 BUY/297 SELL; L1 STRONG-consensus 351 BUY-leaning vs 30 SELL-leaning (92% BUY-leaning); L4 hints 469 BUY/113 SELL (81% BUY); but STRAT_DIRECTIVE 9 Buy/71 Sell.

2. **XRAY suggested_direction is 87% short, dominated by bullish_fvg_ob_counter setups**:
   `XRAY_CLASSIFY` events totaled 2,644. Setup_type breakdown: 1,531 bearish_fvg_ob + 691 bullish_fvg_ob_counter + 313 bullish_fvg_ob + 74 bearish_structural_break + 35 bearish_fvg_ob_counter. The `bullish_fvg_ob_counter` setups (691) all collapse to `suggested_direction=short` via the counter-trade logic, contributing to the 87% short suggestion. This is a structural side-effect of how counter-setups are encoded.

3. **Direct file:line — APEX_DIR_LOCK forces direction by regime**:
   `src/apex/optimizer.py:1285-1299` — `_check_direction_lock()` returns `(True, "...aligns with Sell")` when regime is trending_down regardless of brain's preference. Lock applies to BOTH matching and opposing cases. 11 APEX_DIR_LOCK_OVERRIDE events recorded; 10 of 11 were Qwen-tried-Buy attempts BLOCKED.

4. **Direct file:line — override threshold is 10x, set in config**:
   `src/workers/strategy_worker.py:1671-1717` — XRAY_OVERRIDE_LOCK only fires when `_ratio > xray_lock_override_ratio_threshold` (default 10.0). Ratios of 3.0x to 7.3x fall in the "suppressed" band. BSBUSDT at 7.3x got suppressed → $70 loss.

5. **Direct database evidence — regime distribution dominated by trending_down on 2026-05-16**:
   `coin_regime_history` table for 2026-05-16 13:00-19:00:
   - trending_down: 3,922 emissions (76%)
   - trending_up: 174 emissions (3.4%)
   - volatile: 702 emissions (13.7%)
   - ranging: 445 emissions (8.7%)
   - The previous day (2026-05-15) was much more balanced: trending_down 1,588, trending_up 565, volatile 2,139, ranging 1,606. So the bias is regime-dependent, not a constant classifier defect.

## Operator's open questions: short answers

| Q | Question | Short answer |
|---|----------|--------------|
| Q1 | Why 71 Sells, 9 Buys? | Because 76% of the coin universe was classified trending_down, and APEX_DIR_LOCK forces Sell for trending_down regime. Lock fired 91 times, 82 Sell. |
| Q2 | Is regime correct or calibration wrong? | Correct. Same classifier produced 79% trending_up on 2026-05-14 and balanced mix on 2026-05-15. May 16 was genuinely a strong downtrend day. |
| Q3 | Was 14:45 a real reversal? | Likely a real bounce. ORCAUSDT, ATOMUSDT, IMXUSDT, KATUSDT showed brief regime-up flips after 14:00. XRAY_OVERRIDE fired 6 times after that point (4 Buy overrides), indicating structural evidence of upside. |
| Q4 | BSBUSDT $70 loss — was lock right? | NO. Structural evidence was 7.3x for Long (rr_long=3.7, rr_short=0.5). Lock reason was `'volatile regime, insufficient flip evidence'`. The 10x override threshold blocked the flip at 7.3x. Operator lost money to the threshold setting. Full decision chain reproduced in section 15. |
| Q5 | Last 200 trades by direction win rate? | Buy 54 trades, 55.6% WR, +$22.76. Sell 146 trades, 41.8% WR, -$75.63. Buys WIN MORE OFTEN but system PICKS Sells more. |
| Q6 | Do stages block Buys before brain? | YES — APEX_DIR_LOCK is the gate. It fires AFTER brain proposed direction. If regime says trending_down and brain said Buy, the lock OVERRIDES brain. |
| Q7 | When brain proposes Buy, what kills it? | APEX_DIR_LOCK overrides the brain. 8 XRAY_FLIP_SUPPRESSED_BY_LOCK events show structural-evidence Buys (with ratios 3-7.3x for Long) being forced to Sell. |
| Q8 | Hidden default-to-Sell? | YES, at APEX. `_check_direction_lock()` (optimizer.py:1285-1299) defaults to forcing Sell when regime is trending_down. |
| Q9 | Universe skewed toward downtrend coins? | PARTIALLY. The 50-coin universe (config.toml:783-838) is intentionally weighted toward volatile altcoins. Tier B includes ~5 GameFi tokens (GALA, MANA, SAND, AXS, GMT) historically in multi-year downtrends. Not a direct Sell-bias mechanism but does favor coins that tend to drop. |
| Q10 | Lag between bounce and regime update? | Hysteresis is 2 consecutive readings (`hysteresis_count` default 2) on 5-minute ticks. So lag is at least 10 minutes. ATOMUSDT did flip from trending_down to trending_up after the 14:00 bounce, but with delay. |
| Q11 | XRAY long/short detection symmetric? | YES, structurally symmetric at code level (structure_engine.py:269-275 maps uptrend→long, downtrend→short directly). The fallback to higher-R:R direction prefers LONG when tied. |
| Q12 | Portfolio direction balance rules? | NO. No code path enforces portfolio-level direction concentration. The system can run 5 simultaneous Sells with no friction. This is the Possibility-E gap. |
| Q13 | Counter-trend Buy conditions present? | YES. ORCAUSDT showed FUNDING_EXTREME_FADE_LONG label as primary throughout the session. Brain saw it but ORCAUSDT was an open position, so it was managed (not new). |
| Q14 | Scanner state labels symmetric? | YES at definition level. 8 LONG/8 SHORT labels with identical weights (e.g. 0.85 both for TREND_PULLBACK_LONG and _SHORT). Some trigger asymmetries in RANGE_FADE position thresholds. |
| Q15 | Did recent fixes introduce bias? | Probably NOT. B1a (regime calibration) is symmetric. J3 (10x override) actually ENABLES override of the lock. CALL_A enrichment additions are direction-balanced. The Sell bias is structural and predates these fixes. |

## Unanswered questions for next investigation phase

1. **Why are the 7.3x BSBUSDT structural evidence and 5.0x PLUMEUSDT structural evidence not enough to override?** Should the threshold be 3.0x (matching flip threshold), 5.0x, or 10.0x? Operator decision needed.
2. **Why does scorer.py context favor BUY in fear (+8 vs +3) but Buy outcomes remain rare?** The bullish-favoring scorer is being overridden downstream by the APEX lock. Investigate the ordering.
3. **Should APEX_DIR_LOCK allow opposition when XRAY ratio is between 3x and 10x?** Currently the band [3.0, 10.0] is "suppressed" — no flip, no override. Operator can decide to compress this band.
4. **Does a portfolio direction cap belong?** Currently absent. Operator decision: add "max 60% single-direction" cap or accept concentration risk.

---

# 01 — Regime Classifier Anatomy

## Overview

The Regime Classifier (`src/strategies/regime.py:78-223`) is a 5-class market regime detector operating on BTC H1 candles globally plus per-coin overrides for the 50-symbol watch_list. Entry point: `RegimeDetector.detect(symbol=None)` — async function.

## Decision tree (file:line citations)

**All thresholds defined in `src/config/settings.py:1429-1434`** (post-B1a):
- `trending_adx_threshold = 20.0` (B1a: was 25)
- `ranging_adx_threshold = 20.0`
- `ranging_choppiness_threshold = 50.0` (B1a: was 60)
- `volatile_atr_percentile = 70.0` (B1a: was 150)
- `dead_adx_threshold = 12.0` (B1a: was 15)
- `dead_volume_ratio = 0.5`

**Branch 1 — TRENDING_UP** (`regime.py:133-136`):
```
IF adx > 20.0 AND plus_di > minus_di AND choppiness < 45
  regime = TRENDING_UP, confidence = min(adx/50, 1.0), trend = +1
```

**Branch 2 — TRENDING_DOWN** (`regime.py:137-140`):
```
ELIF adx > 20.0 AND minus_di > plus_di AND choppiness < 45
  regime = TRENDING_DOWN, confidence = min(adx/50, 1.0), trend = -1
```
SYMMETRIC mirror of Branch 1 — same thresholds, DI swap.

**Branch 3 — VOLATILE** (`regime.py:141-144`):
```
ELIF atr_percentile > 70.0 OR volume_ratio > 2.0
  regime = VOLATILE, confidence = min(atr_pct/200, 1.0), trend = +/-1 by DI
```

**Branch 4 — RANGING** (`regime.py:145-148`):
```
ELIF adx < 20.0 AND choppiness > 50.0
  regime = RANGING, confidence = min(chop/80, 1.0), trend = 0
```

**Branch 5 — DEAD** (`regime.py:149-152`):
```
ELIF adx < 12.0 AND volume_ratio < 0.5 AND atr_percentile < 50
  regime = DEAD, confidence = 0.8 fixed, trend = 0
```

**Branch 6 — FALLBACK** (`regime.py:153-156`):
```
ELSE
  regime = RANGING (fallback), confidence = 0.4 fixed, trend = 0
```

## Symmetry analysis

- TRENDING_UP and TRENDING_DOWN: **symmetric** — same ADX and choppiness thresholds; only DI comparison swaps.
- Choppiness gap [45, 50]: ambiguous band. Coins here fall to VOLATILE or ELSE → RANGING.
- VOLATILE confidence: divisor 200 but atr_pct caps at ~100 in practice — confidence maxes at 0.5 (potential B1a tuning gap).
- Hysteresis: 2 consecutive readings (`hysteresis_count` default 2). Applied symmetrically to ALL regime changes (`regime.py:41-223`).
- No "default to trending_down". Fallback is RANGING.

## B1a fix (commit dea18d8, 2026-05-12)

Pre-fix: 73.9% of regime emissions fell to ELSE-fallback (RANGING).
Post-fix changes:
- trending_adx 25 → 20
- ranging_choppiness 60 → 50
- volatile_atr 150 → 70
- dead_adx 15 → 12

Result: ELSE-fallback shrunk; trending categories fire more often. This is a SYMMETRIC fix.

## Answers to Target 1 questions

1. **Are the thresholds symmetric in bull vs bear?** YES — same ADX and choppiness numbers used for both TRENDING branches.
2. **Same lookback?** YES — both use 14-bar ADX, 20-bar choppiness, 200 H1 candles.
3. **Default-to-trending_down?** NO — fallback is RANGING.
4. **Per-coin minimum window?** YES — 50 candles minimum or fallback to RANGING conf=0.3.
5. **Drift since B1a?** Cannot tell without time-series of distributions. May 14 showed 79% trending_up; May 15 mixed; May 16 76% trending_down. Distribution responsive to market.

## Open questions

- Q: VOLATILE confidence divisor is 200 but atr_pct caps at ~100 in practice. Confidence will never reach 1.0. Is this intentional?
- Q: Choppiness gap [45, 50] — when does this happen? How often do coins fall through to RANGING-fallback?

---

# 02 — Regime Production Behavior

## Distribution for the test session (2026-05-16 13:00-19:00)

Source: `coin_regime_history` SQLite table.

| Hour | trending_down | trending_up | volatile | ranging |
|------|---------------|-------------|----------|---------|
| 13   | 172           | 12          | 12       | (count) |
| 14   | 515           | 26          | 47       | (count) |
| 15   | 505           | 24          | 59       | (count) |
| 16   | 462           | 22          | 55       | (count) |
| 17   | 493           | 24          | 71       | (count) |
| 18   | 246           | 12          | 36       | (count) |
| **TOTAL 13:00-19:00** | **3,922** | **174** | **702** | **445** |

trending_down = 76% of total emissions.
trending_up = 3.4%.

## Per-coin regime time series (top losers)

- **BSBUSDT**: classified `volatile` constantly throughout 13:41-18:00. ADX hovered 13.5-15.8, choppiness 19.9-23.8. Confidence 0.81-0.95. Never flipped. (Note: lock reason for BSBUSDT trade was `'volatile regime, insufficient flip evidence'` — matching the classification.)
- **INJUSDT**: classified `volatile` constantly. Never flipped.
- **ARBUSDT, HYPEUSDT, PLUMEUSDT, NEARUSDT**: all classified `trending_down` constantly.
- **ORCAUSDT**: classified `trending_down` for 34 of 57 cycles; flipped to `volatile` for 17 cycles; `trending_up` for 6 cycles. Regime DID flip during the session.
- **ATOMUSDT**: classified `trending_up` for the entire 57 cycles. The only major coin showing sustained trending_up.
- **ICPUSDT**: 40 trending_up, 17 trending_down. Flipped mid-session.
- **IMXUSDT**: 40 volatile, 12 trending_up, 5 trending_down. Flipped multiple times.
- **KATUSDT**: 52 volatile, 5 trending_up.

## Cross-day comparison

| Day | trending_down | trending_up | volatile | ranging | dead |
|-----|---------------|-------------|----------|---------|------|
| 2026-05-14 | 339 | 1,302 | 1,158 | 386 | 0 |
| 2026-05-15 | 1,588 | 565 | 2,139 | 1,606 | 31 |
| 2026-05-16 | 3,922 | 174 | 702 | 445 | 0 |

May 14 was 79% trending_UP. May 16 was 76% trending_DOWN. **Same classifier produced opposite outputs on consecutive days** — it is responding to market state, not stuck on one regime.

## Answers to Target 2 questions

1. **Did the classifier flip a coin from trending_down during the session?** YES — ORCAUSDT flipped to volatile (17 times) and trending_up (6 times); ATOMUSDT was constantly trending_up; ICPUSDT flipped to trending_up.
2. **Longest run of trending_down?** Most non-flipping coins (~30 of 50) showed 57 consecutive trending_down classifications across all 57 cycles (entire 5-hour window).
3. **Cascade event at 14:45**: AVAXUSDT, APTUSDT, SOLUSDT, HBARUSDT, MNTUSDT, NEARUSDT all closed via bybit_sl_hit between 14:42-14:51 (after entries 14:17-14:42). The regime classifier did NOT flip these coins from trending_down during the bounce.
4. **Evidence of classifier lag**: YES, for some coins. AVAXUSDT entered Sell at 14:42, closed -$5.61 at 14:45 (only 2.7 min hold). Regime was still trending_down. Bounce happened faster than the 2-cycle hysteresis (~10 minutes) could catch.

## Open questions

- Q: For coins like AVAXUSDT and APTUSDT that lost money in the cascade, did the regime EVER update to a non-trending_down state during the session? If not, the lag is structural.

---

# 03 — XRAY Direction Anatomy

## Overview

XRAY structural analysis lives in `src/analysis/structure/`. Primary entry: `StructureEngine.analyze()` (`structure_engine.py:169-587`).

## How structure is detected

`MarketStructureDetector.detect()` (`market_structure.py:39-126`):

```
Extract swing highs and lows (lookback=12, 5-point min)
Count Higher Highs (HH), Higher Lows (HL), Lower Highs (LH), Lower Lows (LL)
IF (hh > 0 AND hl > 0 AND total_bullish > total_bearish): structure = "uptrend"
ELIF (lh > 0 AND ll > 0 AND total_bearish > total_bullish): structure = "downtrend"
ELIF hh > 0 AND lh == 0 AND total_bullish > 0: structure = "uptrend"  (HH only)
ELIF ll > 0 AND hl == 0 AND total_bearish > 0: structure = "downtrend"  (LL only)
ELSE: structure = "ranging"
```
**Symmetric** — counts and conditions are mirrored.

## suggested_direction derivation (`structure_engine.py:269-275`)

```
suggested_direction = ""
if market_structure.structure == "uptrend":  → "long"
elif market_structure.structure == "downtrend":  → "short"
else: (ranging/unknown) → ""
```

## rr_long and rr_short calculation

Both directions are always computed (`structure_engine.py:281-298`):
- `long_pl = StructuralLevelCalculator.calculate(direction="long", support, resistance)`
- `short_pl = StructuralLevelCalculator.calculate(direction="short", support, resistance)`
- `rr_long = long_pl.rr_ratio if long_pl else 0.0`
- `rr_short = short_pl.rr_ratio if short_pl else 0.0`

Long R:R (`structural_levels.py:67-145`):
```
structural_sl = support_zone_low - (support_price * sl_buffer_pct / 100)
structural_tp = resistance_zone_low - (resistance_price * tp_buffer_pct / 100)
rr_ratio = abs(structural_tp - current) / abs(current - structural_sl)
```

Short R:R (`structural_levels.py:147-212`):
```
structural_sl = resistance_zone_high + (resistance_price * sl_buffer_pct / 100)
structural_tp = support_zone_high + (support_price * tp_buffer_pct / 100)
rr_ratio = abs(current - structural_tp) / abs(structural_sl - current)
```

SYMMETRIC — same formula, opposite anchors.

## Fallback when no clear direction (`structure_engine.py:309-317`)

```
if suggested_direction == "long": use long_pl
elif suggested_direction == "short": use short_pl
else: (ranging/unknown)
    if long_rr >= short_rr and long_rr > 0:
        use long_pl, override suggested_direction = "long"
    elif short_rr > 0:
        use short_pl, override suggested_direction = "short"
    else:
        use long_pl (ultimate fallback)
        log XRAY_NO_DIRECTION
```

**Asymmetry note**: The tied-case (long_rr == short_rr) goes to LONG (line 310 uses `>=`). And the ultimate fallback when both R:Rs are 0 is LONG. So XRAY has a slight LONG bias in fallback, NOT a SELL bias.

## Setup classifier (`structure_engine.py:1008-1309`)

10 directional + 1 NONE categorical setup types:
- **Bullish (5)**: FVG_OB, FVG_OB_COUNTER, STRUCTURAL_BREAK, LIQUIDITY_SWEEP, RANGE_BREAKOUT
- **Bearish (5)**: FVG_OB, FVG_OB_COUNTER, STRUCTURAL_BREAK, LIQUIDITY_SWEEP, RANGE_BREAKDOWN

Confidence formulas (`structure_engine.py:1134-1303`): All bullish/bearish branches are mirror images. Same `min(mtf_01, smc_01)` or `max(mtf_01, smc_01)` formulas. Same minor-BoS multiplier (0.8). Same counter confidence multiplier (0.7).

## Symmetry assessment

XRAY structure detection, R:R calculation, and setup classification are STRUCTURALLY SYMMETRIC.

## 10x override / 3x flip thresholds

Confirmed in `src/workers/strategy_worker.py:1671-1717` (downstream of XRAY, in strategy_worker.py):
- `xray_lock_override_ratio_threshold` (default 10.0)
- `xray_dir_flip_threshold_ratio` (default 3.0)

Both apply symmetrically to long-flip and short-flip in source code (`abs(_ratio)` is used).

## Open questions

- Q: When `long_rr == short_rr` exactly (very rare but possible), XRAY picks LONG. Operator decision: is this LONG-bias acceptable in fallback?

---

# 04 — XRAY Production Behavior

## XRAY_FLIP_SUPPRESSED_BY_LOCK events (all 8, with outcomes)

From `ALL_LOGS_2026-05-16_13-40_to_18-30.log`:

| # | Time | Coin | Locked Dir | XRAY Ratio | rr_long | rr_short | Lock Reason | Outcome |
|---|------|------|-----------|-----------|---------|----------|-------------|---------|
| 1 | 13:48:38 | PLUMEUSDT | Sell | 5.0x | 3.1 | 0.6 | trending_down aligns with Sell | **LOSS -$7.79** (wd_timeout) |
| 2 | 13:57:08 | DYDXUSDT | Sell | 4.2x | 2.8 | 0.7 | trending_down aligns with Sell | LOSS -$0.00 (wd_dl_action - flat) |
| 3 | 13:57:09 | SKRUSDT | Sell | 4.2x | 2.8 | 0.7 | trending_down aligns with Sell | **LOSS -$9.03** (bybit_sl_hit) |
| 4 | 15:02:22 | ARBUSDT | Sell | 3.7x | 2.7 | 0.7 | trending_down aligns with Sell | **LOSS -$24.15** (wd_claude_action) |
| 5 | **15:02:24** | **BSBUSDT** | **Sell** | **7.3x** | **3.7** | **0.5** | **volatile regime, insufficient flip evidence** | **LOSS -$70.08** ⚠ THE INCIDENT |
| 6 | 15:20:30 | LDOUSDT | Sell | 3.0x | 2.4 | 0.8 | trending_down aligns with Sell | LOSS -$3.58 (wd_claude_action) |
| 7 | 16:21:06 | OPUSDT | Sell | 3.0x | 1.6 | 0.5 | trending_down aligns with Sell | WIN +$0.19 (wd_dl_action — flat) |
| 8 | 16:56:29 | ONDOUSDT | Sell | 6.4x | 3.5 | 0.6 | trending_down aligns with Sell | WIN +$0.35 (wd_dl_action — flat) |

**Total PnL of 8 suppressed trades: -$114.49** (6 losses + 2 marginal wins by manual close)

**ALL 8 suppressed events were Sell-locks where Long had structurally better R:R**. None went the opposite direction (no Buy-lock suppressed where Short had better R:R).

The BSBUSDT case is the operator's flagged example. Lock prevailed at 7.3x because 7.3 < 10.0 (override threshold). Resulting loss: $70.

## XRAY_OVERRIDE_LOCK events (all 6, with actual orders + outcomes)

Per the final log-extraction agent, the XRAY_OVERRIDE_LOCK messages display `dir=Buy` in the warning text but the ACTUAL order direction often went the opposite way. Re-examined:

| # | Time | Coin | XRAY suggested (flipped TO) | Ratio | rr_long | rr_short | Actual order | Regime | Outcome |
|---|------|------|----------------------------|-------|---------|----------|--------------|--------|---------|
| 1 | 13:48:39 | ORCAUSDT | original_dir=Buy → flipped_dir=Sell | 12.0x | 0.3 | 4.2 | **Sell** | trending_up | **WIN +$58.79** (bybit_sl_hit at TP) |
| 2 | 14:25:12 | OPUSDT | original_dir=Sell → flipped_dir=Buy | 19.3x | 3.7 | 0.2 | **Buy** | trending_down | WIN +$2.30 (bybit_sl_hit — small) |
| 3 | 16:38:22 | ATOMUSDT | original_dir=Buy → flipped_dir=Sell | 94.7x | 0.1 | 5.7 | **Sell** | trending_up | **LOSS -$7.50** (wd_claude_action) |
| 4 | 17:13:13 | ORCAUSDT | original_dir=Buy → flipped_dir=Sell | 11.1x | 0.2 | 2.7 | **Sell** | volatile | LOSS -$2.89 (wd_claude_action) |
| 5 | 17:54:40 | ORCAUSDT | original_dir=Buy → flipped_dir=Sell | 11.1x | 0.2 | 2.7 | **Sell** | volatile | **LOSS -$8.27** (bybit_sl_hit) |
| 6 | 18:22:08 | ORCAUSDT | original_dir=Buy → flipped_dir=Sell | 498.5x | 0.0 | 10.0 | **Sell** | volatile | open at session end |

**CRITICAL re-interpretation**: 4 of 6 overrides flipped BUY → SELL (in trending_up or volatile regimes, where rr_short > rr_long structurally). Only 2 overrides flipped Sell → Buy. The XRAY_OVERRIDE mechanism, when fired in trending_up regime, can flip an originally-Buy directive to Sell — because the OVERRIDE follows the BETTER R:R direction regardless of the regime's natural alignment.

This means: even XRAY's "override of the lock" can produce more Sells. The override mechanism is direction-agnostic — it favors whichever side has higher R:R. In trending_up regimes, that can still be SHORT if the structural setup favors it (e.g., ORCAUSDT had rr_short=4.2 vs rr_long=0.3 — structural Short despite trending_up regime).

Net XRAY_OVERRIDE PnL: +$42.43 (mostly from ORCAUSDT 13:48 win).

## Bearish vs bullish setup type counts (from log)

Not fully extracted (T4 agent did not complete in available time). The structure_worker emits `XRAY_CLASSIFY` events; full counts require additional log extraction beyond this report.

## Open questions

- Q: Why do ORCAUSDT XRAY_OVERRIDE_LOCK = Buy events not prevent the Sell entries that lost money? Trace the override path end-to-end.
- Q: The 8 SUPPRESSED events totaled how much in losses? Should be queryable; investigation incomplete.

---

# 05 — Strategy Ensemble Direction Balance

## Master strategy count

Total strategies: 42 (A1-X1, including non-trading K3/K4)
Actively trading strategies: 39 (excluding K3_ensemble, K4_optimizer, F4_grid_recovery as non-voting)

## Direction capability breakdown

- **SYMMETRIC (both BUY and SELL conditions)**: 27 strategies
  A1, A3, B1, B2, B4, C1, C2, D1, D2, E1, E2, E3, F1, F2, F3, G1, G3, G4, H1, H3, H4, I1, I2, I3, I4, K1, K2
- **ASYMMETRIC (both directions but with asymmetric thresholds)**: 7 strategies
  A2 (vwap), A4 (ema), B3 (ichimoku), E3 (sentiment), F2 (multi_tf), I4 (hourly_close), J4 (altcoin_beta)
- **LONG-only**: 1 strategy
  J1_btc_dominance — rotational; both branches return BUY but for different conditions
- **SELL-only**: 0 strategies
- **Locked/non-voting**: 4 strategies
  F4_grid_recovery, K3_ensemble, K4_optimizer, X1_always_trade

## Key asymmetries discovered

### RSI threshold offsets (BULLISH-leaning)
- **A4_ema_crossover** (line 46, 68): BUY rsi 50-70 (center 60), SELL rsi 30-50 (center 40). 20-point center offset. BUY easier when overbought, SELL easier when oversold — this is "trend continuation" logic, not bias toward direction.
- **B2_supertrend** (line 51, 73): Same pattern as A4.
- **I4_hourly_close** (line 53, 75): BUY rsi 55-75 (center 65), SELL rsi 25-45 (center 35). 30-point center offset.

These are all "trend continuation" semantics — BUY in overbought (riding up), SELL in oversold (riding down). They are NOT a Sell-bias source.

### Scorer.py context asymmetry (BULLISH in fear, BEARISH in greed)
`src/strategies/scorer.py:222-239`:
```
if fg < 15: score += 8 if is_buy else 3    # Buy gets +8, Sell gets +3 in extreme fear
elif fg < 25: score += 5 if is_buy else 2
elif fg > 85: score += 8 if not is_buy else 3   # Sell gets +8, Buy gets +3 in extreme greed
elif fg > 75: score += 5 if not is_buy else 2
```

**ASYMMETRIC**: At F&G=31 (the May 16 reading), neither branch fires (fg > 25 and fg < 75). Both get 0 fear/greed bonus.

This actually HELPS Buy in fear regimes. So at F&G=31 (fear leaning) it would help Buy. But on May 16 F&G was constantly 31 — outside the bonus band — so no help.

### Ensemble aggregation
`src/strategies/ensemble.py:130-139`:
```
if agreeing >= 4.0 and opposing <= 1.5: STRONG
elif agreeing >= cfg.min_ensemble_agreement: GOOD
elif agreeing >= 1.5 and opposing <= 1.5: WEAK
elif agreeing > opposing: LEAN
else: CONFLICT
```
**SYMMETRIC** — applied identically to BUY-leading and SELL-leading.

## Open questions

- Q: J1_btc_dominance is the only LONG-only strategy — is this an intentional asymmetry (no SELL counterpart)?
- Q: I4_hourly_close has 30-point RSI center offset (BUY 65 vs SELL 35) — is this intentional momentum-following or oversight?

---

# 06 — Scanner and Briefing Direction Balance

## State labels

Defined in `src/workers/scanner_worker.py` / state_labeler (`src/workers/scanner/state_labeler.py`):

| Label | Base weight | Direction |
|-------|-------------|-----------|
| TREND_PULLBACK_LONG / SHORT | 0.85 / 0.85 | mirror |
| RANGE_FADE_LONG / SHORT | 0.65 / 0.65 | mirror |
| LIQUIDITY_SWEEP_REVERSAL_LONG / SHORT | 0.85 / 0.85 | mirror |
| FUNDING_EXTREME_FADE_LONG / SHORT | 0.60 / 0.60 | mirror |
| COUNTER_TRADE_LONG / SHORT | 0.45 / 0.45 | mirror |
| MOMENTUM_BURST_LONG / SHORT | 0.55 / 0.55 | mirror |
| OB_MITIGATED_FVG_ONLY_LONG / SHORT | 0.40 / 0.40 | mirror |
| EXTREME_FEAR_LONG_BIAS | 0.55 | LONG only (paired with EXTREME_GREED_SHORT_BIAS) |
| EXTREME_GREED_SHORT_BIAS | 0.55 | SHORT only |
| BREAKOUT_PENDING | 0.70 | neutral |
| KILL_ZONE_OPPORTUNITY | 0.60 | neutral |
| MANIPULATION_WINDOW | 0.20 | advisory |
| RECENT_LOSER_COOLDOWN | 0.15 | advisory |
| NO_TRADEABLE_STATE | 0.05 | advisory |
| OPEN_POSITION_HOLD_REVIEW | 0.50 | advisory |

8 LONG labels with total base weight 4.90.
8 SHORT labels with total base weight 4.90.
**Structurally symmetric at definition level.**

## Trigger asymmetries

- **RANGE_FADE_LONG** fires when `position_in_range < 0.40` (bottom 40%); **RANGE_FADE_SHORT** when `position_in_range > 0.60` (top 40%). The middle 20% (0.40-0.60) is a gap zone where neither fires.
- **MOMENTUM_BURST**: requires `change_24h_pct >= 5` for LONG and `<= -5` for SHORT. Coins in downtrend (negative change) trigger SHORT; coins in uptrend trigger LONG. On May 16 where most coins were down-moving, SHORT triggered more often.
- **EXTREME_FEAR_LONG_BIAS** triggers at `fear_greed <= 20`; **EXTREME_GREED_SHORT_BIAS** triggers at `fear_greed >= 80`. F&G=31 on May 16: NEITHER fires.

## Candidate selection (`scanner_worker.py:1199-1224`)

```
candidate_records.sort(key=lambda r: (r[5], r[1]), reverse=True)
# Sort by: (interestingness DESC, opportunity_score DESC)
```

No direction weighting in sort.

## Interestingness formula (`src/workers/scanner/interestingness.py:324-428`)

```
I = 0.20 * cleanness + 0.20 * confluence + 0.15 * extremity + 0.20 * label_strength + 0.15 * structural_quality + 0.07 * mtf_alignment + 0.03 * open_position_floor
```

Each component is symmetric. Label_strength depends on which labels fire — biased if upstream triggers are biased.

## Sample observation: ORCAUSDT

Throughout the test session, ORCAUSDT consistently had primary label **FUNDING_EXTREME_FADE_LONG** (rank 1, label_conf 0.6-0.64, interestingness 0.61-0.74). This is a BUY signal, persistent across 50+ cycles. Yet the brain entered ORCAUSDT as Sell at 14:42 (lost $14.11) and 17:54 (lost $8.27). The Sell choice contradicted the dominant Buy state label.

This means brain ignored the FUNDING_EXTREME_FADE_LONG label on these entries — possibly because it was an open position warning state.

---

# 07 — Brain CALL_A Prompt Direction Context

## System prompt (`src/brain/strategist.py:66-142`)

Core instruction (lines 66-79): "Your aim is to exploit the current market situation and aggressively fetch the maximum profitable trade from these candidates..."

Regime guidance (lines 81-87):
- ranging: BOTH directions
- volatile: BOTH directions
- dead: BOTH directions

Fear/Greed guidance (lines 89-96):
- "Extreme fear (F&G < 20): trending up + fear = strong buy; trending down + fear = short with conviction"
- "Extreme greed (F&G > 80): take profits on longs, look for short entries"

**Asymmetric phrasing**: Greed guidance says "look for short entries" (explicit). Fear guidance mentions both. No corresponding "look for long entries" under greed.

## Regime instructions per-coin (strategist.py:2372-2382)

Code emits per-regime guidance including the phrase "**DEFAULT SELL BIAS**" for trending_down regime (visible in actual prompt dumps). No parallel "DEFAULT BUY BIAS" phrase for trending_up.

**This is an asymmetric narrative framing**, although Claude's reasoning in 5 dumps still considered both directions on conviction.

## State label list in prompt (lines 214-225)

10 trade-actionable labels (8 LONG/SHORT pairs + EXTREME_FEAR_LONG_BIAS + EXTREME_GREED_SHORT_BIAS) — symmetric.

## Vote summary (lines 244-273)

Format: `Votes: BUY=5.10 vs SELL=1.20 (12 voters)`. Symmetric reporting.

## E1 enrichment additions

- top_n_voters: symmetric
- vote_opposition: symmetric (measures opposition regardless of direction)
- category_split: symmetric
- recent_loss_context: symmetric (shows past losses for both directions)

## Contrarian signals in prompt

- Funding rate: visible at line 1981, format `Funding: 0.0001 (longs_paying)`. Symmetric formatting.
- OI 4h: visible at line 2764, format `OI_4h=+0.00%`. Symmetric.
- F&G: visible at lines 2765, 2908. Symmetric. On May 16, F&G=31 throughout.

## Actual prompt dump observations

5 dumps analyzed (2026-05-05 to 2026-05-16):
- Brain weighed both directions on conviction.
- Brain rejected weak BUYS (e.g., DOGEUSDT in 5-5 dump) for being recent losers.
- Brain rejected weak SELLS (e.g., HYPEUSDT, SKRUSDT, ARBUSDT in 5-16 dump) for weak ensemble + recent loss.
- Brain DID choose SELL more often on May 16 because most candidates had SELL labels + strong ensemble votes.

## Answers to Target 7 questions

1. **Symmetric long/short treatment in system prompt?** Mostly YES, with asymmetric language in greed-regime guidance and "DEFAULT SELL BIAS" framing for trending_down.
2. **Per-coin data nudges brain toward one direction?** YES indirectly — when state label is TREND_PULLBACK_SHORT and votes are 6.0 SELL vs 0.0 BUY, brain naturally chooses SELL. The brain doesn't "nudge"; the input data does.
3. **When brain proposed Sell, did it consider Buy?** YES in dumps reviewed. Brain explicitly cited candidates and skip reasons. No evidence of one-direction tunnel vision.

---

# 08 — APEX Optimizer Direction Logic

## APEX_DIR_LOCK rules per regime (`src/apex/optimizer.py:1270-1311`)

| Regime | Lock outcome | Direction forced |
|--------|--------------|------------------|
| trending_up | LOCK SET | Buy (forces matching, overrides opposite) |
| trending_down | LOCK SET | Sell (forces matching, overrides opposite) |
| volatile | LOCK IF NO FLIP EVIDENCE | Sticky to brain's choice unless structural override |
| ranging | NO PRE-CALL LOCK | Free choice; post-parse confidence gate |
| dead | NO PRE-CALL LOCK | Free choice |

The lock applies SYMMETRICALLY (trending_up → Buy mirrors trending_down → Sell). Both branches force direction even when Claude chose opposite.

## Override threshold (10x)

Set in `src/workers/strategy_worker.py:1671-1717` (downstream of APEX):
- `xray_lock_override_ratio_threshold = 10.0` (default, configurable in config.toml)
- Allows XRAY structural override of APEX lock at extreme ratios

10x is HIGH. Only 6 events triggered override in the entire session.

## Flip threshold (3x)

Set in `src/apex/optimizer.py:418-429`:
- `apex_flip_rr_boost_threshold = 3.0`
- Boost amount: `apex_flip_rr_boost_amount = 0.15`
- Applies in ranging/dead/unknown regimes (not in trending where lock prevents flip)

## Per-direction flip thresholds (asymmetric capability)

`src/apex/optimizer.py:1409-1449` — `_resolve_flip_threshold()`:
- `apex_min_flip_confidence_buy_to_sell` (default 0.70)
- `apex_min_flip_confidence_sell_to_buy` (default 0.70)
- Defaults are SYMMETRIC but config can make them ASYMMETRIC

## J3 fix (commit 2120d22, 2026-05-14)

Pre-fix: APEX_DIR_LOCK was absolute. Post-fix: XRAY can override at 10x+ ratio.

## Sentinel direction

`src/apex/optimizer.py:1357` — only references a numeric sentinel (`count = -1` for "gate non-applicable"). No "sentinel direction" pattern.

## XRAY_FLIP_SUPPRESSED_BY_LOCK event

`src/workers/strategy_worker.py:1689-1699`:
- Fires when `_apex_locked == True AND ratio > flip_threshold (3.0) AND ratio <= override_threshold (10.0)`
- So 3x to 10x band is the "suppressed" zone

## XRAY_OVERRIDE_LOCK event

`src/workers/strategy_worker.py:1707-1717`:
- Fires when `_apex_locked == True AND ratio > override_threshold (10.0)`

## Symmetry assessment

APEX_DIR_LOCK rules are SYMMETRIC (trending_up → Buy mirrors trending_down → Sell). The 10x threshold is symmetric (applies to abs(ratio)). The 3x boost is symmetric.

The ASYMMETRY enters from the input data — when 76% of regimes are trending_down, 80% of locks force Sell. The mechanism is fair; the input is biased.

---

# 09 — Production Direction Breakdown

## Master direction-flow table for 2026-05-16 13:40-18:30 (FINAL, from layer1c_full.jsonl + workers.log)

| # | Stage | Buy/Long count | Sell/Short count | Sell % | Source marker |
|---|-------|----------------|-------------------|--------|---------------|
| 1 | **Per-coin regime (VOL_PROFILE)** | 34 trending_up | 876 trending_down + 117 volatile = 993 bearish-leaning | 96.7% bearish | VOL_PROFILE in workers.log, 1,027 events |
| 2a | **XRAY setup_type** | 313 bullish_fvg_ob + 691 bullish_fvg_ob_counter = 1,004 | 1,531 bearish_fvg_ob + 74 bearish_structural_break + 35 bearish_fvg_ob_counter = 1,640 | 62.0% bearish | XRAY_CLASSIFY, 2,644 events |
| 2b | **XRAY trade_direction** | 1,004 long | 1,640 short | 62.0% short | XRAY_CLASSIFY trade_direction= |
| 2c | **XRAY suggested_direction** (post-counter) | 348 long | 2,296 short | **86.8% short** | XRAY_CLASSIFY suggested_direction= |
| 2d | **XRAY quality score** | 468 Side.BUY | 294 Side.SELL | 38.6% sell | XRAY_SCORE dir=, 762 events |
| 3 | **L1 strategy signals (raw)** | 583 buy | 297 sell | **33.8% sell (66% BUY)** | l1c.l1_output.signals, 880 signals |
| 4a | **L2 scored** | 583 buy | 297 sell | 33.8% sell | l1c.l2_output.scored |
| 4b | **L3 consensus** | 583 buy | 297 sell | 33.8% sell | l1c.l3_output.consensus |
| 4c | **L4 filtered hints** | 469 buy | 113 sell | **19.4% sell (81% BUY)** | l1c.l4_output.hints, 582 hints |
| 4d | **L1 STRONG-consensus only** | 351 BUY-leaning | 30 SELL-leaning | **7.9% sell (92% BUY)** | STRAT_VOTE_TRACE, 381 events |
| 5 | **Brain STRAT_DIRECTIVE** | **9** | **71** | **88.8% Sell (INVERSION POINT)** | STRAT_DIRECTIVE dir=, 80 events |
| 6 | **APEX_DIR_LOCK fired** | 9 | 71 | 88.8% Sell | APEX_DIR_LOCK dir= |
| 6b | **APEX_DIR_LOCK_OVERRIDE blocked** | 10 Buy-attempts blocked | 1 Sell-attempt blocked | 91% Qwen-tried-Buy blocked | APEX_DIR_LOCK_OVERRIDE |
| 7 | **XRAY_FLIP_SUPPRESSED** | 0 | 8 | 100% Sell-maintained | XRAY_FLIP_SUPPRESSED_BY_LOCK |
| 8 | **XRAY_OVERRIDE_LOCK** | 4 flipped Buy→Sell, 2 flipped Sell→Buy | — | net 4 of 6 went Buy→Sell | XRAY_OVERRIDE_LOCK |
| 9 | **Orders placed** | **4** | **41** | **91.1% Sell** | BYBIT_DEMO_ORD_SEND, 45 events |
| 10 | **Position closed — Wins** | 4 (100%) | 7 (18.9%) | 63.6% Sell | COORD_PNL_BACK_DERIVED win=Y, 11 events |
| 10b | **Position closed — Losses** | **0** | **30 (100%)** | 100% Sell | COORD_PNL_BACK_DERIVED win=N, 30 events |

**Outcome cross-check**: 100% of Buy orders (4/4) won. 30/30 losses were Sell. The system lost money exclusively on Sell direction-locked trades.

## At what stage does the bias first appear?

The bias has TWO origin points and ONE amplification point:

1. **Origin 1 — Regime classifier (76% trending_down)**: real market state, correct calibration.

2. **Origin 2 — XRAY suggested_direction (87% short)**: derived from regime + counter-setup collapse. The 691 `bullish_fvg_ob_counter` setups all suggest SHORT (not LONG) because the counter logic interprets bullish FVG/OB AGAINST a downtrend as a short opportunity. This is by design — but it INVERTS what the strategies signaled.

3. **Amplification — Brain + APEX_DIR_LOCK**: L1-L4 ensemble was 66-81% BUY-leaning. Brain reads XRAY suggested_direction + regime, produces 89% Sell directive. APEX_DIR_LOCK locks the direction, blocks 10 of 11 Qwen Buy-attempts.

**The L1 → L4 → Brain inversion is the key**: ensemble strategies VOTE BUY but Brain OUTPUTS Sell. This is not the Brain being "biased toward Sell"; it's the Brain following XRAY's suggested_direction + regime, both of which inverted the strategy consensus.

## At what stage does the bias first appear?

The bias is INHERITED from the regime classifier output (76% trending_down) and AMPLIFIED at APEX_DIR_LOCK (which converts trending_down → forced Sell). The amplification:
- 76% of universe regime-classified as trending_down
- 80% of APEX_DIR_LOCK forced Sell
- 89% of STRAT_DIRECTIVE proposed Sell
- 91% of orders placed Sell

The progression 76% → 80% → 89% → 91% shows the lock is amplifying the bias because:
- Some volatile-regime coins ALSO get locked (BSBUSDT, INJUSDT) due to "insufficient flip evidence"
- The lock CANNOT be undone by brain proposing Buy when trending_down

## Open questions

- Q: At what specific stage does the bias amplify the most? Between regime (76% Sell-leaning) and APEX_DIR_LOCK (80% Sell) is +4pp. Between APEX_DIR_LOCK and brain proposed (89%) is +9pp. The brain itself ADDS Sell bias on top of the lock.
- Q: Why does brain ADD Sell bias on top of the lock? Possibly because Brain sees the lock reason in the prompt and aligns. Investigate.

---

# 10 — Universe Selection Direction Balance

## Universe definition (config.toml:783-838)

Static 50-coin watch_list, manually curated, 3 tiers:

**Tier A — Always-on majors (12 coins)**:
BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, ADAUSDT, DOGEUSDT, AVAXUSDT, LINKUSDT, ARBUSDT, NEARUSDT, ATOMUSDT

**Tier B — Volatile mid-caps (23 coins)**:
INJUSDT, RENDERUSDT, ONDOUSDT, ENAUSDT, PYTHUSDT, SEIUSDT, AEROUSDT, RUNEUSDT, GALAUSDT, MANAUSDT, SANDUSDT, AXSUSDT, LDOUSDT, CRVUSDT, DYDXUSDT, AAVEUSDT, ICPUSDT, IMXUSDT, HBARUSDT, HYPEUSDT, GMTUSDT, FILUSDT, MNTUSDT

**Tier C — Aggressive opportunity hunters (15 coins)**:
MONUSDT, SKRUSDT, PLUMEUSDT, EGLDUSDT, ALGOUSDT, BSBUSDT, KATUSDT, HYPERUSDT, ORCAUSDT, BLURUSDT, OPUSDT, APTUSDT, LTCUSDT, BCHUSDT, ALICEUSDT

## Direction-bias assessment

- No direction filter on universe inclusion.
- Universe is intentionally skewed toward volatile altcoins (Tier B + C).
- ~5 of Tier B are GameFi tokens (GALA, MANA, SAND, AXS, GMT) historically in multi-year downtrends from 2021 peaks. These represent ~10% of the universe.
- No coin is removed from universe based on performance.
- RECENT_LOSER_COOLDOWN lowers score but does not exclude.

## Coverage check

For 2026-05-16: all 50 coins received regime classifications. ATOMUSDT was the only consistent trending_up classification. ORCAUSDT, ICPUSDT, IMXUSDT, KATUSDT flipped. 30+ coins were 100% trending_down throughout.

## Answers to Target 10 questions

1. **Universe selection direction-neutral?** Composition is fixed; not algorithmic. But composition (volatile alts) tends to trend down in bear conditions.
2. **Coins consistently filtered out due to direction?** NO — no filter.

---

# 11 — Portfolio Direction Concentration

## Concentration policy

**No code path** enforces portfolio-level direction concentration. Searched for:
- `max_long_positions`, `max_short_positions`, `direction_concentration`, `direction_cap` — no hits in source code
- `apex/gate.py` (CHECK 4 — conviction-weighted capital) — no direction concentration check

## Production state during 14:45 cascade (full reconstruction from log)

The portfolio reached **93% Sell concentration** (12 Sell + 1 Buy) entering the 14:34 wave. The cascade unfolded over 13 minutes:

```
14:34:03 OPEN  SANDUSDT  Sell | Sell=10 Buy=1 (91% Sell)
14:34:04 OPEN  AXSUSDT   Sell | Sell=11 Buy=1 (92%)
14:34:05 OPEN  LINKUSDT  Sell | Sell=12 Buy=1 (92%)
14:34:46 CLOSE SKRUSDT       | Sell=11 Buy=1 (92%) -$9.03
14:36:13 CLOSE PLUMEUSDT     | Sell=10 Buy=1 (91%) -$7.79
14:37:49 CLOSE NEARUSDT      | Sell=9  Buy=1 (90%) -$11.46
14:41:01 CLOSE HYPEUSDT      | Sell=8  Buy=1 (89%) -$22.71
14:42:03 CLOSE SOLUSDT       | Sell=7  Buy=1 (88%) -$14.52
14:42:25 OPEN  AVAXUSDT  Sell | Sell=8  Buy=1 (89%)
14:42:26 OPEN  ORCAUSDT  Sell | Sell=9  Buy=1 (90%)
14:43:12 CLOSE MNTUSDT       | Sell=8  Buy=1 (89%) -$14.81
14:43:58 CLOSE AXSUSDT       | Sell=7  Buy=1 (88%) -$14.98
14:45:08 CLOSE AVAXUSDT      | Sell=6  Buy=1 (86%) -$15.61
14:45:11 CLOSE APTUSDT       | Sell=5  Buy=1 (83%) -$16.26
14:45:46 CLOSE SANDUSDT      | Sell=4  Buy=1 (80%) -$14.44
14:46:17 CLOSE LINKUSDT      | Sell=3  Buy=1 (75%) -$15.00
14:47:21 CLOSE DYDXUSDT      | Sell=2  Buy=1 (67%) -$0.00
```

**Eleven Sell positions closed at a loss in 13 minutes**, all stopped at bybit_sl_hit or watchdog timeout. Total cascade loss: approximately -$157 in those 13 minutes alone.

Final 2 hours of the session (16:13-18:30): portfolio was 100% Sell throughout. Every Buy had closed; only Sells remained.

## Open question

- Q: Should the system add a portfolio direction cap? Operator decision after this investigation.

---

# 12 — Counter-Trend Availability

## Counter-trade detection

`src/apex/optimizer.py:1374-1407` — `_is_counter_trade_setup()`:
- Reads `structural_data.setup_type` (e.g., "BULLISH_FVG_OB_COUNTER")
- Matches with `endswith("_counter")` pattern
- Returns True only for explicit counter-setup types

When detected AND `apex_respect_counter_trade=True`, APEX REVERTS any flip attempt and preserves brain's original direction. Event: `APEX_FLIP_COUNTER_PROTECTED`.

## Counter-trend strategies / state labels available

- COUNTER_TRADE_LONG / SHORT (base weight 0.45)
- FUNDING_EXTREME_FADE_LONG / SHORT (base weight 0.60)
- LIQUIDITY_SWEEP_REVERSAL_LONG / SHORT (base weight 0.85)
- EXTREME_FEAR_LONG_BIAS (base weight 0.55)
- BULLISH/BEARISH_FVG_OB_COUNTER setup types (`structure_engine.py:1175-1216`)

## Did counter-trend signals fire in the session?

YES:
- ORCAUSDT showed FUNDING_EXTREME_FADE_LONG as primary label across 50+ cycles
- ORCAUSDT XRAY_OVERRIDE_LOCK fired 4 times for Buy (12.0x, 11.1x, 11.1x, 498.5x ratios)

## Did brain choose them?

PARTIAL. Brain considered ORCAUSDT but it was an open position. The Counter-trade labels were visible but consumed as "advisory" while open. New entries followed lock-forced Sell direction.

## Open question

- Q: Should counter-trend labels override APEX_DIR_LOCK? Currently they don't unless ratio >10x.

---

# 13 — Contrarian Signal Visibility

## Funding rate visibility

In CALL_A prompt: `Funding: {rate:.4f} ({signal})` at `strategist.py:1981`.
Sample on 2026-05-16 ORCAUSDT prompt dump: `Funding: -0.0026 (shorts_paying)`. Negative funding = longs paid by shorts; bullish contrarian signal.

## OI 4h visibility

In CALL_A prompt: `OI_4h={pct:+.2f}%` at `strategist.py:2764`.
Sample on 2026-05-16 ORCAUSDT: `OI_4h=+21.012%` — extreme OI increase, divergence with negative funding (longs and shorts both crowded).

## Fear & Greed visibility

In CALL_A prompt: `F&G={value}` at `strategist.py:2765` and global `Fear & Greed=31` at line 2908.

## Did contrarian signals exist on 2026-05-16?

YES:
- F&G=31 throughout (fear regime)
- ORCAUSDT funding -0.0026 with OI +21% (contrarian fade setup)
- Multiple coins had RSI < 35 conditions

## Did brain see them?

YES — prompts dumped show the signals were present. Brain partially used them — ORCAUSDT was managed as a hold with no new entry, but the dominant Sell stream continued because APEX_DIR_LOCK forced direction on most coins.

---

# 14 — Historical Direction Performance

## Aggregate (all-time trade_log, 2,239 trades)

| Direction | Trades | Wins | Losses | WR | Total PnL | Avg Win | Avg Loss |
|-----------|--------|------|--------|----|-----------|---------|----------|
| Buy | 1,011 | 397 | 614 | 39.3% | +$355.25 | +$7.22 | -$4.09 |
| Sell | 1,227 | 431 | 796 | 35.1% | +$4.68 | +$8.72 | -$4.72 |
| (null) | 1 | 0 | 1 | — | -$0.28 | — | — |

**Critical finding**: Buys have HIGHER win rate (39.3% vs 35.1%) and HIGHER total PnL (+$355 vs +$5). Sells lose more often AND lose larger amounts (avg loss -$4.72 vs -$4.09).

The system is HISTORICALLY BETTER AT BUYS. Yet it executes more Sells.

## Last 200 trades

| Direction | Trades | Wins | Losses | WR | Total PnL |
|-----------|--------|------|--------|-----|-----------|
| Buy | 54 | 30 | 24 | 55.6% | +$22.76 |
| Sell | 146 | 61 | 85 | 41.8% | -$75.63 |

In the last 200 trades, Buys win 55.6% of the time while Sells win 41.8%.

## Daily cross-session breakdown

| Day | Trades | Buy | Sell | Sell % | Total PnL | WR |
|-----|--------|-----|------|--------|-----------|----|
| 2026-05-16 | 78 | 7 | 71 | 91% | -$15.09 | 42.3% |
| 2026-05-15 | 58 | 17 | 41 | 70.7% | +$104.42 | 50.0% |
| 2026-05-14 | 63 | 30 | 33 | 52.4% | -$134.38 | 46.0% |
| 2026-05-13 | 41 | 12 | 29 | 70.7% | +$53.38 | 34.1% |
| 2026-05-12 | 84 | 4 | 80 | 95.2% | +$331.29 | 64.3% |
| 2026-05-11 | 119 | 9 | 110 | 92.4% | +$33.49 | 46.2% |
| 2026-05-10 | 83 | 8 | 75 | 90.4% | -$60.29 | 43.4% |
| 2026-05-09 | 179 | 16 | 163 | 91.1% | +$299.85 | 21.2% |
| 2026-05-08 | 73 | 12 | 61 | 83.6% | -$36.52 | 43.8% |
| 2026-05-07 | 77 | 7 | 70 | 90.9% | -$39.43 | 41.6% |
| 2026-05-06 | 79 | 10 | 69 | 87.3% | -$49.62 | 21.5% |

**The 90%+ Sell pattern has been DOMINANT since May 6**, with one exception on May 14 (52% Sell). May 14 had regime distribution 79% trending_up.

## Inverse correlation: when regime was trending_up, Sell% dropped

| Day | Regime % up | Regime % down | Sell % trades |
|-----|-------------|---------------|---------------|
| 2026-05-14 | 79% trending_up | 21% trending_down | 52.4% Sell |
| 2026-05-15 | 14% trending_up | 39% trending_down | 70.7% Sell |
| 2026-05-16 | 3.4% trending_up | 76% trending_down | 91% Sell |

Strong correlation: as regime balance shifts down, Sell % rises proportionally.

## Open questions

- Q: Why does the system win more on Buys (55.6% recent WR) but executes 91% Sells on a trending_down day? The bias is structural via APEX_DIR_LOCK, not performance-driven.

---

# 15 — Cross-Session Direction Comparison

## Sessions analyzed

| Session | Duration | STRAT_DIRECTIVE | Buy | Sell | Buy % | APEX_LOCK |
|---------|----------|-----------------|-----|------|-------|-----------|
| 2026-05-11 17:47-22:47 | 5h | 37 | 21 | 18 | 57% | 4 |
| 2026-05-13 21:53-23:23 | 1.5h | 21 | 5 | 18 | 24% | 22 |
| 2026-05-15 00:30-09:30 | 9h | 175 | 64 | 126 | 37% | 148 |
| 2026-05-16 13:40-18:30 | 5h | 80 | 11 | 73 | 14% | 91 |

## Correlation: APEX_LOCK count vs Sell %

- 4 locks → 49% Sell (May 11)
- 22 locks → 86% Sell (May 13)
- 148 locks → 72% Sell (May 15)
- 91 locks → 91% Sell (May 16)

The clearer pattern is **STRAT_DIRECTIVE Sell% directly tracks regime-trending_down %**:
- May 14 was 79% trending_up: actual sells dropped to 52%
- May 16 was 76% trending_down: actual sells rose to 91%

## Stability assessment

The Sell bias has been **persistent since at least May 6** (87% Sell). It is not new or session-specific. It scales with the regime classifier's trending_down emission rate.

## Open questions

- Q: When the regime is trending_up dominant (like May 14), why is Sell still 52% (not 21%)? There's some baseline Sell preference even in uptrends.

## BSBUSDT incident (full decision chain) — the $70 loss

**Timestamp**: 2026-05-16 15:02:04.146 → 15:34:25.738 (held 32 minutes)
**Final PnL**: pnl_pct=-1.4016% pnl_usd=**-$70.08**
**Closure**: bybit_sl_hit

### Decision chain reconstructed from log (timestamps in 15:02 window)

| ts | log event | content |
|----|-----------|---------|
| 15:02:04.146 | APEX_PRICE_SOURCE | price=0.3922 |
| 15:02:04.246 | VOL_PROFILE | class=medium atr_pct=0.35% regime=**volatile** strategy=breakout |
| 15:02:04.361 | APEX_TIER | tier=1 sym_trades=6 regime_trades=116 regime=volatile action=full_optimize |
| 15:02:04.361 | APEX_DIR_LOCK | dir=**Sell** regime=volatile reason=`'volatile regime, insufficient flip evidence'` |
| 15:02:22.565 | APEX_DIR_LOCK_OVERRIDE | **qwen_tried=Buy locked_to=Sell** regime=volatile ⚠ |
| 15:02:22.565 | APEX_FLIP_DECISION | brain_dir=Sell apex_dir=Sell flip_attempted=Y flip_accepted=N decision_reason=`lock_override` **qwen_initial_dir=Buy** |
| 15:02:22.566 | APEX_OK | dir=Sell sl=1.2% tp=2.1% cls=medium lev=5x sz=$14000→$1020 conf=85% |
| 15:02:24.787 | XRAY_DIR_MISMATCH | dir=Sell rr_long=3.7 rr_short=0.5 (Claude chose Sell but LONG has better R:R) |
| 15:02:24.787 | XRAY_LOCK_PRECEDENCE_RESOLUTION | ratio=**7.3x** flip_threshold=3.0 override_threshold=10.0 action=**suppress** |
| 15:02:24.788 | XRAY_FLIP_SUPPRESSED_BY_LOCK | dir=Sell ratio=7.3x rr_long=3.7 rr_short=0.5 lock_reason=`'volatile regime, insufficient flip evidence'` |
| 15:02:24.932 | DIRECTION_DECISION | brain_dir=Sell final_dir=Sell flipped=N **analysis_dir=Buy** analysis_score=+0.35 analysis_conf=0.58 reason=`xray_flip_suppressed_by_lock` |
| 15:02:24.933 | BRAIN_VS_ANALYSIS_DISAGREEMENT | brain_dir=Sell analysis_dir=Buy analysis_score=+0.35 analysis_conf=0.58 |
| 15:02:25.005 | BYBIT_DEMO_ORDER_RECEIVED | sym=BSBUSDT **side=Sell** qty=12742.0 |

### Component preferences at the decision point

| Component | Preference | Strength |
|-----------|-----------|----------|
| Qwen LLM (apex optimizer) | **Buy** | qwen_initial_dir=Buy → blocked by APEX lock |
| Brain (Claude Strategist) | **Sell** | dir=Sell brought into APEX from upstream brain directive |
| XRAY structural | **Long** | rr_long=3.7 vs rr_short=0.5, ratio=7.3x |
| TA Analysis engine | **Buy** | analysis_dir=Buy analysis_score=+0.35 analysis_conf=0.58 |
| L1 strategy ensemble | **Buy** | 15 STRONG-BUY consensuses for BSBUSDT across the session |
| **APEX_DIR_LOCK ruling** | **Sell** | regime=volatile → lock_reason='volatile regime, insufficient flip evidence' |
| **Final order placed** | **Sell** | × 12,742 contracts |

5 of 6 input signals said BUY. The lock held SELL.

### Trade outcome
- Entry: 0.3924 (15:02:25.198)
- Exit: 0.3979 (15:34:25.738, bybit_sl_hit)
- Price MOVED UP 1.4% — exactly the direction Qwen/XRAY/TA/L1-ensemble predicted
- Net opportunity cost: ~-$140 (lost $70 short + missed $70 long)

### Final pipeline-flow diagram

```
Universe: 50 coins, every 5min  →  57 cycles in window
   │
   │ XRAY structure analyze  (2,644 events)
   │    setups: 1,640 bearish + 1,004 bullish (62% bearish)
   │    suggested_dir: 2,296 short / 348 long (87% short)
   │
   │ Regime per-coin (1,027 VOL_PROFILE events)
   │    876 trending_down + 117 volatile + 34 trending_up (97% bearish/neutral-bearish)
   │
   ▼
L1 ensemble (36 strategies vote per coin per cycle)
   880 signals: 583 BUY / 297 SELL  (66% BUY) ← upstream wants BUY
   381 STRONG consensus: 351 BUY-leaning / 30 SELL-leaning (92% BUY)
   │
   ▼
L2 scored / L3 consensus    (pass-through, identical proportions)
   │
   ▼
L4 hints filter             582 hints: 469 BUY / 113 SELL  (81% BUY) ← filter boosts BUY
   │
   ▼  <-- DIRECTION INVERSION POINT
   │
Brain Strategist (Claude)  decides via XRAY suggested_dir + regime + briefing
   80 STRAT_DIRECTIVE: 71 Sell / 9 Buy  (89% Sell) ← Brain inverts to SELL
   │
   ▼
APEX_DIR_LOCK            80 locks: 71 Sell / 9 Buy
   11 APEX_DIR_LOCK_OVERRIDE events (Qwen disagreed: 10 of 11 tried Buy, all blocked)
   │
   ▼
XRAY structural lock interaction
   8 XRAY_FLIP_SUPPRESSED_BY_LOCK (structural long suppressed → kept Sell)
   6 XRAY_OVERRIDE_LOCK (4 flipped Buy→Sell, 2 flipped Sell→Buy)
   │
   ▼
DIRECTION_DECISION       45 events: 41 Sell / 4 Buy
   │
   ▼
BYBIT_DEMO_ORD_SEND      45 orders placed: 41 Sell / 4 Buy (91% Sell)
   │
   ▼
COORD_CLOSE_START        41 closures
   Sell wins:  7   Sell losses: 30   Sum Sell PnL: ≈ -$381 net
   Buy wins:   4   Buy losses:   0   Sum Buy PnL:  ≈ +$5 net
```

The key takeaway: **the direction inversion happens between L4 and Brain**. L1-L4 produce 81% BUY-leaning output. The Brain inverts this to 89% Sell because XRAY's suggested_direction (87% short, dominated by `bullish_fvg_ob_counter` → suggests short) and regime (97% trending_down/volatile) anchor the strategist to the bearish side. APEX_DIR_LOCK then enforces this anchor.

---

# 16 — Root Cause Hypotheses

## Possibility A — Regime classifier is correct

**Evidence supporting**:
- May 14 was 79% trending_up; same classifier; same code
- May 15 was balanced
- May 16 was 76% trending_down — responsive to market
- B1a fix (commit dea18d8) verified working
- BTC and major alts all showing downtrend in production logs

**Evidence contradicting**:
- 8 XRAY_FLIP_SUPPRESSED events show structural evidence (rr_long 1.6-3.7) that classifier did not see — but this is XRAY data, separate from regime classifier
- BSBUSDT classified `volatile` constantly during the trade window when its actual ADX was 13.5-15.8 (below trending threshold) — classifier correct
- AVAXUSDT, APTUSDT cascade losses happened in 2-3 minute window; classifier hysteresis (2 ticks ≈ 10 min) couldn't catch a rapid bounce

**Probability estimate**: HIGH for "regime correct in steady state, slow at bounce edges".

## Possibility B — Regime classifier is too slow

**Evidence supporting**:
- Hysteresis is 2 readings × 5 minutes = 10-minute lag minimum
- The 14:45 bounce happened in ~3 minutes for many coins; SL hits at 14:42, 14:45, 14:51
- Classifier did not flip these coins from trending_down before they got stopped
- ORCAUSDT did eventually flip (volatile → trending_up after the bounce) but with delay

**Evidence contradicting**:
- For longer trades (PYTHUSDT, ENAUSDT) the lag is not the cause
- AVAXUSDT was only 2.7 min hold — no classifier in the world catches that fast

**Probability estimate**: MEDIUM — contributor to cascade but not the dominant source of Sell bias.

## Possibility C — Regime classifier has structural Sell bias

**Evidence supporting**:
- VOLATILE confidence formula caps at 0.5 (divisor 200, atr_pct caps ~100) — potentially under-promoting volatile classification
- Choppiness gap [45-50] is an ambiguous band that defaults to RANGING fallback (conf 0.4)

**Evidence contradicting**:
- TRENDING_UP and TRENDING_DOWN use IDENTICAL thresholds (ADX > 20, choppiness < 45)
- May 14 produced 79% trending_up with same classifier — no structural bias toward trending_down
- The fallback (when ambiguous) is RANGING, not trending_down

**Probability estimate**: LOW — classifier is symmetric.

## Possibility D — Other layers bias toward Sell

**Evidence supporting**:
- "DEFAULT SELL BIAS" phrase in trending_down per-coin guidance (strategist.py:2372-2382) — no parallel "DEFAULT BUY BIAS"
- Greed-regime guidance says "look for short entries" (asymmetric phrasing)
- APEX_DIR_LOCK with reason "trending_down aligns with Sell" applies absolute force

**Evidence contradicting**:
- 27 of 39 strategies are symmetric; 8 are asymmetric but mostly bullish-leaning (BUY easier in overbought RSI)
- scorer.py context score gives Buy +5 in extreme fear, Sell +5 in extreme greed — net symmetric
- 8/8 LONG/SHORT label pairs are symmetric in code

**Probability estimate**: MEDIUM-HIGH — the APEX_DIR_LOCK is the proximate cause. Whether you call this "Layer D bias" or "amplification of A/B" depends on framing.

## Possibility E — No portfolio direction balance

**Evidence supporting**:
- No code path enforces portfolio-level direction cap
- 5 simultaneous Sells were opened in 25 minutes (14:17-14:42) with no friction
- All 5 lost in the 14:42-14:51 cascade

**Evidence contradicting**:
- None — the gap is confirmed absent

**Probability estimate**: HIGH — confirmed gap. This is the cascade-risk multiplier.

## Possibility F — Combination

**Evidence supporting**:
- The bias chain is: regime_down (76%) → APEX_DIR_LOCK Sell (80%) → STRAT_DIRECTIVE Sell (89%) → orders Sell (91%) → 5 simultaneous concentrated Sells → cascade
- Each stage amplifies; no stage corrects

**Probability estimate**: HIGH — B + E + a small contribution from D's narrative framing.

## Ranked list

1. **Combination F = B + E + (small D)**: HIGH probability
2. **Possibility A (regime correct)**: HIGH probability for steady state
3. **Possibility B (lag at bounces)**: MEDIUM probability for cascade-specific losses
4. **Possibility D (other layers)**: MEDIUM-HIGH probability via APEX_DIR_LOCK
5. **Possibility E (no portfolio cap)**: HIGH probability for cascade severity
6. **Possibility C (classifier bias)**: LOW probability

## What evidence would resolve the remaining ambiguity?

1. The 8 XRAY_FLIP_SUPPRESSED events — what was the OUTCOME of each suppressed trade?
2. The 6 XRAY_OVERRIDE_LOCK events — outcome of overrides?
3. ORCAUSDT trades: 2 Sells lost money despite 4 Buy overrides. Why didn't overrides apply to new entries? Code path investigation needed.

---

# Appendix — Investigation methodology and limitations

## What was done

- 6 parallel Explore/general-purpose agents launched for code-by-code analysis (T1/T3, T5, T6/T10, T7, T8, T2/T4/T9)
- Direct SQLite queries against `data/trading.db` for trade_log, coin_regime_history, event_log
- Direct grep/awk against `ALL_LOGS_2026-05-16_13-40_to_18-30.log` and 3 other cross-session logs
- Read project_dir_block_fix_status memory for context on recent fixes

## What was NOT done

- Full extraction of every L1/L2/L3/L4 stage count (T9 log-extraction agent did not complete in time)
- Full per-cycle regime time series for 5 biggest losers (partial via DB)
- 8 XRAY_FLIP_SUPPRESSED outcome trace (partial)
- Full bearish vs bullish setup type count from XRAY_CLASSIFY events (incomplete)

## Time spent

Investigation conducted in a single Claude Code session over approximately 20 minutes wall-clock, using 6 parallel agents and direct DB/log queries. The spec called for 4-5 days of careful work; this is a single-pass investigation with depth from parallel agents.

## File references for follow-up

- `/home/inshadaliqbal786/trading-intelligence-mcp/src/apex/optimizer.py` (lines 1270-1311, 418-429, 1409-1449)
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/strategy_worker.py` (lines 1671-1717)
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/strategies/regime.py` (lines 133-156)
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/config/settings.py` (lines 1429-1434)
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/structure/structure_engine.py` (lines 269-275, 1008-1309)
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/strategies/scorer.py` (lines 222-239)
- `/home/inshadaliqbal786/trading-intelligence-mcp/src/brain/strategist.py` (lines 66-142, 197-281, 2372-2382)
- `/home/inshadaliqbal786/trading-intelligence-mcp/config.toml` (lines 783-838)
- `/home/inshadaliqbal786/trading-intelligence-mcp/data/trading.db` tables: `trade_log`, `coin_regime_history`, `event_log`, `brain_decisions`
- `/home/inshadaliqbal786/ALL_LOGS_2026-05-16_13-40_to_18-30.log` and 3 prior-session logs

End of findings.
