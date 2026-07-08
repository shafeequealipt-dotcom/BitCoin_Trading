# B1 — PriceWorker

**Capture timestamp:** 2026-04-27T23:00:48Z

---

## B.1.1 — File location, size, last modified

- Path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/workers/price_worker.py`
- Size: 11,646 bytes
- Lines of code (`wc -l`): 264
- Last modified: 2026-04-27 20:40:06 UTC

## B.1.2 — Public methods (signatures + tick body)

Class declaration (line 26): `class PriceWorker(BaseWorker):` — `worker_tier = WorkerTier.LAYER1A` (line 41).

### `__init__` (line 43)
```
def __init__(
    self, settings: Settings, db: DatabaseManager,
    ws: BybitWebSocket, scanner=None,
) -> None:
    super().__init__(
        name="price_worker",
        interval_seconds=float(settings.workers.market_data_interval),
        settings=settings,
        db=db,
    )
    self.ws = ws
    self.market_repo = MarketRepository(db)
    self._scanner = scanner  # legacy injection; not read by tick()
    self._tracked_symbols: list[str] = list(settings.universe.watch_list)
    self._connected = False
    self._dropped_count: int = 0
    self._ws_quotes: dict[str, tuple[float, float]] = {}
    self._ws_msg_count: int = 0
    self._ws_health_last_emit: float = _time.monotonic()
```

### `tick()` (line 75) — full body verbatim
```python
async def tick(self) -> None:
    universe = list(self.settings.universe.watch_list)
    if not universe:
        log.warning(
            f"PRICE_UNIVERSE_EMPTY | reason=watch_list_empty | {ctx()}"
        )
        return

    if set(universe) != set(self._tracked_symbols):
        log.info(
            "PriceWorker: Updating symbols {old} -> {new}",
            old=len(self._tracked_symbols), new=len(universe),
        )
        self._tracked_symbols = universe
        if self._connected:
            self._connected = False

    if not self._connected:
        await self.ws.connect_public()
        self.ws.subscribe_ticker(self._tracked_symbols, self._handle_ticker_update)
        self._connected = True
        _sample = ",".join(self._tracked_symbols[:10])
        _suffix = "..." if len(self._tracked_symbols) > 10 else ""
        log.info(
            f"PRICE_WS_CONN | symbols={len(self._tracked_symbols)} "
            f"sample=[{_sample}{_suffix}] | {ctx()}"
        )
        log.info(
            "Price worker: WebSocket connected, subscribed to {n} symbols",
            n=len(self._tracked_symbols),
        )
    else:
        if not self.ws.is_running:
            log.warning(f"PRICE_WS_DISC | rsn=ws_not_running | {ctx()}")
            log.warning("Price worker: WebSocket disconnected, will reconnect")
            self._connected = False

    now_mono = _time.monotonic()
    elapsed_s = max(now_mono - self._ws_health_last_emit, 0.001)
    msgs_per_min = (self._ws_msg_count / elapsed_s) * 60.0
    log.info(
        f"PRICE_WS_HEALTH | "
        f"status={'connected' if self._connected and self.ws.is_running else 'disconnected'} "
        f"msgs_per_min={msgs_per_min:.0f} "
        f"msgs_in_window={self._ws_msg_count} "
        f"window_s={elapsed_s:.1f} "
        f"subscribed={len(self._tracked_symbols)} "
        f"quotes_cached={len(self._ws_quotes)} | {ctx()}"
    )
    self._ws_msg_count = 0
    self._ws_health_last_emit = now_mono
```

### Other public methods
- `_handle_ticker_update(self, data: dict) -> None` (line 161) — WS callback (not async). Validates payload, normalises with `_sf` safe-float helper, drops on `last_price <= 0` (logs `PRICE_SKIP_INVALID`), stores `(last_price, monotonic())` in `self._ws_quotes[symbol]`, increments `self._ws_msg_count`, builds `Ticker` dataclass, schedules `self.market_repo.save_ticker(ticker)` via `loop.create_task`. On exception: increments `_dropped_count`, logs `PRICE_WS_TICK_FAIL`.
- `get_ws_quote(self, symbol: str, max_age_s: float = 5.0) -> float | None` (line 239) — Public read accessor. Reads `self._ws_quotes[symbol]`; returns price if `monotonic() - ts <= max_age_s` and `price > 0`, else `None`.
- `cleanup(self) -> None` (line 260) — disconnects WS on stop.

## B.1.3 — What it READS

- WebSocket subscription set: `self._tracked_symbols`, seeded from `settings.universe.watch_list` and refreshed every tick (price_worker.py:88, :100). Subscribes via `self.ws.subscribe_ticker(symbols, self._handle_ticker_update)` at price_worker.py:111. The `BybitWebSocket.subscribe_ticker` is defined at `src/trading/websocket.py:88`; `connect_public` at `src/trading/websocket.py:45`.
- DB reads at startup: NONE (verified — no `db.fetch_*` or repo read calls in module).
- Config consumed:
  - `settings.workers.market_data_interval` → tick interval seconds (line 49). config.toml: `[workers] market_data_interval = 45`.
  - `settings.universe.watch_list` → 50-symbol list. config.toml: `[universe] watch_list = [...]` (50 entries; verified by inspection).

## B.1.4 — What it WRITES

In-memory caches:
- `self._ws_quotes: dict[str, tuple[float, float]]` (declared price_worker.py:66; written :196). Key = symbol (e.g. `BTCUSDT`); value = `(last_price: float, monotonic_ts: float)`.
- `self._ws_msg_count: int` (declared :72; written :200) — per-tick WS message counter, reset to 0 each tick (:158).
- `self._dropped_count: int` (declared :61; incremented in `_handle_ticker_update` exception branch :224).

DB tables:
- `ticker_cache` — written via `MarketRepository.save_ticker()` at price_worker.py:218 (`loop.create_task(self.market_repo.save_ticker(ticker))`). Schema:
  ```
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
  Insert SQL (market_repo.py:267):
  ```
  INSERT OR REPLACE INTO ticker_cache
  (symbol, last_price, bid, ask, high_24h, low_24h, volume_24h, change_24h_pct, updated_at)
  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  ```

## B.1.5 — Cadence

- `tick()` fires on `BaseWorker` fixed interval. interval_seconds = `settings.workers.market_data_interval` = 45 s (config.toml:`[workers] market_data_interval = 45`).
- WS messages: continuous push from Bybit (no polling). Live measurement: msgs_per_min = 4,482 - 7,148 (range across the last 25 health heartbeats), msgs_in_window 3,362 - 5,361 over 45 s. So ~80–120 msg/s aggregate from 50 subscribed tickers.
- Cache (`_ws_quotes`) update: per WS message (price_worker.py:196). Write rate ≈ msgs/s above.

## B.1.6 — Live measurements

PRICE_WS_HEALTH events (last 5 verbatim from `data/logs/workers.log`):
```
2026-04-27 22:55:55.968 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=5456 msgs_in_window=4092 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:56:40.970 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6224 msgs_in_window=4668 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:57:25.972 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=6258 msgs_in_window=4694 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:58:10.974 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=5204 msgs_in_window=3903 window_s=45.0 subscribed=50 quotes_cached=50
2026-04-27 22:58:55.977 | INFO | PRICE_WS_HEALTH | status=connected msgs_per_min=4482 msgs_in_window=3362 window_s=45.0 subscribed=50 quotes_cached=50
```

PRICE_WS_TICK_* events (per spec request "Last 20 PRICE_WS_TICK_*"):
- `PRICE_WS_TICK_FAIL`: NOT FOUND in `data/logs/workers.log` and `workers.2026-04-27_01-31-00_169356.log` over the available retained window (grep count = 0).
- The `_handle_ticker_update` path emits ONLY DEBUG line "Price update: {s} = {p}" on success (price_worker.py:222) and `PRICE_WS_TICK_FAIL` on exception. With log_level=INFO (config.toml:[general] log_level = "INFO") successes are not retained. So no per-tick events are observable; observability is limited to the 45-s `PRICE_WS_HEALTH` heartbeats above.

WORKER_TICK_DONE events for price_worker — emitted as `LAYER1A_TICK_DONE | sub=price_worker`. Last 5:
```
2026-04-27 22:55:55.968 | LAYER1A_TICK_DONE | sub=price_worker elapsed_ms=0 interval_s=45.0
2026-04-27 22:56:40.971 | LAYER1A_TICK_DONE | sub=price_worker elapsed_ms=0 interval_s=45.0
2026-04-27 22:57:25.972 | LAYER1A_TICK_DONE | sub=price_worker elapsed_ms=0 interval_s=45.0
2026-04-27 22:58:10.975 | LAYER1A_TICK_DONE | sub=price_worker elapsed_ms=0 interval_s=45.0
2026-04-27 22:58:55.977 | LAYER1A_TICK_DONE | sub=price_worker elapsed_ms=0 interval_s=45.0
```
Note: `elapsed_ms` was 576 ms only on the `WORKER_FIRST_TICK` connect event at 22:53:40.962.

Current `_ws_quotes` size: 50 (verified from PRICE_WS_HEALTH `quotes_cached=50` repeatedly across all heartbeats since 22:54:25).

Current message rate: 4,482 - 7,148 msgs/min (window of last 25 heartbeats); median ≈ 5,500.

`WORKER_FIRST_TICK` for price_worker: `2026-04-27 22:53:40.962 | name=price_worker el_to_first_tick_ms=576 first_tick_el_ms=576`.

## B.1.7 — Failure modes (last 24h)

Available log window: `data/logs/workers.log` covers 2026-04-27 22:10 to 22:59 plus `workers.2026-04-27_01-31-00_169356.log` (older session). Search across both files:

| Tag | Count | File:line of emitter |
|-----|------:|----------------------|
| `PRICE_WS_TICK_FAIL` | 0 | price_worker.py:228 |
| `PRICE_WS_DISC` | 0 | price_worker.py:135 |
| `PRICE_SKIP_INVALID` | 0 | price_worker.py:190 (DEBUG level, suppressed under INFO) |
| `PRICE_UNIVERSE_EMPTY` | 0 | price_worker.py:91 |

`PRICE_WS_CONN` (one-shot connect events):
```
2026-04-27 22:53:40.962 | PRICE_WS_CONN | symbols=50 sample=[BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOGEUSDT,AVAXUSDT,LINKUSDT,ARBUSDT...] | no_ctx
```
GAP: with PRICE_SKIP_INVALID logged at DEBUG, any silent zero-price drops cannot be enumerated under the current INFO log level.

## B.1.8 — Dependencies (consumers)

- `src/apex/assembler.py:147–148` — `if price_worker and hasattr(price_worker, "get_ws_quote"): q = price_worker.get_ws_quote(symbol, max_age_s=5.0)`. APEX assembler reads the live WS quote with a 5 s freshness tolerance.
- `ticker_cache` table consumers (DB reads):
  - `src/database/repositories/market_repo.py:294` — `MarketRepository.get_ticker(symbol)` — `SELECT * FROM ticker_cache WHERE symbol = ?`.
  - `src/intelligence/sentiment/aggregator.py:165–166` — `SELECT change_24h_pct FROM ticker_cache WHERE symbol = ?` (used in the no-data SENT branch to log `change_24h`).
- The legacy `_scanner` injection (price_worker.py:55) is documented as not read by `tick()`; it remains for backward-compat.
