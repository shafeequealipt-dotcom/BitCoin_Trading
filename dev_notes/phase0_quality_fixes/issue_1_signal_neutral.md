# Phase 0 — Quality Issue 1: SignalWorker NEUTRAL Distribution

## A — Current observed behaviour

**Live measurement (5+ hour log window, 65 batches × 50 coins = 3,224 signals):**
- Signal type distribution: **NEUTRAL = 3,224 / 3,224 (100%)**, BUY = 0, SELL = 0
- Confidence distribution: min=0.233, mean=0.290, max=0.498, std=0.019–0.055
- 25 / 3,224 signals (0.77%) had confidence ≥ 0.40 (the BUY threshold)
- Phase 29 confidence gate (`signal_generator.py:121-150`) emitted **zero `SIG_DOWNGRADE` events**

**Evidence:**
```
SIG_BATCH_STATS  | conf_min=0.233 conf_max=0.498 conf_mean=0.29 conf_std=0.05
SIG_GEN | sym=DYDXUSDT type=neutral conf=0.26 vol_surge=0.11 ...
SIG_GEN | sym=AAVEUSDT type=neutral conf=0.26 vol_surge=0.47 ...
(3,222 more identical-pattern lines)
```

## B — Expected behaviour

For a market where BTC is mildly trending and altcoins are mixed:
- Signal type distribution: BUY 15–30%, SELL 15–30%, NEUTRAL 40–70%
- Confidence histogram: bell-curve-ish around 0.4–0.6
- For 10 strongly-trending coins: 70%+ of signals match the trend direction

## C — Root cause (verified, NOT what the prompt assumed)

The prompt assumed Mode A (Phase 29 gate too strict). **Investigation refutes this:** the gate is unreachable.

Actual chain of causation:

1. **`SentimentAggregator.aggregate_for_symbol()` at `src/intelligence/sentiment/aggregator.py:163-165`** hard-sets `overall = 0.0` when a coin has no news AND no Reddit:
   ```python
   has_own_data = len(news_scores) > 0 or len(reddit_scores) > 0
   if not has_own_data:
       overall = 0.0    # Phase 15 design — UNKNOWN, not NEUTRAL
   ```

2. **Reddit is intentionally disabled** (`config.toml` has no `reddit.client_id`). Logged once at boot:
   ```
   SENTIMENT_DEGRADED_MODE | reason=no_reddit source=fear_greed_only
   ```

3. **Finnhub free tier covers only majors** (BTC, ETH, SOL, ...). 29 of 50 watch_list altcoins get zero news articles.

4. **Result: 3,156 / 3,224 signals (97.9%) saw `sentiment=0.00`** at evaluation time.

5. **`SignalGenerator._evaluate_signal()` at `src/intelligence/signals/signal_generator.py:313-375`** uses sentiment as a HARD GATE for every BUY/SELL classification rule:
   ```python
   if sentiment > 0.2 and fear_greed <= 20:           # FAIL — sentiment=0
   if sentiment > 0.3 and fear_greed >= 80:           # FAIL
   if funding_rate < -0.01 and sentiment < 0:         # FAIL
   if sentiment > 0.2 and oi_change > 5.0:            # FAIL
   if sentiment < -0.3 and funding_rate > 0.01:       # FAIL
   if sentiment >= 0.5: STRONG_BUY                     # FAIL
   if sentiment >= 0.2: BUY                            # FAIL
   if sentiment <= -0.5: STRONG_SELL                   # FAIL
   if sentiment <= -0.2: SELL                          # FAIL
   else: NEUTRAL                                       # ALL 3,156 LAND HERE
   ```

6. **Phase 29 gate (lines 121-150) only fires on non-NEUTRAL signals** — since 100% are NEUTRAL upstream, the gate is unreachable. Zero `SIG_DOWNGRADE` events confirm this.

## D — Verification approach (post-fix)

| Metric | Measure | Target |
|---|---|---|
| Signal type distribution over 24 cycles × 50 coins | grep `SIG_GEN` workers.log; compute %s | BUY 15-30%, SELL 15-30%, NEUTRAL 40-70% |
| Confidence histogram | `SIG_BATCH_STATS` mean/std over 1 hour | mean 0.4-0.6, std > 0.10 |
| Direction match rate on trending coins | for 10 visually-trending symbols, % matching trend | ≥70% |
| ScannerWorker qualified count rise | `SCANNER_FILTER_AGGREGATE` qualified field, avg over 1h | 5–25 (was 0–2) |
| Phase 29 gate now reachable | grep `SIG_DOWNGRADE` workers.log over 1h | ≥1 event (gate firing) |

## E — Rollback path (if regression)

The fix lives in `_evaluate_signal()` only. Sentiment aggregator unchanged (Phase 15 zero-coverage stays). Rollback is `git revert <phase1-commit>`. Threshold values exposed in `config.toml [signal_generator.multi_source]` so operator can soften without redeploy.

## Files end-to-end mapped

| File | Lines | Role |
|---|---|---|
| `src/workers/signal_worker.py` | 27–177 (class), 69–147 (tick) | SweetSpotWorker; reads SentimentAggregator, calls SignalGenerator, caches per-symbol |
| `src/intelligence/signals/signal_generator.py` | 50–177 (generate_signal), 121–150 (Phase 29 gate), **313–375 (_evaluate_signal — the fix target)** | Where signal_type is decided |
| `src/intelligence/signals/confidence.py` | 19–71 (calculate) | 4-component confidence |
| `src/intelligence/signals/signal_models.py` | 44–50 (CONFIDENCE_THRESHOLDS) | Existing thresholds |
| `src/intelligence/sentiment/aggregator.py` | 88–248 (aggregate_for_symbol), **163–165 (zero-coverage gate — NOT to be touched)** | Phase 15 design — UNCHANGED in this phase |
| `src/workers/scanner_worker.py` | 98–108 (_get_signal_confidence) | Consumer reads `Signal.confidence` only |

## Phase 1 fix outline (preview)

Per user Q1 = "Fix signal rules to use F&G + funding + OI even with sentiment=0.0":

Replace the 9-rule cascade in `_evaluate_signal()` with multi-source weighted scoring:
- Compute 4 component scores in [-1, +1]: `sentiment_score`, `fg_score` (contrarian), `funding_score` (high pos = bearish), `oi_score`
- Mark each "active" iff `abs(score) >= component_min_active`
- `direction_score = weighted_sum_of_active_components`
- Map to STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL by threshold
- If sentiment is 0.0 (no data), it's marked inactive — does NOT pull toward NEUTRAL
- All thresholds in `config.toml [signal_generator.multi_source]`
