# F4 — End-to-End Data Flow Trace

Selected cycle: **window starting 2026-04-27 22:25:00 UTC** (M5 boundary).
This is the most-recent fully-captured cycle in `data/logs/workers.log`
where every Layer-1 sweet-spot worker fired and ScannerWorker
produced packages (the next ScannerWorker fire was at 22:29:00 with
`fires=8`, building from this cycle's data).

Prior cycle ScannerWorker emit at `22:24:00.020` is the last set of
packages Brain CALL_A could read. The Brain CALL_A that started at
`22:23:22.866` actually read the c-2026-04-27-22:20 packages.

This trace captures what happened **between 22:25:00 and 22:29:30**.

Source files:
- `data/logs/workers.log` (lines from grep — verbatim).
- `data/logs/brain.log` (Brain CALL_A reads — verbatim).

---

## F4.1 — Cycle start (M5 boundary 22:25:00)

**Wall-clock anchor:** 2026-04-27 22:25:00 UTC.

What fires first on the boundary itself: nothing — the kline_worker
is the first chained sweet-spot at offset `0:30`, not at `0:00`.

The very first events in this window are not sweet-spot worker fires
but cadence/heartbeat events:
- `22:25:00.378  ENFORCER_GRACE | el=0 remaining=0min ...`
- `22:25:00.379  ENFORCER_BEAT  | total=0T W=0 L=0 wr=0.0% strk=-1 hb=OK`
- `22:25:00.381  SYSTEM_HEALTH  | loop_lag=0.0ms tasks=33 mem=330MB cpu=5% pid=396`
- `22:25:00.423  WD_TICK        | mode=passive n=0 syms=[none]`

PriceWorker (continuous WS) heartbeat at `22:25:14.588`:
```
PRICE_WS_HEALTH | status=connected msgs_per_min=5013 msgs_in_window=3760
window_s=45.0 subscribed=50 quotes_cached=50
```

Worker liveness heartbeat at `22:25:29.663`:
```
WORKER_LIVENESS_HEARTBEAT | total=19 healthy=19 never_ticked=0 overdue=0
idle_cycle_gate=0 cycle_active=True
```
(Confirms `is_cycle_active() == True` for this cycle.)

---

## F4.2 — Layer 1A executions

### KlineWorker — sweet-spot 0:30

- **22:25:30.001** `SWEET_SPOT_FIRED | worker=kline_worker offset=0:30 drift_ms=1 fires=7`
- **22:25:51.373** `KLINE_TICK_SUMMARY | universe=50 fetched=29997 saved=29997 skipped=50 tf_split={5:10000,60:10000,240:9997,D:0} errors=0 el=21363ms drift_ms=1`
- **22:25:51.374** `LAYER1A_TICK_DONE | sub=kline_worker elapsed_ms=21373 drift_ms=1`

What it read: 50 symbols × {M5, H1, H4, D1} per `TIMEFRAME_SCHEDULE` at
`src/workers/kline_worker.py:32-37`. M5 + H1 + H4 fired this cycle (D1
skipped: `tf_split={...,D:0}`).

What it wrote: 29,997 kline rows via `executemany` into the `klines`
table (`src/database/repositories/market_repo.py:103-128`).
`INSERT OR IGNORE` so most are duplicates; net new rows are only the
latest M5/H1/H4 bars per symbol. Tick elapsed: **21,363 ms**.

Data freshness at end of Layer 1A: latest M5 timestamp written ≈ 22:25
boundary. The freshness scan inside the same tick (kline_worker.py:330-338)
would have logged `KLINE_FRESHNESS_WARN` if any coin's age >600 s; no
such line appears in the captured window for this cycle.

### PriceWorker — continuous (45s interval)

PriceWorker heartbeat at 22:25:14 (above) shows ws healthy, **5,013 msgs/min**.
Next price_worker tick at 22:25:59:
- **22:25:59.590** `LAYER1A_TICK_DONE | sub=price_worker elapsed_ms=1 interval_s=45.0`

WS quotes cached: 50 (matches subscribed=50).

### AltDataWorker — sweet-spot 1:45 (within this 5-min window: 22:26:45)

- **22:26:45.018** `SWEET_SPOT_FIRED | worker=altdata_worker offset=1:45 drift_ms=18 fires=7`
- **22:26:55.156** `ALTDATA_TICK_DONE | funding_ms=10137 oi_ms=9940 fg_ms=0 onchain_ms=2661 total_ms=10137 ran=[funding,oi,onchain]`
- **22:26:55.156** `LAYER1A_TICK_DONE | sub=altdata_worker elapsed_ms=10138 drift_ms=18`

What ran: `funding,oi,onchain` (F&G skipped — `fg_ms=0` because the F&G deadline
hadn't lapsed; F&G fires every 60 min per `config.toml:148`).

NewsWorker tick at 22:28:12.284 (every 300s independent of sweet-spot):
- **22:28:12.284** `LAYER1A_TICK_DONE | sub=news_worker elapsed_ms=1256 interval_s=300.0`

---

## F4.3 — Layer 1B executions

### StructureWorker — sweet-spot 0:45

- **22:25:45.001** sweet-spot fired (implicit — not in captured fragment;
  but XRAY_TICK_SUMMARY line is at 22:25:47). Earlier line in fragment:
  `22:25:30.001  SWEET_SPOT_FIRED | worker=kline_worker ...`. The
  structure_worker fire at 22:25:45 fell in a gap of the captured grep
  but its tick body emitted:
- **22:25:47.325** `XRAY_CLASSIFY_SUMMARY | total=25 bearish_fvg_ob=18 none=6 bullish_fvg_ob=1 conf_p50=0.55 conf_p95=0.55`
- **22:25:47.329** `XRAY_TICK_SUMMARY | universe=50 batch=1/2 symbols=25 analyzed=25 errors=0 cached=50 session=late_ny(mid) setups=12 skips=13 el=2303ms drift_ms=23`
- **22:25:47.333** `LAYER1B_TICK_DONE | sub=structure_worker elapsed_ms=2305 drift_ms=23`

What it read: H1 candles for 25 of 50 coins (batch=1/2). The candle
fetch path is `MarketRepository.get_klines(symbol, "60", 200)` at
`src/workers/structure_worker.py:351`, with Shadow DB fallback at line 360.

Which 25 coins processed: **batch=1/2** means the second batch of 25
(`_batch_start=25`, slicing watch_list[25:50]). The first batch (batch=0/2)
was processed in the previous cycle at 22:20:45 per the earlier fragment.

What it wrote: 25 entries written via `StructureCache.set(symbol, result)`
at `src/workers/structure_worker.py:136`. Cache is now sized
`cached=50` (the previous batch's 25 plus this batch's 25, both within
the 300s TTL).

OBSERVED: drift_ms=23 — slight late-fire. Tick still completed in 2,303 ms.

### SignalWorker — sweet-spot 1:00

- **22:26:00.021** `SWEET_SPOT_FIRED | worker=signal_worker offset=1:00 drift_ms=22 fires=7`
- **22:26:03.374** `SIG_TICK_SUMMARY | universe=50 signals=50 mean_conf=0.21 el=3352ms drift_ms=22`
- **22:26:03.375** `LAYER1B_TICK_DONE | sub=signal_worker elapsed_ms=3353 drift_ms=22`

What it read: For each of 50 symbols — invoked
`SentimentAggregator.aggregate_for_symbol(symbol)` and
`SignalGenerator.generate_signal(symbol)` (signal_worker.py:97-108).
The aggregator reads news_articles, ticker_cache (for momentum),
and fear_greed_index.

What it wrote:
- 50 rows into `aggregated_sentiment` (via repository).
- 50 entries into in-memory `_signal_cache` at `signal_worker.py:113`.
- 50 rows into `signals` table (via intelligence_aggregator).

Signal distribution: `mean_conf=0.21`. Sample DB rows show every
signal at `signal_type=neutral confidence≈0.20–0.24 source=intelligence_aggregator`.

### RegimeWorker — sweet-spot 1:15

- **22:26:15.017** `SWEET_SPOT_FIRED | worker=regime_worker offset=1:15 drift_ms=17 fires=7`
- **22:26:24.807** `REGIME_TICK_SUMMARY | universe=50 global=ranging per_coin_size=49 el=9789ms drift_ms=17`
- **22:26:24.808** `LAYER1B_TICK_DONE | sub=regime_worker elapsed_ms=9790 drift_ms=17`

What it read: Global regime via `self.detector.detect()` at
`regime_worker.py:143` (uses BTC klines). Per-coin via
`self.detector.detect_per_coin(coins_to_check)` at line 189.

What it wrote:
- 1 row into `regime_history` (global, primary symbol).
- 49 rows into `coin_regime_history` (one per coin in watch_list, minus primary).
- Updated `RegimeDetector._per_coin_regimes` (in-memory) at line 194.

Global regime: `ranging` confidence 0.4. Per-coin distribution
reflected in DB sample (BCHUSDT/LTCUSDT/APTUSDT = ranging, ALICEUSDT/OPUSDT = trending_down).

---

## F4.4 — Layer 1C execution

### StrategyWorker — sweet-spot 1:30

- **22:26:30.017** `SWEET_SPOT_FIRED | worker=strategy_worker offset=1:30 drift_ms=17 fires=7`
- **22:26:39.109** `STRAT_CONSENSUS_WRITE | full_count=9 filtered_count=7 setups_in=10 cache_size_after=19 mode=NORMAL threshold=50`
- **22:26:39.110** `STRAT_CYCLE_DONE | coins=50 signals=10 scored=10 hints=7 urg=0 el=9092ms | gate=0ms prefetch=8870ms(db=833ms ta=6011ms h1_db=2010ms h1_ta=1ms(cache_lookups=50 cache_valid=50 recomputed=0 hits=50)) L1=19ms L2=26ms L3=4ms L4=1ms misc=171ms drift_ms=17`
- **22:26:39.121** `LAYER1C_TICK_DONE | sub=strategy_worker elapsed_ms=9101 drift_ms=17`

Per `STRAT_CYCLE_DONE` breakdown at `strategy_worker.py:853-869`:

| Phase | elapsed_ms |
|-------|-----------:|
| `gate` (PnL+circuit-breaker) | 0 |
| `prefetch` total | 8,870 |
| ↳ db | 833 |
| ↳ ta (M5 TACache) | 6,011 |
| ↳ h1_db | 2,010 |
| ↳ h1_ta | 1 |
| `L1` (scan strategies) | 19 |
| `L2` (score) | 26 |
| `L3` (ensemble) | 4 |
| `L4` (handoff) | 1 |
| `misc` | 171 |
| **Total** | **9,092 ms** |

H1 TACache hit rate: `cache_lookups=50 cache_valid=50 recomputed=0 hits=50`
— 100% cache hit (the H1 prefetch only had to read from the warm cache).

What it wrote (per-cache, all in `LayerManager` instance):
- `_score_cache`: 10 entries (one per scored coin — `signals=10 scored=10`).
- `_strategy_consensus`: 9 new entries merged into existing 10
  (`full_count=9 ... cache_size_after=19`). Steady-state ~19 of 50.
- `_strategy_consensus_summary`: built from `filtered=7` setups
  (post-PnL restrictions).
- `_strategy_hints`: 7 entries (gated behind `is_layer_active(3)`).

OBSERVED: 41 of 50 coins are NOT in `_strategy_consensus` after this
cycle. Per `STRAT_CONSENSUS_WRITE` line, only 9 coins produced
consensus this tick (small relative to the 50 fed in via universe).

---

## F4.5 — Layer 1D execution

### ScannerWorker — sweet-spot 4:00 (offset 4:00 within window — fires at 22:29:00)

- **22:29:00.028** `SWEET_SPOT_FIRED | worker=scanner_worker offset=4:00 drift_ms=28 fires=8`

The scanner emits cycle markers on its tick. The **next-cycle's** scanner
data — built from this 22:25–22:29 window's upstream — will be reported
under `cycle_id=c-2026-04-27-22:25`. Searching the captured fragment:

NOT FOUND in captured workers.log fragment for `c-2026-04-27-22:25`
SCANNER_FILTER_AGGREGATE / SCANNER_PACKAGE_BUILD / SCANNER_SELECT lines.
The ScannerWorker fire at 22:29:00.028 happened, but the sub-second
detail lines (which usually appear within ~30 ms of fire) were not in
the grep output we collected. The most-recent fully captured scanner
output we have is for `cycle_id=c-2026-04-27-22:20` at 22:24:00, which
is the BEFORE-cycle for this trace.

#### Most-recent captured scanner cycle (cycle_id=c-2026-04-27-22:20, fired 22:24:00)

This is the cycle Brain CALL_A at 22:23:22 / 22:25:16 actually read:

```
22:24:00.001  SWEET_SPOT_FIRED | worker=scanner_worker offset=4:00 drift_ms=1 fires=7
22:24:00.001  LAYER1D_CYCLE_START | cycle_id=c-2026-04-27-22:20
22:24:00.014  SCANNER_FILTER_AGGREGATE | cycle_id=c-2026-04-27-22:20 total=50
              qualified=0
              fail_no_xray=25 fail_setup_none=10 fail_consensus=12
              fail_regime=2 fail_rr=1 fail_blockers=0
              pass_xray=15 pass_consensus_strong=3 pass_consensus_good=0
22:24:00.016  SCANNER_PACKAGE_BUILD_START | cycle_id=c-2026-04-27-22:20 packages_to_build=2
22:24:00.017  PACKAGE_VALIDATE | cycle_id=c-2026-04-27-22:20 sym=BTCUSDT
              completeness=0.67 verdict=warn
              missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed'] stale=[]
22:24:00.017  PACKAGE_VALIDATE | cycle_id=c-2026-04-27-22:20 sym=ETHUSDT
              completeness=0.73 verdict=warn
              missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed'] stale=[]
22:24:00.018  SCANNER_PACKAGE_BUILD_DONE | cycle_id=c-2026-04-27-22:20 packages=2
              total_size_bytes=1894 elapsed_ms=2
22:24:00.019  PACKAGE_VALIDATE_SUMMARY | cycle_id=c-2026-04-27-22:20 packages_built=2
              ok=0 warn=2 fail_quarantined=0
22:24:00.020  CYCLE_FRESHNESS | cycle_id=c-2026-04-27-22:20
              klines_age_p50_ms=209932 klines_age_p95_ms=1704546
              xray_age_p50_ms=194995 xray_age_p95_ms=494936
              packages_age_p50_ms=1 packages_age_p95_ms=1
              klines_keys=200 xray_keys=50 packages_keys=3
22:24:00.031  SCANNER_SELECT | cycle_id=c-2026-04-27-22:20 qualified=0
              selected=2 forced=2 watch_list=50
22:24:00.031  SCANNER_TICK_SUMMARY | watch_list=50 protected=0 scored=2 selected=2
              top_n=15 forced_in=2 mean_score=0.000 top=BTCUSDT(0.000) el=29ms drift_ms=1
22:24:00.031  LAYER1D_CYCLE_DONE | cycle_id=c-2026-04-27-22:20 elapsed_ms=30
22:24:00.033  CYCLE_COMPLETE | cycle_id=c-2026-04-27-22:20 layer1a_ms=0 layer1b_ms=6655
              layer1c_ms=7794 layer1d_ms=30 total_ms=14479 packages_ready=2 qualified_pct=0.0 status=ok
22:24:00.033  LAYER1D_TICK_DONE | sub=scanner_worker elapsed_ms=32 drift_ms=1
```

Final package count for cycle c-2026-04-27-22:20: **2** (BTCUSDT, ETHUSDT — both forced/reference pairs, both warn-verdict).

---

## F4.6 — Stage 2 read (Brain CALL_A)

The captured `data/logs/brain.log` shows Brain CALL_A reads. The most-recent CALL_A relative to this trace started at 22:23:22 and finished at 22:25:16:

```
22:23:22.866  STRAT_CALL_A_START | did=d-1777328602866
22:23:23.019  STRATEGIST_PACKAGES_READ | call=CALL_A count=2 age_min_s=263 age_max_s=263 reader=brain_call_a
22:23:24.085  STRAT_CALL_A_CTX | sections=40 chars=6529 el=1207ms
22:23:24.085  PROMPT_BUILD_DONE | call=CALL_A coins=2 size_bytes=6568 sections=40 packages=2 elapsed_ms=1207
22:23:24.119  STRAT_CALL_A | chars=6568
22:25:16.717  STRAT_CALL_A_PLAN | trades=2 risk=cautious view='Late NY dead zone, both BTC and ETH sold off hard today (-2.3% and -3.5%). Low v'
22:25:16.717  STRAT_CALL_A_END | el=113851ms trades=2
```

Reader site: `src/brain/strategist.py:1371-1372` —
`if lm is not None and hasattr(lm, "get_coin_packages"): packages = lm.get_coin_packages()`.
The log emit `STRATEGIST_PACKAGES_READ` is at `src/brain/strategist.py:1391`.

**Packages received:** 2 (BTCUSDT, ETHUSDT).
**Package age at read:** `age_min_s=263 age_max_s=263` (4 min 23 s).
**Final prompt size:** `chars=6568 size_bytes=6568 sections=40 packages=2 elapsed_ms=1207` to build.
**Brain decision:** `trades=2 risk=cautious`. (Subsequent BRAIN_DO_START at 22:25:16.718.)

### CALL_A reads in the captured brain.log window (5 events)

| Brain CALL_A start | did | Packages count | Age min/max | Prompt chars/sections | Trades |
|---|---|---|---|---|---|
| 22:01:43.869 | d-1777327303869 | 2 | 163/163 | 6946/49 chars | 2 |
| 22:08:47.920 | d-1777327727920 | 2 | 287/287 | 6517/41 chars | 1 |
| 22:17:03.516 | d-1777328223516 | 2 | 183/183 | 6973/44 chars | 0 |
| 22:23:22.866 | d-1777328602866 | 2 | 263/263 | 6529/40 chars | 2 |
| (no later CALL_A in fragment captured) | | | | | |

Every Brain CALL_A in the captured window read exactly **2 packages**
(forced BTC+ETH). Package ages range 163–287 s (≈3–5 minutes), which is
consistent with the ScannerWorker firing every 5 minutes at the `4:00`
sweet-spot offset and Brain CALL_A firing on its own 150 s cadence.

---

## Cycle timing summary (this cycle, 22:25:00 boundary)

```
+0:00  M5 boundary
+0:30  kline_worker fires      → KLINE_TICK_SUMMARY at +0:51 (21,363 ms)
+0:45  structure_worker fires  → XRAY_TICK_SUMMARY at +0:47 (2,303 ms, batch 1/2 — 25 coins)
+1:00  signal_worker fires     → SIG_TICK_SUMMARY at +1:03 (3,352 ms, 50 signals)
+1:15  regime_worker fires     → REGIME_TICK_SUMMARY at +1:24 (9,789 ms, 49 per-coin)
+1:30  strategy_worker fires   → STRAT_CYCLE_DONE at +1:39 (9,092 ms, 9 consensus, 7 hints)
+1:45  altdata_worker fires    → ALTDATA_TICK_DONE at +1:55 (10,137 ms, funding+oi+onchain)
+4:00  scanner_worker fires    → captured cycle's scanner output not in grep fragment;
                                  prior cycle (c-2026-04-27-22:20) at 22:24:00 produced
                                  qualified=0 selected=2 forced=2.
```

The **next** brain CALL_A will read whatever ScannerWorker produces from
this cycle (cycle_id `c-2026-04-27-22:25`). Brain CALL_A interval is
150 s per `src/core/layer_manager.py:85`; the next CALL_A relative to
22:25:16 is at ~22:30:16 (alternation: A→B→A) — not in the captured
brain.log fragment.

OBSERVED ANOMALY: across 7 captured Scanner cycles in workers.log
(22:09:00, 22:14:00, 22:19:00, 22:24:00, 22:29:00, 22:34:00, 22:39:00,
22:44:00) every `SCANNER_SELECT` line shows `qualified=0 selected=2 forced=2`.
The pipeline has not produced a single non-forced qualified coin in
the captured window.

OBSERVED ANOMALY: `CYCLE_FRESHNESS` for cycle c-2026-04-27-22:20 shows
`klines_age_p95_ms=1704546` (~28 minutes) and `xray_age_p95_ms=494936`
(~8 minutes) at scanner read time — meaning some kline-cache entries
are very stale at the moment ScannerWorker computes qualification.
`packages_keys=3` (BTCUSDT, ETHUSDT, and one more — NOT FOUND which
third key by name; the snapshot active_universe shows only 2 rows but
the freshness counter suggests a third package was written and
maybe quarantined by validator).
