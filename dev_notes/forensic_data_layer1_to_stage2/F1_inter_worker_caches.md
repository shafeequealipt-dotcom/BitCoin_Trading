# F1 — Inter-Worker In-Memory Caches

Snapshot timestamp reference: 2026-04-27 ~22:30 UTC (matches log tail).

For every shared in-memory cache used in the Layer 1 → Stage 2 pipeline,
this file records: defining file, owner (writer), every consumer reader,
key/value structure, typical size, TTL/invalidation, and a sample of
recent contents (where available).

Note on snapshots: there is no live snapshot mechanism for runtime worker
dicts; the system is running and these dicts live in worker process
memory only. Where DB-backed surrogates exist (`coin_regime_history`,
`aggregated_sentiment`, `funding_rates`, `ticker_cache`, `klines`,
`active_universe`) the latest rows are read from
`_trading_db_snapshot.db` and shown as proxies. Pure in-memory caches
(`_score_cache`, `_strategy_consensus*`, `_strategy_hints`,
`_coin_packages`, StructureCache, TACache, `_signal_cache`,
`_funding_cache`, `_ws_quotes`) cannot be snapshotted from the snapshot
DB; values come from log tail evidence (sizes, freshness ages, sample
keys/values).

---

## C1 — `_ws_quotes`  (PriceWorker)

- **Defining file:** `src/workers/price_worker.py:66` — `self._ws_quotes: dict[str, tuple[float, float]] = {}`
- **Owner / writer:** `PriceWorker` callback `_handle_ticker_update` at `src/workers/price_worker.py:196`:
  `self._ws_quotes[symbol] = (last_price, _time.monotonic())`
- **Consumers (readers):**
  - `PriceWorker.get_ws_quote(symbol, max_age_s=5.0)` at `src/workers/price_worker.py:239-257` — read at line 251.
  - Heartbeat read for log size: `src/workers/price_worker.py:156` (`quotes_cached={len(self._ws_quotes)}`).
  - No external worker imports the dict directly; access is through `get_ws_quote`. No grep hits in `src/apex/`, `src/brain/`, `src/workers/scanner_worker.py`, `src/workers/structure_worker.py` reading `_ws_quotes` or calling `get_ws_quote` (NOT FOUND for direct cross-worker reader; searched src/).
- **Key format:** symbol string (e.g. `"BTCUSDT"`).
- **Value structure:** `tuple[float, float]` = `(last_price, monotonic_seconds_at_set)`.
- **Typical size:** equal to subscribed universe — at last `PRICE_WS_HEALTH` heartbeat:
  `subscribed=50 quotes_cached={len(self._ws_quotes)}` is the format printed
  at `price_worker.py:156`. The number is not in the captured workers.log
  (no PRICE_WS_HEALTH lines in the 22:25–23:01 window we have); structurally
  it tracks 50 (the watch_list size).
- **TTL / invalidation:**
  - No expiry inside the dict itself.
  - Read-time freshness gate: `get_ws_quote(...)` rejects entries older
    than `max_age_s` (default 5.0). `src/workers/price_worker.py:255-257`.
  - Subscription-set change triggers `self._connected = False` at
    `price_worker.py:106-107` and reconnect — old entries remain but
    will only be refreshed for the new universe.
- **Sample 5 entries:** NOT FOUND — no in-memory snapshot mechanism;
  process introspection not requested (data collection only). DB
  surrogate `ticker_cache` (snapshot file) is shown in F2; first 5 rows
  at end of this file under the table’s entry.

---

## C2 — `ticker_cache` table (PriceWorker — DB persistence of WS ticks)

- **Defining file (DDL):** `src/database/migrations.py:37` (CREATE TABLE).
- **Owner / writer:** `MarketRepository.save_ticker` at
  `src/database/repositories/market_repo.py:268` (`INSERT OR REPLACE INTO ticker_cache ...`).
  Called from `PriceWorker._handle_ticker_update` via
  `loop.create_task(self.market_repo.save_ticker(ticker))`
  at `src/workers/price_worker.py:218`.
- **Consumers (readers):**
  - `MarketRepository.get_ticker` at `src/database/repositories/market_repo.py:285-296`.
  - `src/core/transformer.py:667` — `SELECT last_price, updated_at FROM ticker_cache WHERE symbol = ?`.
  - `src/intelligence/sentiment/aggregator.py:169` — `SELECT change_24h_pct FROM ticker_cache WHERE symbol = ?`.
  - `MarketService.get_ticker` 5-second cache wraps it (mentioned at
    `src/core/layer_manager.py:1199`, `src/workers/profit_sniper.py:836`).
- **Schema (verbatim):**
  ```sql
  CREATE TABLE ticker_cache (
        symbol TEXT PRIMARY KEY,
        last_price REAL NOT NULL,
        bid REAL NOT NULL DEFAULT 0,
        ask REAL NOT NULL DEFAULT 0,
        high_24h REAL NOT NULL DEFAULT 0,
        low_24h REAL NOT NULL DEFAULT 0,
        volume_24h REAL NOT NULL DEFAULT 0,
        change_24h_pct REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  ```
- **Indexes:** PK on `symbol` only.
- **Row count (snapshot):** 200.
- **Sample 5 rows (snapshot):**
  ```
  BATUSDT       0.09915     -5.8404   17192837.2   2026-03-27T10:26:44.755669+00:00
  B3USDT        0.0004844   60.5036   40027177300.0 2026-03-27T13:01:12.009192+00:00
  VIRTUALUSDT   0.6566      -4.6747   34912353.0   2026-03-27T15:16:05.047819+00:00
  PARTIUSDT     0.09979     -6.973    120000664.0  2026-03-27T16:56:32.103897+00:00
  ANKRUSDT      0.005018    5.798     3229061554.0 2026-03-28T02:26:07.257024+00:00
  ```
  OBSERVED ANOMALY: snapshot rows show `updated_at` from late-March
  2026; the latest WS ticks for the live workers are not visible in the
  static snapshot DB taken at 22:56 — full freshness check would
  require querying the live DB. Forensic data only.

---

## C3 — `_funding_cache`  (AltDataWorker)

- **Defining file:** `src/workers/altdata_worker.py:90` —
  `self._funding_cache: dict[str, float] = {}`.
- **Owner / writer:** `AltDataWorker.tick` at
  `src/workers/altdata_worker.py:185-192` —
  ```python
  for fr in result:
      sym = getattr(fr, "symbol", None)
      rate = getattr(fr, "funding_rate", None)
      if sym and rate is not None:
          try:
              self._funding_cache[sym] = float(rate)
  ```
  Updated each funding fetch (every `funding_rates` sweet spot fire,
  default `1:45` per 5-min window).
- **Consumers (readers):**
  - `AltDataWorker.get_funding(coin)` at `src/workers/altdata_worker.py:254-261`.
  - `ScannerWorker._get_funding_strength` at `src/workers/scanner_worker.py:154-170` (calls `adw.get_funding(coin)` line 164).
  - `ScannerWorker._check_blockers` at `src/workers/scanner_worker.py:281-294`.
  - `ScannerWorker._build_package` at `src/workers/scanner_worker.py:427-435`.
- **Key format:** symbol string.
- **Value structure:** `float` (funding rate, raw decimal — e.g. `0.0001` for 0.01%).
- **Typical size:** `cached_size={len(self._funding_cache)}` reported
  in `ALTDATA_FUNDING_TICK` log at `altdata_worker.py:212`. Live tail
  at 22:31:50 / 22:36:54 / 22:41:50 / 22:56:54 / 23:01:50 shows
  `ran=[funding,...]` every 5 min; size approaches universe (50).
  Workers.log fragments captured do not include the explicit
  `cached_size=` value (legacy line is below ALTDATA_TICK_DONE).
- **TTL / invalidation:** none — last value persists until overwritten
  by next tick. No staleness check at read.
- **Sample 5 entries:** NOT FOUND — no in-memory snapshot mechanism.
  DB surrogate `funding_rates` rows from snapshot:
  ```
  ALICEUSDT  0.0001       2026-04-27T22:41:50.060214+00:00
  BCHUSDT    5.26e-06     2026-04-27T22:41:49.991281+00:00
  LTCUSDT    -0.00011332  2026-04-27T22:41:49.920397+00:00
  APTUSDT    0.0001       2026-04-27T22:41:49.848534+00:00
  OPUSDT     -0.00010343  2026-04-27T22:41:49.779746+00:00
  ```

---

## C4 — `_signal_cache`  (SignalWorker)

- **Defining file:** `src/workers/signal_worker.py:67` —
  `self._signal_cache: dict[str, Signal] = {}`.
- **Owner / writer:** `SignalWorker.tick` at
  `src/workers/signal_worker.py:113` — `self._signal_cache[symbol] = signal`.
  Fires at sweet spot `1:00` (every 5-min window).
- **Consumers (readers):**
  - `SignalWorker.get_signal(coin)` at `src/workers/signal_worker.py:169-177`.
  - `ScannerWorker._get_signal_confidence` at `src/workers/scanner_worker.py:110-124` (line 114: `sw.get_signal(coin)`).
  - `ScannerWorker._build_package` at `src/workers/scanner_worker.py:414-420` (line 415: `sigw.get_signal(symbol)`).
- **Key format:** symbol string.
- **Value structure:** `Signal` dataclass from `src.core.types`. Includes at minimum `signal_type`, `confidence`, `direction` (read at scanner_worker.py:417-419).
- **Typical size:** equal to processed universe per tick. Live tail
  shows `signals=50 mean_conf=0.21` (`SIG_TICK_SUMMARY` at 22:26:03).
  Cache reaches 50 each cycle.
- **TTL / invalidation:** none in-cache; overwritten each tick.
  Fresh max age ≈ 5 minutes (one window).
- **Sample 5 entries:** NOT FOUND — in-memory only. DB surrogate
  (latest 5 rows of `signals` table — same shape, persisted by
  intelligence_aggregator):
  ```
  ALICEUSDT  neutral 0.2035      intelligence_aggregator 2026-04-27T22:26:03.368403+00:00
  BCHUSDT    neutral 0.20302435  intelligence_aggregator 2026-04-27T22:26:03.347217+00:00
  LTCUSDT    neutral 0.2436106   intelligence_aggregator 2026-04-27T22:26:03.327064+00:00
  APTUSDT    neutral 0.2035      intelligence_aggregator 2026-04-27T22:26:03.310434+00:00
  OPUSDT     neutral 0.20358255  intelligence_aggregator 2026-04-27T22:26:03.289664+00:00
  ```

---

## C5 — `_per_coin_regimes`  (RegimeWorker / RegimeDetector)

- **Defining file:** `src/strategies/regime.py:40` —
  `self._per_coin_regimes: dict[str, RegimeState] = {}`.
- **Owner / writer:** `RegimeWorker.tick` at
  `src/workers/regime_worker.py:194` — `self.detector._per_coin_regimes.update(per_coin)`.
  Initial restore from DB at `regime_worker.py:111-118` (after first tick).
- **Consumers (readers):**
  - `RegimeDetector.get_coin_regime` at `src/strategies/regime.py:46-48`.
  - `RegimeWorker.get_regime` at `src/workers/regime_worker.py:300-312` — wraps `RegimeDetector.get_coin_regime`, with fallback to direct `_per_coin_regimes` lookup at line 312.
  - `StrategyWorker.tick` reads at `src/workers/strategy_worker.py:166` —
    `coin_regimes = getattr(self.regime_detector, '_per_coin_regimes', {})`.
  - `ScannerWorker._get_regime_alignment` at `src/workers/scanner_worker.py:135-138` calls `rw.get_regime(coin)`.
  - `ScannerWorker._qualifies` at `src/workers/scanner_worker.py:565-573`.
  - `ScannerWorker._build_package` at `src/workers/scanner_worker.py:462-469`.
  - APEX assembler — `src/apex/assembler.py:590` uses
    `RegimeState.regime` enum value (consumer is via the same accessor route).
  - TIAS — `src/tias/collector.py:277-284` (RegimeState dataclass shape).
  - ProfitSniper — `src/workers/profit_sniper.py:1006` reads
    `getattr(self.regime_detector, "_last_regime", None)` (a separate
    field on the detector; per-coin path is `detector.detect()` fallback).
- **Key format:** symbol string.
- **Value structure:** `RegimeState` dataclass from
  `src/strategies/models/regime_types.py:42`. Fields seen in restore
  code (`regime_worker.py:111-118`): `regime` (MarketRegime enum),
  `confidence`, `adx`, `atr_percentile`, `choppiness`, `volume_ratio`,
  `trend_direction`, `active_strategy_categories`.
- **Typical size:** live tail —
  `REGIME_TICK_SUMMARY | universe=50 ... per_coin_size=49 el=9789ms drift_ms=17` (22:26:24).
  Steady state 49 of 50 (primary BTC tracked separately as `_last_regime`).
- **TTL / invalidation:** none in-cache. Persisted each tick via
  `INSERT INTO coin_regime_history` (regime_worker.py:252-258). Stale
  entries preserved across ticks.
- **Sample 5 entries (DB surrogate `coin_regime_history`):**
  ```
  ALICEUSDT  trending_down  0.61862976  2026-04-27 22:26:24
  BCHUSDT    ranging        0.4         2026-04-27 22:26:24
  LTCUSDT    ranging        0.4         2026-04-27 22:26:24
  APTUSDT    ranging        0.4         2026-04-27 22:26:24
  OPUSDT     trending_down  0.55720908  2026-04-27 22:26:24
  ```

OBSERVED ANOMALY (already noted in 22:27 monitor): `regime_history`
table for the global symbol has only 1 row at 22:21:15 and 6 rows in
the 22:00 hour, but the prior 06:00–21:00 hours contain 0 rows in our
snapshot — there is a gap from 06:00 to 21:00 (sqlite3 query
`SELECT substr(detected_at,1,13) hour, COUNT(*) ... GROUP BY hour`):
```
2026-04-27 00 .. 06    each 11–12 rows
[no rows for 06–21]
2026-04-27 21          1 row
2026-04-27 22          6 rows
```

---

## C6 — `_score_cache`  (StrategyWorker)

- **Defining file:** `src/workers/strategy_worker.py:93` —
  `self._score_cache: dict[str, float] = {}`.
- **Owner / writer:** `StrategyWorker` Layer 2 path at
  `src/workers/strategy_worker.py:588` —
  `self._score_cache[_sym] = float(_ss.total_score)`.
  Fires at sweet spot `1:30` (every 5-min window).
- **Consumers (readers):**
  - `StrategyWorker.get_score(coin)` at `src/workers/strategy_worker.py:891-900`.
  - `ScannerWorker._get_strategy_score` at `src/workers/scanner_worker.py:97-108` (line 101).
  - `ScannerWorker._build_package` at `src/workers/scanner_worker.py:396-397`.
  - Internal log at `strategy_worker.py:809` — `_score_cache_size = len(...)`.
- **Key format:** symbol string.
- **Value structure:** float `total_score` from a `ScoredSetup`.
- **Typical size:** populated only for coins that produced a raw signal in the current tick.
  Live tail `STRAT_CYCLE_DONE` 22:26:39: `coins=50 signals=10 scored=10 hints=7`.
  Cache size reflected at `strategy_worker.py:821` —
  `score_cache_size={_score_cache_size}` in the
  STRAT_L4_HANDOFF event (the workers.log fragments captured did not
  include this raw line; the log emit site is verified).
- **TTL / invalidation:** none — entries persist until overwritten.
- **Sample 5 entries:** NOT FOUND — in-memory only. No DB persistence
  of `_score_cache` values (it is the wrapper around L2 total_score
  which is not separately tabled).

---

## C7 — `_strategy_consensus`  (LayerManager — owned, written by StrategyWorker)

- **Defining file:** `src/core/layer_manager.py:104` —
  `self._strategy_consensus: dict[str, dict] = {}` (owner is `LayerManager`).
- **Owner / writer:** `StrategyWorker.tick` at
  `src/workers/strategy_worker.py:720-734` (Phase 3 — written EVERY
  tick, NOT under Layer 3 gate):
  ```python
  if layer_manager:
      new_consensus = self._build_per_coin_consensus(consensus_setups)
      existing = getattr(layer_manager, "_strategy_consensus", {}) or {}
      ... existing.update(new_consensus) ...
      layer_manager._strategy_consensus = existing
  ```
- **Consumers (readers):**
  - `LayerManager.get_strategy_consensus(symbol)` at
    `src/core/layer_manager.py:1388-1400` (`return self._strategy_consensus.get(symbol)`).
  - `ScannerWorker._build_package` at `src/workers/scanner_worker.py:388-390` (line 390: `consensus = lm.get_strategy_consensus(symbol)`).
  - `ScannerWorker._qualifies` at `src/workers/scanner_worker.py:552-553`.
  - `Strategist` (Brain) — `src/brain/strategist.py:1029-1035` and `1734-1741` falls back to `_strategy_consensus` if `_strategy_consensus_summary` missing.
- **Key format:** symbol string.
- **Value structure:** dict with keys `"consensus"` (e.g. STRONG/GOOD/NONE),
  `"consensus_score"` (float), `"vote_count"` (int), `"direction"`
  (str: long/short/neutral), `"last_updated"` (timestamp). Per
  docstring at `layer_manager.py:1391-1395`.
- **Typical size:** Live tail `STRAT_CONSENSUS_WRITE`:
  - 22:11:33 → `full_count=12 ... cache_size_after=18`
  - 22:16:38 → `full_count=11 ... cache_size_after=18`
  - 22:21:37 → `full_count=10 ... cache_size_after=19`
  - 22:26:39 → `full_count=9 ...  cache_size_after=19`
  Steady-state size ≈ 18–19 of 50.
- **TTL / invalidation:** none. Stale entries preserved across cycles
  via merge (`existing.update(new_consensus)`).
- **Sample 5 entries:** NOT FOUND — in-memory only.

---

## C8 — `_strategy_consensus_summary`  (LayerManager — alias for legacy strategist reads)

- **Defining file:** `src/core/layer_manager.py:106` —
  `self._strategy_consensus_summary: dict = {}`.
- **Owner / writer:** `StrategyWorker.tick` at
  `src/workers/strategy_worker.py:737` —
  `layer_manager._strategy_consensus_summary = self._build_consensus_summary(filtered)`.
- **Consumers (readers):**
  - `Strategist._build_..._prompt` at `src/brain/strategist.py:1034-1035` and `1740-1741`.
- **Key format:** symbol string.
- **Value structure:** Legacy summary dict with `{"buy", "sell", "total_score"}` per the
  defensive migration check at `strategy_worker.py:727-732`.
- **Typical size:** logged at `strategy_worker.py:815` as
  `consensus_summary_size={_summary_size}`. Built from `filtered`
  setups (post PnL restrictions). Same range as filtered_count
  (7–9 in the 22:11–22:26 window).
- **TTL / invalidation:** overwritten each tick, no stale-entry merge.
- **Sample 5 entries:** NOT FOUND — in-memory only.

---

## C9 — `_strategy_hints`  (LayerManager — written by StrategyWorker)

- **Defining file:** `src/core/layer_manager.py:108` —
  `self._strategy_hints: list = []`.
- **Owner / writer:** `StrategyWorker.tick` at
  `src/workers/strategy_worker.py:803` —
  `layer_manager._strategy_hints = hints`. Gated behind
  `if layer_manager.is_layer_active(3):` (line 776) — so hints are
  only written when Layer 3 (Execution) is on.
- **Consumers (readers):**
  - `Strategist` at `src/brain/strategist.py:1019-1020` and
    `src/brain/strategist.py:1725-1726`:
    `hints = getattr(layer_manager, "_strategy_hints", []) or []`.
- **Key format:** N/A — list, not dict.
- **Value structure (per `strategy_worker.py:786-793`):**
  ```python
  {
    "symbol":   <str>,
    "direction": <"long"|"short">,
    "strategy":  <strategy_name str>,
    "score":     <float, rounded 1 decimal>,
    "consensus": <"STRONG"|"GOOD"|...>,
  }
  ```
- **Typical size:** capped at 20 (`filtered[:20]` at `strategy_worker.py:783`).
  Live tail `STRAT_CYCLE_DONE`: `hints=7` to `hints=9` in the captured
  window — well under the cap.
- **TTL / invalidation:** overwritten each tick.
- **Sample 5 entries:** NOT FOUND — in-memory only.

---

## C10 — `StructureCache`  (StructureWorker)

- **Defining file:** `src/analysis/structure/structure_cache.py:18` —
  `class StructureCache`. Holds `self._cache: dict[str, tuple[float, StructuralAnalysis]]` at line 27.
- **Owner / writer:** `StructureWorker.tick` at
  `src/workers/structure_worker.py:136` — `self._cache.set(symbol, result)`.
  Fires at sweet spot `0:45` (every 5-min window).
  Internal `set` at `structure_cache.py:46-48` stamps `time.monotonic()`.
- **Consumers (readers):**
  - `StructureCache.get(symbol)` at `structure_cache.py:31-44` (TTL check at line 40).
  - `StructureCache.get_all()` at `structure_cache.py:50-60` (returns fresh entries only).
  - `StructureCache.get_top_setups(n)` at `structure_cache.py:62-77`.
  - `StructureCache.get_ranked_setups()` at `structure_cache.py:99-101` — returns the scanner-ranked subset (a separate field set by `set_ranked_setups`).
  - `StructureWorker.get_setup_score(coin)` at
    `src/workers/structure_worker.py:296-313` (calls `self._cache.get(coin)`).
  - `ScannerWorker._build_package` at
    `src/workers/scanner_worker.py:336-340` —
    `cache = getattr(sw, "_cache", None) ... structure = cache.get(symbol)`
    (note: this uses the cache's TTL-respecting `.get`).
  - `ScannerWorker._qualifies` at `src/workers/scanner_worker.py:534-540` (same pattern as build_package).
  - `Strategist` reads ranked setups at `src/brain/strategist.py:747` and `:1578` — `structure_cache.get_ranked_setups()`.
  - `PerformanceEnforcer` at `src/strategies/performance_enforcer.py:462` — `ranked = structure_cache.get_ranked_setups()`.
  - `Telegram analysis handler` at `src/telegram/handlers/analysis.py:40`.
- **Key format:** symbol string.
- **Value structure:** `tuple[float, StructuralAnalysis]` =
  `(monotonic_set_time, analysis)` where `StructuralAnalysis` is from
  `src/analysis/structure/models/structure_types.py`. Per
  `scanner_worker.py:336-381`, the analysis exposes attributes:
  `current_price`, `structural_placement` (with `direction`, `long_sl_price`,
  `long_tp_price`, `short_sl_price`, `short_tp_price`, `structural_sl`,
  `structural_tp`, `rr_ratio`), `setup_type` (enum with `.value`),
  `setup_score`, `setup_type_confidence`, `confluence_quality`,
  `session_context` (with `current_session`, `session_phase`,
  `manipulation_likely`).
- **Typical size:** live tail —
  `XRAY_TICK_SUMMARY | universe=50 ... cached=50` (22:25:47, 22:20:45, 22:15:45,
  22:10:45). Cache fills to 50 across the 2 batches of 25.
- **TTL / invalidation:**
  - DEFAULT_TTL = 300.0 seconds at `structure_cache.py:15`. `get()`
    rejects entries older than `self._ttl` at line 40.
  - `invalidate(symbol|None)` at line 87-92.
  - `clear()` at line 79-81.
- **Cache-health log:** `structure_cache.py:117-128` — `get_oldest_entry_age_seconds()`.
  Workers.log tail `XRAY_CLASSIFY_SUMMARY` shows confidence p50/p95.
- **Sample 5 entries:** NOT FOUND — in-memory only. Per `XRAY_CLASSIFY_SUMMARY`
  at 22:25:47: `total=25 bearish_fvg_ob=18 none=6 bullish_fvg_ob=1 conf_p50=0.55 conf_p95=0.55`.

---

## C11 — `TACache`  (lazy, shared via service registry)

- **Defining file:** `src/analysis/ta_cache.py:62` — `class TACache`.
  Holds `self._cache: OrderedDict[str, tuple[float, dict]]` at line 91.
- **Owner / writer:** `TACache.analyze(...)` itself populates on miss at
  `ta_cache.py:166-174` (under lock; LRU-evicts at maxsize). Single
  instance constructed at `src/workers/manager.py:189`:
  `ta_cache = TACache(ta_engine_raw, ttl_seconds=120.0)`.
  Registered three times for back-compat at `manager.py:190-192`:
  `services["ta"] = ta_cache`, `services["ta_engine"] = ta_cache`,
  `services["ta_cache"] = ta_cache`.
- **Consumers (readers / lazy populators):**
  - `StrategyWorker` at `src/workers/strategy_worker.py:1451-1454` — `_ta_cache.analyze(...)`.
  - `ProfitSniper` at `src/workers/profit_sniper.py:980-984` — `ta_cache.analyze(...)`.
  - `Strategist` at `src/brain/strategist.py:598`, `625-627`, `1347`, `1451-1453`, `2308-2321` — calls `ta_cache.analyze(...)`.
  - `APEX assembler` at `src/apex/assembler.py:204-208`.
  - `TIAS collector` at `src/tias/collector.py:355-360`.
  - `VolatilityProfiler` at `src/analysis/volatility_profile.py:198-219`.
  - `FreshnessGuard` at `src/core/freshness_guard.py:59-61` (reads `is_fresh`).
- **Key format:** `f"{sym}:{tf}"` (unified across both candles-path and
  symbol-path per the comment at `ta_cache.py:27-48`).
- **Value structure:** `tuple[float, dict]` = `(monotonic_set_time, analysis_result_dict)`.
- **Typical size:** maxsize=200 (line 58 `_DEFAULT_MAXSIZE`). Steady-state
  working set ≈ 32–64 entries per the comment at lines 50-53.
  No live `TA_CACHE_SIZE` log lines in the 22:25–23:01 workers.log
  fragments captured.
- **TTL / invalidation:**
  - TTL = 120 s at construction (`manager.py:189`); module-level
    DEFAULT_TTL = 90 s at `ta_cache.py:25`. Live wiring uses 120 s.
  - LRU eviction past maxsize at `ta_cache.py:171-174`.
  - `invalidate(symbol|None)` at `ta_cache.py:183-200`.
- **Hit rate (live tail evidence):** `STRAT_CYCLE_DONE` at 22:26:39
  reports `cache_lookups=50 cache_valid=50 recomputed=0 hits=50` —
  100% hit rate after StrategyWorker H1 prefetch.
- **Sample 5 entries:** NOT FOUND — in-memory only.

---

## C12 — `_coin_packages`  (LayerManager — written by ScannerWorker)

- **Defining file:** `src/core/layer_manager.py:113` —
  `self._coin_packages: dict = {}`.
- **Owner / writer:** `ScannerWorker.tick` at
  `src/workers/scanner_worker.py:884-886`:
  ```python
  lm = self.services.get("layer_manager")
  if lm is not None:
      lm._coin_packages = packages
  ```
  Fires at sweet spot `4:00` (every 5-min window).
- **Consumers (readers):**
  - `LayerManager.get_coin_packages()` at
    `src/core/layer_manager.py:1402-1409` (`return getattr(self, "_coin_packages", {}) or {}`).
  - `Strategist._build_trade_prompt` at `src/brain/strategist.py:1371-1372`:
    `if lm is not None and hasattr(lm, "get_coin_packages"): packages = lm.get_coin_packages()`.
  - Configuration switch documented at `src/config/settings.py:402` and
    `src/config/settings.py:1695`.
- **Key format:** symbol string.
- **Value structure:** `CoinPackage` dataclass from
  `src/core/coin_package.py`. Fields populated by
  `ScannerWorker._build_package` (lines 316-499) include:
  `symbol`, `qualified` (bool), `opportunity_score` (float),
  `qualification_reasons` (list[str]), `price_data` (PriceDataBlock:
  `current`, `change_24h_pct`, `volume_24h_usd`, `regime`),
  `xray` (XrayBlock: `setup_type`, `setup_score`,
  `setup_type_confidence`, `structural_levels`, `mtf_confluence`,
  `session`, `session_phase`, `key_features`),
  `strategies` (StrategiesBlock: `fired_count`, `fired_strategies`,
  `ensemble_consensus`, `consensus_score`, `total_score`),
  `signals` (SignalsBlock: `direction`, `confidence`),
  `alt_data` (AltDataBlock: `funding_rate`, `funding_signal`, `fear_greed`),
  `open_position` (dict|None — populated only if forced),
  `blockers_observed` (list[str]).
- **Typical size:** live tail `SCANNER_PACKAGE_BUILD_DONE`:
  - 22:14:00 → `packages=2 total_size_bytes=1876 elapsed_ms=3`
  - 22:19:00 → `packages=2 total_size_bytes=1956 elapsed_ms=2`
  - 22:24:00 → `packages=2 total_size_bytes=1894 elapsed_ms=2`
  Stuck at 2 (forced BTC + ETH) every cycle in the captured window.
- **TTL / invalidation:** rebuilt fresh each scanner tick (assignment,
  not merge). Stale only if scanner doesn't fire.
- **Sample (validation result, snapshot of last cycle in window):**
  - `PACKAGE_VALIDATE | sym=BTCUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed']` (22:24:00:017).
  - `PACKAGE_VALIDATE | sym=ETHUSDT completeness=0.73 verdict=warn missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed']` (22:24:00:017).
  - The most-recent `PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=0 warn=2 fail_quarantined=0` (22:24:00:019).

---

## C13 — `aggregated_sentiment` (DB-backed; SentimentAggregator writes)

This is a TABLE, not an in-memory cache, but listed in the inventory.
Documented for completeness here; full F2 entry below.

- **Owner / writer:** `SentimentAggregator.aggregate_for_symbol(...)` at
  `src/intelligence/sentiment/aggregator.py:270` —
  `await self._sentiment_repo.save_aggregated_sentiment(result)`.
  Called from `SignalWorker.tick` at `src/workers/signal_worker.py:97-98`.
- **Consumers (readers):** `SentimentRepository.get_aggregated_sentiment_*`
  at `src/database/repositories/sentiment_repo.py:157` and `:174`;
  `MCP get_aggregated_sentiment` tool at
  `src/mcp/tools/sentiment_tools.py:72-90`.
- **Schema (verbatim from snapshot):**
  ```sql
  CREATE TABLE aggregated_sentiment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        overall_score REAL NOT NULL DEFAULT 0,
        level TEXT NOT NULL DEFAULT 'neutral',
        news_score REAL NOT NULL DEFAULT 0,
        news_count INTEGER NOT NULL DEFAULT 0,
        reddit_score REAL NOT NULL DEFAULT 0,
        reddit_count INTEGER NOT NULL DEFAULT 0,
        fear_greed_value INTEGER NOT NULL DEFAULT 50,
        momentum REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  CREATE INDEX idx_agg_sentiment_symbol
      ON aggregated_sentiment(symbol, created_at DESC);
  ```
- **Row count (snapshot):** 276,330.
- **Sample 5 latest rows:**
  ```
  ALICEUSDT 0.0 unknown 0 47 2026-04-27 22:26:03
  BCHUSDT   0.0 unknown 0 47 2026-04-27 22:26:03
  LTCUSDT   0.0 unknown 0 47 2026-04-27 22:26:03
  APTUSDT   0.0 unknown 0 47 2026-04-27 22:26:03
  OPUSDT    0.0 unknown 0 47 2026-04-27 22:26:03
  ```
- **Growth rate (snapshot):** 56 rows in 21:00 hour, 116 rows in 22:00 hour
  → ~50/min steady-state per signal_worker fire, 50 coins/min.

---

## Other discovered shared dicts (out-of-scope but inventoried)

Greps for `self\._.*cache` across `src/workers/` returned these
additional caches that exist in single-worker scope only — listed for
completeness:

- `_atr_cache` at `src/workers/profit_sniper.py:146` — `dict[str, tuple[float, float]]`. Owner+reader: `ProfitSniper` only.
- `_cached_regime` + `_regime_cache_time` at `src/workers/profit_sniper.py:151-152`. Owner+reader: `ProfitSniper` only (30-second cache wrapping `RegimeDetector.detect()`).
- `_arrays_cache` at `src/workers/sniper_ring_buffer.py:71`. Owner+reader: ring buffer instance only.
- `_market_data` (mentioned but no shared cross-worker access).

These do NOT cross worker boundaries; they are scoped to their owner.

---

## Snapshot mechanism gap

Hard Rule 4 (live state snapshot at named timestamp) — there is no
runtime introspection endpoint that dumps any of `_score_cache`,
`_strategy_consensus*`, `_strategy_hints`, `_signal_cache`,
`_per_coin_regimes`, `StructureCache._cache`, or `TACache._cache`.

What exists:
- `StructureCache.get_stats()` at `structure_cache.py:107-115` — returns hit/miss counts (logged by structure_worker as `XRAY_CACHE_HEALTH`).
- `TACache.get_stats()` at `ta_cache.py:202-233` — returns hit/miss/eviction counts (logged by strategy_worker as `TA_CACHE_SIZE`).
- `STRAT_L4_HANDOFF` at `strategy_worker.py:819-826` — cache **sizes** but not contents.
- `SCANNER_PACKAGE_BUILD_DONE` at `scanner_worker.py:899-903` — **size and total bytes** of `_coin_packages` but not contents.

For per-entry contents, code change would be required (out of scope).

NOT FOUND — searched: `src/workers/*.py`, `src/core/layer_manager.py`,
`src/analysis/ta_cache.py`, `src/analysis/structure/structure_cache.py`
for any `dump_cache`, `snapshot`, or `to_json` method on the cache
classes.
