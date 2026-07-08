# Phase 0 — ShadowKlineReader Investigation

**Date:** 2026-04-25
**Brief:** `/home/inshadaliqbal786/IMPLEMENT_SHADOWKLINEREADER_ROOT_CAUSE_FIX.md`
**Factual baseline:** `dev_notes/layer1_to_xray_complete_state.md`
**Status:** Investigation complete. No code changed.

---

## 0. System State at Investigation Time

```
trading-workers.service: active (running) since Sat 2026-04-25 20:13:06 UTC; 3h 12min ago
Main PID: 397 (python /home/inshadaliqbal786/trading-intelligence-mcp/.venv/bin/python workers.py)
Tasks: 58 (limit 4687)
Memory: 548.0M (high: 600.0M, max: 800.0M, available: 52.0M)
CPU: 1h 53min 42s

shadow.db:      856,588,288 bytes  (≈817 MB)  Apr 25 23:23
shadow.db-wal:    7,436,632 bytes  (≈7 MB)    Apr 25 23:25
trading.db:     151,547,904 bytes  (≈145 MB)  Apr 25 23:13
```

Memory headroom is 52 MB before MemoryHigh kicks in — confirms the brief's pressure assessment (was 15.8 MB at the original observation).

---

## 1. File-by-File Documentation

### 1.1 `src/analysis/structure/shadow_kline_reader.py` (191 lines)

**Imports (lines 1-14):**
```
import sqlite3
from datetime import datetime, timezone

from src.core.logging import get_logger
from src.core.types import OHLCV, TimeFrame

log = get_logger("xray")
```

No locks, no `aiosqlite`, no `asyncio` import. Pure synchronous sqlite3.

**Module-level table (lines 17-24) — TF_MS:** maps timeframe strings (`"1"`, `"5"`, `"15"`, `"60"`, `"240"`, `"D"`) to milliseconds.

**Class signature (lines 27-35):**
```python
class ShadowKlineReader:
    """Reads and aggregates Shadow DB klines into OHLCV objects.
    ...
    """

    def __init__(self, shadow_db_path: str) -> None:
        self._db_path = shadow_db_path
```
Stores ONLY the DB path. **No connection state, no locks.** Each call opens fresh connections.

**Public method `get_klines` (lines 37-102):**

```python
def get_klines(
    self,
    symbol: str,
    timeframe: str = "60",
    limit: int = 200,
) -> list[OHLCV]:
    ...
    tf_ms = TF_MS.get(timeframe, 3_600_000)
    try:
        uri = f"file:{self._db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)         # ← LINE 59 — CONNECTION #1
        cursor = conn.cursor()
        # Aggregate 1-min candles into the target timeframe
        # Group by floored timestamp bucket
        cursor.execute(
            f"""
            SELECT
                (timestamp / ?) * ? as bucket_ts,
                MIN(open) as first_open,
                MAX(high) as high,
                MIN(low) as low,
                MAX(close) as last_close,
                SUM(volume) as volume,
                SUM(turnover) as turnover,
                MIN(timestamp) as first_ts,
                MAX(timestamp) as last_ts
            FROM (
                SELECT timestamp, open, high, low, close, volume, turnover,
                       ROW_NUMBER() OVER (PARTITION BY (timestamp / ?) ORDER BY timestamp ASC) as rn_first,
                       ROW_NUMBER() OVER (PARTITION BY (timestamp / ?) ORDER BY timestamp DESC) as rn_last
                FROM klines
                WHERE symbol = ?
            )
            GROUP BY bucket_ts
            ORDER BY bucket_ts DESC
            LIMIT ?
            """,
            (tf_ms, tf_ms, tf_ms, tf_ms, symbol, limit),
        )

        rows = cursor.fetchall()                                  # ← LINE 90
        conn.close()                                              # ← LINE 91

        if not rows:
            return []                                             # ← LINE 94 — early exit

        # The query above doesn't correctly get open/close per bucket
        # Let's use a simpler approach
        return self._aggregate_simple(symbol, timeframe, tf_ms, limit)   # ← LINE 98 — fall through

    except Exception as e:
        log.debug(f"XRAY_SHADOW_KLINE_ERR | sym={symbol} err={str(e)[:80]}")  # ← LINE 101
        return []
```

The variable `rows` is referenced exactly twice: at the assignment (line 90) and at the conditional `if not rows` (line 93). It is NEVER returned, NEVER passed to another function, NEVER logged. The query results are **discarded**; only the truthiness (any rows exist? yes/no) is used as a redundant early-exit gate before calling `_aggregate_simple`.

The comment at lines 96-97 acknowledges the windowed query is broken:
```
# The query above doesn't correctly get open/close per bucket
# Let's use a simpler approach
```

**Private method `_aggregate_simple` (lines 104-190):**

```python
def _aggregate_simple(
    self,
    symbol: str,
    timeframe: str,
    tf_ms: int,
    limit: int,
) -> list[OHLCV]:
    try:
        uri = f"file:{self._db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)         # ← LINE 114 — CONNECTION #2
        cursor = conn.cursor()

        minutes_per_bar = tf_ms // 60_000
        raw_limit = limit * minutes_per_bar + minutes_per_bar

        cursor.execute(
            """
            SELECT timestamp, open, high, low, close, volume, turnover
            FROM klines
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (symbol, raw_limit),
        )

        rows = cursor.fetchall()                                  # ← LINE 133
        conn.close()                                              # ← LINE 134

        if not rows:
            return []

        # Reverse to chronological order
        rows.reverse()

        # Aggregate into buckets
        buckets: dict[int, dict] = {}
        for ts_ms, o, h, l, c, v, t in rows:
            bucket = (ts_ms // tf_ms) * tf_ms
            if bucket not in buckets:
                buckets[bucket] = {
                    "open": o, "high": h, "low": l, "close": c,
                    "volume": v, "turnover": t, "ts": bucket,
                }
            else:
                b = buckets[bucket]
                b["high"] = max(b["high"], h)
                b["low"] = min(b["low"], l)
                b["close"] = c          # last close wins
                b["volume"] += v
                b["turnover"] += t

        sorted_buckets = sorted(buckets.values(), key=lambda x: x["ts"])
        sorted_buckets = sorted_buckets[-limit:]

        tf_enum = TimeFrame.H1   # default
        for tf in TimeFrame:
            if tf.value == timeframe:
                tf_enum = tf
                break

        result = []
        for b in sorted_buckets:
            ts_sec = b["ts"] / 1000.0
            dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
            result.append(OHLCV(
                symbol=symbol,
                timeframe=tf_enum,
                timestamp=dt,
                open=b["open"],
                high=b["high"],
                low=b["low"],
                close=b["close"],
                volume=b["volume"],
                turnover=b["turnover"],
            ))

        return result

    except Exception as e:
        log.debug(f"XRAY_SHADOW_AGG_ERR | sym={symbol} err={str(e)[:80]}")   # ← LINE 189
        return []
```

**Connection inventory per `get_klines` call:** 2 sqlite3 connections (lines 59, 114). Both are read-only (`?mode=ro` URI), 5-second timeout, opened-and-closed within the same call.

**Exception handling:**
- `get_klines` outer try/except (lines 57-102) → `XRAY_SHADOW_KLINE_ERR` at DEBUG (line 101).
- `_aggregate_simple` try/except (lines 112-190) → `XRAY_SHADOW_AGG_ERR` at DEBUG (line 189).

**Locks/threading:** none. Pure synchronous sqlite3 — when called from an async context (which it is, see §1.2), it **blocks the asyncio event loop** for the entire duration of both connections + queries + Python aggregation.

**Conn.close discipline:** both `close()` calls (line 91, line 134) are NAKED — not in a `try/finally`. If an exception fires after `connect()` but before `close()`, the connection is leaked until garbage collection (Python's sqlite3 binding closes on `__del__`, but the leak window is non-deterministic).

**TODO/FIXME/XXX/HACK comments:** None marked as such. The lines 96-97 comment is the closest thing — an acknowledgment that the windowed query is broken.

### 1.2 `src/workers/structure_worker.py` (210 lines)

**Imports (lines 1-23):**
```
import time

from src.analysis.structure.structure_cache import StructureCache
from src.analysis.structure.structure_engine import StructureEngine
from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.types import TimeFrame
from src.database.connection import DatabaseManager
from src.database.repositories.market_repo import MarketRepository
from src.workers.base_worker import BaseWorker

log = get_logger("xray")
```

**Constructor (lines 39-72):**
```python
def __init__(
    self,
    settings: Settings,
    db: DatabaseManager,
    engine: StructureEngine,
    cache: StructureCache,
    scanner=None,
    coin_discovery=None,
    shadow_kline_reader=None,
) -> None:
    super().__init__(
        name="structure_worker",
        interval_seconds=float(settings.structure.worker_interval_seconds),
        settings=settings,
        db=db,
    )
    self._engine = engine
    self._cache = cache
    self._scanner = scanner
    self._market_repo = MarketRepository(db)
    self._coin_discovery = coin_discovery
    self._shadow_reader = shadow_kline_reader
    ...
    self._batch_size = settings.structure.batch_size
    self._scan_full = settings.structure.scan_full_market
    self._coin_refresh_interval = settings.structure.coin_refresh_interval
```

`shadow_kline_reader` is **constructor injection** (line 47), stored on `self._shadow_reader` (line 60). It's optional (`=None`) and gracefully handled at the call site.

**Tick method (lines 74-145):** single `tick()` per `worker_interval_seconds=60` (config.toml:672).

Key calls into `_fetch_klines`:
- **Line 88 (session context):** `first_candles = await self._fetch_klines(universe[0]) if universe else None`
- **Line 102 (per-symbol loop):** `candles = await self._fetch_klines(symbol)` inside `for symbol in universe:`

The session-context call fetches `universe[0]`, then the analysis loop iterates over the same `universe`, including `universe[0]` again — **one redundant fetch per tick** (logged in §6 below as discovered issue D-1).

**`_fetch_klines` method (lines 189-209):**
```python
async def _fetch_klines(self, symbol: str) -> list | None:
    """Fetch H1 klines — try trading.db first, fall back to Shadow DB."""
    try:
        candles = await self._market_repo.get_klines(            # ← LINE 192 — async, trading.db
            symbol, TimeFrame.H1.value, 200,
        )
        if candles and len(candles) >= self.settings.structure.min_candles:
            return candles
    except Exception:
        pass

    # Fall back to Shadow DB kline reader
    if self._shadow_reader:
        try:
            candles = self._shadow_reader.get_klines(symbol, "60", 200)   # ← LINE 203 — SYNC, BLOCKS EVENT LOOP
            if candles and len(candles) >= self.settings.structure.min_candles:
                return candles
        except Exception:
            pass

    return None
```

**This is the only call site of `ShadowKlineReader.get_klines` in the entire project** (verified by grep — see §3 below). The call is **synchronous** inside an `async def` method — every sqlite3.connect, query, and Python aggregation blocks the asyncio event loop, starving every other worker.

`TimeFrame.H1.value` (line 192) is `"60"` (verified in `src/core/types.py:47`). Trading.db stores klines with `timeframe="60"` for H1 — same value passed to `shadow_reader.get_klines(symbol, "60", 200)`. The fallback fires when trading.db has fewer than `min_candles` (50, config.toml:674) for the symbol — i.e., for the broad-market coins that `kline_worker` doesn't track.

**Universe selection (`_get_universe`, lines 147-187):** when `scan_full_market=true` and `coin_discovery` is wired (both true in this deployment), pulls 126 coins from `CoinDiscovery.get_analyzable_coins()` once every 600 s, then yields a 25-symbol batch per tick (cycling through the universe over 5 ticks ≈ 5 minutes).

### 1.3 `src/workers/manager.py` (1962 lines — relevant slices)

**`ShadowKlineReader` instantiation (lines 178-194):**
```python
# Full market scanning via Shadow DB
if settings.structure.scan_full_market:
    try:
        from src.analysis.structure.coin_discovery import CoinDiscovery
        from src.analysis.structure.shadow_kline_reader import ShadowKlineReader
        shadow_path = settings.structure.shadow_db_path
        coin_discovery = CoinDiscovery(
            shadow_db_path=shadow_path,
            refresh_interval=settings.structure.coin_refresh_interval,
        )
        shadow_reader = ShadowKlineReader(shadow_db_path=shadow_path)   # ← LINE 186
        self._services["coin_discovery"] = coin_discovery
        self._services["shadow_kline_reader"] = shadow_reader            # ← LINE 188
        log.info(
            "X-RAY: Full market mode (Shadow DB: {path})",
            path=shadow_path,
        )
    except Exception as e:
        log.warning("X-RAY full market unavailable: {err}", err=str(e))
```

- Construction (line 186): once, at boot.
- Service registration (line 188): singleton in `self._services["shadow_kline_reader"]`.
- Constructor opens NO connection (verified in §1.1).

**Service-keys registry (lines 580-581):**
```python
"structure_engine", "structure_cache", "coin_discovery",
"shadow_kline_reader",
```
String literal, not a method call — no behavior, just a manifest for `_emit_services_wired`.

**Worker construction (lines 919-926):**
```python
sw = StructureWorker(
    settings=s, db=db, engine=se, cache=sc,
    scanner=self._services.get("scanner"),
    coin_discovery=self._services.get("coin_discovery"),
    shadow_kline_reader=self._services.get("shadow_kline_reader"),    # ← LINE 923
)
self.workers.append(sw)
self._services["structure_worker"] = sw
```
Same singleton instance is injected into the StructureWorker constructor.

**Shutdown path (`stop_all`, lines 1871-1906):**
```python
async def stop_all(self) -> None:
    log.info("Stopping all workers...")
    for w in self.workers:
        w.running = False
    for w in self.workers:
        try:
            await asyncio.wait_for(w.stop(), timeout=10.0)            # ← BaseWorker.stop() → cleanup()
        except (asyncio.TimeoutError, Exception) as e:
            log.warning("Worker '{name}' stop timed out: {err}", name=w.name, err=str(e))
    for t in self.tasks:
        if not t.done():
            t.cancel()
    bybit = self._services.get("bybit")
    if bybit and hasattr(bybit, "disconnect"):
        try:
            await bybit.disconnect()
        except Exception as e:
            log.debug("bybit disconnect failed: {err}", err=str(e))
    ws = self._services.get("ws")
    if ws and hasattr(ws, "disconnect"):
        try:
            await ws.disconnect()
        except Exception as e:
            log.debug("websocket disconnect failed: {err}", err=str(e))
    await self.db.disconnect()                                         # ← LINE 1905
    log.info("All workers stopped")
```

The pattern for service shutdown is "check existence, hasattr, try/except, log debug." Phase 3's cleanup hook will follow this exactly — inserted before line 1905.

### 1.4 `src/database/repositories/market_repo.py` — `get_klines`

**`MarketRepository.get_klines` (lines 139-178):**
```python
async def get_klines(
    self,
    symbol: str,
    timeframe: str,
    limit: int = 200,
) -> list[OHLCV]:
    rows = await self._db.fetch_all(
        """
        SELECT symbol, timeframe, timestamp, open, high, low, close, volume, turnover
        FROM klines
        WHERE symbol = ? AND timeframe = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (symbol, timeframe, limit),
    )
    result = []
    for r in reversed(rows):
        result.append(OHLCV(
            symbol=r["symbol"],
            timeframe=TimeFrame(r["timeframe"]),
            timestamp=datetime.fromisoformat(r["timestamp"]),
            ...
        ))
    return result
```

- Reads from `trading.db` exclusively (the `DatabaseManager` instance injected at construction).
- Uses the persistent aiosqlite connection + asyncio.Lock from `DatabaseManager` — already pooled, NOT per-call.
- Query requires exact `timeframe = ?` match — for H1 it's `"60"`.
- Returns `[]` if no rows (no exception); structure_worker's `if candles and len(candles) >= self.settings.structure.min_candles` guard then triggers the shadow_reader fallback.
- Live confirmation: `sqlite3 trading.db "SELECT timeframe, COUNT(*) FROM klines GROUP BY timeframe"` returns:
  ```
  5|50419
  60|21825
  240|10929
  D|5116
  ```
  Trading.db DOES have H1 data — for the ~30 active-universe symbols that `kline_worker` writes. The other ~95 symbols in the structure_worker universe (126 from CoinDiscovery) have NO trading.db data and always fall through to shadow_reader.

### 1.5 `src/analysis/structure/coin_discovery.py` (106 lines)

Same per-call sync sqlite3 pattern as ShadowKlineReader (lines 57-80), against the same shadow.db. **One** SQL query per call: `SELECT symbol, COUNT(*) ... GROUP BY symbol HAVING cnt >= ? ORDER BY symbol`.

Called only from `structure_worker._get_universe` at line 153, gated by `(now - self._universe_refreshed_at) > self._coin_refresh_interval` (600 s). Effective rate: ~0.1 calls per structure_worker tick. **Not the bottleneck.** Listed in §6 as deferred discovered issue D-2.

### 1.6 `src/database/connection.py` — `DatabaseManager` (the reference pattern)

Already documented in the plan. Key shape:
```python
class DatabaseManager:
    def __init__(self, db_path: str, wal_mode: bool = True) -> None:
        self.db_path = db_path
        self.wal_mode = wal_mode
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None: ...   # opens persistent aiosqlite + sets PRAGMAs
    async def disconnect(self) -> None: ...
    async def execute / executemany / fetch_one / fetch_all / transaction()
```

Phase 3's `ShadowKlineReader` redesign mirrors this exactly: `self._db` + `self._lock`, idempotent `connect()`/`close()`, explicit lifecycle managed by `WorkerManager`.

### 1.7 `src/workers/base_worker.py` — `BaseWorker`

Confirmed: `BaseWorker.cleanup()` hook exists at line 153, default no-op. Override point for resource shutdown. We will NOT use this for shadow_reader cleanup — the reader is a SHARED service in `self._services`, not owned by a single worker. `WorkerManager.stop_all` is the canonical owner of `_services` lifecycle (per §1.3 above).

### 1.8 Configuration (`config.toml [analysis.structure]`, lines 670-702)

```
[analysis.structure]
enabled = true
worker_interval_seconds = 60
cache_ttl_seconds = 300
min_candles = 50
swing_lookbacks = [3, 5, 10]
cluster_pct = 0.3
min_touches = 2
max_levels_per_side = 5
ms_swing_lookback = 5
ms_min_swing_points = 3
sl_buffer_pct = 0.15
tp_buffer_pct = 0.10
min_rr_ratio = 2.0
sl_fallback_pct = 2.0
tp_fallback_pct = 4.0
fvg_min_gap_pct = 0.1
fvg_max_age_candles = 50
ob_displacement_min = 0.6
ob_max_age_candles = 50
liq_equal_tolerance_pct = 0.05
liq_min_equal_count = 2
liq_round_number_step = 100.0
sweep_max_age_candles = 10
sweep_min_wick_pct = 0.3
setup_scanner_mode = "supplement"
scan_full_market = true
batch_size = 25
coin_refresh_interval = 600
shadow_db_path = "../shadow/data/shadow.db"
```

Effective values relevant to Phase 0:
- `worker_interval_seconds = 60` → tick every 60 s
- `cache_ttl_seconds = 300` → cache hit reduces shadow_reader calls during the 5-min TTL (per analysis result, not per call — does not eliminate the fetch step that precedes caching)
- `min_candles = 50` → shadow_reader fallback rejects symbols with < 50 candles
- `scan_full_market = true` → broad-market mode (126 coins), batched at 25/tick
- `batch_size = 25`
- `coin_refresh_interval = 600`
- `shadow_db_path = "../shadow/data/shadow.db"` → relative to working dir → resolves to `/home/inshadaliqbal786/shadow/data/shadow.db`

Settings dataclass `StructureSettings` is at `src/config/settings.py:691`. Builder `_build_structure` is at line 1440, instantiated at line 1019.

### 1.9 Logging (`src/core/logging.py`)

`get_logger("xray")` → `logger.bind(component="xray")`. The `xray` component routes to `data/logs/workers.log` (line 67). Format conventions:
- 10 MB rotation, 7-day retention
- Format includes timestamp, level, module:function:line, message
- Existing tag style: `f"XRAY_TAG | k=v ... | {ctx()}"` — `ctx()` from `src/core/log_context.py` returns context IDs (`did=...`, `tid=...`, `wid=...`, `sid=...`) or `"no_ctx"`.

Existing XRAY tags (grep over `src/`):
- `XRAY_TICK`, `XRAY_TICK_ERR`, `XRAY_SCANNER_ERR`, `XRAY_SESSION_ERR` (`structure_worker.py`)
- `XRAY_COINS`, `XRAY_COINS_ERR` (`coin_discovery.py`)
- `XRAY_SHADOW_KLINE_ERR`, `XRAY_SHADOW_AGG_ERR` (`shadow_kline_reader.py`)

New tags planned for Phase 3: `XRAY_SHADOW_CONN_OPEN`, `XRAY_SHADOW_CONN_CLOSE`, `XRAY_SHADOW_STATS`, `XRAY_SHADOW_NOT_CONNECTED`. The redundant `XRAY_SHADOW_KLINE_ERR` is removed in Phase 2 (the dead query is removed) — `XRAY_SHADOW_AGG_ERR` covers all real error paths.

---

## 2. Complete Call Chain (file:line for every step)

```
asyncio.run(main())                          workers.py:165
└─ WorkerManager.run()                        manager.py:1814 → calls stop_all on shutdown
   └─ asyncio.create_task(_run_worker(w))     manager.py (each worker → BaseWorker.start)
      └─ BaseWorker.start (loop)              base_worker.py:84
         └─ await self.tick()                 base_worker.py:91
            └─ StructureWorker.tick()         structure_worker.py:74
               ├─ universe = await self._get_universe()      line 79
               │  └─ self._coin_discovery.get_analyzable_coins()    structure_worker.py:153
               │     └─ sqlite3.connect(shadow.db)            coin_discovery.py:60   (~once per 600 s)
               ├─ first_candles = await self._fetch_klines(universe[0])   line 88   (session context)
               │  ├─ await self._market_repo.get_klines(s, "60", 200)   structure_worker.py:192
               │  │  └─ await self._db.fetch_all(...)         market_repo.py:155
               │  │     └─ aiosqlite query against trading.db (persistent conn + asyncio.Lock)
               │  └─ FALLBACK if insufficient:
               │     └─ self._shadow_reader.get_klines(s, "60", 200)    structure_worker.py:203  (SYNC!)
               │        ├─ sqlite3.connect(shadow.db)          shadow_kline_reader.py:59  ← CONN #1 (DEAD)
               │        ├─ cursor.execute(windowed query)      shadow_kline_reader.py:64
               │        ├─ rows = cursor.fetchall()            shadow_kline_reader.py:90  ← DISCARDED
               │        ├─ conn.close()                        shadow_kline_reader.py:91
               │        ├─ if not rows: return []              shadow_kline_reader.py:94  (only signal used)
               │        └─ return self._aggregate_simple(...)  shadow_kline_reader.py:98
               │           ├─ sqlite3.connect(shadow.db)       shadow_kline_reader.py:114 ← CONN #2 (REAL)
               │           ├─ cursor.execute(simple SELECT)    shadow_kline_reader.py:122
               │           ├─ rows = cursor.fetchall()         shadow_kline_reader.py:133
               │           ├─ conn.close()                     shadow_kline_reader.py:134
               │           └─ Python aggregation into buckets  shadow_kline_reader.py:142-186
               └─ for symbol in universe:                      structure_worker.py:100
                  └─ candles = await self._fetch_klines(symbol)  structure_worker.py:102
                     └─ same chain as above (1 → 2 connections per call)
```

---

## 3. Per-Tick Connection Inventory

**Universe size per tick:** `batch_size = 25` symbols.
**`_fetch_klines` calls per tick:** `1` (session_context) + `25` (analysis loop) = `26`.

**Per `_fetch_klines` call:**
- Always: 1 async `market_repo.get_klines` call (uses persistent aiosqlite — NOT a fresh connection).
- If trading.db data insufficient: fall through to `shadow_reader.get_klines` → **2 fresh sqlite3 connections** (lines 59 + 114).

**Trading.db hit rate:** trading.db has H1 data for ~30 of the ~126 universe coins (the active scanner universe that `kline_worker` writes). For a 25-coin batch, ~5-7 hit trading.db, ~18-20 fall through.

**Per-tick shadow.db connections (typical):** ~36-40 = `(18-20 fallbacks × 2 conns)` + `(1 session_context fallback × 2)` ≈ 38.
**Per-tick shadow.db connections (worst case, all 26 fall through):** `26 × 2 = 52`.

`CoinDiscovery` adds 1 connection every ~600 s (~once every 10 ticks). Negligible.

**Reference investigation `layer1_to_xray_complete_state.md` §2.11 confirms** the workers process holds NO persistent fd to shadow.db — only fresh per-call connections.

---

## 4. Trading.db (`MarketRepository`) Bottleneck Analysis

`MarketRepository.get_klines` uses the **persistent `DatabaseManager` connection** for trading.db (single aiosqlite + asyncio.Lock). It does NOT open per-call connections.

The reference investigation §9.7 shows `STRAT_PREFETCH_CRITICAL` lines with `db=3980ms h1_db=5050ms` — so trading.db reads ARE slow, BUT the slowness is on the SQL execution side (large table scans on `klines`/`trades` tables, see strategy_worker prefetch logic), not on connection-open overhead.

**Conclusion for this fix:** structure_worker's tick-time degradation is dominated by **shadow.db** (52 per-tick connections + sync blocking). Trading.db slowness is a SEPARATE concern relevant to strategy_worker (and listed as discovered issue D-3 below).

---

## 5. Consumers of `ShadowKlineReader.get_klines`

`grep -rn "shadow_kline_reader\|ShadowKlineReader\|_shadow_reader\.get_klines" /home/inshadaliqbal786/trading-intelligence-mcp/src/ /home/inshadaliqbal786/trading-intelligence-mcp/tests/`:

| File:line | Reference type |
|---|---|
| `src/analysis/structure/shadow_kline_reader.py:27` | class definition |
| `src/workers/manager.py:180` | import |
| `src/workers/manager.py:186` | construction (singleton) |
| `src/workers/manager.py:188` | service-container registration |
| `src/workers/manager.py:581` | `_EXPECTED_SERVICE_KEYS` string literal (manifest only) |
| `src/workers/manager.py:923` | injection into StructureWorker constructor |
| `src/workers/structure_worker.py:36` | docstring mention |
| `src/workers/structure_worker.py:47` | constructor parameter (`shadow_kline_reader=None`) |
| `src/workers/structure_worker.py:60` | stored as `self._shadow_reader` |
| `src/workers/structure_worker.py:201` | guard `if self._shadow_reader:` |
| `src/workers/structure_worker.py:203` | **the only `get_klines` call site** |
| `tests/` | none |

**Exactly one consumer of `ShadowKlineReader.get_klines`. Phase 3's signature change (sync→async) requires updating exactly one call site.**

---

## 6. Discovered Issues (DEFERRED per scope discipline)

Per the brief: "If during execution you discover other bugs, list them in your report — but do not fix them as part of this task. Stay focused on ShadowKlineReader."

- **D-1 — Duplicate session-context fetch.** `structure_worker.py:88` fetches `_fetch_klines(universe[0])` for session context. The analysis loop at line 100 then iterates over `universe`, including `universe[0]` again at line 102, triggering a second identical fetch. One redundant fetch per tick. Trivial follow-up: cache the first-symbol candles between line 88 and line 100, reuse when the loop reaches `universe[0]`.

- **D-2 — `CoinDiscovery` uses the same per-call sqlite3 pattern.** `src/analysis/structure/coin_discovery.py:60`. Called every 600 s (~0.1 calls per structure_worker tick) — NOT the bottleneck. Could share the same persistent connection or grow its own. Defer to a follow-up; consider unifying with `ShadowKlineReader` as a "Shadow DB read service."

- **D-3 — `MarketRepository.get_klines` is sequential per-symbol.** A `get_klines_batch` method already exists at `market_repo.py:180+`. `_fetch_klines` could call it once per tick instead of looping per symbol. Helps strategy_worker (large prefetch) more than structure_worker; orthogonal to this task.

---

## 7. Verification Gate (Phase 0 → Phase 1)

The brief requires the investigation answer five questions concretely before proceeding:

1. **How many sqlite3 connections does ShadowKlineReader open per call to `get_klines`?**
   → **2.** Lines 59 (windowed query — discarded result) and 114 (`_aggregate_simple` — actual data fetch).

2. **How many calls to `get_klines` happen per structure_worker tick?**
   → **Up to 26** (1 session context + 25 batch symbols). Typical: 18-20 reach `shadow_reader.get_klines` (others satisfied by trading.db). At 2 connections each, that's typically **36-40, worst case 52** shadow.db connections per 60-second tick.

3. **Is the windowed query result actually used anywhere, or is it truly discarded?**
   → **Truly discarded.** Variable `rows` (line 90) is referenced exactly twice: at the assignment and at `if not rows` (line 93). Never returned, never logged, never assigned to anything that escapes the function scope. Only the truthiness is used as a redundant early-exit gate before falling through to `_aggregate_simple` (which would also return `[]` for empty data).

4. **Is `MarketRepository` (trading.db reader) also using a per-call connection pattern?**
   → **No.** `MarketRepository` uses the **persistent** `DatabaseManager` connection (single aiosqlite + asyncio.Lock). Its slowness (when present, e.g. STRAT_PREFETCH_CRITICAL) is on SQL execution against large tables, not connection-open overhead. **Out of scope for this fix.**

5. **Is there any existing connection pool or persistent connection elsewhere in the codebase that could be a reference pattern?**
   → **Yes.** `src/database/connection.py::DatabaseManager` — single persistent `aiosqlite.Connection` per instance, protected by `asyncio.Lock`, idempotent `connect()/disconnect()`, lifecycle owned by `WorkerManager`. Phase 3 will mirror this pattern exactly (within `ShadowKlineReader`, with read-only PRAGMAs since shadow.db is owned by the Shadow process).

**Verification gate PASSED. Proceeding to Phase 1.**
