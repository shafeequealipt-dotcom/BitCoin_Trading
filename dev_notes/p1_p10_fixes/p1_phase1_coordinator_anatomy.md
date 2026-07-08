# P1 Phase 1 — TradeCoordinator anatomy + on_trade_closed flow

## File scope

`src/core/trade_coordinator.py` — `TradeCoordinator` class. The single fan-out point for trade-close events.

## on_trade_closed signature

```python
# Lines 578-587
def on_trade_closed(
    self,
    symbol: str,
    pnl_pct: float,
    pnl_usd: float,
    was_win: bool,
    closed_by: str = "watchdog",
    exit_price: float | None = None,
    price_source: str | None = None,
) -> None:
```

| Param | Required | Used for |
|-------|----------|----------|
| `symbol` | yes | trade lookup + cooldown key |
| `pnl_pct` | yes | TIAS / data lake / strategist lessons |
| `pnl_usd` | yes (computed if 0) | enforcer / fund manager / data lake |
| `was_win` | yes | enforcer accumulators / strategist outcome bucket |
| `closed_by` | default `"watchdog"` | recorded in close record + drives cooldown duration (180/600/900s) |
| `exit_price` | optional | preferred over back-derivation; recorded as `close_price` in callback record |
| `price_source` | optional | provenance label for TIAS audit (`"shadow_authoritative"` / `"ticker_fallback"` / `"derived"`); the new P1 source becomes `"bybit_ws_authoritative"` |

## Idempotency model (lines 597-606)

```python
state = self._trades.pop(symbol, None)
if state is None:
    log.warning(
        f"COORD_DOUBLE_CLOSE | sym={symbol} by={closed_by} | "
        f"already closed — skipping duplicate | {ctx()}"
    )
    return
```

**Two-layer idempotency:**

1. **First writer wins.** `self._trades.pop(symbol, None)` removes the trade state. The first caller gets the state; subsequent callers get `None` and emit `COORD_DOUBLE_CLOSE`. This is atomic in Python (dict.pop is GIL-protected).

2. **Cooldown gate (downstream).** After successful close, `self._symbol_cooldowns[symbol] = time.time() + cooldown_sec` (lines 717-724). Other components (e.g., `position_watchdog.py:2936`) check `coordinator.is_symbol_cooled_down(symbol)` BEFORE attempting a close. This prevents race-induced double-close attempts upstream.

For P1, this means **the WS handler can call `on_trade_closed` directly without project-side dedup**. If the poll-detection later finds the same close, the cooldown gate at `position_watchdog.py:2936` skips the duplicate (already verified — emits `WD_SKIP_CLOSE | rsn=already_processed_by_coordinator`).

The COORD_DOUBLE_CLOSE warning is the safety net if both paths somehow land in the coordinator's fan-out. It logs a WARNING and returns cleanly.

## Callback fan-out (lines 707-715)

```python
for i, callback in enumerate(self._callbacks_on_close):
    try:
        callback(record)
        cb_name = getattr(callback, "__name__", str(callback)[:50])
        log.debug(f"COORD_CB_OK | #{i+1} {cb_name} sym={symbol} | {ctx()}")
    except Exception as e:
        cb_name = getattr(callback, "__name__", str(callback)[:50])
        log.error(f"COORD_CB_FAIL | #{i+1} {cb_name} sym={symbol} err='{str(e)[:500]}' | {ctx()}")
        log.error("Close callback failed: {err}", err=str(e))
```

Per-callback try/except — a single failing callback does NOT abort the chain. `COORD_CB_FAIL` emits with index, callback name, error. (Baseline shows 0 `COORD_CB_FAIL` over 24-36h — chain healthy.)

## All 14 close callbacks (registered in workers/manager.py)

| # | Line | Callback | What it does |
|---|------|----------|--------------|
| 1 | 547 | `_close_to_telegram_alert_callback` | Telegram alert on close |
| 2 | 1640 | `_enforcer_close_callback` | Performance Enforcer state update |
| 3 | 1664 | `_fund_close_callback` | Fund Manager state update |
| 4 | 1682 | `_perf_close_callback` | Strategy performance recorder |
| 5 | 1695 | `_registry_callback` | Strategy registry stats |
| 6 | 1713 | `_pnl_close_callback` | PnL Manager + capital tier update |
| 7 | 1744 | `_thesis_close_callback` | thesis_manager.close_thesis (P5 root cause) |
| 8 | 1775 | `_data_lake_close_callback` | data_lake.write_trade (P8 root cause) |
| 9 | 1799 | `_sniper_unsubscribe_on_close` | ProfitSniper cleanup |
| 10 | 1816 | `_event_buffer_clear_on_close` | Event buffer purge |
| 11 | 1833 | `_transformer_cache_clear_on_close` | Transformer position cache invalidation |
| 12 | 1850 | `_strategist_position_invalidate_on_close` | Brain prompt-cache invalidation |
| 13 | 1853 | `_learning_log_callback` | Learning log entry |
| 14 | 1982 | `_tias_close_callback` | TIAS recorder |

Total: **14 callbacks** confirmed. Audit's count is accurate.

## Pre-fan-out work (lines 597-705)

Every call to `on_trade_closed` does:

1. Atomic pop of `_trades[symbol]` → state
2. Compute hold_seconds from state.opened_at
3. Resolve close_price: prefer `exit_price` arg → back-derive from pnl_pct + entry_price
4. Compute pnl_usd if 0: prefer state.size * entry_price → fall back to amount_usd * leverage
5. Build the close `record` dict (50+ fields including APEX optimization data)
6. Append to `_closed_trades` ring buffer (max 100)
7. Emit `COORD_CLOSE_START` log
8. Pop `_last_brain_context[symbol]`, `_trade_plans[symbol]`, `_trade_info[symbol]`
9. Run callback fan-out (14 callbacks)
10. Set cooldown
11. Emit `COORD_CLOSE_END` log

For P1 WS handler, the call is just `coordinator.on_trade_closed(symbol, pnl_pct, pnl_usd, was_win, closed_by, exit_price, price_source)`. All fan-out + cooldown is handled.

## What P1 must wire into on_trade_closed

The WS execution stream gives:
- `symbol` (direct)
- `execPrice` → `exit_price`
- `closedSize`, `execQty`, `side`, `execFee` → use to compute `pnl_pct`, `pnl_usd`
- `closedPnl` (when closing a position) → use directly for `pnl_usd` (Bybit's authoritative post-fee value)
- `orderId` → cross-reference with state.order_id (already in coordinator state)
- `execType` (e.g., "Trade", "AdlTrade", "Funding", "BustTrade", "Settle") → maps to `closed_by`

Mapping rules (P1 implementation will codify):
- If `execType == "Trade"` and `closedSize > 0` → standard SL/TP/manual close. `closed_by` resolved from coordinator's pending close-reason (which already supports `pop_close_reason` for system-initiated closes) or fall back to `"bybit_sl_tp"` (mode-aware per P2 — but P2 ships before P3, after P1, so P1 just needs to NOT hardcode "shadow_sl_tp")
- `was_win = closedPnl > 0` (or `pnl_pct > 0`)
- `price_source = "bybit_ws_authoritative"` (new label)

## What P1 must NOT do

- Do NOT call `_trades.pop` directly — must go through `on_trade_closed` so the cooldown sets.
- Do NOT bypass the callback fan-out — the 14 callbacks are the entire close-side data pipeline.
- Do NOT change `on_trade_closed` signature — adding kwargs is OK, removing or repurposing is not (other callers exist).
- Do NOT call `on_trade_closed` from inside the pybit thread — must dispatch back to the asyncio loop via `asyncio.run_coroutine_threadsafe` (because some downstream callbacks issue async DB writes that need the project's event loop).

Wait: `on_trade_closed` is **sync** (`def`, not `async def`). The 14 callbacks are sync. But several callbacks fire-and-forget async work via `asyncio.create_task` from inside their sync handlers (e.g., `_thesis_close_callback` calls `db.execute` via `asyncio.run`). This relies on a running event loop in the calling thread. From the pybit thread, `asyncio.create_task` would fail (no running loop).

**Therefore: P1 WS handler MUST dispatch the `on_trade_closed` call into the project's event loop.** Pattern (from `price_worker.py`):
```python
def _ws_callback(message):  # runs in pybit thread
    asyncio.run_coroutine_threadsafe(
        self._dispatch_close_async(message),
        self._loop,
    )

async def _dispatch_close_async(self, message):  # runs in project loop
    # ... parse message, call coordinator.on_trade_closed(...) ...
```

This is the same pattern PriceWorker uses for its DB writes; P1 mirrors it.
