# D1 — Strategy Worker (Layer 1C / Strategy Pipeline / Stage 1)

**Capture timestamp:** 2026-04-27 23:03:34 UTC
**Source files (verified line counts and bytes):**
- `src/workers/strategy_worker.py` — 1592 lines, 77,902 bytes (per `wc -l` / `wc -c`)
- `src/strategies/scanner.py` — 668 lines
- `src/strategies/scorer.py` — 467 lines
- `src/strategies/ensemble.py` — 162 lines
- `src/strategies/registry.py` — 133 lines
- `src/strategies/register_all.py` — 132 lines
- `src/strategies/pnl_manager.py` — 449 lines
- `src/strategies/categories/` — 39 strategy files (40 listed in dir, X1 not registered on this run)

**Live observation window (workers.log):** 5 STRAT cycles, 2026-04-27 22:06:30 → 22:26:39

---

## D.1.1 — File overview: `src/workers/strategy_worker.py`

### Methods (every method, one-line description)

| Line | Method | Description |
|---|---|---|
| 56 | `__init__` | Constructs the worker; injects registry/scanner/regime/scorer/ensemble/pnl/ta/repo/services; sets `_score_cache`, `_prev_consensus`, `_tick_times`. |
| 98 | `tick()` | Full Layer 1-4 pipeline (PnL gate → universe → regime → prefetch → L1 scan → L2 score → L3 ensemble → apply restrictions → L4 hand-off). Async. |
| 891 | `get_score(coin)` | Public accessor; returns `_score_cache.get(coin)` (last L2 total_score for the symbol). |
| 904 | `_build_consensus_summary(setups)` | Builds legacy per-coin consensus dict `{symbol: {"buy": int, "sell": int, "total_score": float}}` from a setup list (fed `filtered`). |
| 923 | `_build_per_coin_consensus(setups)` | Builds new per-coin consensus payload `{symbol: {"consensus": str, "consensus_score": float, "vote_count": int, "direction": str, "last_updated": float}}`; takes the highest-`total_score` setup per symbol. |
| 983 | `_execute_claude_trade(trade, position_symbols, plan)` | Executes a single Claude-directed trade: validates symbol/dup/X-RAY/SL-TP/qty, calls `order_svc.place_order(purpose="layer3_entry", layer_snapshot=...)`, registers with coordinator, saves thesis, records to DB, sends Telegram. |

Two top-level constants/state on the class:
- `worker_tier = WorkerTier.LAYER1C` (line 52)
- `cycle_gated = True` (line 54) — Phase 4 LayerManager skip-when-inactive

### The 4 internal "layers" (line ranges inside `tick()`)

The `tick()` method spans lines **98 → 889**. Each numbered comment block declares one layer (numbering follows the source comments `# 1.` … `# 9.` plus the explicit `LAYER` headers).

| Layer | In-file label | Lines | What it does |
|---|---|---|---|
| Pre-pipeline gate | `# 1. Check PnL manager` … `# 5b. Pre-fetch sentiment and altdata` | 113–394 | PnL halt check (line 113); kline-circuit check (140); universe load (157); regime detection (165); active-strategies query (196); M5 + H1 kline batch + TA prefetch (207–315); H1 TA pre-population (317–359); altdata/sentiment fetch (366–394). |
| **Layer 1 (Scanner)** | `# 6. LAYER 1: Scan — run strategies on coins` | 466–549 | Iterates `candles_map` × `symbol_strategies`; calls `strategy.scan(...)`; collects `raw_signals`. Emits `STRAT_L1_DONE`/`STRAT_L1`/`STRAT_L1_SIG`. |
| **Layer 2 (Scorer)** | `# 7. LAYER 2: Score (with sentiment + altdata + structural context)` | 557–634 | `self.scorer.score_batch(...)` → `scored: list[ScoredSetup]`; populates `_score_cache` (lines 585–590); computes percentile + component-avg distribution; emits `STRAT_L2_DONE`/`STRAT_L2`. |
| **Layer 3 (Ensemble)** | `# 8. LAYER 3: Ensemble` | 636–676 | `self.ensemble.vote_batch(...)` → `consensus_setups: list[EnsembleResult]`; emits `STRAT_L3_DONE`/`STRAT_L3`/`STRAT_L3_VOTE`. |
| **Layer 4 (Hand-off)** | `# 9. Apply PnL restrictions (start of L4)` … `═════ LAYER 4: STORE HINTS FOR CLAUDE ═════` | 678–828 | `pnl_manager.apply_restrictions(...)`, builds and writes `_strategy_consensus`, `_strategy_consensus_summary`, `_strategy_hints` on the layer manager; emits `STRAT_CONSENSUS_WRITE`/`STRAT_CONSENSUS_CHANGE`/`STRAT_CONSENSUS_SUMMARY`/`STRAT_L4_HANDOFF`/`STRAT_L4`. |

Cycle close: lines 830–889 emit `STRAT_CYCLE_DONE`, `STRAT_TICK_SLOW` (`>30 s`), and the rolling 10-tick `STRAT_HEALTH`.

---

## D.1.2 — Layer 1: Strategy Scanner

**Files:**
- Universe selection: `src/strategies/scanner.py` (lines 350–579 = `scan_market`); ranks Bybit USDT perps by 0–100 opportunity score (5 components: momentum 0–30, volatility 0–25, trend strength 0–15, volume 0–20, spread 0–10; +regime bonus, −chop penalty). Hard disqualifiers (vol < $5M, price < $0.0001, spread > 0.5%) at scanner.py:424–434. *Not the same as the per-strategy scanning loop in `strategy_worker.tick`.*
- Strategy registration: `src/strategies/registry.py` lines 23–34 (`register`), 44–53 (`get_active_for_regime` — returns ALL enabled strategies; comment line 45-49: "ALL strategies run in ALL regimes. Regime affects sizing, not activation.")
- Bulk register: `src/strategies/register_all.py` — `register_strategies_a_to_f` (A1–F4, 19 strategies, lines 10–57), `register_strategies_g_to_k` (G1–K4, 20 strategies, lines 60–109), `register_all_strategies` (top-level, line 112; X1 only on testnet, lines 117–130).

### All 40 strategy files (39 active in current run, X1 testnet-only)

Live boot log confirms 39 registered (workers.log 22:53:35.187: `Total strategies registered: 39`). X1 file exists at `src/strategies/categories/x1_always_trade.py` but did not register on the 2026-04-27 22:53 boot (testnet flag false).

| ID | Class | File | Category | Detects |
|---|---|---|---|---|
| A1 | `RSIReversalScalp` | `a1_rsi_reversal.py:14` | scalping | RSI<25 oversold at lower BB + stoch crossing up + vol≥1.5× (long); mirror short. Line 1 docstring: "Buy oversold, sell overbought on 5-min chart." |
| A2 | `VWAPBounceScalp` | `a2_vwap_bounce.py:14` | scalping | Price within 0.1% of VWAP + RSI 40–50 + 8/12 candles above VWAP + bullish pattern + vol < 0.8× (long). |
| A3 | `BBSqueezeScalp` | `a3_bb_squeeze_scalp.py:14` | scalping | BB bandwidth < 2.0 + price > BB upper + macd_hist > 0 + vol_ratio ≥ 2.0 (long). |
| A4 | `EMACrossoverMomentum` | `a4_ema_crossover.py` | scalping | EMA crossover with trend confirmation. |
| B1 | `VolumeBreakout` | `b1_volume_breakout.py:14` | momentum | BB bandwidth < 3 + price > BB upper + vol_ratio ≥ 3.0 + RSI > 60 + macd_hist > 0 + ADX ≥ 20 (long). M15 timeframe. |
| B2 | `SupertrendFollower` | `b2_supertrend_follower.py:14` | momentum | Supertrend dir == 1 + price > SMA50 + MACD line > 0 + ADX ≥ 25 + 50 ≤ RSI ≤ 70 + vol ≥ 1.0 (long). H1 timeframe. |
| B3 | `IchimokuBreakout` | `b3_ichimoku_breakout.py` | momentum | Multi-indicator trend confirmation using Ichimoku proxies. |
| B4 | `DoubleBottomTop` | `b4_double_bottom_top.py` | momentum | Pattern-based reversal with divergence confirmation. |
| C1 | `BBMeanReversion` | `c1_bb_mean_reversion.py` | mean_reversion | Buy at lower BB, sell at upper BB in ranging markets. |
| C2 | `RSIDivergence` | `c2_rsi_divergence.py` | mean_reversion | Detect price/RSI divergence for reversals. |
| D1 | `FundingRateFade` | `d1_funding_rate_fade.py` | funding_arb | Contrarian trade when funding rates are extreme. |
| D2 | `OIDivergence` | `d2_oi_divergence.py` | funding_arb | Trade when price and open interest diverge. |
| E1 | `FearGreedExtreme` | `e1_fear_greed_extreme.py` | sentiment | Contrarian trade at extreme F&G levels. |
| E2 | `NewsBreakout` | `e2_news_breakout.py` | sentiment | Trade strong news-driven moves with volume confirmation. |
| E3 | `SentimentMomentum` | `e3_sentiment_momentum.py` | sentiment | Trade sentiment shifts confirmed by price and volume. |
| F1 | `SupportResistanceBounce` | `f1_support_resistance.py` | advanced | Trade bounces off key S/R levels. |
| F2 | `MultiTFAlignment` | `f2_multi_tf_alignment.py` | advanced | Enter when all TF indicators align. |
| F3 | `LiquidationHunt` | `f3_liquidation_hunt.py` | advanced | Trade liquidation cascades in leveraged markets. |
| F4 | `GridRecovery` | `f4_grid_recovery.py` | advanced | Add to losing positions in ranging markets only. (Activates only for losing positions per docstring.) |
| G1 | `StopHuntSniper` | `g1_stop_hunt_sniper.py` | predatory | Trade reversals after stop hunt wicks beyond S/R. |
| G2 | `RetailSentimentFade` | `g2_retail_sentiment_fade.py` | predatory | Contrarian trade against extreme crowd sentiment. |
| G3 | `LiquidationFrontrunner` | `g3_liquidation_frontrunner.py` | predatory | Front-run liquidation cascades. |
| G4 | `WhaleShadow` | `g4_whale_shadow.py` | predatory | Follow unusually large volume candles (whale activity). |
| H1 | `FundingPrediction` | `h1_funding_prediction.py` | microstructure | Position before extreme funding collection. |
| H2 | `SpreadBasisExploit` | `h2_spread_basis.py` | microstructure | Trade perp premium/discount to index. |
| H3 | `VolatilitySwitch` | `h3_volatility_switch.py` | microstructure | Trade breakout from ultra-tight squeeze. |
| H4 | `OrderFlowImbalance` | `h4_order_flow.py` | microstructure | Detect directional flow from consecutive candles. |
| I1 | `KillZoneTrading` | `i1_kill_zone.py` | time_based | Trade during high-impact session opens. |
| I2 | `WeekendGapExploit` | `i2_weekend_gap.py` | time_based | Trade thin-volume weekend stop hunts. |
| I3 | `OptionsExpiryPlay` | `i3_options_expiry.py` | time_based | Trade mean reversion near monthly expiry. |
| I4 | `HourlyCloseMomentum` | `i4_hourly_close.py` | time_based | Trade consecutive strong closes. |
| J1 | `BTCDominanceRotation` | `j1_btc_dominance.py` | cross_market | Trade BTC vs alts rotation. |
| J2 | `CorrelationBreakdown` | `j2_correlation_breakdown.py` | cross_market | Trade when asset diverges from BTC. |
| J3 | `CrossExchangeLag` | `j3_cross_exchange_lag.py` | cross_market | Arbitrage last_price vs mark_price. |
| J4 | `AltcoinBetaAmplification` | `j4_altcoin_beta.py` | cross_market | Trade lagging alts that will catch up to BTC. |
| K1 | `ClaudeConviction` | `k1_claude_conviction.py` | ai_enhanced | Deep Claude API analysis for high-quality setups. K1 does not independently scan (per docstring, line 11–12). |
| K2 | `PatternMemory` | `k2_pattern_memory.py` | ai_enhanced | Match current market state to historical patterns. |
| K3 | `MultiStrategyEnsemble` | `k3_ensemble.py:14` | ai_enhanced | Placeholder. Logic in `ensemble.py`. `scan()` returns None, `vote()` returns `("NEUTRAL", 0.0, "K3 does not vote — it IS the voting system")` (lines 25–29). |
| K4 | `AdaptiveOptimizer` | `k4_adaptive_optimizer.py` | ai_enhanced | Placeholder. Logic in `optimizer.py`. Doesn't trade — tunes parameters and weights. |
| X1 | `AlwaysTradeStrategy` | `x1_always_trade.py` | (testnet only) | Forces trades on testnet for data generation. NOT registered in current run. |

### Live measurement: which strategies fire most / never fire

Source: `data/logs/workers.log` `STRAT_L1_DONE` lines (5 cycles between 22:06–22:26 in the live log; 7 cycles total when including the previous workers.2026-04-27 file). Cumulative `top_firing` counts:

| Strategy | Fire count (5 cycles) | Notes |
|---|---|---|
| `B4_double_bottom_top` | 63 | Dominates — fires 9 signals on every cycle observed. (`B4_double_bottom_top:9` repeated 5 cycles.) |
| `A3_bb_squeeze` | 7 | Fires 1× in 3 cycles. |
| `H3_vol_switch` | 5 | Fires 2× one cycle, 1× another. |
| `A4_ema_crossover` | 3 | Fires once. |
| `B2_supertrend` | 3 | Fires 2× one cycle. |
| `B3_ichimoku` | 2 | Fires once each in 2 cycles. |
| `A2_vwap_bounce` | 1 | Fires once. |
| `H4_order_flow` | 1 | Fires once. |
| `I4_hourly_close` | 1 | Fires once. |
| **Never fired (count = 0):** | | A1, B1, C1, C2, D1, D2, E1, E2, E3, F1, F2, F3, F4, G1, G2, G3, G4, H1, H2, I1, I2, I3, J1, J2, J3, J4, K1, K2, K3, K4 — **30 of 39** |

(Note: the `non_firing` log field is truncated to `[:5]` in `strategy_worker.py:535`, so the bottom-five list is not exhaustive. The "never fired" count above is computed from `top_firing` absence across all observed cycles.)

Verbatim sample STRAT_L1_DONE (workers.log line @ 22:06:33.311):
```
STRAT_L1_DONE | signals=10 strategies=39 coins=50 per_strategy_avg=0.26 top_firing=[B4_double_bottom_top:9,A3_bb_squeeze:1] non_firing=[A1_rsi_reversal,A2_vwap_bounce,A4_ema_crossover,B1_volume_breakout,B2_supertrend] el=29ms | sid=s-1777327590001
```

---

## D.1.3 — Layer 2: Trade Scorer

**File:** `src/strategies/scorer.py`

### Score formula (verbatim, lines 47–53)

```python
base = self._score_base(signal)
confluence = self._score_confluence(signal, ta_data)
context = self._score_context(signal, ta_data, sentiment_data, altdata, regime)
quality = self._score_quality(signal, candles, ta_data, structural_data)

total = base + confluence + context + quality
```

Grade thresholds (lines 58–67):
```
total >= 80 -> A+
total >= 68 -> A
total >= 56 -> B
total >= 45 -> C
else        -> D
```

### Component breakdown

| Component | Range | Source method | Formula summary |
|---|---|---|---|
| `base` | 0–40 | `_score_base` (lines 114–125) | Starts at 30. +3 per condition with strength > 0.8, +2 per > 0.6, +1 per > 0.4. Clamped at 40. |
| `confluence` | 0–25 | `_score_confluence` (lines 127–172) | Trend agreement ±5/−3, momentum agreement ±5/−3, volume confirmation +5, overall TA signal ±5/−3, volatility favorable +5. Clamped 0–25. |
| `context` | 0–20 | `_score_context` (lines 174–240) | Higher-TF agreement +10 (or +3 if disagree, when conf>0.6), sentiment ±3, F&G 0–8 (extreme contrarian gets max), funding 0–4, regime match +2. Clamped 20. |
| `quality` | 0–20 | `_score_quality` (lines 242–319) | Volume 0–3, S/R proximity 0–3 basic OR 0–8 X-RAY (`_xray_sr_score` at line 322), clean candle structure 0–3, baseline +3. Clamped 20. |

X-RAY `_xray_sr_score` (lines 322–467) modifiers (each tracked in `_m` dict):
- entry_quality: ideal +3 / good +2 / poor −1
- rr_quality: excellent +2 / good +1
- structure dir: aligned +2 / against −2
- BOS/CHoCH: +1 / −2
- FVG: +1; OB fresh: +1.2; SMC≥70: +0.8; sweep: high_prob +1.5 / mod +0.8
- POC favorable: +0.5; FIB confluence: +0.8; MTF: +1.0
- Session: NY mid +0.3; manipulation_likely −0.5
- RR-skip penalty: −3.0 (when `rr_quality=='skip'` and not fallback)
- Final clamp 0–8.

### Live distribution — last 50 ScoredSetups (5 cycles aggregated)

`STRAT_L2_DONE` percentile + component avg lines from `workers.log` (5 most-recent cycles):

| Cycle (UTC) | scored | p25 | p50 | p75 | p95 | base avg | confl avg | ctx avg | qual avg |
|---|---|---|---|---|---|---|---|---|---|
| 22:06:33 | 10 | 43.0 | 52.0 | 60.8 | 68.0 | 33.5 | 9.0 | 3.2 | 8.4 |
| 22:11:33 | 14 | 45.8 | 66.0 | 69.0 | 72.0 | 33.2 | 12.5 | 5.1 | 9.0 |
| 22:16:38 | 13 | 48.3 | 60.0 | 68.8 | 79.0 | 33.8 | 11.2 | 6.8 | 7.9 |
| 22:21:37 | 12 | 47.8 | 53.0 | 59.0 | 65.0 | 33.2 | 7.7 | 4.0 | 8.6 |
| 22:26:39 | 10 | 49.3 | 50.0 | 55.8 | 62.0 | 33.1 | 8.5 | 4.2 | 6.7 |
| **Aggregate (n=59)** | — | — | — | — | — | **33.4** | **9.78** | **4.66** | **8.12** |

Total composite mean ≈ 56.0 (B grade). Best single setup observed: 82 (A+) at 22:16:38 (CRVUSDT).

Component dominance: `base` (~33) carries the majority of every score; `confluence` floats 7.7–12.5; `context` low (3.2–6.8 — sentiment/F&G mostly empty in this run); `quality` 6.7–9.0.

Sample verbatim:
```
STRAT_L2_DONE | scored=14 score_p25=45.8 score_p50=66.0 score_p75=69.0 score_p95=72.0 score_components_avg=[base:33.2,confluence:12.5,context:5.1,quality:9.0] el=34ms | sid=s-1777327890003
```

---

## D.1.4 — Layer 3: Ensemble Voter

**File:** `src/strategies/ensemble.py`

### Voting logic (verbatim, lines 35–135 — entry method `vote`)

```python
def vote(
    self,
    setup: ScoredSetup,
    candles_map: dict[str, list[OHLCV]],
    ta_map: dict[str, dict],
    sentiment_data: dict | None,
    altdata: dict | None,
    regime: RegimeState,
) -> EnsembleResult:
    signal = setup.raw_signal
    symbol = signal.symbol
    direction = signal.direction
    originator = signal.strategy_name

    active = self.registry.get_active_for_regime(regime.regime)
    candles = candles_map.get(symbol, [])
    ta_data = ta_map.get(symbol, {})

    votes: list[EnsembleVote] = []
    for strategy in active:
        if strategy.name == originator:
            continue
        try:
            vote_str, confidence, reasoning = strategy.vote(
                symbol=symbol,
                direction=direction,
                candles=candles,
                ta_data=ta_data,
                sentiment_data=sentiment_data,
                altdata=altdata,
            )
            perf = self.registry.get_performance(strategy.name)
            weight = perf.ensemble_weight

            votes.append(EnsembleVote(
                strategy_name=strategy.name,
                vote=vote_str,
                confidence=confidence,
                weight=weight,
                reasoning=reasoning,
            ))
        except Exception as e:
            log.warning(...)

    buy_votes = sum(v.weight * v.confidence for v in votes if v.vote == "BUY")
    sell_votes = sum(v.weight * v.confidence for v in votes if v.vote == "SELL")
    neutral_votes = sum(v.weight for v in votes if v.vote == "NEUTRAL")

    agreeing = buy_votes if direction == Side.BUY else sell_votes
    opposing = sell_votes if direction == Side.BUY else buy_votes
    consensus_dir = "BUY" if direction == Side.BUY else "SELL"

    # Consensus determines SIZE, not eligibility. All levels pass.
    CONSENSUS_SIZE = {"STRONG": 1.0, "GOOD": 0.75, "LEAN": 0.50, "WEAK": 0.30, "CONFLICT": 0.15}
    cfg = self.settings.strategy_engine
    if agreeing >= 4.0 and opposing <= 1.5:
        consensus = "STRONG"
    elif agreeing >= cfg.min_ensemble_agreement and opposing <= cfg.max_ensemble_opposition:
        consensus = "GOOD"
    elif agreeing >= 1.5 and opposing <= 1.5:
        consensus = "WEAK"
    elif agreeing > opposing:
        consensus = "LEAN"
    else:
        consensus = "CONFLICT"
        log.warning(f"ENSEMBLE_CONFLICT | sym={setup.raw_signal.symbol} buy={agreeing:.1f} sell={opposing:.1f} | {ctx()}")

    size_mult = CONSENSUS_SIZE.get(consensus, 0.3)
```

### Consensus categorization (lines 99–113 verbatim)

```
CONSENSUS_SIZE = {"STRONG": 1.0, "GOOD": 0.75, "LEAN": 0.50, "WEAK": 0.30, "CONFLICT": 0.15}
STRONG  : agreeing >= 4.0  AND opposing <= 1.5
GOOD    : agreeing >= cfg.min_ensemble_agreement AND opposing <= cfg.max_ensemble_opposition
WEAK    : agreeing >= 1.5  AND opposing <= 1.5
LEAN    : agreeing > opposing
CONFLICT: else
```

`size_multiplier` is a **fixed lookup**, not a continuous score: `{1.0, 0.75, 0.50, 0.30, 0.15}`.

### Why every STRONG coin scores `votes=38 score=1.00` (root cause)

Source of the log line: `strategy_worker.py:755-759`:

```python
log.info(
    f"STRAT_CONSENSUS_CHANGE | sym={sym} "
    f"from={prev or 'NONE'} to={entry['consensus']} "
    f"votes={entry['vote_count']} score={entry['consensus_score']:.2f} | {ctx()}"
)
```

`entry` is a row of `_build_per_coin_consensus(...)`. From `strategy_worker.py:959-961`:

```python
consensus_score = float(getattr(sw, "size_multiplier", 0.5) or 0.5)
vote_count = len(getattr(sw, "votes", []) or [])
```

So:

1. **`votes=38` is constant by construction.** `vote_count` = number of `EnsembleVote` rows, which is `len(active) - 1` (originator is excluded at ensemble.py:63). Active count is 39 for the current registration → every setup yields exactly 38 votes regardless of how those 38 voted. The field is a count of voters polled, not a count of agreement.
2. **`score` is the `size_multiplier`, which is one of 5 fixed values.** From ensemble.py:99–113, `size_multiplier ∈ {1.0, 0.75, 0.50, 0.30, 0.15}` — discrete category labels, not a continuous score. So every STRONG coin will always log `score=1.00`, every GOOD `0.75`, every WEAK `0.30`, every LEAN `0.50`, every CONFLICT `0.15`. The label is doing the work; the number is a 1:1 alias.

The "every STRONG coin scoring identical votes=38 score=1.00" observation is therefore a **definition mismatch**, not a bug: the log field labelled `score` is the discrete size-multiplier alias (1.0 ↔ STRONG), and `votes` is the polled-voter count (always 38 with a 39-strategy registry minus originator).

The actual continuous quantities — `agreeing`, `opposing`, `setup.total_score` — are not in the `STRAT_CONSENSUS_CHANGE` line.

Verbatim live ENSEMBLE summary (workers.log 22:11:33.802):
```
ENSEMBLE | setups=14 strong=7 good=2 weak=4 conflict=0 | sid=s-1777327890003
```

---

## D.1.5 — Layer 4: Hand-off

Hand-off targets are written by `tick()` after `apply_restrictions`:

| Target | Where written | Source value | Live size (5 cycles) |
|---|---|---|---|
| `self._score_cache` | `strategy_worker.py:585-590` | `{symbol: float(scored_setup.total_score)}` for every L2 setup. Note: keyed by symbol, so a later cycle's entries overwrite earlier ones. | 16, 18, 18, 19, 19 (size grows monotonically across the run; never the full 50). |
| `layer_manager._strategy_consensus` | `strategy_worker.py:721-734` | `_build_per_coin_consensus(consensus_setups)` output. Built from FULL `consensus_setups` (not `filtered`) — Phase 4 fix per comment line 707-719. Updated via `existing.update(new_consensus)` so stale entries from prior cycles persist (only updates processed coins). | `cache_size_after`: 16, 18, 18, 19, 19. |
| `layer_manager._strategy_consensus_summary` | `strategy_worker.py:737` | `_build_consensus_summary(filtered)` — legacy shape `{sym: {"buy": int, "sell": int, "total_score": float}}`. Built from POST-PnL-filter `filtered` (preserved for legacy strategist reads at `strategist.py:1017/1587`). | 7, 7, 7, 6, 7. |
| `layer_manager._strategy_hints` | `strategy_worker.py:803` (gated by `is_layer_active(3)`) | List of dicts `{symbol, direction, strategy, score, consensus}` from `filtered[:20]`. | 7, 9, 9, 7, 7. |

`STRAT_L4_HANDOFF` verbatim (workers.log 22:11:33.807):
```
STRAT_L4_HANDOFF | score_cache_size=18 consensus_size=18 consensus_summary_size=7 hints_top20_size=9 el=2ms | sid=s-1777327890003
```

---

## D.1.6 — Why ensemble flaps for AAVEUSDT

`STRAT_CONSENSUS_CHANGE` events for AAVEUSDT, last 6 cycles (5 in current log, 1 from prior run):

| Cycle (UTC) | from → to | votes | score (size_mult alias) |
|---|---|---|---|
| 21:56:33 | NONE → STRONG | 38 | 1.00 |
| 22:06:33 | STRONG → GOOD | 38 | 0.75 |
| 22:11:33 | GOOD → WEAK | 38 | 0.30 |
| (22:16:38 — no change logged → AAVEUSDT was either WEAK still, missing, or absent from `consensus_setups` that cycle.) | | | |
| 22:21:37 | WEAK → STRONG | 38 | 1.00 |
| 22:26:39 | STRONG → GOOD | 38 | 0.75 |

Pattern: **STRONG → GOOD → WEAK → (no entry) → STRONG → GOOD** within 30 minutes.

What's changing each cycle that produces this oscillation:

1. **The originator strategy can flip between cycles.** The log does not record originator per cycle, but the consensus is computed *only* if a strategy fired a raw signal on AAVEUSDT that cycle. From the L1 fire counts, the dominant signal-producer is `B4_double_bottom_top` (63 of ~59 total signals). Different originators → different excluded voter → different `agreeing/opposing` totals.
2. **`agreeing`/`opposing` are continuous floats** (sum of `weight * confidence`) but get bucketed by hard thresholds:
   - STRONG requires `agreeing >= 4.0 AND opposing <= 1.5`
   - GOOD requires `agreeing >= cfg.min_ensemble_agreement AND opposing <= cfg.max_ensemble_opposition`
   - WEAK requires `agreeing >= 1.5 AND opposing <= 1.5`
   - LEAN otherwise (and `agreeing > opposing`)
   
   A 0.1-point movement of `agreeing` across 4.0 (e.g. 4.05 → 3.95) flips STRONG↔GOOD; movement across `min_ensemble_agreement` flips GOOD↔WEAK. The categorical output exaggerates small input changes.
3. **TA inputs change each cycle.** `STRAT_PREFETCH` shows fresh M5 + H1 kline batches every cycle (`db=...ms ta=...ms`). Each strategy's `vote()` reads fresh `ta_data` (RSI, BB, MACD, etc.); even small numeric drift can flip a `BUY/SELL/NEUTRAL` vote.
4. **No regime change observed during the window** — `STRAT_REGIME_DIST` is identical across all 5 cycles (`up=0 down=20 ranging=23 volatile=6 dead=0 other=0 total=49 global=ranging`), so regime is *not* the input flipping.
5. **Direction may flip across cycles.** `_build_per_coin_consensus` keeps the highest-`total_score` setup per symbol; if cycle N's top AAVEUSDT signal is BUY and cycle N+1's is SELL, the ensemble computes `agreeing` from the opposite sign, which can flip categories sharply.

The `STRAT_CONSENSUS_CHANGE` line does not preserve the originator name, the direction, or the raw `agreeing`/`opposing` values — those would be needed to diagnose which specific input flipped between any two cycles. From the log alone, the cause is "the underlying continuous votes drift across hard category thresholds, while the L1 originator and direction can change cycle-to-cycle."

---

## D.1.7 — Why 5 strategies (A1/A2/A3/B1/B2) never fire

The prompt names A1/A2/A3/B1/B2. **Live measurement disagrees about A2/A3/B2:** in the 5-cycle window, A2 fired 1×, A3 fired 7×, B2 fired 3×. Only **A1, B1, and (effectively) A3 in early cycles** are non-firing. For completeness, all 5 are analysed below against current observed market conditions (`global=ranging`; per-coin: 23 ranging, 20 down, 6 volatile, 0 up).

### A1 — `RSIReversalScalp` (`a1_rsi_reversal.py:14`, scan lines 27–87)

Trigger conditions (verbatim, lines 46–53 long path):
```
if rsi < 25 and bb_lower and price <= bb_lower:
    if vol_ratio < 1.5: return None
    if stoch_k is None or stoch_d is None or not (stoch_k > stoch_d and stoch_k < 25):
        return None
    if adx > 30 and minus_di > plus_di:
        return None  # Strong downtrend
```
Conjunction: RSI<25 AND price<=BB lower AND vol_ratio>=1.5 AND stoch_k>stoch_d AND stoch_k<25 AND NOT(adx>30 AND −DI>+DI).

Under current conditions: most coins are ranging or in mild downtrend (regime dist 23 ranging / 20 down). RSI<25 is itself a tail event; combined with vol_ratio≥1.5 AND stoch crossover<25 AND no strong downtrend, the conjunction is very tight. The mirror SHORT path requires RSI>75. **Could fire** if a coin enters a sharp oversold flush with low ADX, but probability per coin per cycle is low. 0 fires in 5 cycles is consistent.

### A2 — `VWAPBounceScalp` (`a2_vwap_bounce.py:14`, scan lines 27–82)

Trigger (verbatim, lines 41–49):
```
if vwap_dist_pct < 0.001 and 40 <= rsi <= 50:
    above_count = sum(1 for c in candles[-12:] if c.close > vwap)
    if above_count < 8: return None
    if not has_bullish_pattern(ta_data): return None
    if vol_ratio > 0.8: return None  # Want low volume pullback
```
Requires applicable_regimes ∈ {TRENDING_UP, TRENDING_DOWN} (line 19). In the current window, `up=0 down=20`. Live: A2 fired 1× (in the 22:26 cycle on a coin in a downtrend), so this strategy DOES fire under current conditions, just rarely (price within 0.1% of VWAP is a narrow band).

### A3 — `BBSqueezeScalp` (`a3_bb_squeeze_scalp.py:14`, scan lines 27–83)

Trigger (verbatim, lines 40–57):
```
if bb_bw is None or bb_upper is None or bb_lower is None: return None
if bb_bw >= 2.0: return None  # No squeeze
...
if vol_ratio < 2.0: return None  # Need volume on breakout
if price > bb_upper and macd_hist and macd_hist > 0:
    # LONG signal
```
Applicable regimes: RANGING, VOLATILE. Live: A3 fired 7 times across the window. Conjunction (BB_bw<2.0 AND vol_ratio≥2.0 AND price-broke-band AND macd_hist sign-aligned) is reachable in current ranging market.

### B1 — `VolumeBreakout` (`b1_volume_breakout.py:14`, scan lines 27–82)

Trigger (verbatim, lines 46–50):
```
if bb_bw < 3 and price > bb_upper and vol_ratio >= 3.0 and rsi > 60:
    if macd_hist is None or macd_hist <= 0: return None
    if adx < 20: return None
```
Conjunction: BB_bw<3 AND price>BB upper AND vol_ratio>=3.0 AND RSI>60 AND macd_hist>0 AND ADX>=20. **Vol_ratio≥3.0 is the rare gate** — it means current candle volume is ≥3× SMA20 of volume. M15 timeframe (line 21). In a ranging market with `volatile=6`, vol_ratio≥3.0 is uncommon. 0 fires consistent.

### B2 — `SupertrendFollower` (`b2_supertrend_follower.py:14`, scan lines 27–88)

Trigger (verbatim, lines 46–54):
```
if st_dir == 1 and price > sma_50:
    if macd_line is None or macd_line <= 0: return None
    if adx < 25: return None
    if not (50 <= rsi <= 70): return None
    if vol_ratio < 1.0: return None
```
Applicable regimes: TRENDING_UP, TRENDING_DOWN (line 19). H1 timeframe. With current `up=0 down=20`, only the SHORT path is reachable. Conjunction (ADX≥25 AND 30≤RSI≤50 AND macd_line<0 AND vol_ratio≥1.0 AND price<SMA50 AND st_dir=−1) requires a clean H1 downtrend. Live: B2 fired 3× in the 5-cycle window (2× on one cycle), so this DOES fire under current conditions.

### Summary

Of the 5 named: **B2 fires (3×), A3 fires (7×), A2 fires (1×). A1 and B1 are 0/5.** 30 of 39 strategies have 0 fires across the observed window. Possible causes (per code, not measured): tight conjunctions on rare conditions (B1's vol_ratio≥3.0); regime mismatch (A2/B2 require trending; current is ranging); or dependence on data the prefetch loop doesn't carry (e.g., funding-rate-based D1/D2/H1, sentiment-based E*/G2, news E2 — `sentiment_context` and `altdata_context` are mostly empty in the current run; see strategy_worker.py:366–394). The L1 sweep iterates all 39 strategies on all 50 coins; nothing structural prevents the silent strategies from firing — their conjunctions are simply not satisfied by the current market state and prefetched-data set.

---

## D.1.8 — `apply_restrictions` filter

**File:** `src/strategies/pnl_manager.py`
**Method:** `DailyPnLManager.apply_restrictions` (lines 310–333)

Verbatim:
```python
def apply_restrictions(
    self, setups: list[EnsembleResult], mode: dict,
) -> list[EnsembleResult]:
    """Filter setups based on current mode restrictions."""
    if mode["mode"] == "HALTED":
        return []

    threshold = mode["max_score_threshold"]
    allowed_coins = mode.get("allowed_coins")
    allowed_risk = mode.get("allowed_risk_levels", [])

    filtered: list[EnsembleResult] = []
    for setup in setups:
        signal = setup.scored_setup.raw_signal
        if setup.scored_setup.total_score < threshold:
            continue
        if allowed_coins is not None and signal.symbol not in allowed_coins:
            continue
        if allowed_risk and signal.strategy_category not in ("scalping",):
            # Check risk level from strategy category as proxy
            pass
        filtered.append(setup)

    return filtered
```

Threshold is read from the active mode dict. NORMAL mode is defined at lines 241–250:
```python
elif pct >= cfg.caution_threshold_pct:
    return {
        "mode": "NORMAL",
        "max_score_threshold": 50,
        "max_leverage": 5,
        "allowed_coins": None,
        "max_positions": 10,
        "allowed_risk_levels": ["low", "medium", "high"],
        "message": "Normal mode. Full aggression.",
    }
```

So in NORMAL mode the filter passes any setup whose `total_score >= 50`, no coin restriction, no risk restriction effectively (the `allowed_risk` block is a no-op in current code: the inner `if` body is `pass`). The other modes have higher thresholds (CAUTION 80, SURVIVAL 80, PROTECT 85, TARGET_HIT 90, HALTED 100).

### Live measurement: setup survival per cycle (mode=NORMAL, threshold=50)

`STRAT_CONSENSUS_WRITE` lines (5 cycles):

| Cycle | scored (in) | filtered (out) | survival |
|---|---|---|---|
| 22:06:33 | 10 | 7 | 70% |
| 22:11:33 | 14 | 9 | 64% |
| 22:16:38 | 13 | 9 | 69% |
| 22:21:37 | 12 | 7 | 58% |
| 22:26:39 | 10 | 7 | 70% |
| **Aggregate** | **59** | **39** | **66.1%** |

Verbatim sample (workers.log 22:06:33.370):
```
STRAT_CONSENSUS_WRITE | full_count=10 filtered_count=7 setups_in=10 cache_size_after=16 mode=NORMAL threshold=50 | sid=s-1777327590001
```

Live PnL gate (`STRAT_PNL_GATE`) confirms NORMAL mode: `pnl_pct=+0.00`, halted=N across all 5 cycles (e.g. `STRAT_PNL_GATE | halted=N rsn=ok pnl_pct=+0.00 wins=0 losses=2 el=0ms`). With `pct=0` ≥ `caution_threshold_pct`, `get_current_mode()` returns NORMAL (lines 242–250).
