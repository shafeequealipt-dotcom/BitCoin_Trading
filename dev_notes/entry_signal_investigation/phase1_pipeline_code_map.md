# Phase 1 — Entry Pipeline Code Map

Maps the entry pipeline end-to-end. Read-only investigation. Five parallel agents read all 17,463 lines of source. Findings consolidated with file:line citations throughout.

## Pipeline Layers

```
Layer 1A — regime detection (RegimeDetector)
Layer 1B — structure_worker (XRAY setup_type), signal_worker/signal_generator (SIG_CLASSIFY), regime_worker
Layer 1C — strategy_worker (36 strategies), ensemble (consensus + final_size_mult)
Layer 1D — scanner_worker (briefing), state_labeler (entry-setup labels)
Layer 2  — strategist CALL_A (consumes everything, sends to Claude, receives JSON)
Layer 3  — APEX (assembler → gate → optimizer; can flip direction & size; final pre-execution)
Layer 4  — Execution (Bybit demo or shadow)
```

Schedule order each 5-minute cycle: structure @ 0:45 → signal @ 1:00 → regime @ 1:15 → strategy @ 2:00 → scanner @ 4:00 → strategist @ 5:00 → APEX → execute. This means setup_type and SIG_CLASSIFY are computed BEFORE the cycle's per-coin regime is refreshed.

---

## Layer 1B — Structure, Signal, Regime Workers

### src/workers/structure_worker.py (530 lines)

**Contract.** Reads `settings.universe.watch_list` (`:488`), `settings.structure.*` (`:82,142,516,525`), H1 klines via `MarketRepository.get_klines(symbol, "60", 200)` (`:513-515`) with `ShadowKlineReader` fallback (`:524`). Delegates classification to `StructureEngine.analyze()` (`:146-149`). Writes to `StructureCache.set()` (`:151`). Emits `XRAY_CLASSIFY`, `XRAY_NONE_REASON`, `XRAY_COUNTER_INVERSION_APPLIED`, `XRAY_CLASSIFY_SUMMARY`, `XRAY_DIRECTION_SPLIT`, `XRAY_CACHE_HEALTH`, `XRAY_TICK_SUMMARY` log events. No DB writes from the worker itself.

**setup_type classifier.** Lives in `StructureEngine.classify_setup()` at `src/analysis/structure/structure_engine.py:1061-1362`. Top-down first-match-wins decision tree. Eight terminal labels:
1. `bullish_fvg_ob` (`:1187-1202`) — bullish FVG present + bullish OB fresh + `_bull_alignment(direction, struct)` + `mtf_score_01 >= fvg_ob_min(0.7)`. Confidence = `min(mtf_score_01, smc_01)`.
2. `bearish_fvg_ob` (`:1204-1217`) — mirror.
3. `bullish_fvg_ob_counter` (`:1228-1247`) — counter-trade variant; sets `trade_direction="long"` (inversion). Confidence multiplied by `counter_mult` (default 0.7). Requires `mtf_score_01 >= counter_mtf_min` (default 0.40).
4. `bearish_fvg_ob_counter` (`:1249-1269`) — mirror.
5. `bullish_structural_break` (`:1272-1294`) — `last_bos.direction=="bullish"`, `direction=="long"`, AND (`not require_retest` OR `bos.significance=="major"`). Confidence = `max(mtf_score_01, smc_01)`, multiplied by `bos_minor_mult` (0.8) when not major.
6. `bearish_structural_break` (`:1296-1309`) — mirror.
7. `bullish_liquidity_sweep` (`:1316-1323`) — sweep_depth_pct >= `sweep_min_pct` (0.5) AND sweep_type bullish AND direction long.
8. `bearish_liquidity_sweep` (`:1324-1330`) — mirror.
9. `bullish_range_breakout` (`:1339-1347`) — `position_in_range >= 0.95` AND direction long.
10. `bearish_range_breakdown` (`:1348-1356`) — `position_in_range <= 0.05` AND direction short.
11. `SetupType.NONE` otherwise (`:1358-1362`).

**Hardcoded values catalogue.**
- `:1099` `fvg_ob_min=0.7`
- `:1100-1102` `require_retest=True`
- `:1103-1105` `sweep_min_pct=0.5`
- `:1106-1108` `breakout_min_bars=20`
- `:1118-1120` `ranging_market_mtf_threshold=0.55`
- `:1123-1125` `counter_enabled=True`, `counter_mult=0.7`, `counter_mtf_min=0.40`
- `:1131` `bos_minor_mult=0.8`
- `:1149,1152` normalisation divisors 10.0 (MTF) and 100.0 (SMC)
- `:1340,1349` `position_in_range` extremes 0.95 / 0.05
- structure_worker.py:514 klines lookback 200; `:298` p95 idx mult 0.95; `:297` p50 idx mult 0.50

**Regime conditionality: NONE.** Zero references to `regime`, `RegimeDetector`, `MarketRegime`. `_bull_alignment` uses `market_structure.structure` ∈ {uptrend, downtrend, ranging} which is structural, not regime. The pipeline classifies setup_type *before* regime is refreshed for the cycle.

**Discrepancies.** Legacy `_scanner=None` constructor arg (`:56`) dead code. Stale comment about "scanner-sourced universe" (`:384`). `xray_setup_type_count` migration column (`migrations.py:1291`) exists with comment "Population is wired in a follow-up commit" — no writes found in source, column durably NULL.

### src/workers/signal_worker.py (243 lines)

**Contract.** Reads watch_list (`:77`), pre-warms `SentimentAggregator` (`:108`), delegates to `SignalGenerator.generate_signal(symbol)` (`:119`). Stores into `_signal_cache` (`:125`). Emits `SIG_BATCH`, `SIG_TICK_SUMMARY`, `SIG_INPUT_AVAILABILITY`, `SIG_BATCH_STATS`. No DB writes (delegated to generator).

**Worker is observability-only.** Classification happens in `signal_generator.py:_evaluate_signal`.

**Discrepancy.** Worker's `_input_active` check at `:138-145` uses `abs(x) > 0.0`; the classifier uses `abs(x) >= cfg.*_min_active`. The two diverge, so `SIG_INPUT_AVAILABILITY` reports more inputs active than `_evaluate_signal` actually consumes.

### src/intelligence/signals/signal_generator.py (545 lines)

**Contract.** Per-symbol producer. Reads sentiment (`:103-104`), F&G (`:107-108`), funding (`:111-112`), OI (`:115-116`), klines for volume-surge (`:342`), config `settings.signal_generator.multi_source` (`:64`). Output: `Signal` with `signal_type`, `confidence`, `components`, `reasoning`. Persists to `signals` table via `_altdata_repo.save_signal()` (`:251`). Log: `SIG_GEN`, `SIG_CLASSIFY`, `SIG_DOWNGRADE`, `SIG_GEN_INPUT`, `SENT_CONSUMPTION_DISABLED`.

**The SIG_CLASSIFY classifier** — `_evaluate_signal()` (`:393-545`):

Step 1, clamp four scores to [-1, +1]:
- `s_sentiment = clamp(sentiment, -1.0, 1.0)` (`:454`)
- `s_fg = clamp((50.0 - fear_greed) / cfg.fg_normalize_range, -1.0, 1.0)` (`:455-457`), `fg_normalize_range = 30.0` (settings.py:3046), CONTRARIAN
- `s_funding = clamp(-funding_rate / cfg.funding_normalize, -1.0, 1.0)` (`:458-460`), `funding_normalize = 0.005`, INVERTED
- `s_oi = clamp(oi_change / cfg.oi_normalize_pct, -1.0, 1.0)` (`:461`), `oi_normalize_pct = 5.0`

Step 2, activity gating (`:474-483`). Sentiment force-deactivated when `consumption_enabled = False`. Per-component minimums: `sentiment_min_active=0.05`, `fg_min_active=0.10`, `funding_min_active=0.10`, `oi_min_active=0.10`.

Step 3, weighted sum over active set only (`:497-513`). Weights from `settings.signal_generator.multi_source`: sentiment 0.40, fg 0.25, funding 0.20, oi 0.15. `direction_score = sum(weights[c]*scores[c] for c in active) / active_weight_sum`.

Step 4, threshold mapping (`:515-524`):
- `direction_score >= strong_threshold (0.55)` → STRONG_BUY
- `direction_score >= buy_threshold (0.18)` → BUY
- `direction_score <= -strong_threshold` → STRONG_SELL
- `direction_score <= -buy_threshold` → SELL
- else NEUTRAL

**Confidence downgrade** (`:194-224`) after classification. Uses `CONFIDENCE_THRESHOLDS["strong_buy"]=0.60`, `["buy"]=0.40` (signal_models.py:44-50). If STRONG_* with confidence<0.60 → downgrade to BUY/SELL. If BUY/SELL with confidence<0.40 → NEUTRAL. NON-DESTRUCTIVE — original kept in `components.original_signal_type`.

Confidence itself = `agreement*0.40 + magnitude*0.25 + volume*0.20 + freshness*0.15` (confidence.py:65-68).

**Critical discrepancies.**
1. **Two competing normalisers per input**. The CONFIDENCE path (`:141-143`) normalises F&G by `/50.0`, funding by `*100`, OI by `/20.0`. The CLASSIFICATION path (`:455-461`) normalises F&G by `/30.0`, funding by `/0.005` (= 1/200), OI by `/5.0`. The two paths see different normalised values for the same inputs. Funding ratio differs by 2× (confidence saturates at ±0.01 funding, classifier saturates at ±0.005).
2. **`buy_threshold=0.18`** was lowered from 0.25 to match observed BUY-leaning direction_scores (settings.py:3034-3038 comment). The default-symmetric mapping of `direction_score` to label is therefore tuned asymmetrically toward BUY entries.
3. **Dead constants** `SENTIMENT_THRESHOLDS`, `FEAR_GREED_THRESHOLDS`, `FUNDING_RATE_THRESHOLDS`, `OI_CHANGE_THRESHOLDS`, `SOURCE_WEIGHTS` in signal_models.py:7-60 — never consumed by the post-Phase-1 classifier.
4. **`_sentiment_consumption_enabled` default mismatch**: signal_generator.py:76 defaults `True`, settings.py:1825 defaults `False`. Behaviour depends on how the generator is constructed.

**Regime conditionality: NONE.** No regime references in signal_generator.py, signal_models.py, or confidence.py. The classifier is regime-agnostic.

### src/workers/regime_worker.py (315 lines)

**Contract.** Reads watch_list (`:66`), settings.regime.primary_symbol="BTCUSDT" (`:150,176`). On cold start, restores `_per_coin_regimes` from `coin_regime_history` last 30 min (`:91-117`). Delegates to `RegimeDetector.detect()` for global (`:143`) and `detect_per_coin()` for per-coin (`:186`). Inserts to `regime_history` (every tick, `:145-157`) and `coin_regime_history` (per-coin, `:249-255`). Cleans `coin_regime_history` older than 24h every 100 ticks (`:283`). Emits `REGIME_GLOBAL`, `REGIME_PERCOIN`, `REGIME_PERCOIN_SUMMARY`, `REGIME_DIVERGE`, `REGIME_TICK_SUMMARY`.

**The regime classifier** — `RegimeDetector.detect()` at `src/strategies/regime.py:90-156`:
- `adx > trending_adx_threshold (20.0)` AND `+DI > -DI` AND `choppiness < 45` → TRENDING_UP, conf=`min(adx/50, 1.0)`
- `adx > 20.0` AND `-DI > +DI` AND `choppiness < 45` → TRENDING_DOWN
- `atr_percentile > 70.0` OR `volume_ratio > 2.0` → VOLATILE, conf=`min(atr_percentile/200, 1.0)`
- `adx < 20.0` AND `choppiness > 50.0` → RANGING, conf=`min(choppiness/80, 1.0)`
- `adx < 12.0` AND `volume_ratio < 0.5` AND `atr_percentile < 50` → DEAD, conf=0.8
- else → RANGING, conf=0.4 (ELSE-fallback)

Hysteresis (`:178-223`) — `hysteresis_count=2` consecutive readings to confirm a regime change.

**Discrepancies.**
- `regime_history` is write-only audit (no readers per cleanup_worker.py:82).
- DB restore at regime_worker.py:107-117 loses `volume_ratio` (set to 1.0) and `atr_percentile` (set to 0). `trend_direction` heuristically inferred from regime *string*.
- `detection_interval_seconds=300` in settings.py:1556 is unused; worker is sweet-spot-scheduled instead.
- `primary_symbol="BTCUSDT"` is excluded from per-coin map (`:173-176`). Consumers reading `_per_coin_regimes["BTCUSDT"]` get None.

---

## Layer 1C — Strategy Worker + Ensemble

### src/workers/strategy_worker.py (2964 lines)

**Contract.** Reads watch_list, regime_detector global+per-coin, klines (batched), TA via TAEngine, sentiment/F&G/funding/OI, services.structure_cache. Writes to `_score_cache` (`:98-100`), `layer_manager._strategy_consensus/_strategy_consensus_summary/_strategy_votes/_strategy_hints/_scorer_components` (`:875,878,894,966,705`). Per-cycle dump to `data/logs/layer1c_full.jsonl` (`:1212`). Emits 30+ log events including `STRAT_REGIME_DIST`, `STRAT_L1_SIG`, `STRAT_L2_DONE`, `STRAT_L3_VOTE`, `STRAT_CONSENSUS_WRITE`, `STRAT_HEALTH`.

**The 36 strategies — actual count is 39 mainnet (40 with testnet X1)**. 5 are non-voting placeholders (F4, J3, K1, K3, K4). Effective directional voter ceiling ≈ 34 per cycle.

Full enumeration (with category guess + entry rule + vote-confidence formula):

| # | Strategy | Category | Direction Rule | Vote Confidence |
|---|---|---|---|---|
| 1 | A1_rsi_reversal | contrarian | LONG RSI<25 + bb_lower + vol>=1.5 + stoch_k>d&<25; ADX>30 +DI>-DI → reject | `min((25-rsi)/25, 1.0)`, vote at RSI<40: 0.30 → max 1.0 |
| 2 | A2_vwap_bounce | trend pullback | LONG within 0.1% of VWAP + 40<=RSI<=50 + 8/12 above VWAP + bullish pattern | scan max ≈ 0.7; vote 0.6 |
| 3 | A3_bb_squeeze | breakout | LONG bb_bw<2 + price>bb_upper + vol>=2.0 + macd>0 | vote 0.7 |
| 4 | A4_ema_crossover | trend/momentum | LONG EMA12>26>SMA50 + 50<=RSI<=70 + vol>=1.5 + price>VWAP + ADX>=20 | vote 0.65 |
| 5 | B1_volume_breakout | breakout | LONG bb_bw<3 + price>bb_upper + vol>=3.0 + RSI>60 + macd>0 + ADX>=20 | vote 0.70 |
| 6 | B2_supertrend | trend | LONG supertrend+1 + price>SMA50 + macd>0 + ADX>=25 + 50<=RSI<=70 + vol>=1.0 | vote `min(adx/40, 1.0)` |
| 7 | B3_ichimoku | trend | LONG price>SMA50&200 + EMA12>26 + RSI>=50 + macd_line>signal>0 + ADX>=25 + +DI>-DI | vote 0.70 |
| 8 | B4_double_bottom_top | structural reversal | LONG pattern=double_bottom OR (RSI<35 + near support + bullish) | vote 0.70 within 1.5% S/R |
| 9 | C1_bb_mean_reversion | contrarian | LONG RSI<25 + price<bb_lower*0.997 + chop>=45 + MFI<20 + bullish | vote 0.70 |
| 10 | C2_rsi_divergence | contrarian | LONG last_low<=recent*1.002 + 25<RSI<35 + vol<0.8 + stoch crossover | vote 0.65 |
| 11 | D1_funding_fade | contrarian | SHORT funding>0.0004 + RSI>70 + F&G>70 + 24h>5%; LONG mirror | vote `min(\|fund\|/0.001, 1.0)` |
| 12 | D2_oi_divergence | contrarian | SHORT 24h>1% + oi<-2% + funding>0.0001 + vol<0.8 + RSI>65; LONG mirror | vote 0.60 |
| 13 | E1_fear_greed | contrarian | LONG F&G<=15 + RSI<35 + sentiment<-0.5 + funding<-0.0001 + drop>=10% + green | vote `min((25-fg)/25, 1.0)` |
| 14 | E2_news_breakout | event-driven | LONG news_score>0.7 + vol>=3.0 + 5m_change>0.5%; SHORT mirror | vote `min(\|news\|, 1.0)` |
| 15 | E3_sentiment_momentum | trend confirmed | LONG sent>0.4 + news>0.3 + news_count>=3 + RSI<=70 + price>VWAP & EMA12 + vol>=1.2 + 30<=F&G<=60 | vote `min(\|sent\|, 1.0)` |
| 16 | F1_support_resistance | structural | LONG within 0.5% of support + 30<=RSI<=40 + vol<0.8 + bullish + trend!=BEARISH | vote `max(0.4, 1-dist*100)` |
| 17 | F2_multi_tf_alignment | trend alignment | LONG trend=BULLISH + price>SMA50 + supertrend+1 + macd_line>0 & hist>0 + ADX>=25 + 40<=RSI<=55 + within 0.3% of EMA12 + vol>=1.0 | vote **0.85** (highest in ensemble) |
| 18 | F3_liquidation_hunt | event-driven | SHORT funding>0.0003 + vol>=2.5 + RSI<40 + bearish + body>=1.5*ATR + 3-bar decline + oi>=5% | vote 0.70 |
| 19 | F4_grid_recovery | structural DCA | Activates only with open_position altdata | **NEUTRAL/0.0 — never votes** |
| 20 | G1_stop_hunt | event-driven | LONG last_low<support + last_close>support + pierce>0.2% + vol>=2.0 + RSI>=35 | vote 0.70 |
| 21 | G2_retail_fade | contrarian | SHORT sent>0.7 + news>0.6 + F&G>80 + funding>0.0005 + RSI>75 + oi>3 + 24h>5%; LONG mirror | vote `min((fg-75)/25, 1.0)` |
| 22 | G3_liq_frontrunner | event-driven | SHORT oi>=8 + vol>=2.0 + funding>0.0004 + RSI<50 + body>=ATR + 3-bar decline | vote 0.70 |
| 23 | G4_whale_shadow | event-driven volume | LONG 1 of last 3 candles vol>=5x avg + close_pos>0.7 + bullish + oi>0 + no opposing ADX trend | vote 0.75 |
| 24 | H1_funding_predict | event-driven (time) | Active hours 5-7 of 8h cycle. SHORT predicted>0.0005 + RSI>60 + 24h>0; LONG mirror | vote `min(\|fund\|/0.001, 0.8)` |
| 25 | H2_basis_exploit | structural basis | SHORT funding>0.0003 + RSI>30; LONG mirror | vote 0.50 |
| 26 | H3_vol_switch | breakout | LONG bb_bw<1.5 + KC inside BB + natr<0.5 + price>bb_upper + vol>=1.5 | vote 0.70 |
| 27 | H4_order_flow | momentum | LONG 3 consec bullish + vol monotonic + last 2 close in top 20% + force_idx>0 + CMF>=0.1 + vol>=2.0 | vote 0.60 |
| 28 | I1_kill_zone | event-driven session | Active first 30 min of 4 KILL_ZONES + vol>=1.5 + ADX>=15 | vote 0.50 |
| 29 | I2_weekend_gap | event-driven calendar | Weekend only. LONG vol<=0.5 + RSI<35 + near support; SHORT mirror | vote 0.50 |
| 30 | I3_options_expiry | event-driven calendar | Last week of month, weekday. SHORT price>round*1.03 + RSI>65 + vol<=1.0; LONG mirror | vote 0.40 |
| 31 | I4_hourly_close | momentum | LONG 3 hourly closes in top 25% + monotonic rise + vol monotonic + 55<=RSI<=75 + macd>0 | vote 0.60 |
| 32 | J1_btc_dominance | structural | LONG BTC + btc_dom>55 + 24h>1% + 50<RSI<70 OR alt + btc_dom<45 + 24h>2% | vote 0.50 |
| 33 | J2_correlation | contrarian catchup | LONG alt + btc_change>2% + this<0.5% + RSI<55 + no negative news | vote 0.60 |
| 34 | J3_price_lag | structural arb | SHORT last_price>mid*1.002 + deviation>0.2% + vol>=1.5 + natr<=3.0 | **NEUTRAL/0.3 — never votes** |
| 35 | J4_alt_beta | structural catchup | LONG alt + btc_change>2% + this<btc*0.3 + RSI<65 + vol<1.5 + no neg news | vote 0.60 |
| 36 | K1_claude_conviction | structural trigger | Triggers only with `altdata.k1_trigger` score>80 + STRONG consensus | **NEUTRAL/0.0 — never votes** |
| 37 | K2_pattern_memory | structural history | LONG >=5 matches with up_rate>=0.7 + sample size tier (0.5/0.7/0.85) | vote `min(up_rate, 0.8)` |
| 38 | K3_ensemble | placeholder | NO-OP — logic in ensemble.py | **NEUTRAL/0.0 — never votes** |
| 39 | K4_optimizer | placeholder | NO-OP — logic in optimizer.py | **NEUTRAL/0.0 — never votes** |
| 40 | X1_always_trade | testnet-kickstart | Returns signal on every call. BUY if RSI<45+macd>0 OR RSI<40 OR last bullish; else SELL | vote 0.7 / 0.5 |

**Persistence.** `ensemble_votes` SQL table declared at migrations.py:434-446 — **NEVER WRITTEN TO**. Grep confirms zero INSERT. Per-trade per-strategy votes live only in:
- The `STRAT_VOTE_TRACE` log (ensemble.py:354-358) — emitted only when `consensus=="STRONG"` and `vote_trace_enabled=True`.
- `data/logs/layer1c_full.jsonl` per-cycle dump.
- In-memory `layer_manager._strategy_votes`.

Trade-time aggregates (`ensemble_strength`, `ensemble_votes_for`, `ensemble_votes_against`) go to `strategy_trades` via brain_v2.py:509-521.

**Regime conditionality.** `registry.get_active_for_regime(regime)` is called (`:573`) — but `registry.get_active_for_regime()` at registry.py:44-53 **ignores its argument** and returns every enabled strategy. Regime is plumbed but functionally a no-op for activation. `REGIME_ACTIVE_CATEGORIES` is dead code at the activation gate.

### src/strategies/ensemble.py (390 lines)

**Combination logic** (`vote()` at `:149-305`):
1. Per non-originator strategy, call `strategy.vote()` → `(vote_str, confidence, reasoning)` (`:176-204`).
2. `weight = registry.get_performance(strategy.name).ensemble_weight` (`:188-189`). Default **1.0 uniform** (signal_types.py:195). Optimizer-mutable to [0.1, 3.0] via `set_ensemble_weight()` (registry.py:96-104).
3. Per-strategy contribution = `weight * confidence` (`:218-220`).
4. `buy_votes = _capped_contribution("BUY")`, `sell_votes = _capped_contribution("SELL")`, `neutral_votes = sum(weights of NEUTRAL voters)` — **note NEUTRAL uses WEIGHT only, not weight×confidence** (`:233-235`).
5. Cap (when `single_strategy_max_share < 1.0`, default 1.0 disabled): each contribution capped at `rest_total * share / (1 - share)` (`:228`).

**Consensus thresholds** (`:261-275`):
- STRONG: `agreeing >= 4.0 AND opposing <= 1.5` (HARDCODED)
- GOOD: `agreeing >= cfg.min_ensemble_agreement (5.0) AND opposing <= cfg.max_ensemble_opposition (1.0)`
- WEAK: `agreeing >= 1.5 AND opposing <= 1.5` (HARDCODED)
- LEAN: `agreeing > opposing`
- CONFLICT: else
- CONSENSUS_SIZE map: `STRONG=1.0, GOOD=0.75, LEAN=0.50, WEAK=0.30, CONFLICT=0.15` (`:261`)

**Surprising ordering.** Branch order: STRONG fires first, GOOD second. STRONG threshold (4.0/1.5) is LOWER than GOOD threshold (5.0/1.0). At `agreeing=4.5, opposing=1.5`, the trade is STRONG, not GOOD. A higher-bar GOOD is unreachable when STRONG's lower bar is met first.

**`final_size_mult` formula** (`:275-293`):
```
size_mult = CONSENSUS_SIZE[consensus]                    # base_size_mult
_struct_conf = setup.scoring_details["setup_type_confidence"] or 0.85
_conf_factor = max(0.5, min(1.0, _struct_conf))          # clamped to [0.5, 1.0]
size_mult *= _conf_factor                                # final_size_mult
```
Domain: [0.075, 1.0]. STRONG with default conf 0.85 = 0.85. Counter-setup (`_struct_conf≈0.35` → clamped 0.5) on STRONG = 0.50.

**Regime conditionality.** `registry.get_active_for_regime(regime.regime)` called once (`:171`) — same no-op as strategy_worker.py. Regime has ZERO functional effect on combination.

**Logged events.** `ENSEMBLE_VOTE_WEIGHTED` (only when `_struct_conf<0.85`, `:322-329`), `STRAT_VOTE_TRACE` (only on STRONG, `:354-358`), `ENSEMBLE_CONFLICT` (`:273`), `STRAT_VOTE_FAIL` (`:201`), `ENSEMBLE_CACHE_WRITE_FAIL` (`:251`).

---

## Layer 1D — Scanner + State Labeler

### src/workers/scanner_worker.py (2070 lines)

**Contract.** Reads `settings.universe.watch_list` (50 coins), reads outputs of structure_worker, strategy_worker, signal_worker, regime_worker, altdata_worker, market.ticker_cached, position_service. Writes DB `active_universe` (DELETE + INSERT, `:1449-1465`/`:1962-1982`) and `lm._coin_packages` in-memory (`:1323`, `:1855`). Two modes: `briefing` (default) vs `exclusion`. Emits ~30 log events.

**Opportunity score** (`_compute_opportunity_score` at `:285-355`):
```
score = w.structure * struct_norm        # default w.structure = 0.27
      + w.strategy  * strat_norm         # 0.27
      + w.signal    * sig_norm           # 0.13
      + w.regime    * regime_norm        # 0.13
      + w.funding   * funding_norm       # 0.10
      + w.rr        * rr_norm            # 0.10
```
- `struct_norm = (setup_score/100) * clamp(setup_type_confidence, 0.5, 1.0)`
- `regime_norm = (regime_alignment + 1) / 2` where alignment is +1/+0.5/0/-1 for trending/volatile/ranging/dead

**Regime is consumed**: `_get_regime_alignment` (`:185-211`) maps regime → [-1, +1]; `_regime_aligns` (`:378-392`) is the exclusion-mode HARD GATE (long aligns with trending_up|ranging, short aligns with trending_down|ranging).

**Briefing-mode ranking.** Sort non-forced candidates by `(interestingness_score, opportunity_score)` DESC, take top `top_n_packages` (default 15), force-include open-position coins, soft-floor up to `min_briefing_packages=12` (`:1245-1258`).

**Exclusion-mode ranking.** Apply 5-criterion gate (XRAY setup != NONE, consensus ∈ {STRONG,GOOD}, regime aligns, RR >= 1.3, no blockers), sort by `opportunity_score` DESC, take top 15 (`:1725-1736`).

**Bug.** Exclusion-mode `_enrich_for` reads `pkg.price.volume_24h_usd` / `pkg.alt.funding_rate` (`:1954-1956`) — wrong attribute names. The correct names are `pkg.price_data` / `pkg.alt_data` (briefing mode does it correctly at `:1441-1443`). The try/except swallows the AttributeError and returns zeros, so exclusion-mode `active_universe` writes always have zero volume/change/funding. Dormant in production (default mode = briefing) but active in `ab_mode="alternating"`.

### src/workers/scanner/state_labeler.py (833 lines)

**Pure function** `label_state(...)` (`:598-625`) returning `StateLabelResult{primary, secondary[], confidence}`. 22 possible labels enumerated below with detection condition and base weight (which determines the `primary`):

| Label | Trigger | Hardcoded threshold | Base weight |
|---|---|---|---|
| TREND_PULLBACK_LONG | direction=long + setup ∈ {bullish_fvg_ob, bullish_structural_break} | regime soft-gate (haircut 0.5 if not trending_up) | 0.85 |
| TREND_PULLBACK_SHORT | mirror short | mirror | 0.85 |
| LIQUIDITY_SWEEP_REVERSAL_LONG | setup="bullish_liquidity_sweep" + trade_direction in {"", "long"} | conf 0.40 floor | 0.85 |
| LIQUIDITY_SWEEP_REVERSAL_SHORT | mirror | — | 0.85 |
| BREAKOUT_PENDING | setup ∈ {bullish_range_breakout, bearish_range_breakout} OR (range_compression=True + regime ranging/dead) | conf 0.40 | 0.70 |
| RANGE_FADE_LONG | direction=long OR consensus_direction=long, position_in_range<0.40 (when provided) | regime soft-gate (ranging required) | 0.65 |
| RANGE_FADE_SHORT | mirror, pos>0.60 | mirror | 0.65 |
| FUNDING_EXTREME_FADE_LONG | funding < -0.0015 | pos<0.55, regime != trending_down | 0.60 |
| FUNDING_EXTREME_FADE_SHORT | funding > 0.0015 | pos>0.45, regime != trending_up | 0.60 |
| KILL_ZONE_OPPORTUNITY | session ∈ {london, new_york} + phase ∈ {early, mid} + setup != none | conf 0.55 | 0.60 |
| MOMENTUM_BURST_LONG | regime=volatile + change_24h>=5.0 + consensus !=short + signal !=short + vol_ratio>=1.5 | conf scale | 0.55 |
| MOMENTUM_BURST_SHORT | mirror at <=-5.0 | — | 0.55 |
| EXTREME_FEAR_LONG_BIAS | 0<fg<20 + consensus=long OR direction=long | regime != trending_down | 0.55 |
| EXTREME_GREED_SHORT_BIAS | 80<fg<=100 + consensus=short OR direction=short | regime != trending_up | 0.55 |
| OPEN_POSITION_HOLD_REVIEW | has_open_position=True | flat 0.7 | 0.50 |
| COUNTER_TRADE_LONG | setup="bullish_fvg_ob_counter" + direction=long | NO regime gate | 0.45 |
| COUNTER_TRADE_SHORT | mirror | NO regime gate | 0.45 |
| OB_MITIGATED_FVG_ONLY_LONG | direction=long + FVG present + OB not present | flat 0.50 | 0.40 |
| OB_MITIGATED_FVG_ONLY_SHORT | mirror | flat 0.50 | 0.40 |
| MANIPULATION_WINDOW | manipulation_likely=True | flat 0.6 | 0.20 |
| RECENT_LOSER_COOLDOWN | is_recent_loser=True | flat 0.5 | 0.15 |
| NO_TRADEABLE_STATE | fallback when nothing fires | flat 0.05 | 0.05 |

**Ranking** (`:819-832`): `(LABEL_BASE_WEIGHTS[label] * confidence, LABEL_BASE_WEIGHTS[label])` DESC. Top = `primary`; rest go to `secondary[:2]`.

**FUNCTIONALLY DEAD labels** because scanner_worker doesn't populate the inputs:
- `OB_MITIGATED_FVG_ONLY_*` — requires `in_direction_fvg_present` / `_ob_present`, never populated by scanner.
- `BREAKOUT_PENDING` second branch — requires `range_compression`, never populated.
- `RANGE_FADE_*` and `FUNDING_EXTREME_FADE_*` `position_in_range` gate — never populated.
- `MOMENTUM_BURST_*` `volume_ratio >= 1.5` gate — never populated.

**BREAKOUT_PENDING bearish arm is broken**: labeler at `:368` checks `bearish_range_breakout`, but structure_types.py:48 emits `bearish_range_breakdown`. String mismatch — never matches.

**Discrepancies** (Phase 4 config block "label_thresholds" promised in docstring `:48-51`, never landed). Two sources of truth for the funding-extreme boundary disagree (`0.0015` vs `0.001`). `signal_direction` parameter is wired to `consensus_direction` (legacy alias) at `:763-770`. `regime_confidence` and `atr_pct_h1` parameters accepted but unused.

**Downstream.** Strategist (`src/brain/strategist.py:2089-2107, 2546-2548, 2396-2403`) is the SOLE consumer of `state_label.primary`. Renders into the Stage-2 prompt; filters per `LABEL_NO_TRADEABLE_STATE` and `LABEL_RECENT_LOSER_COOLDOWN`; reads ACTION_HINTS[primary] for hint text.

---

## Layer 2 — Strategist CALL_A

### src/brain/strategist.py (5072 lines)

**CALL_A entry-point.** `create_trade_plan()` at `:851`. Builds prompt via `_build_trade_prompt()` (`:2998-4017,909`); selects system prompt at `:920-934` (TRADE_SYSTEM_PROMPT default; TRADE_SYSTEM_PROMPT_ZERO_TWO if `enable_zero_two_contract=True`; appends BRIEFING_SYSTEM_PROMPT_SUFFIX if `surface_briefing_fields=True`; appends urgent override if `_has_urgent_concerns`). Sends to Claude (`:969`). Parses via `_parse_trade_plan()` (`:976`).

**Inputs consumed in the prompt:**
- **CoinPackages** from scanner — `layer_manager.get_coin_packages()` at `:3164`. Per package renders header + `Setup:` + `Price:` + `Suggested SL/TP:` + `Strategies:` + `Signal:` + `Funding:` + `Why:` lines.
- **Ensemble consensus** — `pkg.strategies.ensemble_consensus` at `:2160`. NO `final_size_mult` field read (it doesn't exist on CoinPackage).
- **Signal classifier** — `pkg.signals.confidence/.direction` (`:2168-2171`). NO `SIG_CLASSIFY` label string emitted in CALL_A.
- **Per-coin regime** — `pkg.price_data.regime` (`:2150`) AND `_rd.get_coin_regime(symbol)` for the "[TRENDING_UP 75%]" tag on market-data line (`:3363-3389`). `## REGIME DIVERGENCE` block (`:3402-3420`). `## MARKET REGIME (CONTEXT)` block (`:3570-3596`).
- **Setup type** — `pkg.xray.setup_type` + `setup_type_confidence` (`:2133-2145`). Counter-setup annotation: "(COUNTER-TRADE — opposite to structural bias; lower conviction)" at `:2134-2139`.
- **TIAS lessons** — recent-loss async-prefetched per flagged candidate (`:2359-2438, 2440-2489`).
- **Indicators** — TA via `services["ta_cache"].analyze(symbol, H1)` (`:3331-3351`), emits RSI/MACD_hist/ADX on market-data line.
- **F&G** — `services["fear_greed"].get_latest()` (`:3063-3068`), shown in `## SENTIMENT` (`:3551-3555`).
- **Universe** — `services["scanner"].get_active_universe()` (`:3142`).
- **Account equity** — `services["account_service"].get_wallet_balance()` (`:3674-3684`). Fallback $168,000 if None.
- **Tiered capital limit** — `tiered_capital.get_limits(equity, deployed).max_single_trade` → `Per-trade size limit: $X` (`:3704-3727`).
- **Strategy hints** — `lm._strategy_hints[:20]` (`:3628`); consensus summary `[:15]` (`:3653`).
- **Event buffer** — `services["event_buffer"].get_prompt_text(max_events=20)` (`:3753-3777`).
- **Urgent queue** — `services["urgent_queue"].drain_concerns()` (`:3781-3791`).

**The system prompt** (TRADE_SYSTEM_PROMPT at `:67-151`, ZERO_TWO variant at `:333-400`):
- Is a STATIC module-level constant, not built per-call.
- Embeds regime ONLY as categorical text — "trending_down: Sell is the NATURAL direction. Only flip ... >65% WR with >5 trades. trending_up: Buy is the NATURAL direction. ranging: BOTH directions. volatile: BOTH directions" (`:82-88` / mirror `:348-354`).
- F&G contrarian rules at `:90-97`:
  - "Trending up + fear = strong buy"
  - "Trending down + fear = short with conviction"
  - "Ranging + fear = buy near support"
  - "Extreme greed (F&G > 80): take profits on longs, look for short entries"
- Rule 1 at `:128, :390`: **"Return between 2 and 4 trades. Zero or one only when the entire candidate set is genuinely flat — this should be rare."** — non-zero floor mandate.
- Rule 4 at `:134, :395`: "SL should be at least 1.5% from entry."
- Rule 11 at `:151`: "Use leverage 3-5x on testnet — this is paper money, we need meaningful results."

**Direction-decision logic.** The strategist itself **does NOT compute direction**. Claude does. The features the strategist surfaces to Claude include per-coin regime tags, setup_type with counter-suffix annotation, votes summary, opposition tier, regime-divergence block. **No code-side refusal of any direction × regime combination exists.** `bullish_structural_break` string does not appear in this file. The closest thing to a regime-conditional refusal — `_build_regime_instructions` (`:4450-4546`) and `_build_direction_performance` (`:4548-4635`) — was deliberately removed from CALL_A on 2026-05-05 in the aggressive-framing rewrite (comment at `:3079-3095`). They still run only inside the legacy `_build_context_prompt`.

**The May-19 direction-bias fix.** No `R1/R2/R3/R4/ALPHA/BETA/GAMMA` agent markers in this file. The strategist-side surface of the fix is the regime-block symmetrisation (`:1553-1589` legacy mirror, `:3561-3596` active block, `STRAT_REGIME_BLOCK_VERSION=2` constant at `:210`, sentinel `STRAT_REGIME_INSTR_REFRAMED | block_version=2 mode=symmetric_scenario` at `:657-666`). The asymmetric "DEFAULT SELL BIAS" was replaced with symmetric "Bias for shorts/longs when per-coin evidence agrees; per-coin tags override" NOTES at confidence > 0.60.

**Conviction / size selection.** The strategist does NOT choose conviction or size. Claude does. The strategist only surfaces a numeric ceiling: `Per-trade size limit: $X` (`:3718-3720`). No `low/med/high` bucketing. The strategist does NOT read `final_size_mult` from the ensemble — the field doesn't exist on `CoinPackage`. The system prompt instructs Claude: "size_usd: within the per-trade size limit shown above — strong conviction = larger, borderline = smaller" (`:106`).

**Hardcoded values in CALL_A path** (exhaustive, abbreviated to the gating ones):
- `:128` "Return between 2 and 4 trades" — non-zero floor.
- `:134` "SL should be at least 1.5% from entry."
- `:148` "Hold times: 15-45 min for scalps, up to 60 min for momentum."
- `:151` "Use leverage 3-5x on testnet."
- `:2060,2062,2633,2635` `prompt_floor_interestingness=0.20` — briefing-mode per-coin skip floor.
- `:2283` opposition strong-voter threshold confidence>=0.6.
- `:2286-2293` opposition tier thresholds `<0.05/<0.20/<0.50` for NEGLIGIBLE/WEAK/MODERATE/STRONG.
- `:2392-2393` recent_loss_lookback_hours=336 (14d), max_lessons=2.
- `:3198` `top_n_to_brain=6` per Stage-2 cap.
- `:3354-3357` market-data inclusion filter `abs(change)>3.0 OR rsi<30 OR rsi>70 OR adx>30`.
- `:3584` regime confidence threshold > 0.60 for high-confidence NOTE.
- `:3707` equity fallback $168,000.
- `:3853-3854` prompt size caps `_SECTION_CAP=80, _CHAR_CAP=30000`.
- `:4015` slow-build warning > 5000 ms.
- `:4729-4736` parser defaults: max_positions=4, max_per_coin=1, default_sl_pct=2.0, default_tp_pct=2.5, default_hold_minutes=30, default_leverage=2, trailing_activation_pct=0.5.

**The setup × regime gate.** **DOES NOT EXIST** in `strategist.py`. No literal `bullish_structural_break` in the file. No code path refuses or filters any setup × regime combination. Counter-trade annotation at `:2133-2139` is informational only; does not block. `## REGIME DIVERGENCE` text says "Do NOT short a coin that is individually in an uptrend" — but it's prompt guidance to Claude, not a code-side filter.

---

## Layer 3 — APEX

### src/apex/ (six files, ~4267 lines)

**Flow.** Strategist proposes trade → `TradeOptimizer.optimize(directive, plan)` (optimizer.py:125) → `IntelligenceAssembler.assemble(directive)` builds `IntelligencePackage` (assembler.py:50) → `build_apex_user_prompt(package)` (prompts.py:82) → `QwenClient.optimize(system_prompt, user_prompt, ...)` → DeepSeek (qwen_client.py:134) → `_parse_response()` → lock/counter-trade/insufficient-data/confidence-gate filters → `_apply_constraints()` → `_apply_flip_resize_policy()` → return `OptimizedTrade`. Then layer_manager wraps and `TradeGate.validate(trade)` (gate.py:48) runs 14 checks.

### assembler.py (831 lines)

Builds the APEX prompt's IntelligencePackage. Reads:
- TA via TAEngine: `rsi_14, macd_hist/signal/line, stoch_k/d, adx, atr/atr_pct, vol_ratio, ema_20/50, bollinger_pct` (`:251-286`)
- Mode4 metrics (`:327-332`)
- Orderbook top-5 imbalance (`:374-380`)
- Volatility profile: `volatility_class, recommended_tp/sl/hold/strategy` (`:406-410`)
- TIAS symbol history filtered by regime (`:434, :440-451`; falls back to all-regime + warning text "Respect the regime direction bias" at `:446-452` when sparse)
- TIAS situation data keyed by regime (`:551,554`)
- F&G from DB with 24h staleness (`:654-657`; default 50 on miss)
- XRAY structural data: setup_type, setup_score, trade_direction (R1 fix), rr_long/rr_short/rr_best_direction, FVG/OB, sweep, smc_confluence, POC/Fib/MTF, session, setup_rank (`:752-822`)

**Regime usage**: assembler embeds regime in symbol-history filter (`:79,434`), situation key (`:82,551`), prompt text "Regime: {regime}" (`:554`). Conditional fallback to all-regime when sparse (`:440-451`).

### prompts.py (226 lines)

`APEX_SYSTEM_PROMPT` (`:21-75`):
- Direction rules at `:41-47`:
  - "trending_down regime: Sell is the NATURAL direction. Only flip Sell→Buy if Buy has >65% WR with >5 trades in THIS regime for THIS coin."
  - "trending_up regime: Buy is the NATURAL direction. Only flip if Sell has >65% WR with >5 trades."
  - "ranging regime: Both directions valid. Use DIRECTION BREAKDOWN data to decide."
  - "volatile regime: Be conservative. Only flip with overwhelming evidence (>70% WR, >8 trades)."
  - "INSUFFICIENT DATA: If fewer than 5 trades exist for a direction in the current regime, that is NOT enough to justify a flip. Keep the trader's original direction."
- Sizing rule (`:53`): "Scale by TIAS profit factor. High PF (>2.0) coins get MORE capital. Low PF (<1.0) get LESS."
- TP rule (`:52`): "NEVER set TP below the trader's original TP."
- Volatility-class TP/SL ranges at `:60-64`: DEAD 0.3-0.5/0.2-0.3, LOW 0.4-0.8/0.3-0.5, MEDIUM 1.0-2.0/0.8-1.5, HIGH 2.0-4.0/1.5-2.5, EXTREME 3.0-8.0/2.0-4.0.
- Constraints at `:69-73`: Max size 1200 USD, max leverage 5x, SL 0.2-5.0%, TP 0.3-8.0%.

### optimizer.py (1763 lines)

**Three-tier data fallback** (`:202-247`):
- Tier 1: >= `min_tias_trades_for_optimization` symbol trades → full DeepSeek call
- Tier 2: >= `min_regime_trades_for_fallback` regime trades → regime defaults
- Tier 3: fallback with Claude defaults (`APEX_DEFAULT | using_defaults=Y`)

**Direction lock** — `_check_direction_lock(package, claude_direction, regime)` (`:254, :1339-1516`). Composite score over 5 signals (regime, structural, trade_dir, wr, symbol_evidence). Locks when `score < score_threshold` (default 0.0, `:1404`). regime_signal: +1 if regime supports Claude's direction (trending_up+Buy or trending_down+Sell), −1 if opposed (`:1413-1417`); 0 for ranging/dead/unknown/volatile. structural_signal: `log(rr_claude/rr_opposite)` clamped (`:1421-1438`).

**Direction-flipping logic** (`:892`): DeepSeek returns `direction`, validated against ("Buy", "Sell"). `was_flipped = qwen_dir != original_dir`. Post-call lock override at `:396-422` reverts if locked. Counter-trade gate at `:513-537` reverts if `setup_type.endswith("_counter")`. Insufficient-data gate at `:546-572` reverts if <`apex_min_trades_for_flip` (default 5) trades in target direction. Confidence gate `_enforce_flip_confidence` (`:1656-1708`) only fires when regime NOT in (trending_up, trending_down, volatile). RR-weighted confidence boost at `:463-504` only applies when regime NOT in trending/volatile.

**Sizing in APEX** (`_apply_constraints` at `:948-996`):
- `max_position_size_usd=1200` static cap (`:949`)
- `apex_size_cap_pct_of_equity=0.0` dynamic J5 cap (`:950-952`)
- `apex_size_conviction_floor=0.5` (`:953-955`)
- `conviction_scale = max(_conviction_floor, trade.confidence)` (`:971-972`)
- `final_size = max(100.0, _post_cap * _conviction_scale)` (`:975`)
- Floor 100 USD. SL clamp [0.2%, 5.0%] (`:1008,1011`). TP clamp [0.3%, 8.0%] (`:1019,1024`).

**Cap multipliers** — `_cap_mult_map` at `:319-321`: `{dead:1.4, low:1.5, medium:1.6, high:1.8, extreme:2.0}`. **CRITICAL discrepancy with models.py:149-153** which shows DeepSeek `{dead:1.2, low:1.3, medium:1.3, high:1.4, extreme:1.5}` — DeepSeek sees a TIGHTER cap than the optimizer enforces. DeepSeek likely respects the displayed cap and never approaches the higher actual cap.

### gate.py (618 lines)

14 checks. Reject reasons (sets `_gate_rejected`):
- `zero_conviction` (`:161-182`) — all three of `_xray_confidence`, `_setup_score`, `_expected_rr` at-or-below mins (defaults 0.0). NOT regime-conditional.
- `reentry_cooldown_5min_{remaining}s` (`:292-319`) — 5-min cooldown via `trade_coordinator.is_reentry_blocked`. NOT regime-conditional.

Downgrade-only checks (mutate trade in-place; do NOT reject):
- CHECK 0 size cap (`:71-92`) — `size_usd > _claude_original * 1.5x`
- CHECK 1 max 1200 USD (`:99-104`)
- CHECK 2 max leverage 5 (`:106-111`)
- CHECK 3 max 5 concurrent → size × 0.3 (`:113-129`)
- CHECK 4 conviction-weighted capital ceiling (`:131-258`)
  - profit_factor → weight: `>3.0→2.0, >2.0→1.5, >1.0→1.0, >0.5→0.7, else 0.5` (`:595-604`)
  - score-tier mult: `>=80→1.20, >=68→nochange, >=56→0.90, >0→0.80` (`:200-207`)
  - xray-conf mult: `>=0.85→1.20, >=0.70→nochange, >0→0.85` (`:217-222`)
  - rr mult: `>=3.0→1.15, >=1.5→nochange, >0→0.90` (`:226-231`)
  - weighted_pct clamp [0.05, 0.50] (`:244`); base_pct=0.4 (`:239`)
- CHECK 5 duplicate position → size × 0.5 (`:263-274`)
- CHECK 7 floor min_size=50 (`:321-326`)
- CHECK 8 TP floor — APEX TP cannot be worse than Claude's TP (`:354-370`)
- CHECK 9 trail activation floor 15% of TP, absolute 0.5% (`:372-395`)
- CHECK 10 trail distance floor 40% (`:397-408`)
- CHECK 11 mode override `trail_only` → `trail_with_ceiling` (`:410-417`)
- CHECK 12 confidence-based size scaling — `_apex_confidence < 0.50` → `size *= max(0.3, conf/0.50)` (`:419-431`)
- CHECK 13 RR validation — `rr==0 → ×0.25; 0<rr<0.5 → ×0.5` (`:436-453`)
- CHECK 14 TP/SL sanity ±2% (`:455-470`)

**None of the gate checks are regime-conditional.** Regime read only for conviction-weight cache key (`:511-538`).

### qwen_client.py (364 lines)

DeepSeek wire. Cost `(in_tok * 0.30 + out_tok * 0.88) / 1M` (`:249-252`). Defaults: temp 0.2, max_tokens 800, timeout 30s (`:139-141`). Forces `response_format={"type": "json_object"}` (`:181`). Slow warning if `_deepseek_ms > 5000` (`:725-730`).

---

## Cross-File Findings (Phase 1 distillation)

1. **Setup_type and SIG_CLASSIFY are computed regime-blind.** Both layer-1B classifiers run BEFORE the per-coin regime is refreshed. Setup_type lookup `_bull_alignment` reads `market_structure.structure` not regime. SIG_CLASSIFY has zero regime references.

2. **Ensemble combination is regime-blind in practice.** `registry.get_active_for_regime(regime)` is called twice in the pipeline (strategy_worker.py:573, ensemble.py:171) but `get_active_for_regime` at registry.py:44-53 IGNORES its regime argument and returns all enabled strategies. `REGIME_ACTIVE_CATEGORIES` is dead code.

3. **Strategy weights are uniform at boot** (1.0 each, signal_types.py:195). Mutated only by the Optimizer over time, clamped to [0.1, 3.0] via `set_ensemble_weight()`. No strategy is hardcoded down-weighted.

4. **Ensemble STRONG threshold is LOWER than GOOD threshold.** STRONG fires first via branch order; STRONG `agreeing>=4.0, opposing<=1.5` (HARDCODED at ensemble.py:263) is weaker than GOOD `>=5.0 / <=1.0` (config-driven at ensemble.py:265). A trade at `agreeing=4.5, opposing=1.5` is labeled STRONG.

5. **`final_size_mult` formula** = `CONSENSUS_SIZE[consensus] × clamp(setup_type_confidence, 0.5, 1.0)`. STRONG with default conf 0.85 → 0.85. STRONG with counter-setup → 0.50. Domain [0.075, 1.0]. **But the strategist never reads `final_size_mult`** — it doesn't appear on `CoinPackage`. Size is left to Claude under the per-trade ceiling.

6. **`SIG_CLASSIFY` label does not flow into the CALL_A prompt.** The strategist surfaces `pkg.signals.confidence` and `pkg.signals.direction` but no label string ("buy" / "strong_buy") reaches Claude. The label is recorded in the `signals` DB table and STRAT log only.

7. **No setup × regime gate exists in the entry path.** The string `bullish_structural_break` is absent from strategist.py. There is no code-side filter that refuses any setup × regime combination. The closest thing is the strategist's prompt text (advisory only) and the labeler's regime soft-haircut (multiplies confidence by 0.5 when regime doesn't match label expectation).

8. **`buy_threshold=0.18`** in the SIG_CLASSIFY classifier was deliberately lowered from 0.25 (settings.py:3034-3038 comment) "to match the typical BUY-leaning direction_score observed in forensic data." This is an asymmetric calibration that biases the classifier toward BUY labels at lower direction scores than SELL labels would need to fire.

9. **`ensemble_votes` DB table is dead schema.** Declared, never written. Per-trade per-strategy votes live only in logs and in-memory caches. Phase 3 verification of Claim 3 (herding) and Claim 5 (per-strategy attribution) must use log parsing.

10. **APEX direction lock has a regime-conditional signal contributor** (regime_signal: +1 for trending+aligned-direction, −1 for trending+opposed, 0 for ranging/volatile/dead/unknown). This is the ONLY regime-conditional decision in the entry pipeline. But it only kicks in when the composite score is below threshold (default 0.0), which is a high bar.

11. **APEX TP-cap mismatch between models.py and optimizer.py**. Prompt shows DeepSeek `{dead:1.2,...,extreme:1.5}`; optimizer enforces `{dead:1.4,...,extreme:2.0}`. DeepSeek likely respects the tighter displayed cap.

12. **Two normaliser ladders for the same SIG inputs**. Confidence normalises F&G by /50, funding by *100, OI by /20. Classifier normalises F&G by /30, funding by /0.005, OI by /5. The two paths see the same raw inputs at different magnitudes.

This concludes Phase 1.
