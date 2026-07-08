# Q1 + Q1b Synthesis

## Answer to Question 1 — Is the regime detector per-coin or market-aggregate?

**Definitive answer: per-coin.**

### Code evidence (architectural)

- `RegimeDetector.detect(symbol)` and `RegimeDetector.detect_per_coin(symbols)` at `src/strategies/regime.py:78-233` both run the same per-symbol pipeline: each symbol gets its own 200 H1 klines via `market_repo.get_klines(symbol, ...)`, its own `TAEngine.analyze`, its own ADX / Plus-DI / Minus-DI / Choppiness / NATR / Volume-SMA-ratio.
- No cross-coin signals are fed to the classifier. No BTC override. No fear-greed / funding / OI feed.
- Per-symbol state in `_per_coin_regimes`, `_confirmed_regimes`, `_pending_regime` dictionaries — all keyed by `symbol`.
- Per-symbol hysteresis (`hysteresis_count` configured 2) means each symbol confirms or rejects regime transitions independently of other symbols.

### Log evidence (empirical, 48h window)

- 7508 `REGIME |` emissions across 159 5-minute time buckets.
- **92.5% of buckets show >1 distinct regime label across the 50 watch_list symbols**. 62.9% of buckets contain all 5 possible labels simultaneously.
- Per-symbol distributions vary from 36.1% ranging (ETHUSDT) to 99.3% ranging (ARBUSDT). Per-symbol behavior is consistent over time but differs between symbols.

The detector is unambiguously per-coin.

## Answer to Question 1b — What is the flip-causation chain?

**The proximate cause is XRAY's structural R:R asymmetry (threshold 3.0). The root enabler is the detector's `ELSE = RANGING` fallback at `regime.py:153-156`, which prevents APEX direction lock from firing on weakly-trending coins.**

### Key numerical findings from 10 traced flips

- 10 / 10 Buy → Sell XRAY flips had `apex_locked=N`. APEX direction lock did not pre-empt any of them.
- 8 / 10 occurred on a `ranging` label with `conf=0.40` (the ELSE fallback signature).
- 2 / 10 occurred on a `dead` label.
- 0 / 10 occurred on a trending label (consistent with: trending → APEX lock → XRAY suppressed).
- XRAY ratios ranged from 5.7x to 668x. Median ≈ 25x. All well above the 3.0 threshold.

### The post-PRIMARY-fix bug surface

Events 8 and 9 (HBARUSDT and MANAUSDT at 22:50) show the system as it exists today:

1. APEX correctly detected a flip attempt and blocked it: `decision_reason=conf_below_threshold` (eff_conf 0.90 < 0.95 Buy→Sell threshold).
2. APEX wrote `apex_dir=Buy`, preserving the brain decision.
3. XRAY downstream saw the same structural picture, saw `_ratio = 34.2x` (HBARUSDT) and `24.0x` (MANAUSDT), and flipped Buy → Sell.

APEX's confidence gate is the right protection at its layer, but XRAY does not consult APEX's flip-confidence reasoning. They operate on independent inputs (Qwen secondary model confidence vs structural R:R ratio) and reach different conclusions on the same trade.

### Quantitative impact in the 48h window

- 76.3% of all REGIME emissions are labeled `ranging`.
- 96.9% of those `ranging` labels (73.9% of ALL labels) come from the ELSE fallback (`conf=0.40`).
- Only 3.1% of `ranging` labels come from the strict ranging criteria.
- Trending labels are 8.8% of total (6.6% trending_down, 2.2% trending_up — bearish trends labeled 3x more than bullish).

## Architecture summary (plain language)

The regime detector is a per-symbol classifier that takes 200 hourly candles of one symbol, computes a small set of indicators (ADX, Plus-DI, Minus-DI, choppiness, NATR-based ATR percentile, volume SMA ratio), and assigns one of 5 labels (`trending_up`, `trending_down`, `ranging`, `volatile`, `dead`) plus a confidence value. The classifier uses 5 explicit branches plus one catch-all `else` that defaults to `ranging` with confidence `0.4`. With current production thresholds, the explicit ranging branch requires very strict conditions (`ADX < 20 AND choppiness > 60`) so the catch-all fires for 96.9% of `ranging` classifications. Per-symbol hysteresis prevents single-tick label flips. The labeled state is cached per-symbol and persisted to two database tables (`regime_history` for the global BTCUSDT regime, `coin_regime_history` for per-coin overrides).

## Number of regime consumers

Counted in `q1_consumers.md`:

- **15 control-flow consumers** that change trading behavior based on the label: ensemble category gate, scorer filter, scanner score bonus, smart_leverage multiplier, APEX direction lock, Layer 4 protection, multiple strategist prompt slots (Stage 2 CALL_A market data, per-coin tags, setup review), brain_v2 evaluator, TIAS collector field write.
- **1 validation consumer**: coin_package_validator completeness check.
- **2 analysis consumers**: volatility_profile, rule_engine.
- **5 display/telemetry consumers**: telegram, backtest_repo column.

The most important consumer for the Sell-bias question is APEX's `_check_direction_lock` (`src/apex/optimizer.py:1033-1079`), which only fires for trending regimes. Mislabeling a weakly-trending coin as `ranging` removes the lock and allows the entire flip chain to proceed.

## Most surprising finding

**73.9% of all regime emissions in production are produced by a single fallback line of code** (`else: regime = MarketRegime.RANGING` at `regime.py:153-156`). The detector reports "ranging" but the underlying conditions are "no clearly trending pattern, no strict ranging signature, no spike, no death — fall through to default." With the current production thresholds, the classifier is more accurately a binary trending vs not-trending classifier with "not-trending" labeled `ranging`. The `dead` and `volatile` branches fire infrequently. The strict ranging branch fires only 3.1% of the time.

This single-line fallback dominates the entire regime signal seen by the rest of the system.

## What Phase 2 must answer

Q1 + Q1b reveal the mechanism by which the regime detector might be biasing the system, but they do not tell us whether the regime labels match actual market behavior. Phase 2 must:

1. For a sample of regime classifications, compute the objective regime from actual 5-min price action and produce a confusion matrix.
2. Compute the false-ranging rate specifically for the ELSE-fallback subset (the 73.9% with `conf=0.40`).
3. Per-symbol accuracy breakdown.
4. Correlate accuracy with trade outcomes.

The Phase 3 discussion report can then weigh Path A (XRAY threshold tune) against Path B (detector fix — most likely candidate B1, closing the ELSE fallback gap).
