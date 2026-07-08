# P1 Phase 1 — existing public WebSocket usage pattern

## File scope

`src/workers/price_worker.py` — the only consumer of `BybitWebSocket` today.

## Connection lifecycle

```python
# Lines 144-163, inside PriceWorker.tick():
if not self._connected:
    await self.ws.connect_public()
    self.ws.subscribe_ticker(self._tracked_symbols, self._handle_ticker_update)
    self._connected = True
    log.info(f"PRICE_WS_CONN | symbols={len(self._tracked_symbols)} sample=[...] | {ctx()}")
else:
    if not self.ws.is_running:
        log.warning(f"PRICE_WS_DISC | rsn=ws_not_running | {ctx()}")
        self._connected = False  # next tick will reconnect
```

Pattern: **connect-on-first-tick + per-tick health check**. When `ws.is_running` flips False, `_connected` is reset and the next tick reconnects + re-subscribes. Universe changes (new active set) trigger the same path — `set(universe) != set(self._tracked_symbols)` flips `_connected = False` (lines 130-142). No incremental subscribe / unsubscribe; pybit has no unsubscribe primitive, so full reconnect is the only mechanism.

## Callback signature

```python
# Lines 204-218
def _handle_ticker_update(self, data: dict) -> None:
    """Process incoming ticker data from WebSocket callback.

    Args:
        data: Raw WebSocket message.
    """
    try:
        tick_data = data.get("data", data)
        if isinstance(tick_data, list):
            tick_data = tick_data[0] if tick_data else {}
        symbol = tick_data.get("symbol", "")
        if not symbol:
            return
        # ... parse fields, persist to ticker_cache via self._ws_quotes ...
    except Exception:
        pass
```

Key conventions:
1. `data.get("data", data)` — pybit V5 wraps real payload in `{"topic": ..., "data": [...], "ts": ...}`. The handler tolerates both wrapped and raw shapes.
2. `if isinstance(tick_data, list): tick_data = tick_data[0] if tick_data else {}` — V5 streams emit list of objects; ticker uses len-1 list, but position/execution may emit multi-element lists (one per affected position/fill).
3. `try/except` swallow at the top level — pybit runs callbacks in its own thread. An uncaught exception would kill the WebSocket. The wrapper at `BybitWebSocket._wrap_callback` already does this; the inner `try/except` here is belt-and-braces.
4. **Async work from sync callback** — see lines 286-289 (referenced by audit but not in current excerpt): the callback uses `asyncio.run_coroutine_threadsafe(self._loop, coro)` to dispatch DB writes from the pybit thread back into the main asyncio event loop. This pattern is **mandatory** for P1's WS subscriber when calling the (async) coordinator path.

## Health observability

```python
# Lines 174-202
log.info(
    f"PRICE_WS_HEALTH | "
    f"status={'connected' if self._connected and self.ws.is_running else 'disconnected'} "
    f"msgs_per_min={msgs_per_min:.0f} "
    f"msgs_in_window={self._ws_msg_count} "
    f"persist_fails_in_window={self._ws_persist_fail_count} "
    f"window_s={elapsed_s:.1f} "
    f"subscribed={len(self._tracked_symbols)} "
    f"quotes_cached={len(self._ws_quotes)} | {ctx()}"
)
```

Per-tick (45s default) health log: status + throughput (msgs/min) + persist-failure count + window length + subscribed-symbols count + cache-size. P1's WS subscriber should mirror this pattern with `BYBIT_DEMO_WS_HEALTH | msgs_per_min=... topic_msgs={...} persist_fails=... last_event_age_s=... subscribed=position,execution | ctx()`.

## What P1 reuses from this pattern

| Reusable | Notes |
|----------|-------|
| Connect-on-first-tick + health-check loop | Same shape: P1 subscriber's `tick()` (or boot-once init) reads `_connected` + `ws.is_running` |
| Universe-change → reconnect | Not applicable — private subscription is account-wide, no universe; just static `position`/`execution`/`order` topics |
| `data.get("data", data)` + list normalisation in callback | Same |
| `asyncio.run_coroutine_threadsafe` for DB / coordinator dispatch | Mandatory — `coordinator.on_trade_closed` is sync but downstream callbacks are async (e.g., `_data_lake_close_callback`) |
| `try/except` swallow in callback | Mandatory — pybit thread isolation |
| Per-tick health log | Mirror with `BYBIT_DEMO_WS_HEALTH` tag |
| `PRICE_WS_CONN` / `PRICE_WS_DISC` event tags | Mirror with `BYBIT_DEMO_WS_CONN` / `BYBIT_DEMO_WS_DISC` |

## What P1 must NOT copy

- The PriceWorker is a `BaseWorker` subclass with periodic `tick()` (default 45s). The private subscriber is **not** tick-driven — it should boot once, stay connected, and emit when the WS connection drops (caught by a separate health-check tick at lower frequency, e.g., 30s, similar to PriceWorker's pattern but with no per-tick subscribe re-issue). Or wire as a non-`BaseWorker` class registered to lifecycle hooks. P1 Phase 2 chooses.
