# Phase 0 — Seven Workers Universe Integration: Investigation

**Date:** 2026-04-26
**Engagement:** `IMPLEMENT_SEVEN_WORKERS_UNIVERSE_INTEGRATION_PROFESSIONAL.md`
**Method:** End-to-end read of every worker file plus the canonical universe source (`MarketScanner.get_active_universe()`) and the master callback dispatcher (`manager.py:912-923`). All file:line citations verified by direct read.

---

## Baseline: Layer 1 State (already live)

Verified end-to-end on 2026-04-26 02:30 UTC (`dev_notes/layer1_pipeline_verification.md`). Summary:

- `[universe].watch_list` = 50 operator-curated symbols, validated by `UniverseSettings` (size ≥ 10, regex `^[A-Z0-9]+USDT$`, no duplicates).
- `MarketScanner.scan_market()` filters scoring input to `watch_list ∪ open_positions` (Phase 2 of Layer 1) and produces a 30-coin top-N list, BTC/ETH force-prepended → `_active_universe` ≈ 32 symbols.
- `MarketScanner.subscribe(callback)` exposes a publish/subscribe channel; `_update_universe()` computes `(added, removed)` sets and notifies subscribers when the membership changes.
- `manager.py:912-923` registers a single master callback that loops over `self.workers` and invokes `_on_universe_change(symbols, added, removed)` on any worker that defines it.
- `structure_worker._get_universe()` (lines 163-209) is the gold-standard pattern: three reason codes (`no_scanner_injected`, `scanner_error`, `scanner_returned_empty`), each emits `XRAY_UNIVERSE_EMPTY` and returns `[]`. No `default_symbols` fallback.

Live runtime evidence (workers process PID 51772, 02:05:21 → 03:02:50 UTC, 57 min uptime before stop): 32 active coins, 97 structure_worker ticks, 0 errors, 0 `XRAY_UNIVERSE_EMPTY`.

---

## Per-Worker Findings — Sections A–G

For each of the seven workers, the table below reports:

- A: Universe source (line, cadence, caching, fallback)
- B: Per-coin operation (kind, expected runtime characteristics)
- C: State maintained (instance variables, caches, DB tables, TTL/eviction)
- D: Cleanup on rotation-out (explicit removal? state accumulation?)
- E: Bootstrap on rotation-in (warm-up assumptions, first-cycle behavior)
- F: Empty universe (traced code path — crash? skip? default? log?)
- G: Logging (tag list, with focus on universe-relevant tags)

All citations are read directly from the on-disk file.

---

### 1. KlineWorker — `src/workers/kline_worker.py` (~247 LOC)

**A — Universe source (lines 97-105):**

```python
if self._scanner:
    try:
        universe = await self._scanner.get_active_universe()
        if universe:
            self._tracked_symbols = universe
    except Exception:
        pass
```

- Called once per tick.
- Result cached in `self._tracked_symbols` (line 55, init to `settings.bybit.default_symbols`).
- **Fallback path (HR-3 violation):** on empty universe (`if universe:` is False), `self._tracked_symbols` is silently retained. On exception, `pass` swallows the error and processes the previous list.

**B — Per-coin operation (lines 111-137):**

For each symbol in `self._tracked_symbols`, iterate `TIMEFRAME_SCHEDULE` (M5, H1 every 60s; H4 every 300s; D1 every 3600s). Per scheduled (symbol, tf): `await self.market_service.get_klines(symbol, timeframe, limit=200)` then `await asyncio.sleep(0.1)`. API-bound. With 30 coins × 4 timeframes (most ticks: M5+H1 only ≈ 30 × 2 = 60 fetches, ~12s base + sleep overhead).

**C — State maintained:**

- `self._tracked_symbols: list[str]` (line 55) — current universe snapshot.
- `self._last_fetch: dict[str, float]` (line 56) — keyed by `f"{symbol}:{timeframe.value}"`. **No TTL, no eviction.**
- `self._last_tick_per_symbol: dict[str, int]` (line 61) — overwritten each tick (line 140).
- `self._circuit_breaker_until: float` (line 66) — global monotonic deadline (intentionally global; correct for global API outages).
- DB writes: `klines` table via `market_service` (downstream).
- DB reads: `KLINE_WRITE_LAG` diagnostic reads `klines` table (lines 190-225).

**D — Cleanup on rotation-out:**

- `_on_universe_change()` at line 234: updates `self._tracked_symbols = list(symbols)` and **backfills `added` only**. The `removed` set is unused.
- `self._last_fetch[f"{old_sym}:{tf}"]` entries persist forever for departed coins. **HR-1 violation.**
- `self._last_tick_per_symbol[old_sym]` is cleared each tick by `dict(per_symbol_fetched)` (line 140) — overwrite-based, no leak.

**E — Bootstrap on rotation-in:**

- `_on_universe_change()` lines 239-246: for each `sym in added`, fetches all 4 timeframes (200 candles each) immediately. Logs `KLINE_BACKFILL`.
- No warm-up assumption violations.

**F — Empty universe trace:**

- `if self._scanner:` (line 98) — true once scanner is wired.
- `await self._scanner.get_active_universe()` returns `[]` during scanner startup or transient failure.
- `if universe:` (line 101) — False on empty list, so `self._tracked_symbols` keeps its prior value.
- Tick proceeds with the stale list. No log warning. **HR-3 violation.**
- During the very first tick (before scanner is wired), `self._tracked_symbols = settings.bybit.default_symbols` (5 coins). Worker fetches for those 5. **HR-3 indirect violation (default-list use).**

**G — Logging:**

- `KLINE_FETCH | klines=N expected=N symbols=N quality={ok|short_10pct|short_50pct|zero_fetch}` (line 150-152)
- `KLINE_GAP | sym=X expected=Y got=Z stale_since=Zs` (line 165-168)
- `KLINE_CIRCUIT_BREAKER | open_until=+30s reason=...` (line 173-175)
- `KLINE_WRITE_LAG | stale_count=N threshold_s=180 top5=[...]` (line 220-223)
- `KLINE_BACKFILL | sym=X tfs=N` (line 244)
- `KLINE_BACKFILL_FAIL | sym=X err=...` (line 246)
- **Missing:** `KLINE_UNIVERSE_EMPTY`, `KLINE_STATE_CLEANUP`.

**Verdict:** Fix needed. HR-1, HR-2 (partial), HR-3 violations.

---

### 2. SignalWorker — `src/workers/signal_worker.py` (~134 LOC)

**A — Universe source (lines 53-60):**

```python
if self._scanner:
    try:
        symbols = await self._scanner.get_active_universe()
    except Exception:
        symbols = self.settings.bybit.default_symbols
else:
    symbols = self.settings.bybit.default_symbols
```

- Called once per tick.
- **Two band-aid fallbacks (HR-3 violation):** on exception, on `_scanner is None`, falls back to `default_symbols`.
- The brief explicitly forbids: *"if universe is empty, use default_symbols — this defeats the empty-universe rule"*.

**B — Per-coin operation (lines 71-101):**

For each symbol: `await self.aggregator.aggregate_for_symbol(symbol)` then `await self.signal_generator.generate_signal(symbol)`. Compute-bound (synthesis over cached sentiment/news data). Light per-coin work; total tick budgeted by `health_check_interval`.

**C — State maintained:**

- `self._scanner = None` (line 49) — late-wired by manager.
- `self.aggregator`, `self.signal_generator` — service refs.
- No per-coin instance state; no caches; no warmup.
- DB writes: `signals` table (via `signal_generator.generate_signal`).

**D — Cleanup on rotation-out:**

- `_on_universe_change()` at line 124: handles `added` only (line 128-133), backfills signals.
- `removed` set is unused. No in-memory state to prune; observability gap only.

**E — Bootstrap on rotation-in:**

- `_on_universe_change()` line 130: `await self.signal_generator.generate_signal(sym)` immediately. No warm-up.

**F — Empty universe trace:**

- If scanner returns `[]`: passes through to `for symbol in symbols:` (line 71) which is a no-op. `signals_generated=0`. Logs `SIG_BATCH | n=0 coins=0`.
- No crash, but uses `default_symbols` on exception/None scanner — HR-3 violation per the brief's literal language.

**G — Logging:**

- `SIG_BATCH | n=N coins=N strongest=X type=Y conf=Z` (line 102)
- `SIG_BATCH_STATS | n=N conf_min=X conf_max=Y conf_mean=Z conf_std=W` (line 118-122)
- `SIGNAL_BACKFILL | sym=X` (line 131)
- `SIGNAL_BACKFILL_FAIL | sym=X err=...` (line 133)
- **Missing:** `SIGNAL_UNIVERSE_EMPTY`, `SIGNAL_REMOVED`.

**Verdict:** Fix needed. HR-3 violation (default_symbols fallback). HR-2 partial (no observability for removed).

---

### 3. AltDataWorker — `src/workers/altdata_worker.py` (~117 LOC)

**A — Universe source (lines 56-63):**

```python
if self._scanner:
    try:
        universe = await self._scanner.get_active_universe()
        if universe:
            self.symbols = universe
    except Exception:
        pass
```

- Called once per tick.
- **HR-1 init violation:** `self.symbols = settings.bybit.default_symbols` (line 51).
- **HR-3 silent retention:** on empty/exception, keeps prior `self.symbols`.

**B — Per-coin operation (lines 65-85):**

`asyncio.gather()` over four async tasks: `_fetch_fear_greed`, `_fetch_funding_rates(self.symbols)`, `_fetch_open_interest(self.symbols)`, `_fetch_onchain` (global metric). API-bound to Bybit (funding, OI), CoinGecko (10/min rate-limited), Fear & Greed.

**C — State maintained:**

- `self.symbols` (line 51) — universe snapshot.
- `self._scanner = None` (line 52) — late-wired.
- No per-coin instance state, no caches.
- DB writes via clients: `funding_rates`, `open_interest_history`, `fear_greed_history` tables.

**D — Cleanup on rotation-out:**

- **No `_on_universe_change()` method exists.** HR-2 violation (no clean lifecycle observability; in-memory `self.symbols` updates only on next tick if non-empty).

**E — Bootstrap on rotation-in:**

- No explicit handler; relies on next tick to include the new coin. Acceptable in practice, but no immediate fetch.

**F — Empty universe trace:**

- Identical to KlineWorker: silent retention. HR-3 violation.

**G — Logging:**

- `ALTDATA | fg=X funding=N oi=M` (line 101)
- Per-source failure: `AltData {src} failed: {err}` (line 93)
- **Missing:** `ALTDATA_UNIVERSE_EMPTY`, `ALTDATA_REMOVED`.

**Verdict:** Fix needed. HR-1 (init), HR-2 (no callback), HR-3 violations.

---

### 4. PriceWorker — `src/workers/price_worker.py` (~199 LOC)

**A — Universe source (lines 54-68):**

```python
if self._scanner:
    try:
        universe = await self._scanner.get_active_universe()
        if universe and set(universe) != set(self._tracked_symbols):
            log.info("PriceWorker: Updating symbols ...")
            self._tracked_symbols = universe
            if self._connected:
                self._connected = False
    except Exception:
        pass
```

- Called once per tick.
- Change-detection compares `set(universe) != set(self._tracked_symbols)`. On change: force reconnect (only way pybit allows unsubscribe).
- **HR-1 init:** `self._tracked_symbols = settings.bybit.default_symbols` (line 43).
- **HR-3 silent retention** on empty/exception.

**B — Per-coin operation (lines 70-98):**

If not `self._connected`, calls `await self.ws.connect_public()` then `self.ws.subscribe_ticker(self._tracked_symbols, self._handle_ticker_update)`. Otherwise health-check; if ws not running, marks disconnected for reconnect on next tick. WebSocket-driven (callbacks), not poll-based.

**C — State maintained:**

- `self._tracked_symbols: list[str]` (line 43).
- `self._connected: bool` (line 44).
- `self._dropped_count: int` (line 45) — total ticker callback errors.
- `self._ws_quotes: dict[str, tuple[float, float]]` (line 50) — `{symbol: (last_price, monotonic_ts)}`. Read-side TTL (5s default, line 161-179) — but **dict keys persist** until overwrite or full reconnect.
- DB writes via `MarketRepository.save_ticker` (line 147 inside callback).

**D — Cleanup on rotation-out:**

- `_on_universe_change()` at line 181-192: sets `self._tracked_symbols = list(symbols)`, forces `self._connected = False`. **Does not prune `_ws_quotes`.** HR-2 partial violation.
- pybit has no unsubscribe — the force-reconnect dance is the only mechanism. Correct in spirit; just needs `_ws_quotes` cleanup to complete the lifecycle.

**E — Bootstrap on rotation-in:**

- Reconnect path subscribes to `self._tracked_symbols` (post-rotation universe). First WS tick lands within ~1-10s (Bybit's typical delivery latency).

**F — Empty universe trace:**

- `if universe and set(universe) != set(self._tracked_symbols):` (line 58) — empty list short-circuits. `self._tracked_symbols` retained, no log. HR-3 violation.

**G — Logging:**

- `PRICE_WS_CONN | symbols=N sample=[...]` (line 82-85)
- `PRICE_WS_DISC | rsn=ws_not_running` (line 96)
- `PRICE_UNIVERSE_SYNC | added=A removed=R total=N` (line 189-192)
- `Price worker: {n} tickers dropped total` (line 156-159)
- **Missing:** `PRICE_UNIVERSE_EMPTY`, `PRICE_UNSUB`.

**Verdict:** Fix needed. HR-1 init, HR-2 (`_ws_quotes` not pruned), HR-3 violations. **Highest risk because of WebSocket subscription state.**

---

### 5. RegimeWorker — `src/workers/regime_worker.py` (~205 LOC)

**A — Universe source (line 110-117):**

```python
if self._scanner:
    try:
        universe = await self._scanner.get_active_universe()
        coins_to_check = [
            s for s in universe
            if s != self.settings.regime.primary_symbol
        ]
```

- Called once per tick. No fallback to `default_symbols`. Excludes primary BTC symbol (separate global detection path).

**B — Per-coin operation:**

- Global regime: `self.detector.detect()` (line 86) — BTC-based, runs every tick.
- Per-coin: `self.detector.detect_per_coin(coins_to_check)` (line 120) — uses cached H1 klines from DB; no API.
- Persists to `regime_history` (global) and `coin_regime_history` (per-coin).

**C — State maintained:**

- `self.detector` — `RegimeDetector` instance with `_per_coin_regimes: dict[str, RegimeState]`, plus likely `_confirmed_regimes` and `_pending_regime` (hysteresis state — confirm by reading `src/strategies/regime.py` in Phase 5).
- `self._restored: bool` — set once after first-tick restore.
- `self._cleanup_counter: int` — counts ticks for periodic DB cleanup (every 100 ticks → ~16h).
- DB tables: `regime_history`, `coin_regime_history`.

**D — Cleanup on rotation-out:**

- `_on_universe_change()` at line 185-204:
  - **Good:** `for sym in removed: self.detector._per_coin_regimes.pop(sym, None)` (line 203-204).
  - **Gap:** hysteresis caches (`_confirmed_regimes`, `_pending_regime`) not pruned. HR-2 partial.
- DB rows for departed coins persist until 24h time-based cleanup. Acceptable per the brief's "correctness only, not retention policy."

**E — Bootstrap on rotation-in:**

- `_on_universe_change()` line 189-200: immediate `detect_per_coin(list(added))`, merges into `_per_coin_regimes`. Logs `REGIME_BACKFILL`.

**F — Empty universe trace:**

- Global regime always runs (line 86) — BTC-only, immune to universe.
- `coins_to_check = []` after primary filter on empty universe → `if coins_to_check:` (line 119) skips per-coin detection silently. **HR-3 partial: no log warning.**

**G — Logging:**

- `REGIME_RESTORE | loaded=N per-coin regimes from DB` (line 79)
- `REGIME_RESTORE_FAIL | err='...'` (line 83)
- `REGIME_GLOBAL | rgm=X conf=Y adx=Z chop=W` (line 102)
- `REGIME_PERCOIN | detected=N total_cached=N universe=N divergent=N` (line 134-137)
- `REGIME_DIVERGE | global=G divergent=[...]` (line 147-151)
- `REGIME_BACKFILL | coins=N results=[...]` (line 195-198)
- `REGIME_BACKFILL_FAIL | err=...` (line 200)
- `REGIME_DB_FAIL | sym=X err='...'` (line 165-167)
- **Missing:** `REGIME_PERCOIN_EMPTY`.

**Most important gap — first-tick restore query (lines 50-58):**

```sql
SELECT symbol, regime, confidence, adx, choppiness
FROM coin_regime_history
WHERE timestamp > datetime('now', '-30 minutes')
AND id IN (
    SELECT MAX(id) FROM coin_regime_history
    WHERE timestamp > datetime('now', '-30 minutes')
    GROUP BY symbol
)
```

Restores ALL symbols seen in the last 30 minutes regardless of current universe membership. A coin that rotated out 25 minutes ago is restored into `_per_coin_regimes` and never gets pruned (the rotation-out happened before the restart, so no `_on_universe_change` for `removed` is fired post-restart). **HR-1 violation.**

**Verdict:** Fix needed. HR-1 (restore-without-filter), HR-2 (hysteresis caches not pruned), HR-3 partial (no empty-percoin log).

---

### 6. StrategyWorker — `src/workers/strategy_worker.py` (~1,221 LOC)

**A — Universe source (line 121-124):**

```python
universe = await self.scanner.get_active_universe()
if not universe:
    log.debug("Strategy worker: no active coins")
    return
```

- Called once per tick. Explicit early-return on empty. **HR-3 compliant** (modulo cosmetic: `log.debug` should be `log.warning` for parity with `XRAY_UNIVERSE_EMPTY`).

**B — Per-coin operation (lines 165-291):**

- Layer 1 (signals): `registry.scan(symbol=..., candles=..., ...)` per active strategy.
- Layer 2 (scorer): `scorer.score_batch(...)`.
- Layer 3 (ensemble): `ensemble.vote_batch(...)`.
- Layer 4 (rule engine / hints): `layer_manager._run_strategic_review()`.
- Prefetch: two batched `market_repo.get_klines_batch(list(universe), ...)` queries — M5 (line 176-178) and H1 (line 194-196). Universe-parameterized.

**C — State maintained:**

- `self._tick_times: list[float]` (line 76) — rolling 10-tick history (cleared every 10 ticks at line 606 — observability only).
- `_section_ms`, `candles_map`, `ta_map`, `_slow_coins`, `_stats_before/_after` — all per-tick locals, discarded after each cycle.
- External caches consumed: `self.ta_engine` (TACache, 120s TTL — manager.py:140), `self.services.get("structure_cache")` (TTL from settings).
- Coin regimes read via `getattr(self.regime_detector, '_per_coin_regimes', {})` (line 128) — state owned by RegimeWorker; not mutated here.

**D — Cleanup on rotation-out:**

- **Stateless across rotations.** All per-coin data is per-tick local. TACache and StructureCache are TTL-managed by their owners.

**E — Bootstrap on rotation-in:**

- Stale-skip rule (lines 210-227): `if (datetime.now(utc) - klines[-1].timestamp).total_seconds() > 300: continue` — skips coins whose newest M5 kline is >5 min old. Per Layer 1 Phase 4, Shadow streams every coin in the 50-coin watch_list, so any coin entering active_universe already has fresh klines in `klines` table; stale-skip is correct, no grace period needed.

**F — Empty universe trace:**

- Line 122-124: explicit `return` with `log.debug`. Compliant; promote to `warning` for symmetry.

**G — Logging:**

- `STRAT_PNL_GATE | halted=Y/N rsn=ok el=Xms` (line 96)
- `STRAT_SKIP_CIRCUIT | rsn=kline_circuit_open` (line 113-115)
- `STRAT_REGIME_DIST | up=X down=Y ...` (line 150-155)
- `STRAT_PREFETCH_DB_FAIL | err=... coins=N` (line 181)
- `STRAT_PREFETCH_DB_H1_FAIL | err=... coins=N` (line 198-200)
- `STRAT_SKIP_STALE | sym=X kline_age=Ys max=300s` (line 222-225)
- `STRAT_PREFETCH_H1_ITEM_FAIL | sym=X err=...` (line 273-275)
- `STRAT_PREFETCH_CRITICAL | ...` (mentioned in inventory; in deeper code)
- `STRAT_L1`, `STRAT_L2`, `STRAT_L3`, `STRAT_L4`, `STRAT_HEALTH`, `STRAT_CYCLE_DONE` (downstream lines)

**Verdict:** No functional fix needed. Cosmetic: promote line 123 `log.debug` to `log.warning("STRAT_UNIVERSE_EMPTY | reason=scanner_returned_empty | {ctx()}")`.

---

### 7. structure_worker — `src/workers/structure_worker.py` (~232 LOC)

**A — Universe source (line 80, calls `_get_universe()` at line 163-209):**

Three-reason-code empty-universe pattern:

```python
if not self._scanner:
    log.warning(f"XRAY_UNIVERSE_EMPTY | reason=no_scanner_injected | {ctx()}")
    return []

try:
    universe = await self._scanner.get_active_universe()
except Exception as e:
    log.warning(f"XRAY_UNIVERSE_EMPTY | reason=scanner_error err={...} | {ctx()}")
    return []

if not universe:
    log.warning(f"XRAY_UNIVERSE_EMPTY | reason=scanner_returned_empty | {ctx()}")
    return []
```

- No `default_symbols` fallback. Only fresh scanner reads. **HR-1, HR-3 compliant.**

**B — Per-coin operation:**

- Per tick: `_fetch_klines(symbol)` + `engine.analyze(symbol, ...)` for the batch slice. Batch size 25; universe ≈ 32 → 2 ticks per full sweep. Setup Scanner runs after batch. Session context fetched once per tick using `universe[0]`'s candles.

**C — State maintained:**

- `self._full_universe: list[str]` (line 71) — refreshed each tick from scanner.
- `self._batch_start: int` (line 72) — wraparound cursor.
- `self._batch_size = settings.structure.batch_size` (line 73).
- `self._cache: StructureCache` — TTL-evicted (settings).
- `self._session_timer`, `self._setup_scanner` — lazy-init once.

**D — Cleanup on rotation-out:**

- `StructureCache` TTL evicts old entries. No explicit pruning needed; acceptable.

**E — Bootstrap on rotation-in:**

- Symbol enters via `_full_universe` next tick; if it falls in this batch's slice, analyzed immediately. No warm-up.

**F — Empty universe:**

- Three reason codes, all log + return `[]`. Compliant.

**G — Logging:**

- `XRAY_TICK | batch=K/N symbols=N analyzed=N errors=N cached=N session=... setups=N skips=N el=Yms` (line 156-161)
- `XRAY_UNIVERSE_EMPTY | reason={no_scanner_injected|scanner_error|scanner_returned_empty}` (lines 178, 187, 192)
- `XRAY_SESSION_ERR | err=...` (line 95)
- `XRAY_TICK_ERR | sym=X err=...` (line 118)
- `XRAY_SCANNER_ERR | err=...` (line 134)

**Verdict:** No fix. Verify only.

---

## Summary Table

| # | Worker | LOC | HR-1 | HR-2 | HR-3 | Action |
|---|---|---:|:---:|:---:|:---:|---|
| 1 | KlineWorker | 247 | ❌ `_last_fetch` accumulates | ⚠ added-only `_on_universe_change` | ❌ silent retain | **Phase 1 fix** |
| 2 | SignalWorker | 134 | ✓ stateless | ⚠ added-only `_on_universe_change` | ❌ `default_symbols` fallback | **Phase 2 fix** |
| 3 | AltDataWorker | 117 | ⚠ init `default_symbols` | ❌ no `_on_universe_change` | ❌ silent retain | **Phase 3 fix** |
| 4 | PriceWorker | 199 | ⚠ `_ws_quotes` retains keys | ⚠ no `_ws_quotes` prune | ❌ silent retain | **Phase 4 fix (highest risk)** |
| 5 | RegimeWorker | 205 | ❌ restore unfiltered, hysteresis cache leak | ✓ `_per_coin_regimes` pruned | ⚠ silent skip | **Phase 5 fix** |
| 6 | StrategyWorker | 1,221 | ✓ stateless | ✓ stateless | ✓ debug→warning only | **Phase 6 cosmetic** |
| 7 | structure_worker | 232 | ✓ | ✓ | ✓ | **Phase 7 verify only** |

## Cross-Worker Shared State

| Service | Owner | Consumers | TTL |
|---|---|---|---|
| `MarketScanner` (`scanner`) | manager.py:890 | All 7 workers | persistent; refreshes on `scan_market()` ~5min |
| `RegimeDetector` (`regime_detector`) | manager.py:951 | RegimeWorker (writer), StrategyWorker (reader of `_per_coin_regimes`) | per-tick rebuild via `detect_per_coin` + persistent dict |
| `TACache` (`ta_engine`) | created in services init | StrategyWorker, RegimeDetector | 120s TTL |
| `StructureCache` (`structure_cache`) | services init | StructureWorker (writer), StrategyWorker (reader) | settings TTL |
| `MarketRepository` | shared via DI | KlineWorker (writes via service), StrategyWorker, structure_worker | stateless DB access |

No worker mutates a shared cache owned by another worker. RegimeDetector's `_per_coin_regimes` is read-only from StrategyWorker (line 128) — safe. Cleanup boundaries are clear.

## Verification Gate Answers

The brief's Phase 0 gate requires concrete answers to four questions:

1. **Which workers maintain per-coin caches that could leak after universe changes?**
   - KlineWorker: `_last_fetch` (line 56) — leaks per (symbol, timeframe) on rotation-out.
   - PriceWorker: `_ws_quotes` (line 50) — keys persist post-rotation until next reconnect cycle clears them.
   - RegimeWorker (via RegimeDetector): `_per_coin_regimes` (cleaned by `_on_universe_change`); `_confirmed_regimes` and `_pending_regime` hysteresis caches likely leak (verify in Phase 5).

2. **Which workers have explicit universe-change handlers?**
   - KlineWorker (line 234), SignalWorker (line 124), PriceWorker (line 181), RegimeWorker (line 185).
   - Missing: AltDataWorker (no method).
   - StrategyWorker, structure_worker correctly stateless — no callback needed.

3. **Which workers handle empty-universe gracefully today?**
   - structure_worker (gold standard — three reason codes).
   - StrategyWorker (early-return with debug log; needs warning promotion).
   - RegimeWorker (silent skip; needs warning log).
   - KlineWorker, SignalWorker, AltDataWorker, PriceWorker: silently retain stale list and/or fall back to `default_symbols` — non-compliant.

4. **Which workers might break if universe size drops from 100+ to 30?**
   - None will *break*. All are universe-size agnostic (loop over the list). The improvements are correctness (cleanup, empty-handling) not capacity.

## Risk Priority for Phases 1–7

1. **Phase 4 (PriceWorker)** — highest risk: WebSocket subscription state, `_ws_quotes` lifecycle.
2. **Phase 1 (KlineWorker)** — high risk: `_last_fetch` cleanup + empty-universe gate (touches the most-trafficked tick path).
3. **Phase 5 (RegimeWorker)** — medium-high: SQL filter on first-tick restore + hysteresis cleanup.
4. **Phase 2 (SignalWorker)** — medium: drop band-aid fallbacks, add gate.
5. **Phase 3 (AltDataWorker)** — medium: gate + new `_on_universe_change` method.
6. **Phase 6 (StrategyWorker)** — low: cosmetic log promotion + audit.
7. **Phase 7 (structure_worker)** — low: verification only.

---

## Phase Order (per brief)

```
Phase 0 → Investigation (this doc) — DONE
Phase 1 → KlineWorker
Phase 2 → SignalWorker
Phase 3 → AltDataWorker
Phase 4 → PriceWorker
Phase 5 → RegimeWorker
Phase 6 → StrategyWorker
Phase 7 → structure_worker (verify only)
Phase 8 → Cross-worker 60-min observation
Phase 9 → 24-hour live observation runbook (handoff)
```

Each Phase 1–7 lands as one git commit with its own phase report. Phase 8 produces an observation report. Phase 9 produces a runbook + health-snapshot script for operator-driven 24h verification.
