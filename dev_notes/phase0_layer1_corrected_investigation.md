# Phase 0 — Layer 1 Corrected Migration: Investigation Pass

**Engagement:** Layer 1 corrected migration to the architecture defined in `LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md`.
**Date:** 2026-04-26
**Branch:** `main` (HEAD `9db6ed3` — phase9-fix: DB_LOCK_WAIT preserves last-holder across release)
**Status:** Phase 0 of 9 — investigation only, NO code changes in this commit.
**Working tree state at investigation:** 11 modified files (incl. `trading.db`), 11 untracked items (incl. earlier phase reports). Operator opted to "leave dirty, work on top." This Phase 0 commit only adds this file.

---

## 0. Purpose and Method

This file is the deliverable for Phase 0 of the IMPLEMENT_LAYER1_CORRECTED_MIGRATION_PROFESSIONAL prompt. It rebuilds an accurate, file-by-file understanding of the current Layer 1 implementation BEFORE any code is modified, so subsequent migration phases proceed on verified facts rather than the misunderstanding that produced the previous Layer 1 design.

Method:
- Every file referenced has been read in full or in the precisely-cited line range.
- Every claim about a behavior is anchored to a `path:line` citation.
- Cross-cutting registries (every reader of `active_universe`, every `_on_universe_change` handler, every `watch_list` reader, every `KLINE_BACKFILL`/`SIGNAL_BACKFILL` log site) were enumerated by `rg` and classified.
- Five concrete questions from the prompt's "Verification Gate" are answered at the end.

---

## 1. Per-Worker Investigation

### 1.1 KlineWorker

**File:** `src/workers/kline_worker.py` (380 LOC)
**Parent:** `BaseWorker` (`src/workers/base_worker.py:46`)
**Log component:** `worker`

**Section A — How it gets its coin list (TODAY).** Line 127: `universe = await self._scanner.get_active_universe()`. The scanner is the SOLE source of truth (HR-1 of the previous Layer-1 work). Three explicit failure modes return early without processing (lines 119–139): `no_scanner_injected`, `scanner_error`, `scanner_returned_empty`. After line 141 (`self._tracked_symbols = universe`) the working set is the 30-coin active_universe.

**Section B — How it schedules its ticks (TODAY).** Constructor passes `interval_seconds=float(settings.workers.market_data_interval)` (line 49) into `BaseWorker`. In `config.toml` `[workers] market_data_interval = 45`. The dataclass default in `src/config/settings.py:126` is 60. BaseWorker's `start()` loop at `base_worker.py:108–175` calls `await self.tick()` then `await asyncio.sleep(self.interval)`. Workers tick on a fixed cadence regardless of when underlying data sources update.

**Section C — State the worker maintains.**
- `_tracked_symbols: list[str]` — current working set (line 55). Init to `settings.bybit.default_symbols`; replaced each tick.
- `_last_fetch: dict[str, float]` — per-(symbol, timeframe) cooldown timestamps (line 56). Steady-state size ≈ `len(universe) × len(TIMEFRAME_SCHEDULE)` ≈ 30 × 4 = 120.
- `_last_tick_per_symbol: dict[str, int]` — per-symbol fetched count for KLINE_GAP attribution (line 61).
- `_circuit_breaker_until: float` — 30-s circuit on zero-fetch (line 66). Strategy worker reads via `is_circuit_open()` accessor.
- `_consecutive_fails: dict[str, int]` + `_fail_streak_started: dict[str, float]` — STRAGGLER promotion (lines 71–72).
- DB tables read/written: writes `klines` (PK/UNIQUE on `(symbol, timeframe, timestamp)`), reads `kline_recency_check` view via the post-tick freshness scan.
- TIMEFRAME_SCHEDULE (lines 22–27): `M5=60s, H1=60s, H4=300s, D1=3600s` — minimum interval between fetches per (symbol, timeframe).

**Section D — `_on_universe_change` handler (TODAY).** Lines 339–379. On rotation-out: prunes `_last_fetch` and `_last_tick_per_symbol` entries for departed coins and emits `KLINE_STATE_CLEANUP | removed=N sample=[...] last_fetch_size=N | {ctx()}`. On rotation-in: eager backfill — for each newly-added symbol, fetches every timeframe in TIMEFRAME_SCHEDULE with a 100 ms inter-call sleep, emits `KLINE_BACKFILL | sym=... tfs=...` (line 377) and `KLINE_BACKFILL_FAIL` on failure (line 379). **Becomes obsolete under the corrected architecture** (workers always operate on 50; nothing rotates from a worker's perspective). Disable in Phase 2 (deprecation no-op), delete in Phase 7.

**Section E — Log tags emitted (TODAY).** `KLINE_UNIVERSE_EMPTY` (3 reasons), `KLINE_TICK` summary (legacy `Kline worker: fetched N klines for S symbols` line 334–337), `KLINE_GAP` (per-symbol shortfall), `KLINE_FETCH` per-(symbol, timeframe) DEBUG/INFO, `KLINE_WRITE_LAG` (line 322), `KLINE_BACKFILL` / `KLINE_BACKFILL_FAIL` / `KLINE_STATE_CLEANUP` (universe-change), `KLINE_STRAGGLER` (consecutive fails), `KLINE_CIRCUIT_OPEN` (zero-fetch), all class `_classify_fetch_quality` log severities (INFO/WARNING/ERROR/CRITICAL).

**Section F — External dependencies.** Bybit REST via `MarketService.get_klines(symbol, timeframe, limit=200)` (one HTTP call per (symbol, timeframe) past cooldown). Writes `klines` rows via `DatabaseManager.execute` / `executemany` — known D-3 lock contention site (memory: `project_shadowklinereader_fix.md`). No WebSocket use here.

**Section G — Where output is consumed.**
- `MarketRepository.get_klines(symbol, tf, limit)` (DB read) → consumed by `StrategyWorker.tick()` prefetch (`get_klines_batch` line 197), `StructureWorker._fetch_klines` (line 223), `RegimeDetector` (per-coin), `TACache.analyze(candles=...)` (uses the candles directly).
- `is_circuit_open()` consumed by `StrategyWorker` (line 125) to gate TA on a fetch collapse.
- TACache rebuilds via pull-based `analyze(candles=)` calls; no event subscription on klines writes.

---

### 1.2 structure_worker (X-RAY)

**File:** `src/workers/structure_worker.py` (240 LOC)
**Parent:** `BaseWorker`
**Log component:** `xray`

**Section A.** Line 193: `universe = await self._scanner.get_active_universe()`. Three failure modes (lines 187–204) emit `XRAY_UNIVERSE_EMPTY` with reasons `no_scanner_injected`, `scanner_error`, `scanner_returned_empty`. After line 210 (`self._full_universe = universe`) batches `batch_size` (default 25) per tick with wrap-around (lines 213–218), so 30 coins / 25 = ~2 ticks per full sweep.

**Section B.** Constructor: `interval_seconds=float(settings.structure.worker_interval_seconds)` (line 53). config.toml `[analysis.structure] worker_interval_seconds = 60`. Same `BaseWorker.start()` fixed-interval loop as kline.

**Section C.** Instance state:
- `_engine: StructureEngine` — runs all 10 X-RAY phases.
- `_cache: StructureCache` (`src/analysis/structure/structure_cache.py:14–116`, TTL 300 s, key=symbol, value=`(monotonic_ts, StructuralAnalysis)`).
- `_market_repo: MarketRepository` — reads klines from `trading.db`.
- `_shadow_reader: ShadowKlineReader | None` — async-aiosqlite reader against `shadow.db` (the 2026-04-25/26 fix per memory `project_shadowklinereader_fix.md`).
- `_session_timer: SessionTimer | None` — Asian/London/NY session classifier (lazy init).
- `_setup_scanner: SetupScanner | None` — Phase 11 setup ranking (lazy init).
- `_full_universe: list[str]`, `_batch_start: int`, `_batch_size: int` — batching cursor.
- DB read: `klines` (H1) via `market_repo.get_klines` then via `shadow_reader.get_klines(sym, "60", 200)` fallback.
- DB write: NONE direct (cache lives in-memory; setups available via cache accessors).

**Section D.** No `_on_universe_change` handler defined on this class. Rotation events still fire but are no-ops here (manager's master dispatcher has nothing to call). The `_full_universe` is refreshed every tick via `_get_universe()` (line 210), so universe rotations propagate naturally on next tick.

**Section E.** `XRAY_UNIVERSE_EMPTY` (3 reasons), `XRAY_TICK` summary (line 165–170), `XRAY_TICK_ERR` per-symbol (Phase-11 promoted from DEBUG to WARNING — line 127), `XRAY_SESSION_ERR` (line 99), `XRAY_SCANNER_ERR` (line 143; still DEBUG). The `XRAY_TICK` line currently lacks `el_ms` (it has `el=` but the prompt's required `XRAY_TICK_SUMMARY` adds `drift_ms` + `universe=`).

**Section F.** Calls `_engine.analyze(symbol, current_price, candles, session_context=...)` synchronously (StructureEngine is CPU-bound). Klines fetch goes through `MarketRepository.get_klines` first (trading.db, hits the same lock as kline_worker — D-3 site), falls through to `ShadowKlineReader.get_klines` (shadow.db, async-aiosqlite, separate connection — no D-3 collision). `SetupScanner.scan(all_analyses, session_context)` runs once per tick reading the FULL cache.

**Section G.** Output consumers:
- `StructureCache.get(symbol)` → `StructuralAnalysis` for the symbol (consumed by APEX assembler, scorer, strategist Section 5 prompt builder).
- `StructureCache.get_top_setups(n=8)` → top-N consumed by strategist's prompt builder.
- `StructureCache.set_ranked_setups(ranked, skip_list)` written each tick (line 139); `get_ranked_setups()` consumed by APEX prompts.
- **No public accessor for `setup_score(coin)` exists yet** — Phase 6's new ScannerWorker will need one. Phase 3 of this migration adds `get_setup_score(coin: str) -> float | None` reading from `cache.get(coin).setup_score`.

---

### 1.3 SignalWorker

**File:** `src/workers/signal_worker.py` (179 LOC)
**Parent:** `BaseWorker`
**Log component:** `worker`

**Section A.** Line 73: `symbols = await self._scanner.get_active_universe()`. Three failure modes (lines 66–85) emit `SIGNAL_UNIVERSE_EMPTY`. `_scanner` late-wired in manager.py via the loop at lines 900–902.

**Section B.** Constructor: `interval_seconds=float(settings.workers.health_check_interval)` (line 45). Default 60 s in dataclass; config.toml shows `health_check_interval = 120`.

**Section C.** Instance state:
- `aggregator: SentimentAggregator` — pre-computes sentiment per coin (`aggregate_for_symbol`).
- `signal_generator: SignalGenerator` — generates final signal per coin.
- `ta_engine` — accepted in constructor but unused (kept for backward-compat init signature; comment line 30).
- DB: aggregator writes `aggregated_sentiment`; signal_generator may write `signals`. SignalWorker holds NO per-coin in-memory state of its own — it's a thin pipeline orchestrator.

**Section D.** `_on_universe_change` at lines 150–178. On rotation-out: emits `SIGNAL_REMOVED` log, no state cleanup needed (no per-coin in-memory state). On rotation-in: eagerly calls `signal_generator.generate_signal(sym)` per added coin, emits `SIGNAL_BACKFILL` / `SIGNAL_BACKFILL_FAIL`. **Obsolete under corrected architecture** — Phase 4 disables, Phase 7 deletes.

**Section E.** `SIGNAL_UNIVERSE_EMPTY` (3 reasons), per-coin `Signal for {s}: ...` legacy line (114–117), `SIG_BATCH | n=...` summary with `el=Xms` (line 128), `SIG_BATCH_STATS | conf_min/max/mean/std/n` distribution diagnostic (lines 144–148), `SIGNAL_REMOVED`, `SIGNAL_BACKFILL`, `SIGNAL_BACKFILL_FAIL`, "Sentiment aggregation failed" warning (line 103).

**Section F.** Calls `aggregator.aggregate_for_symbol(symbol)` and `signal_generator.generate_signal(symbol)` per coin per tick. Both consume DB tables (sentiment scores, news headlines, F&G index, funding rates) and produce `signals` rows.

**Section G.** Output: `signals` table rows. **No public accessor** for `get_signal(coin)` returning the most recent SignalResult — Phase 4 must add one for Phase 6's composite scoring.

---

### 1.4 RegimeWorker

**File:** `src/workers/regime_worker.py` (302 LOC)
**Parent:** `BaseWorker`
**Log component:** `worker`

**Section A.** Line 57: `universe = await self._scanner.get_active_universe() or []`. Failure: `REGIME_UNIVERSE_FETCH_FAIL` warning, falls through with `universe = []`.

**Section B.** Constructor: `interval_seconds=float(settings.regime.detection_interval_seconds)` (line 32). Default 300 s (5 min).

**Section C.** Instance state:
- `detector: RegimeDetector` — owns `_per_coin_regimes: dict[str, RegimeState]`, `_confirmed_regimes`, `_pending_regime` (the hysteresis caches).
- DB tables: writes `regime_history` (global) and `coin_regime_history` (per-coin); reads `coin_regime_history` once on first tick to restore state across restarts (lines 64–127). The first-tick restore filters by `WHERE symbol IN (universe)` so departed coins don't re-enter — HR-1 compliance.
- `_cleanup_counter: int` — incremented per tick; runs `DELETE FROM coin_regime_history WHERE timestamp < datetime('now', '-24 hours')` every 100 ticks.
- `_restored: bool` — one-shot first-tick gate.

**Section D.** `_on_universe_change` at lines 247–301. On rotation-out: prunes `_per_coin_regimes`, `_confirmed_regimes`, `_pending_regime` for departed coins; emits `REGIME_STATE_CLEANUP`. On rotation-in: eagerly runs `detect_per_coin([added])` → updates `_per_coin_regimes`; emits `REGIME_BACKFILL` / `REGIME_BACKFILL_FAIL`. **Obsolete under corrected architecture** — Phase 4 disables, Phase 7 deletes.

**Section E.** `REGIME_UNIVERSE_FETCH_FAIL`, `REGIME_RESTORE` / `REGIME_RESTORE_SKIP` / `REGIME_RESTORE_FAIL`, `REGIME_GLOBAL` (line 146 — global regime + ADX + chop), `REGIME_PERCOIN` (line 185 — count + total cached + universe size + divergent count), `REGIME_PERCOIN_EMPTY`, `REGIME_PERCOIN_FAIL` (per-coin DB write fail), `REGIME_DIVERGE` (which coins diverge from global), `REGIME_BACKFILL` / `REGIME_BACKFILL_FAIL`, `REGIME_STATE_CLEANUP`. No `el_ms` field on the existing summary lines yet.

**Section F.** Calls `detector.detect()` (BTC-only global regime) then `detector.detect_per_coin(coins_to_check)` (per-coin batch). Both read klines via the detector's internal repo wiring. Writes one `regime_history` row + N `coin_regime_history` rows per tick.

**Section G.** `_per_coin_regimes` dict consumed by `MarketScanner` (regime bonus, scanner.py line 355: `self.regime_detector.get_coin_regime(ticker.symbol)`), by `StrategyWorker` (line 149: `coin_regimes = getattr(self.regime_detector, '_per_coin_regimes', {})`), by APEX prompts. Public accessor on `RegimeDetector`: `get_coin_regime(symbol)` → `RegimeState | None`. **The worker itself does not expose a `get_regime(coin)` accessor** — Phase 4 should expose `RegimeWorker.get_regime(coin)` as a thin wrapper around `detector.get_coin_regime`, so Phase 6's ScannerWorker has a stable worker-level API to read.

---

### 1.5 StrategyWorker

**File:** `src/workers/strategy_worker.py` (1242 LOC)
**Parent:** `BaseWorker`
**Log component:** `worker`

**Section A.** Line 140: `universe = await self.scanner.get_active_universe()`. If empty: `STRAT_UNIVERSE_EMPTY` warning + return.

**Section B.** Constructor: `interval_seconds=float(settings.strategy_engine.scan_interval_seconds)` (line 61). Default 60 s.

**Section C.** Instance state (selected highlights):
- `registry: StrategyRegistry` — 43 registered strategy classes, indexed by regime.
- `scanner: MarketScanner`, `regime_detector: RegimeDetector`, `scorer: TradeScorer`, `ensemble: EnsembleVoter`, `pnl_manager: DailyPnLManager`, `ta_engine: TAEngine` (which IS the TACache instance — comment line 266–268), `market_repo: MarketRepository`, `services: dict`.
- `_tick_times: list[float]` — rolling 10-tick history for STRAT_HEALTH aggregate.
- DB tables: reads `klines` (M5 + H1) via `market_repo.get_klines_batch`. Indirectly reads `aggregated_sentiment`, `funding_rates`, `open_interest`, `fear_greed_index`, `coin_regime_history` (via detector), `signals` (via signal sources).

**Section D.** No `_on_universe_change` handler defined on this class. Rotations are absorbed naturally because `tick()` re-fetches `universe` at line 140.

**Section E.** Critical log tags:
- `STRAT_PNL_GATE | halted=Y/N rsn=... pnl_pct=... wins=... losses=... el=Xms` (line 104) — Phase 11 follow-up enrichment.
- `STRAT_SKIP_CIRCUIT | rsn=kline_circuit_open` (line 127) — gates TA on KlineWorker fetch collapse.
- `STRAT_UNIVERSE_EMPTY` (line 142) — universe gate.
- `STRAT_REGIME_DIST | up=... down=... ranging=... volatile=... dead=... other=... total=N global=...` (line 171) — diagnoses prompt-bias question "why 95% Buys?".
- `STRAT_PREFETCH_DB_FAIL`, `STRAT_PREFETCH_DB_H1_FAIL`, `STRAT_PREFETCH_H1_ITEM_FAIL` (per prefetch fail).
- `STRAT_SKIP_STALE | sym=... kline_age=Xs max=300s` (line 244) — per-coin staleness gate.
- `STRAT_PREFETCH_SLOW | el=Xms` (line 388), `STRAT_PREFETCH_CRITICAL | el=Xms` (line 396) — surfaces prefetch latency cliffs (memory note: D-3 contributes to STRAT_PREFETCH_CRITICAL spikes).
- `STRAT_CYCLE_DONE | coins=N signals=N scored=N ... el=Xms` (line 596) — final summary.

**Section F.** `market_repo.get_klines_batch(list(universe), TimeFrame.M5.value, 200)` (line 197) and same for H1 (line 215). Each batch call hits the trading.db lock — D-3 contention site. `ta_engine.analyze(candles=...)` per coin — uses TACache (the `ta_engine` reference IS the TACache instance, manager registers them as the same object). Sentiment/altdata fetched from various services. Eventually: rule_engine.evaluate_setups + APEX optimizer (downstream pipeline).

**Section G.** Output: rule_engine setups → trade execution path. **No public accessor `get_score(coin)` exists yet** — Phase 4 must add one (likely returning a snapshot of the most recent ensemble score for a coin) for Phase 6.

---

### 1.6 AltDataWorker

**File:** `src/workers/altdata_worker.py` (188 LOC)
**Parent:** `BaseWorker`
**Log component:** `worker`

**Section A.** Line 82: `universe = await self._scanner.get_active_universe()`. Three failure modes emit `ALTDATA_UNIVERSE_EMPTY` with reasons (lines 75–94). After line 96 (`self.symbols = universe`) → consumed in `_fetch_funding_rates` and `_fetch_open_interest` per-tick.

**Section B.** Constructor: `interval_seconds=float(settings.workers.altdata_interval)` (line 44). Default 300 s. **All four sources currently fire on the same 300-s timer**: fear_greed (which only updates hourly upstream), funding (which Bybit updates every 8 h), open_interest (5-min updates), onchain (rate-limited). This is over-fetching for fear_greed and funding, under-fetching for OI by a small amount.

**Section C.**
- `fear_greed: FearGreedClient | None`, `funding: FundingRateTracker | None`, `oi_tracker: OpenInterestTracker | None`, `onchain: OnChainClient | None` — each a thin REST-API wrapper that writes to its respective DB table (`fear_greed_index`, `funding_rates`, `open_interest`, on-chain via CoinGecko writes).
- `symbols: list[str]` — refreshed each tick from scanner.

**Section D.** `_on_universe_change` at lines 149–175. On rotation: updates `self.symbols` immediately (so next tick uses correct universe without waiting for `tick()` to re-fetch), emits `ALTDATA_ADDED` / `ALTDATA_REMOVED` for observability. **Obsolete** — Phase 5 disables (the in-tick refresh at line 96 is sufficient under the corrected architecture; with no rotations affecting workers, this handler does nothing useful), Phase 7 deletes.

**Section E.** `ALTDATA_UNIVERSE_EMPTY` (3 reasons), `ALTDATA_SOURCE_FAIL | src=... err=...` (line 131 — Phase-12 structured per-source failure), `ALTDATA | fg=... funding=... oi=... el=Xms` summary (line 143), legacy `AltData worker: FG=..., funding_rates=..., OI=...` (line 144–147), `ALTDATA_ADDED`, `ALTDATA_REMOVED`. **No per-source TICK summary** — Phase 5 adds three: `ALTDATA_FUNDING_TICK`, `ALTDATA_OI_TICK`, `ALTDATA_FG_TICK`.

**Section F.** REST: Bybit funding rates endpoint (`fetch_current_rates(symbols)`), Bybit OI endpoint (`fetch_current(symbols)`), alternative.me F&G API, CoinGecko global metrics.

**Section G.** Outputs go to DB tables (`funding_rates`, `open_interest`, `fear_greed_index`). Read by StrategyWorker scoring, by APEX prompts (per-coin funding tag), by scanner's regime bonus indirectly. **No `get_funding(coin)` accessor on the worker** — Phase 5 must add one (returning the most recent funding rate from `funding_rates` table or in-memory cache).

---

### 1.7 PriceWorker

**File:** `src/workers/price_worker.py` (299 LOC)
**Parent:** `BaseWorker`
**Log component:** `worker`

**Section A.** Line 91: `universe = await self._scanner.get_active_universe()`. Three failure modes emit `PRICE_UNIVERSE_EMPTY`. After line 110 (`self._tracked_symbols = universe`) and line 121 the worker subscribes WS to that list.

**Section B.** Constructor: `interval_seconds=float(settings.workers.market_data_interval)` (line 36). Default 60 s. **PriceWorker is structurally different**: the heavy lifting is the WebSocket callback, not the tick body. The tick is a 60-s health/reconnect loop. This is why the corrected blueprint says PriceWorker stays continuous (no sweet spot needed).

**Section C.**
- `ws: BybitWebSocket` — pybit-based WebSocket client.
- `market_repo: MarketRepository` — `save_ticker` writes from callback.
- `_tracked_symbols: list[str]`.
- `_connected: bool`.
- `_dropped_count: int` — callback exception counter.
- `_ws_quotes: dict[str, tuple[float, float]]` — Phase-6 in-memory quote cache `{sym: (last_price, monotonic_ts)}` consumed by APEX.
- `_ws_msg_count: int`, `_ws_health_last_emit: float` — heartbeat state for `PRICE_WS_HEALTH`.

**Section D.** `_on_universe_change` at lines 256–292. On rotation: prunes `_ws_quotes` for departed coins (HR-1 cleanup) — emits `PRICE_UNSUB`. Sets `_connected = False` to force reconnect-with-new-list on next tick. Emits `PRICE_UNIVERSE_SYNC`. **Subtle** — pybit has no unsubscribe primitive, so a full reconnect is the only mechanism. Phase 5 disables this re-subscribe path because under the corrected arch the universe is fixed at 50 (rotations don't add/remove from PriceWorker's subscription set).

**Section E.** `PRICE_UNIVERSE_EMPTY` (3 reasons), `PriceWorker: Updating symbols ...` legacy diff, `PRICE_WS_CONN | symbols=N sample=[...]` (line 132), `Price worker: WebSocket connected, subscribed to N symbols`, `PRICE_WS_DISC | rsn=ws_not_running` (line 145), `PRICE_WS_HEALTH | status=... msgs_per_min=... msgs_in_window=... window_s=... subscribed=... quotes_cached=... | {ctx()}` (line 159 — every tick), `PRICE_UNSUB`, `PRICE_UNIVERSE_SYNC`, `Price update: {s} = {p}` DEBUG (callback line 226).

**Section F.** Bybit WebSocket public stream (`subscribe_ticker(symbols, callback)`). Sync callback `_handle_ticker_update` writes `_ws_quotes`, schedules `market_repo.save_ticker(ticker)` via `loop.create_task` (so DB write doesn't block the WS callback thread).

**Section G.** Outputs:
- `_ws_quotes` consumed by APEX assembler / Transformer for "live price" tag.
- `tickers` table written via `market_repo.save_ticker`.
- `get_ws_quote(symbol, max_age_s)` accessor (line 236) — already public, Phase 6 ScannerWorker can use it directly without adding new method.

---

## 2. ScannerWorker (Sections H + I)

### 2.1 Section H — ScannerWorker today

**Files:**
- `src/workers/scanner_worker.py` (59 LOC) — the worker shell.
- `src/strategies/scanner.py` (482 LOC) — the `MarketScanner` class with the actual scoring logic.

**ScannerWorker (the BaseWorker shell):**
- Line 27: `interval_seconds=float(settings.scanner.scan_interval_seconds)` — default 300 s, config.toml has 300.
- `tick()` (line 33) calls `self.scanner.scan_market()`, then writes results to `active_universe` table — `DELETE FROM active_universe` then `INSERT OR REPLACE` for each result.
- Logs `SCANNER | coins=N top=... score=... | {ctx()}` (line 53) plus a legacy human-readable line.

**MarketScanner.scan_market() (the heavy lifting):**
- Line 213–228: HR-2 fetch open positions ONCE per scan (used both as input filter and protected_symbols for `_update_universe`).
- Line 230: `tickers = await self.market_service.get_all_linear_tickers()` — fetches every Bybit USDT linear perp ticker (~500). Fallback to `default_symbols` only on bulk fail (line 234).
- Line 246–257: HR-1 watch_list filter — `tickers = [t for t in tickers if t.symbol in (watch_list ∪ protected_symbols)]`. Logs `SCANNER_INPUT | watch_list=50 protected=N input_set=N all_tickers=500 filtered=50ish`.
- Line 259–410: 7-component scoring per ticker:
  1. **Momentum** (0–30) — `abs(change_24h_pct)`.
  2. **Volatility** (0–25) — `daily_range_pct = (high_24h-low_24h)/price*100`.
  3. **Trend strength** (0–15) — `change_abs / daily_range_pct`.
  4. **Volume** (0–20) — `volume_24h` thresholds.
  5. **Spread** (0–10) — `(ask-bid)/bid*100` thresholds.
  6. **Regime alignment bonus** (+10/+5/0/-10) — reads `regime_detector.get_coin_regime(symbol)`.
  7. **Chop penalty** (-15) — chop detection (line 369 onward).
- **Hard disqualifiers:** vol < 5M, price < 0.0001, spread > 0.5% (lines 266–276).
- Line 442 (in `_scan_testnet`) and line 450 — `_update_universe(results)` — picks top-`max_coins` (default 30, but config sets 30), force-includes BTC/ETH (line 92–94), force-includes positions (HR-3 at lines 96–110), notifies subscribers.
- Line 445–455: `get_active_universe()` returns `list(self._active_universe)` (cached in-memory; falls back to `scan_market()` if cold).

**Force-include logic (HR-3):** Lines 209–227 (input-side fetch) and `_update_universe`'s position-protection block lines 96–125 (output-side enforcement). After the new ScannerWorker is built, this exact mechanism must be preserved.

**Subscribe mechanism:** `MarketScanner.subscribe(callback)` (line 61) appends to `self._subscribers: list`. After `_update_universe` (line 162 area), the scanner notifies subscribers with `(symbols, added, removed)` triplets so workers can run their `_on_universe_change` handlers.

### 2.2 Section I — ScannerWorker tomorrow (target shape)

The new ScannerWorker (per blueprint §9 and prompt §"PHASE 6"):
- **Sweet spot: 4:00** within the 5-min window (after every other worker has finished its sweet spot).
- **Input pool: the 50 from `config.universe.watch_list`** (no Bybit REST scoring path; no `get_all_linear_tickers()` call in the scoring critical path).
- **Per-coin opportunity score** = weighted sum of:
  - `structure_worker.get_setup_score(coin)` (added in Phase 3) — normalize 0–100.
  - `strategy_worker.get_score(coin)` (added in Phase 4) — normalize 0–1.
  - `signal_worker.get_signal(coin).confidence` (added in Phase 4) — 0–1.
  - `regime_worker.get_regime(coin)` → alignment factor (e.g. trending=+1, dead=-1, ranging=0).
  - `altdata_worker.get_funding(coin)` → signal strength (e.g. abs(funding_rate)>0.01% counts).
- **Weights:** `[scanner.scoring_weights]` new section in config.toml.
- **Output:** top 30 by score (force-include open positions per HR-3) → `_update_universe(results)` → DELETE+INSERT `active_universe` table → notify subscribers (Phase 7 will trim the worker subscribers).
- **Logging:** `SCANNER_TICK_SUMMARY | watch_list=50 protected=N scored=50 selected=30 mean_score=M drift_ms=D el=Xms | {ctx()}` and per-coin DEBUG `SCANNER_SELECTED | rank=R coin=C score=S src=structure:A,strategy:B,signal:C,regime:D,funding:E`.
- **Stays:** the `_active_universe` list and `get_active_universe()` getter. Stage 2 keeps reading via that getter (`strategist.py:592`, `:1250`).

**Migration impact on `MarketScanner.scan_market`:** the body becomes the new composite-score path or the function is deprecated and ScannerWorker bypasses it. `_update_universe` is reused as-is.

---

## 3. Stage 2 / Cycle (Section J)

**File:** `src/brain/strategist.py` (2393 LOC).

**Two readers of `active_universe` in cycle code:**

1. `_build_context_prompt` line 592: `universe = await scanner.get_active_universe() if scanner else []`. Used to build the legacy / Call A+B combined prompt.

2. `_build_trade_prompt` line 1250: `universe = await scanner.get_active_universe() if scanner else []`. Then:
   - Line 1252: filtered to testnet-supported when `bybit.testnet=True`.
   - Line 1254–1259: emits the `TRADEABLE COINS THIS CYCLE` section into the prompt.
   - Line 1298–1370: per-coin loop fetches ticker (from `market_service.get_all_linear_tickers()` bulk + per-symbol fallback), TA (from TACache), open-position tag, regime tag.

**Both reads stay correct under the corrected architecture** — they're cycle-side. Stage 2 reads the 30 coins ScannerWorker selected. The data behind those 30 is fresh because Layer 1A workers maintain warm caches for ALL 50.

**No assumption "active_universe = working set":** the strategist treats `universe` as "the list of coins to mention to Claude in this cycle." It does not iterate `universe` for any backfill or rotation purpose. It's pure read.

**Other consumers of `_active_universe` outside strategist:** none in cycle code (`MCP tools`, `telegram handlers`, `factory`, `fund_manager` — all clean per cross-cutting registry §4).

---

## 4. Cross-Cutting Registries

### 4.1 Every reader of `active_universe` / `get_active_universe()`

Output of `rg -n 'get_active_universe|active_universe' src/`:

| File | Line | Code | Class |
|---|---|---|---|
| `src/strategies/scanner.py` | 51 | `self._active_universe: list[str] = []` | Storage |
| `src/strategies/scanner.py` | 113 | `protected_symbols = set(self._active_universe)` | Worker (scanner internal protection) |
| `src/strategies/scanner.py` | 140 | `old_set = set(self._active_universe)` | Worker (scanner internal diff) |
| `src/strategies/scanner.py` | 160 | `self._active_universe = new_symbols` | Worker (scanner internal write) |
| `src/strategies/scanner.py` | 177–178 | `self._active_universe = new_symbols` | Worker (scanner internal write, fallback branch) |
| `src/strategies/scanner.py` | 227 | `protected_symbols = set(self._active_universe)` | Worker (scanner internal protection) |
| `src/strategies/scanner.py` | 445 | `async def get_active_universe()` | API |
| `src/database/migrations.py` | 377 | `CREATE TABLE IF NOT EXISTS active_universe` | Schema |
| `src/config/settings.py` | 790 | `# active_universe (~30 coins) directly` | Comment |
| `src/brain/strategist.py` | 592 | `await scanner.get_active_universe() if scanner else []` | **Cycle** |
| `src/brain/strategist.py` | 1250 | `await scanner.get_active_universe() if scanner else []` | **Cycle** |
| `src/workers/altdata_worker.py` | 82 | `universe = await self._scanner.get_active_universe()` | **Worker → migrate** |
| `src/workers/manager.py` | 179 | comment "ScannerWorker.get_active_universe() exclusively" | Doc |
| `src/workers/manager.py` | 532 | `universe = await scanner.get_active_universe()` | **Init-time** (initial scan log line). Not worker-side, not cycle-side — a one-shot startup log. Keep as-is or relabel; harmless. |
| `src/workers/manager.py` | 933 | comment "scanner.get_active_universe() exclusively. CoinDiscovery was removed in Phase 6" | Doc |
| `src/workers/kline_worker.py` | 127 | `universe = await self._scanner.get_active_universe()` | **Worker → migrate** |
| `src/workers/kline_worker.py` | 353 | comment `len(active_universe) * len(TIMEFRAME_SCHEDULE)` | Doc |
| `src/workers/scanner_worker.py` | 14, 38–42 | Writes `active_universe` table | **Worker (scanner itself)** |
| `src/workers/price_worker.py` | 79 | comment "this tick's `get_active_universe()` call" | Doc |
| `src/workers/price_worker.py` | 91 | `universe = await self._scanner.get_active_universe()` | **Worker → migrate** |
| `src/workers/price_worker.py` | 262 | comment "bounded by `len(active_universe)`" | Doc |
| `src/workers/regime_worker.py` | 57 | `universe = await self._scanner.get_active_universe() or []` | **Worker → migrate** |
| `src/workers/strategy_worker.py` | 140 | `universe = await self.scanner.get_active_universe()` | **Worker → migrate** |
| `src/workers/structure_worker.py` | 4, 29, 69, 176 | comments / docstrings | Doc |
| `src/workers/structure_worker.py` | 193 | `universe = await self._scanner.get_active_universe()` | **Worker → migrate** |
| `src/workers/signal_worker.py` | 73 | `symbols = await self._scanner.get_active_universe()` | **Worker → migrate** |

**Summary:**
- 7 worker-side reads (kline, structure, signal, regime, strategy, altdata, price) — all migrate to `config.universe.watch_list` in Phases 2–5.
- 2 cycle-side reads (strategist.py:592, :1250) — STAY as-is.
- 1 init-time read (manager.py:532) — keep as a startup log; benign under either architecture.
- All other hits are storage, schema, comments, doc, or scanner internals.
- **Zero MCP, telegram, factory, fund_manager, APEX, gate, execute reads.**

### 4.2 Every `_on_universe_change` handler (the obsolete rotation fan-out)

Output of `rg -n '_on_universe_change|register_universe_callback|universe_change|KLINE_BACKFILL|SIGNAL_BACKFILL|STATE_CLEANUP' src/`:

| File | Line | Role |
|---|---|---|
| `src/workers/manager.py` | 912 | Master callback dispatcher (`_on_universe_change` defined inside `_create_workers` closure) |
| `src/workers/manager.py` | 914 | `if hasattr(w, '_on_universe_change'):` — the broadcast loop |
| `src/workers/manager.py` | 916 | `await w._on_universe_change(symbols, added, removed)` |
| `src/workers/manager.py` | 923 | `scanner.subscribe(_on_universe_change)` — registers the master with the scanner |
| `src/workers/kline_worker.py` | 339 | KlineWorker handler (KLINE_BACKFILL + KLINE_STATE_CLEANUP) |
| `src/workers/kline_worker.py` | 366 | `KLINE_STATE_CLEANUP` log |
| `src/workers/kline_worker.py` | 377 | `KLINE_BACKFILL` log |
| `src/workers/kline_worker.py` | 379 | `KLINE_BACKFILL_FAIL` log |
| `src/workers/signal_worker.py` | 150 | SignalWorker handler (SIGNAL_BACKFILL + SIGNAL_REMOVED) |
| `src/workers/signal_worker.py` | 176 | `SIGNAL_BACKFILL` log |
| `src/workers/signal_worker.py` | 178 | `SIGNAL_BACKFILL_FAIL` log |
| `src/workers/regime_worker.py` | 247 | RegimeWorker handler (REGIME_BACKFILL + REGIME_STATE_CLEANUP) |
| `src/workers/regime_worker.py` | 298 | `REGIME_STATE_CLEANUP` log |
| `src/workers/altdata_worker.py` | 149 | AltDataWorker handler (ALTDATA_ADDED + ALTDATA_REMOVED) |
| `src/workers/price_worker.py` | 256 | PriceWorker handler (PRICE_UNSUB + force reconnect) |
| `src/strategies/scanner.py` | 61–63, 162? | `_subscribers` list + notify pattern |

**Verdict:** 5 of the 7 data workers (kline, signal, regime, altdata, price) have handlers; structure_worker and strategy_worker do not (they refresh the universe inside `tick()` instead). All 5 handlers + the master dispatcher + the scanner subscriber-fire become obsolete under the corrected architecture. Phase 2–5 each disable their worker's handler (deprecation no-op log + body short-circuit). Phase 7 deletes them.

### 4.3 Every `watch_list` reader

Output of `rg -n 'watch_list' src/ tests/`:

| File | Line(s) | Role |
|---|---|---|
| `src/config/settings.py` | 299, 309–337, 1338–1339 | UniverseSettings dataclass + validation + builder |
| `src/strategies/scanner.py` | 1, 4, 22, 28, 30, 38, 45, 54, 55, 57, 58, 211, 242, 244, 246, 247, 250, 252 | MarketScanner constructor + filter logic |
| `src/workers/manager.py` | 885, 887, 889, 894 | Reads `s.universe.watch_list` and passes to MarketScanner |
| `src/workers/structure_worker.py` | 176, 183 | Comments only |
| `tests/test_scanner_filter.py` | 1, 63 | Existing test for scanner watch_list filter |

**Verdict:** Today, **only the MarketScanner reads `watch_list`** (and via injection from manager.py). No worker reads it directly. The migration adds 7 new readers (the 7 data workers, each replacing its `scanner.get_active_universe()` call with `self.settings.universe.watch_list`).

### 4.4 Every config value the 7 workers read

| Worker | config key (today) | Default | config.toml value | Future (post-migration) |
|---|---|---|---|---|
| KlineWorker | `settings.workers.market_data_interval` | 60 | 45 | `settings.workers.sweet_spots.kline_worker = "0:30"` |
| structure_worker | `settings.structure.worker_interval_seconds` | 60 | 60 | `settings.workers.sweet_spots.structure_worker = "0:45"` (also keeps `settings.structure.batch_size`, `cache_ttl_seconds`, `min_candles`, etc.) |
| SignalWorker | `settings.workers.health_check_interval` | 60 | 120 | `settings.workers.sweet_spots.signal_worker = "1:00"` |
| RegimeWorker | `settings.regime.detection_interval_seconds` | 300 | 300 | `settings.workers.sweet_spots.regime_worker = "1:15"` |
| StrategyWorker | `settings.strategy_engine.scan_interval_seconds` | 60 | 60 | `settings.workers.sweet_spots.strategy_worker = "1:30"` |
| AltDataWorker | `settings.workers.altdata_interval` | 300 | 300 | Three sub-cadences in `settings.workers.sweet_spots.altdata.*` |
| PriceWorker | `settings.workers.market_data_interval` | 60 | 45 | UNCHANGED — PriceWorker stays continuous |
| ScannerWorker | `settings.scanner.scan_interval_seconds` | 300 | 300 | `settings.workers.sweet_spots.scanner_worker = "4:00"` |

All 7 + ScannerWorker also read `settings.universe.watch_list` (post-migration) and `settings.scanner.max_coins` (only ScannerWorker — for the 30-pick).

### 4.5 Memory footprint estimate (30 → 50)

Per-coin steady-state caches (each entry ~):

| Cache | Per-entry approx | At 30 coins | At 50 coins | Δ |
|---|---|---|---|---|
| TACache (`_cache: OrderedDict`, key=`sym:tf`) | ~12 KB (RSI/MACD/ADX/etc. dict) | 30×2tf×12KB = 720 KB | 50×2tf×12KB = 1.2 MB | +480 KB |
| StructureCache | ~25 KB (StructuralAnalysis: phase 1–10 outputs) | 750 KB | 1.25 MB | +500 KB |
| RegimeDetector `_per_coin_regimes` | ~1 KB (RegimeState) | 30 KB | 50 KB | +20 KB |
| PriceWorker `_ws_quotes` | ~64 B (`(float, float)` tuple) | 2 KB | 3 KB | +1 KB |
| KlineWorker `_last_fetch` | ~80 B per (sym, tf) key | 30×4×80B ≈ 10 KB | 50×4×80B ≈ 16 KB | +6 KB |
| StructureWorker `_full_universe` | 32 B per str | ~1 KB | ~1.6 KB | +0.6 KB |

**Total in-memory cache delta: ~1 MB.** The dominant memory cost is actually downstream — strategist's prompt includes per-coin sections, and the 67% larger universe means longer prompts.

Per-coin DB row writes (per tick where applicable):
- klines: 50 × 4 timeframes × ~200 candles initial backfill, then ~5–20 incremental rows per tick. Roughly 3,000–6,000 rows/tick at sweep peaks under sweet-spot cadence. Memory negligible (rows are committed and freed); the cost is D-3 lock-hold time during `executemany`.
- coin_regime_history: +50 rows per regime tick (was +30). Negligible.
- funding_rates / open_interest: +50 rows (was +30). Negligible.

**The real risk is not RAM — it's DB lock contention (D-3) and Bybit rate.**

### 4.6 Bybit API call rate estimate

Today (fixed-interval, 30-coin universe):
- KlineWorker: 30 syms × ~1.5 timeframes-per-tick (M5+H1 always, H4 every 5×, D1 every 60×) × every 45 s ≈ **60 calls/min**.
- ScannerWorker: `get_all_linear_tickers()` once per 5 min = **1 bulk call ~ 0.2/min**.
- AltDataWorker: 1 funding bulk + 1 OI bulk + 1 F&G + 1 onchain per 5 min = **0.8 calls/min**.
- PriceWorker: WS only, no REST.
- StructureWorker: per-symbol klines via `market_repo` (DB-only, no Bybit). MarketRepo's API: zero calls.
- StrategyWorker: zero direct Bybit calls (all data via repo/cache).
- **Total: ~61 calls/min** at fixed cadence.

Future (sweet-spot, 50-coin universe):
- KlineWorker: 50 syms × ~1.5 tf × every 5 min = **15 calls/min** (huge reduction — 4× drop).
- ScannerWorker: zero Bybit calls in scoring (reads caches only).
- AltDataWorker: same per-source cadences — funding rate fetch every 5 min, OI every 5 min, F&G every 60 min — **~0.6 calls/min**.
- **Total: ~16 calls/min** — 4× under today.

Bybit's spot+linear public API limit is 600/sec window. We're nowhere near it either way; sweet-spot scheduling actually saves API budget.

---

## 5. Verification Gate (5 Concrete Questions From The Prompt)

### Q1: How many places in the codebase read `scanner.get_active_universe()`?

**Answer:** 9 active call sites, broken down as:
- **7 worker-side reads** (must migrate to `watch_list`): kline_worker.py:127, structure_worker.py:193, signal_worker.py:73, regime_worker.py:57, strategy_worker.py:140, altdata_worker.py:82, price_worker.py:91.
- **2 cycle-side reads** (stay): strategist.py:592, strategist.py:1250.
- **1 init-time read** (benign one-shot startup log): manager.py:532. Re-classify or keep as-is — leave as-is.
- Plus `MarketScanner` internal `self._active_universe` access at scanner.py:51, 113, 140, 160, 177, 178, 227, 451 (the storage and writers).

### Q2: How does the master callback dispatcher fire universe-rotation events?

**Answer:** Defined as a closure inside `WorkerManager._create_workers` at `manager.py:912–923`. Pseudocode:
```python
async def _on_universe_change(symbols, added, removed):
    for w in self.workers:
        if hasattr(w, '_on_universe_change'):
            try:
                await w._on_universe_change(symbols, added, removed)
            except Exception as e:
                log.warning("Universe change handler failed for {n}: {e}", ...)

scanner.subscribe(_on_universe_change)  # appends to MarketScanner._subscribers list
```
The scanner fires the subscriber callback (with `await callback(symbols, added, removed)`) at the end of `_update_universe` (around scanner.py:160 area; the actual fire-site uses `await asyncio.gather(*[cb(...) for cb in self._subscribers])`).

**Phase 7 cleanup target:** the entire `_on_universe_change` closure + the `scanner.subscribe(...)` call. The master dispatcher dies; the scanner's `_subscribers` list itself can stay if anything else uses it (verify in Phase 7 — current grep shows nothing else does).

### Q3: Are there any workers whose internal logic ASSUMES a fixed universe size?

**Answer:** No. All 7 workers iterate `universe` dynamically. Notable size-aware code:
- StructureWorker batches at `batch_size=25` with wrap-around — handles arbitrary universe sizes correctly. Going from 30 to 50 changes "1 batch + 5-coin remainder" to "2 batches + 0 remainder", which the modulo math at lines 213–218 handles. Verified.
- KlineWorker `_last_fetch` dict size is bounded by `len(universe) × len(TIMEFRAME_SCHEDULE)` — comment says "≈ 30 × 4 = 120" but the value is recomputed dynamically; no fixed-size array.
- TACache `_maxsize=200` (line 58) — at 50 coins × 2 timeframes = 100 entries, well under the 200 LRU bound.
- StrategyWorker prefetch uses `get_klines_batch(list(universe), ...)` — handles any size.
- PriceWorker `subscribe_ticker` uses pybit's bulk subscribe — Bybit allows up to 200 symbols per WS connection, so 50 is fine.

**No fixed-size assumption discovered.** The only "size sensitivity" is performance scaling, captured in §4.5.

### Q4: What's the current memory footprint per worker per coin?

**Answer:** See §4.5 table. Aggregate per-coin steady-state in-memory cost ~40 KB/coin. Going from 30 to 50 coins adds ~800 KB to ~1 MB to RSS in caches. The dominant memory pressure is downstream (strategist prompt size scales with universe).

Process-level note: the prior overhaul memory `feedback_overhaul29_execution.md` flags D-4 ("memory headroom routinely tight (>600 MB peaks)") as deferred and operational. Going to 50 coins will add modest pressure; mitigation in Phase 9 if needed: raise systemd MemoryHigh.

### Q5: What's the current Bybit API call rate?

**Answer:** ~61 calls/min today (KlineWorker dominates at ~60/min via 45-s × 30 syms × ~1.5 tf-per-tick). Post-migration: ~16 calls/min (sweet-spot scheduling once per 5-min window for 50 syms × ~1.5 tf). Net reduction: 4×.

The corrected architecture spends LESS on the Bybit API even with 67% more coins, because today's 45-s polling causes 8× redundant fetches per actual M5 candle close (each M5 candle finalizes once per 5 min, but KlineWorker queries for it every 45 s — 6.7 redundant queries per real change).

---

## 6. Summary Tables

### 6.1 Worker → migration impact per phase

| Worker | File | LOC | Phase | Universe migration | Sweet-spot | New accessor for Phase 6 |
|---|---|---|---|---|---|---|
| KlineWorker | kline_worker.py | 380 | 2 | yes (line 127) | 0:30 | (none — already exposes `is_circuit_open()`) |
| structure_worker | structure_worker.py | 240 | 3 | yes (line 193) | 0:45 | `get_setup_score(coin) -> float | None` |
| SignalWorker | signal_worker.py | 179 | 4 | yes (line 73) | 1:00 | `get_signal(coin) -> SignalResult | None` |
| RegimeWorker | regime_worker.py | 302 | 4 | yes (line 57) | 1:15 | `get_regime(coin) -> RegimeState | None` |
| StrategyWorker | strategy_worker.py | 1242 | 4 | yes (line 140) | 1:30 | `get_score(coin) -> float | None` |
| AltDataWorker | altdata_worker.py | 188 | 5 | yes (line 82) | 1:45 (funding) + per-source | `get_funding(coin) -> float | None` |
| PriceWorker | price_worker.py | 299 | 5 | yes (line 91) | continuous (no sweet spot) | `get_ws_quote(coin)` already exists |
| ScannerWorker | scanner_worker.py + scanner.py | 59 + 482 | 6 | input → watch_list, scoring → composite | 4:00 | (consumer of all the above) |

### 6.2 Existing observability lines vs target (per worker)

| Worker | Existing summary line | Target line per spec |
|---|---|---|
| KlineWorker | `Kline worker: fetched N klines for S symbols` (line 334) | `KLINE_TICK_SUMMARY | universe=50 fetched=N saved=M skipped=K tf_split={M5:a,H1:b,H4:c,D1:d} el=Xms drift_ms=D | {ctx()}` |
| structure_worker | `XRAY_TICK | batch=k/n symbols=s analyzed=a errors=e cached=c session=... setups=st skips=sk el=Xms` (line 165) | `XRAY_TICK_SUMMARY | universe=50 batch=k/n symbols=s analyzed=a errors=e cached=c setups=st skips=sk el=Xms drift_ms=D | {ctx()}` |
| SignalWorker | `SIG_BATCH | n=N coins=N strongest=... type=... conf=... el=Xms` (line 128) + `SIG_BATCH_STATS | conf_min/max/mean/std` | Add `SIG_TICK_SUMMARY | universe=50 signals=N mean_conf=M el=Xms drift_ms=D | {ctx()}` (the existing SIG_BATCH stays) |
| RegimeWorker | `REGIME_GLOBAL`, `REGIME_PERCOIN`, `REGIME_DIVERGE` | Same lines + add `el=Xms drift_ms=D` |
| StrategyWorker | `STRAT_CYCLE_DONE | coins=N signals=N scored=N ... el=Xms` (line 596) | Same + add `STRAT_PREFETCH | el=Xms src=...` and `drift_ms=D` |
| AltDataWorker | `ALTDATA | fg=... funding=... oi=... el=Xms` (line 143) | Replace with `ALTDATA_FUNDING_TICK | universe=50 fetched=N el=Xms`, `ALTDATA_OI_TICK | ...`, `ALTDATA_FG_TICK | value=V el=Xms` |
| PriceWorker | `PRICE_WS_HEALTH | ...` (line 159 — already excellent) | Keep; bump `subscribed=50` post-migration |
| ScannerWorker | `SCANNER | coins=N top=... score=...` (line 53) | Replace with `SCANNER_TICK_SUMMARY | watch_list=50 protected=P scored=50 selected=30 mean_score=M drift_ms=D el=Xms | {ctx()}` and per-coin DEBUG `SCANNER_SELECTED | rank=R coin=C score=S src=structure:A,strategy:B,signal:C,regime:D,funding:E` |

---

## 7. Risks Discovered During Investigation

### 7.1 D-3 lock contention is a real but bounded risk

The kline_worker holds `DatabaseManager._lock` on trading.db during `executemany` saves for 5–30 s under load (see memory `project_shadowklinereader_fix.md`). Sweet-spot scheduling at :30 reduces firing frequency from every 45 s to every 5 min — a 6.7× reduction in lock-acquisition events. This won't eliminate D-3 but materially reduces its blast radius.

**Action:** Document in Phase 2 report. Don't fix in this engagement.

### 7.2 The `manager.py:532` initial-scan read

This single line happens once during startup (not per worker tick). It populates `_active_universe` so workers don't see an empty universe on their first tick. Under the corrected architecture, workers don't depend on `_active_universe` anymore — they read `watch_list` directly. So this initial scan still runs (because Stage 2 still needs an `_active_universe` pre-cycle), but it ceases to gate the workers.

**Action:** Leave as-is. No code change needed; the line is benign under both architectures.

### 7.3 The `MarketScanner._subscribers` list

Today only the master `_on_universe_change` callback is registered (manager.py:923). Phase 7 removes that registration. After Phase 7 the `_subscribers` list will be empty and the scanner's `_update_universe` will fire callbacks to nothing — equivalent to a no-op. The list infrastructure can stay (lightweight) or be removed in a later cleanup.

**Action:** Phase 7 just removes the registration. Don't touch the `_subscribers` list infrastructure itself.

### 7.4 PriceWorker sweet-spot decision

PriceWorker's "tick" is a 60-s health check, not a data fetch. The actual data lives in the WebSocket callback. Putting it on a 5-min sweet spot would mean health checks every 5 min (slower failover detection) — net regression.

**Decision:** PriceWorker stays on `BaseWorker` with its current 45-s interval (or per-config), NOT on `SweetSpotWorker`. The blueprint explicitly notes "PriceWorker: continuous (not in chain)".

**Action:** Phase 5 changes only the `_tracked_symbols` source (watch_list) and the `_on_universe_change` body (no-op + deprecation log). The interval and scheduling stay.

### 7.5 AltDataWorker has 3 distinct cadences

The blueprint sec. 8.2 gives AltDataWorker three sub-schedules (funding 1:45, OI every 5 min, F&G every 60 min). The current implementation runs all three on the single 300-s `altdata_interval` timer. Phase 5 needs to internally track 3 next-fire times.

**Action:** Phase 5 implements a small `_next_funding`, `_next_oi`, `_next_fg` dict in AltDataWorker. The worker itself still extends `SweetSpotWorker` for the funding cadence; OI and F&G are decided inline within `tick()` based on `time.monotonic()` deadlines.

### 7.6 ShadowKlineReader is async-aiosqlite (per the 2026-04-25/26 fix)

structure_worker's klines fallback path goes through `ShadowKlineReader.get_klines(sym, "60", 200)` — the lifecycle is now manager-owned, the connection is persistent, and it's on a separate aiosqlite DB. **This worker fix is NOT impacted by the migration.** Phase 3's verification can also confirm the reader stats stay healthy.

### 7.7 Workers without `_on_universe_change` (StructureWorker, StrategyWorker)

These two refresh the universe inside `tick()` instead of via the callback. Under the corrected architecture they won't even need the in-tick refresh — they read `watch_list` directly. No handler-disable / handler-delete work needed for them in Phase 7.

---

## 8. Phase 0 Conclusion

The investigation answers all five Verification Gate questions with concrete file:line citations. The current Layer 1 implementation is well-documented in code comments (legacy of prior phases) and the migration touchpoints are all enumerable: 7 worker-side `get_active_universe` calls to convert, 5 `_on_universe_change` handlers + 1 master dispatcher to disable then delete, 4 new public accessors to add (`get_setup_score`, `get_signal`, `get_regime`, `get_funding`, plus the existing `get_score` / `get_ws_quote`), 1 new config section (`[workers.sweet_spots]`), 1 new helper module (`sweet_spot_scheduler.py`), 1 new BaseWorker subclass (`SweetSpotWorker`), and 1 ScannerWorker scoring rewrite.

No surprises that block the plan. Risks are catalogued in §7. Phase 1 begins next.

---

## 9. References

- `/home/inshadaliqbal786/LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md` — design authority
- `/home/inshadaliqbal786/IMPLEMENT_LAYER1_CORRECTED_MIGRATION_PROFESSIONAL.md` — execution prompt
- `/home/inshadaliqbal786/.claude/plans/plan-mode-today-zippy-music.md` — execution plan for this engagement
- Prior memory: `project_architecture.md`, `project_xray_status.md`, `feedback_overhaul29_execution.md`, `project_shadowklinereader_fix.md`
