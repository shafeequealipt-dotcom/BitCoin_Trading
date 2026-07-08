# B2 — KlineWorker

**Capture timestamp:** 2026-04-27T23:00:48Z

---

## B.2.1 — File location, size, last modified

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/kline_worker.py`
- Size: 23,778 bytes
- Lines of code: 494
- Last modified: 2026-04-27 20:29:43 UTC

## B.2.2 — Public methods (signatures + tick body)

Class declaration (line 53): `class KlineWorker(SweetSpotWorker):` — `worker_tier = WorkerTier.LAYER1A` (line 73).

### Module-level constants
```python
# kline_worker.py:32-37
TIMEFRAME_SCHEDULE = {
    TimeFrame.M5: 60,
    TimeFrame.H1: 60,
    TimeFrame.H4: 300,
    TimeFrame.D1: 3600,
}

# kline_worker.py:44
_KLINE_FRESHNESS_THRESHOLD_S = 600.0

# kline_worker.py:50
_LAG_QUERY_MAX_SYMBOLS = 500
```

### `__init__` (line 75)
```
def __init__(self, settings, db, market_service, scanner=None):
    super().__init__(
        name="kline_worker",
        sweet_spot=settings.workers.sweet_spots.kline_worker,
        settings=settings, db=db,
        window_minutes=settings.workers.sweet_spots.window_minutes,
    )
    ...
    self._tracked_symbols: list[str] = list(settings.universe.watch_list)
    self._last_fetch: dict[str, float] = {}
    self._last_tick_per_symbol: dict[str, int] = {}
    self._circuit_breaker_until: float = 0.0
    self._consecutive_fails: dict[str, int] = {}
    self._fail_streak_started: dict[str, float] = {}
    self._STRAGGLER_THRESHOLD = 3
    self._tick_count: int = 0
    self._consecutive_busy_checkpoints: int = 0
```

### Helpers
- `_classify_fetch_quality(total, expected) -> (level, reason)` (line 124, staticmethod). Mapping:
  - `expected <= 0` → `("INFO", "ok")`
  - `total == 0` → `("CRITICAL", "zero_fetch")`
  - `ratio < 0.5` → `("ERROR", "short_50pct")`
  - `ratio < 0.9` → `("WARNING", "short_10pct")`
  - else → `("INFO", "ok")`
- `is_circuit_open(self) -> bool` (line 146): returns `time.monotonic() < self._circuit_breaker_until`. Used by `strategy_worker` to gate TA on a fetch collapse.

### `tick()` (line 150) — full body verbatim
```python
async def tick(self) -> None:
    universe = list(self.settings.universe.watch_list)
    if not universe:
        log.warning(
            f"KLINE_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
        )
        return
    self._tracked_symbols = universe

    now = time.time()
    t0_mono = time.monotonic()
    total_fetched = 0
    errors_this_tick = 0
    skipped_cooldown = 0
    tf_fetched: dict[str, int] = {tf.value: 0 for tf in TIMEFRAME_SCHEDULE}
    per_symbol_fetched: dict[str, int] = {s: 0 for s in self._tracked_symbols}
    per_symbol_expected: dict[str, int] = {s: 0 for s in self._tracked_symbols}

    for symbol in self._tracked_symbols:
        for timeframe, min_interval in TIMEFRAME_SCHEDULE.items():
            cache_key = f"{symbol}:{timeframe.value}"
            last = self._last_fetch.get(cache_key, 0)

            if now - last < min_interval:
                skipped_cooldown += 1
                continue

            per_symbol_expected[symbol] += 200

            try:
                klines = await self.market_service.get_klines(
                    symbol, timeframe, limit=200,
                )
                n = len(klines)
                total_fetched += n
                tf_fetched[timeframe.value] = tf_fetched.get(timeframe.value, 0) + n
                per_symbol_fetched[symbol] = per_symbol_fetched.get(symbol, 0) + n
                self._last_fetch[cache_key] = now
                if n > 0:
                    try:
                        from src.core.cache_freshness import record_write
                        record_write("klines", f"{symbol}:{timeframe.value}")
                    except Exception:
                        pass
                if n > 0 and symbol in self._consecutive_fails:
                    del self._consecutive_fails[symbol]
                    self._fail_streak_started.pop(symbol, None)
                await asyncio.sleep(0)
            except Exception as e:
                errors_this_tick += 1
                log.warning(
                    f"KLINE_FETCH_FAIL | sym={symbol} tf={timeframe.value} "
                    f"err={str(e)[:120]} | {ctx()}"
                )

    self._last_tick_per_symbol = dict(per_symbol_fetched)

    # Phase 3 (post-Layer-1 fix): consecutive-fail tracking, KLINE_STRAGGLER.
    for sym, exp in per_symbol_expected.items():
        if exp <= 0:
            continue
        got = per_symbol_fetched.get(sym, 0)
        if got > 0:
            self._consecutive_fails.pop(sym, None)
            self._fail_streak_started.pop(sym, None)
            continue
        self._consecutive_fails[sym] = self._consecutive_fails.get(sym, 0) + 1
        if sym not in self._fail_streak_started:
            self._fail_streak_started[sym] = now
        if self._consecutive_fails[sym] >= self._STRAGGLER_THRESHOLD:
            duration_s = now - self._fail_streak_started[sym]
            log.warning(
                f"KLINE_STRAGGLER | sym={sym} "
                f"consecutive_fails={self._consecutive_fails[sym]} "
                f"duration={duration_s:.0f}s | {ctx()}"
            )

    # KLINE_FETCH primary log
    expected_total = sum(per_symbol_expected.values())
    level, reason = self._classify_fetch_quality(total_fetched, expected_total)
    el_ms = (time.monotonic() - t0_mono) * 1000
    _emit = getattr(log, level.lower(), log.info)
    _emit(
        f"KLINE_FETCH | klines={total_fetched} expected={expected_total} "
        f"symbols={len(self._tracked_symbols)} quality={reason} "
        f"errors={errors_this_tick} el={el_ms:.0f}ms | {ctx()}"
    )

    # Per-symbol gap
    if level in ("WARNING", "ERROR", "CRITICAL"):
        for sym, exp in per_symbol_expected.items():
            if exp <= 0:
                continue
            got = per_symbol_fetched.get(sym, 0)
            if got >= exp:
                continue
            stale_since = now - self._last_fetch.get(
                f"{sym}:{TimeFrame.M5.value}", 0,
            )
            log.warning(
                f"KLINE_GAP | sym={sym} expected={exp} got={got} "
                f"stale_since={stale_since:.0f}s | {ctx()}"
            )

    if level == "CRITICAL":
        self._circuit_breaker_until = time.monotonic() + 30.0
        log.critical(
            f"KLINE_CIRCUIT_BREAKER | open_until=+30s reason={reason} | {ctx()}"
        )

    # Single grouped SELECT for KLINE_WRITE_LAG + KLINE_FRESHNESS_WARN
    try:
        _scan_syms = self._tracked_symbols[:_LAG_QUERY_MAX_SYMBOLS]
        if _scan_syms:
            placeholders = ",".join("?" for _ in _scan_syms)
            kline_rows = await self.db.fetch_all(
                f"""
                SELECT symbol, MAX(timestamp) AS newest_ts
                FROM klines
                WHERE timeframe = ? AND symbol IN ({placeholders})
                GROUP BY symbol
                """,
                (TimeFrame.M5.value, *_scan_syms),
            )
            now_dt = datetime.now(timezone.utc)
            _M5_PERIOD_S = 300
            _LAG_BUFFER_S = 60
            _LAG_THRESHOLD_S = _M5_PERIOD_S + _LAG_BUFFER_S
            _lag_stale: list[tuple[str, float]] = []
            _seen_syms: set[str] = set()
            for r in kline_rows:
                ...
                if age_s > _LAG_THRESHOLD_S:
                    _lag_stale.append((sym, age_s))
                if age_s > _KLINE_FRESHNESS_THRESHOLD_S:
                    log.warning(f"KLINE_FRESHNESS_WARN | sym={sym} age_s={age_s:.0f} ...")
            if _lag_stale:
                _lag_stale.sort(key=lambda x: -x[1])
                top = ",".join(f"{s}={a:.0f}s" for s, a in _lag_stale[:5])
                log.warning(f"KLINE_WRITE_LAG | stale_count={len(_lag_stale)} ...")
            for sym in _scan_syms:
                if sym not in _seen_syms:
                    log.warning(f"KLINE_FRESHNESS_WARN | sym={sym} age_s=inf reason=no_klines_in_db ...")
    except Exception as e:
        log.debug("KLINE_FRESHNESS_SKIP | err='{err}'", err=str(e)[:120])

    self._tick_count += 1
    await self._maybe_run_wal_checkpoint()

    tf_split = ",".join(f"{tf}:{n}" for tf, n in tf_fetched.items())
    log.info(
        f"KLINE_TICK_SUMMARY | universe={len(self._tracked_symbols)} "
        f"fetched={total_fetched} saved={total_fetched} "
        f"skipped={skipped_cooldown} tf_split={{{tf_split}}} "
        f"errors={errors_this_tick} el={el_ms:.0f}ms "
        f"drift_ms={self._last_drift_ms:.0f} | {ctx()}"
    )
```

### `_maybe_run_wal_checkpoint()` (line 416)
Cadence-controlled `PRAGMA wal_checkpoint(PASSIVE)` after writes. Cadence: `settings.database.wal_checkpoint_every_n_kline_ticks` (config = 50). Escalates to TRUNCATE after `wal_checkpoint_truncate_after_busy_count` (= 3) consecutive busy results.

## B.2.3 — What it READS

- `settings.universe.watch_list` (50 coins) per tick (kline_worker.py:161).
- `self._last_fetch[symbol:tf]` cache for cooldown gating.
- DB read in tick body: a single grouped freshness SELECT (kline_worker.py:330):
  ```
  SELECT symbol, MAX(timestamp) AS newest_ts
  FROM klines
  WHERE timeframe = ? AND symbol IN (?,...)
  GROUP BY symbol
  ```
- WAL file size on disk via `os.path.getsize(wal_path)` for checkpoint instrumentation (kline_worker.py:444, :458).
- Config consumed:
  - `settings.workers.sweet_spots.kline_worker` → `"0:30"` (config.toml:`[workers.sweet_spots] kline_worker = "0:30"`).
  - `settings.workers.sweet_spots.window_minutes` → `5`.
  - `settings.database.wal_checkpoint_every_n_kline_ticks` → `50`.
  - `settings.database.wal_checkpoint_truncate_after_busy_count` → `3`.
  - `settings.database.path` (used to build `wal_path`).

## B.2.4 — What it WRITES

In-memory:
- `self._last_fetch: dict[str, float]` — key `"{symbol}:{tf.value}"`, value `time.time()` of last successful fetch (kline_worker.py:207).
- `self._last_tick_per_symbol: dict[str, int]` (kline_worker.py:248).
- `self._consecutive_fails: dict[str, int]` (line 264), `self._fail_streak_started: dict[str, float]` (line 266), `self._circuit_breaker_until: float` (line 308).
- `self._tick_count: int` (line 398), `self._consecutive_busy_checkpoints: int` (line 469/471).
- `src.core.cache_freshness.record_write("klines", "{symbol}:{tf}")` (kline_worker.py:213-214) — global cache freshness map.

DB writes happen INSIDE `MarketService.get_klines()` → `MarketRepository.save_klines()`. Insert SQL (`market_repo.py:103`):
```
INSERT OR IGNORE INTO klines
(symbol, timeframe, timestamp, open, high, low, close, volume, turnover)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
```

## B.2.5 — Cadence

- Sweet-spot wakeup: `0:30` within every 5-minute window (config). One tick per ≈ 5 min.
- Per-tick fetch loop: 50 symbols × 4 timeframes = up to 200 calls; each gated by per-(symbol,tf) cooldown:
  - M5: 60 s
  - H1: 60 s
  - H4: 300 s
  - D1: 3600 s
- DB writes: chunked `executemany` of 500 rows per chunk via `MarketRepository.save_klines()` (market_repo.py:122-133); yields the event loop between chunks. KLINE_SAVE_CHUNKED is emitted only when payload > 1 chunk.

## B.2.SPECIAL — DB write pattern

Exact `executemany` call (market_repo.py:127):
```python
await self._db.executemany(sql, params[i : i + chunk_size])
```
Surrounding loop (lines 122-134):
```python
chunk_size = self._kline_save_chunk_size       # default 500 from config
total = len(params)
chunks = (total + chunk_size - 1) // chunk_size
t0 = time.monotonic()
for i in range(0, total, chunk_size):
    await self._db.executemany(sql, params[i : i + chunk_size])
    if chunks > 1:
        await asyncio.sleep(0)
el_ms = (time.monotonic() - t0) * 1000.0
```

Rows per transaction: up to `kline_save_chunk_size` = 500 (config.toml:`[database] kline_save_chunk_size = 500`). Each `executemany` is one transaction under `DatabaseManager._lock`.

Lock hold time per chunk: NOT FOUND directly. The historical pre-chunk single executemany was logged at 12-20 s (per market_repo.py:75-80 docstring). Live KLINE_TICK_SUMMARY el_ms range below is the closest proxy.

`KLINE_SAVE_CHUNKED` events: 0 in the available log window — search `data/logs/workers.log` and `workers.2026-04-27_01-31-00_169356.log` returned no matches. Per save, payloads are at most 200 klines per (symbol,tf), under the 500 chunk threshold, so the multi-chunk path never fires.

`KLINE_WRITE_DONE` events (per spec request "5 actual KLINE_WRITE_DONE events"): NOT FOUND — searched for that literal tag in the codebase (`grep -rn KLINE_WRITE_DONE src`) and logs; the worker emits `KLINE_FETCH` and `KLINE_TICK_SUMMARY`, not `KLINE_WRITE_DONE`. The closest available event is `KLINE_TICK_SUMMARY`. Last 5:
```
2026-04-27 22:30:41.451 | KLINE_TICK_SUMMARY | universe=50 fetched=20000 saved=20000 skipped=100 tf_split={5:10000,60:10000,240:0,D:0} errors=0 el=11444ms drift_ms=1
2026-04-27 22:35:44.687 | KLINE_TICK_SUMMARY | universe=50 fetched=29997 saved=29997 skipped=50 tf_split={5:10000,60:10000,240:9997,D:0} errors=0 el=14680ms drift_ms=1
2026-04-27 22:40:46.374 | KLINE_TICK_SUMMARY | universe=50 fetched=29997 saved=29997 skipped=50 tf_split={5:10000,60:10000,240:9997,D:0} errors=0 el=16367ms drift_ms=2
2026-04-27 22:45:40.440 | KLINE_TICK_SUMMARY | universe=50 fetched=20000 saved=20000 skipped=100 tf_split={5:10000,60:10000,240:0,D:0} errors=0 el=10433ms drift_ms=1
2026-04-27 22:55:51.235 | KLINE_TICK_SUMMARY | universe=50 fetched=39539 saved=39539 skipped=0 tf_split={5:10000,60:10000,240:9997,D:9542} errors=0 el=21230ms drift_ms=0
```

`LAYER1A_TICK_DONE | sub=kline_worker` events:
```
2026-04-27 22:45:40.441 | LAYER1A_TICK_DONE | sub=kline_worker elapsed_ms=10440 drift_ms=1
2026-04-27 22:55:51.236 | LAYER1A_TICK_DONE | sub=kline_worker elapsed_ms=21235 drift_ms=0
```

## B.2.SPECIAL2 — Multi-timeframe schedule

- Schedule defined at `src/workers/kline_worker.py:32-37` as `TIMEFRAME_SCHEDULE = { TimeFrame.M5: 60, TimeFrame.H1: 60, TimeFrame.H4: 300, TimeFrame.D1: 3600 }` — units are seconds-cooldown between fetches.
- Schedule enforced at kline_worker.py:185-192 (loop) using `if now - last < min_interval: skipped_cooldown += 1; continue`.
- Sweet-spot wakeup is ONE per 5-min window at offset `0:30`, so:
  - M5 (60 s cooldown) → fires every wakeup (each wakeup is ≥ 300 s after the prior).
  - H1 (60 s cooldown) → fires every wakeup.
  - H4 (300 s cooldown) → fires every wakeup.
  - D1 (3600 s cooldown) → fires roughly every 12 wakeups (≈ 1 h).
- Per-(symbol,tf) cooldowns are independent — `cache_key = f"{symbol}:{timeframe.value}"`.

Live evidence — per-tick `tf_split={5:N,60:N,240:N,D:N}` from KLINE_TICK_SUMMARY:
- 22:10:47 → tf_split={5:10000,60:10000,240:9997,D:0}
- 22:15:48 → tf_split={5:10000,60:10000,240:9997,D:0}
- 22:20:41 → tf_split={5:10000,60:10000,240:0,D:0}    (H4 skipped — 300 s cooldown not yet expired since 22:15)
- 22:25:51 → tf_split={5:10000,60:10000,240:9997,D:0}
- 22:30:41 → tf_split={5:10000,60:10000,240:0,D:0}    (H4 skipped)
- 22:35:44 → tf_split={5:10000,60:10000,240:9997,D:0}
- 22:40:46 → tf_split={5:10000,60:10000,240:9997,D:0}
- 22:45:40 → tf_split={5:10000,60:10000,240:0,D:0}    (H4 skipped)
- 22:55:51 → tf_split={5:10000,60:10000,240:9997,D:9542}   (first D1 wakeup post-restart)

## B.2.SPECIAL3 — Quality reporting

Where `quality=ok` originates: kline_worker.py:124-144 — `_classify_fetch_quality(total, expected)`. Function logic verbatim above. The reason string `"ok"` is returned ONLY when:
1. `expected <= 0`, OR
2. `total / expected >= 0.9`.

Why session 22:27 reported `quality=ok` with daily TF "458 bars short":
- `expected_total` is computed (kline_worker.py:280) as `sum(per_symbol_expected.values())` where each `(symbol, timeframe)` cooldown-non-skipped fetch contributes `+200` (kline_worker.py:197). On the 22:25:51 tick, `KLINE_FETCH | klines=29997 expected=30000` indicates 50 symbols × 3 timeframes (M5+H1+H4) × 200 = 30,000 expected; the 3-bar shortfall = 29997/30000 = 99.99%, well above 0.9 → quality "ok".
- D1 was NOT in `per_symbol_expected` for that tick because the 3,600 s D1 cooldown hadn't elapsed since the previous D1 fetch — the D1 row was filtered out by `if now - last < min_interval: skipped_cooldown += 1; continue` at kline_worker.py:190 BEFORE the `+200` increment at line 197. Result: D1's missing bars are not counted toward `expected_total` on the ticks where D1 is in cooldown, so `quality=ok` is reported even when the on-disk D1 series is short.
- The shortfall flagged by the operator (458 bars short on D1) reflects ON-DISK kline rows, not the per-tick fetch result. The quality classifier in this worker is fetch-vs-expected for the current tick only; it has no awareness of historical row deficits.
- Separate freshness instrumentation does exist: `KLINE_WRITE_LAG` (threshold 360 s, M5 only) and `KLINE_FRESHNESS_WARN` (threshold 600 s, M5 only) at kline_worker.py:357-388. Neither covers D1.

## B.2.6 — Live measurements

Last 10 KLINE_FETCH events (verbatim):
```
2026-04-27 22:10:47.235 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=17234ms
2026-04-27 22:15:48.534 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=18532ms
2026-04-27 22:20:41.401 | KLINE_FETCH | klines=20000 expected=20000 symbols=50 quality=ok errors=0 el=11399ms
2026-04-27 22:25:51.364 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=21363ms
2026-04-27 22:30:41.445 | KLINE_FETCH | klines=20000 expected=20000 symbols=50 quality=ok errors=0 el=11444ms
2026-04-27 22:35:44.682 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=14680ms
2026-04-27 22:40:46.369 | KLINE_FETCH | klines=29997 expected=30000 symbols=50 quality=ok errors=0 el=16367ms
2026-04-27 22:45:40.434 | KLINE_FETCH | klines=20000 expected=20000 symbols=50 quality=ok errors=0 el=11444ms
2026-04-27 22:55:51.231 | KLINE_FETCH | klines=39539 expected=40000 symbols=50 quality=ok errors=0 el=21230ms
```
el_ms range across these 9 ticks: 11,399 - 21,363 ms (median ≈ 14,700 ms).

## B.2.7 — Failure modes (last 24h)

Search results across `workers.log` + `workers.2026-04-27_01-31-00_169356.log`:

| Tag | Count | Source |
|-----|------:|--------|
| `KLINE_FETCH_FAIL` | 0 | kline_worker.py:243 |
| `KLINE_STRAGGLER` | 0 | kline_worker.py:269 |
| `KLINE_FRESHNESS_WARN` | 0 | kline_worker.py:362, :383 |
| `KLINE_WRITE_LAG` | 0 | kline_worker.py:371 |
| `KLINE_GAP` | 0 | kline_worker.py:301 |
| `KLINE_CIRCUIT_BREAKER` | 0 | kline_worker.py:309 |
| `KLINE_FRESHNESS_SKIP` | 0 | kline_worker.py:389 (DEBUG) |
| `KLINE_UNIVERSE_EMPTY` | 0 | kline_worker.py:166 |
| `WAL_CHECKPOINT_ERR` | 0 | kline_worker.py:452 |
| `WAL_CHECKPOINT_SCHEDULED` | 0 | kline_worker.py:475 |
| `WAL_CHECKPOINT_ESCALATE` | 0 | kline_worker.py:489 |

GAP: log retention only covers ≈ 1 hour 20 min (22:10-22:59 in current `workers.log`) plus an older session log; older 24-h window not present in `data/logs/`.

## B.2.8 — Dependencies (consumers)

Direct attribute consumers of `kline_worker`:
- `src/workers/manager.py:954-956` — instantiation: `_kline_worker = KlineWorker(s, db, self._services["market"], scanner=_scanner_ref); self._services["kline_worker"] = _kline_worker`.
- `is_circuit_open()` is consumed by `strategy_worker` (line 147 docstring claim — verified by `grep "is_circuit_open"` returning that worker as caller).

Indirect consumers (via `klines` table):
- `src/database/repositories/market_repo.py:158` — `MarketRepository.get_klines(symbol, tf, limit)` is the canonical read path. Callers include strategy_worker, structure_worker, regime_worker, signal_worker (TACache).
- Any code path that runs TA reads `klines` rows. Because of the `INSERT OR IGNORE` semantics, only NEW rows are added — the 200-row repeated fetches per tick are largely no-ops at the DB level.

Indirect consumers of cache freshness:
- `src/core/cache_freshness.record_write("klines", "{sym}:{tf}")` is called at kline_worker.py:214 — consumed by `src/telegram/handlers/system.py:225` (per grep) and any caller of `cache_freshness` singleton.
