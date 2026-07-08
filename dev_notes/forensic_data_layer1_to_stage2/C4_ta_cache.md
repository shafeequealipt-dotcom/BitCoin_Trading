# C4 — TACache (Forensic Data)

CAPTURE TIMESTAMP (UTC): 2026-04-27 22:58:35
Log file: `/home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log`

---

## C.4.1 — Where it lives

**File:** `/home/inshadaliqbal786/trading-intelligence-mcp/src/analysis/ta_cache.py` — 237 lines (verified `wc -l`).

Module docstring (`ta_cache.py:1-9`, verbatim):

```
"""Centralized TA cache — compute once, share everywhere.

Eliminates duplicate TA computation across:
- strategy_worker (every 45s)
- signal_worker (every 120s)
- position_watchdog (every 15s)

Drop-in replacement for TAEngine — same analyze() interface.
"""
```

### Public API

```python
class TACache:
    def __init__(self, ta_engine, ttl_seconds: float = DEFAULT_TTL,
                 maxsize: int = _DEFAULT_MAXSIZE) -> None
    async def analyze(self, candles=None, symbol: str | None = None,
                      timeframe=None, limit: int = 200) -> dict
    def is_fresh(self, symbol: str, timeframe: str = "60",
                 max_age: int = 60) -> bool
    def invalidate(self, symbol: str | None = None) -> None
    def get_stats(self) -> dict
    # __getattr__(name)   → proxies to underlying TAEngine
```

Constants in module:

```
DEFAULT_TTL              = 90.0   # ta_cache.py:25
_DEFAULT_MAXSIZE         = 200    # ta_cache.py:58
_SIZE_LOG_MIN_INTERVAL   = 300.0  # ta_cache.py:59 (TA_CACHE_SIZE log throttle)
```

**Live wiring:** `manager.py:189` — `ta_cache = TACache(ta_engine_raw, ttl_seconds=120.0)`. Live TTL is **120 s** (not the 90 s default). Same instance is registered as `services["ta"]`, `services["ta_engine"]`, `services["ta_cache"]` (`manager.py:190-192`).

**Internal state:**

```python
self._cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()   # ta_cache.py:91
self._lookups, self._valid_hits, self._recomputed = 0, 0, 0           # ta_cache.py:102-104
self._evictions = 0                                                    # ta_cache.py:106
self._lock = asyncio.Lock()                                            # ta_cache.py:108
```

Cache key (verbatim `ta_cache.py:133-141`):

```python
if candles:
    sym = getattr(candles[0], "symbol", symbol or "UNK") if candles else "UNK"
    tf = getattr(candles[0], "timeframe", timeframe)
    tf_val = tf.value if hasattr(tf, "value") else str(tf) if tf else "?"
    key = f"{sym}:{tf_val}"
elif symbol and timeframe:
    tf_val = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
    key = f"{symbol}:{tf_val}"
else:
    return await self._engine.analyze(...)
```

Both candle-path and symbol-path produce the **same key shape** `f"{sym}:{tf_val}"` (per the Stage-1/2 fix documented at `ta_cache.py:27-48`).

---

## C.4.2 — Lazy population mechanism

**Trigger:** Every call to `TACache.analyze()` is the population trigger. There is no proactive pre-warm — populate-on-miss only.

Hot path (`ta_cache.py:118-174`, verbatim):

```python
async def analyze(self, candles=None, symbol: str | None = None,
                  timeframe=None, limit: int = 200) -> dict:
    if candles:
        ...
        key = f"{sym}:{tf_val}"
    elif symbol and timeframe:
        ...
        key = f"{symbol}:{tf_val}"
    else:
        return await self._engine.analyze(candles=candles, symbol=symbol,
                                          timeframe=timeframe, limit=limit)

    now = time.monotonic()
    self._lookups += 1
    async with self._lock:
        cached = self._cache.get(key)
        if cached:
            cache_time, result = cached
            if now - cache_time < self._ttl:
                self._valid_hits += 1
                self._cache.move_to_end(key)   # promote to MRU
                return result

    # Miss — compute outside lock
    self._recomputed += 1
    result = await self._engine.analyze(candles=candles, symbol=symbol,
                                        timeframe=timeframe, limit=limit)

    async with self._lock:
        self._cache[key] = (time.monotonic(), result)
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)
            self._evictions += 1
    return result
```

**Recomputation triggers:**

1. Cache miss (key not present).
2. Stale entry: `now - cache_time >= self._ttl` (TTL = 120 s live).

**Invalidation:** `ta_cache.py:183-200` — `invalidate(symbol)` deletes entries whose key starts with `f"{symbol}:"`; `invalidate()` (no arg) clears the whole cache. Searched the codebase for callers:

```
$ grep -rn "ta_cache.invalidate\|TACache.invalidate" src/  
```

Returns 0 hits in src/ (excluding the definition itself). NOT FOUND — no production code path explicitly invalidates the cache; freshness is enforced solely via TTL expiration on read.

**LRU eviction:** when `len(self._cache) > self._maxsize` (default 200), the LRU entry is dropped via `popitem(last=False)` (`ta_cache.py:171-173`).

---

## C.4.3 — Live measurements

`TA_CACHE_SIZE` log lines (rate-limited to one emission per 300 s, `ta_cache.py:59`). All recent emissions (verbatim):

```
2026-04-27 22:11:33.698  TA_CACHE_SIZE | entries=100 maxsize=200 evictions=0 hit_rate=0.39
2026-04-27 22:16:38.573  TA_CACHE_SIZE | entries=100 maxsize=200 evictions=0 hit_rate=0.40
2026-04-27 22:26:38.898  TA_CACHE_SIZE | entries=100 maxsize=200 evictions=0 hit_rate=0.40
```

**Current cache size:** 100 entries (50 coins × 2 timeframes commonly used: M5 + H1).
**hit_rate:** 0.39 → 0.40 over the captured 15-min window.
**Evictions:** 0 (well under maxsize=200).

### Cache miss latency

NOT FOUND as a direct measurement. The cache wraps `TAEngine.analyze()`; per-call elapsed time is not separately logged by the cache layer. The closest proxy is the `_recomputed` counter increment (`ta_cache.py:161`) which simply counts misses, not their duration.

A miss invokes `await self._engine.analyze(...)` — `TAEngine` runs the indicator pipeline against the supplied candles. Since `XRAY_ANALYZE el=…` lines for analyses that include a TA path (used downstream by RegimeDetector via `ta_engine.analyze`) generally measure in the 8–177 ms range for the entire phase pipeline, TA computation alone is in the lower end of that range, but a single `TACache` miss latency is not separately captured by either log emitter.

### Hit rate over last 1000 reads

`hit_rate=0.40` means 40% of cache lookups are within TTL. Inferred from the rolling counter and `_lookups`:
- The reported hit_rate is `self._valid_hits / max(self._lookups, 1)` (`ta_cache.py:215`), measured over **the lifetime of the cache**, NOT a rolling window of 1000.
- NOT FOUND: a rolling-window counter. The counters reset only on process restart.

---

## C.4.4 — Consumers (every caller of `TACache.analyze()`)

Production call sites (excluding tests, generated `.pyc`, and the cache class itself):

| File:line | Context |
|-----------|---------|
| `src/analysis/volatility_profile.py:198` | `ta_5m = await self._ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.M5, limit=limit)` |
| `src/analysis/volatility_profile.py:219` | `ta_1h = await self._ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.H1, limit=limit)` |
| `src/tias/collector.py:360` | `ta = await ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.M5, limit=100)` |
| `src/brain/strategist.py:627` | `ta = await ta_cache.analyze(...)` |
| `src/brain/strategist.py:1453` | `ta = await ta_cache.analyze(...)` |
| `src/brain/strategist.py:2321` | `ta = await ta_cache.analyze(...)` |
| `src/apex/assembler.py:208` | `ta = await ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.M5, limit=100)` |
| `src/workers/profit_sniper.py:984` | `ta_result = await self.ta_cache.analyze(...)` |
| `src/workers/strategy_worker.py:1454` | `_ta_entry = await _ta_cache.analyze(symbol=symbol, timeframe=TimeFrame.M5, limit=100)` |

Indirect consumers via the same `TACache` instance (registered as `services["ta"]/["ta_engine"]/["ta_cache"]`):

- `RegimeDetector.detect()` calls `self.ta_engine.analyze(candles=klines)` (`regime.py:104`). The `ta_engine` it receives from `manager.py` is the TACache instance, so this is the candle-path of `TACache.analyze`.

Strategy worker also drives the **prefetch** (`strategy_worker.py:317-318` comment: "self.ta_engine IS the TACache (manager.py registers `ta`/`ta_engine`/`ta_cache` as the same TACache instance)"), populating entries that downstream readers (strategist, profit sniper, apex assembler, tias collector, volatility profile) hit on subsequent reads.

`SignalWorker` does NOT call TACache directly — its docstring specifically notes "Sentiment aggregation only (no TA — handled by TACache)" (`signal_worker.py:95`).

---

## OBSERVED

- TTL configured at instantiation = 120 s (live), not the 90 s module default. The TTL discrepancy is intentional per the comment block at `ta_cache.py:20-25` ("ttl_seconds=120.0 … pattern MISS, HIT, HIT, MISS, HIT, HIT").
- 100 entries / maxsize 200 → 50% utilisation. Eviction never fires in current steady state (`evictions=0`).
- Hit rate ~40% indicates ~60% of reads either miss outright or hit a stale entry. Given the fixed 50-coin universe and the 120 s TTL, the recompute frequency is dominated by callers that read at intervals > 120 s (e.g. brain strategist on a 150 s cadence).
- No proactive invalidation in production code; cache is purely TTL-bounded.
