# T3-2 + T3-3 + T3-4 — Combined investigation + proposal

Three Phase 5 close-side defects share root cause in `BybitDemoAdapter` and can be fixed in two surgical edits:

## T3-3 + T3-4 — `close_trigger` mislabel + COORD_DOUBLE_CLOSE race

**Root cause**: `close_position` calls `self._record_close_trigger(symbol, close_trigger)` (stashes for `get_last_close`) but does NOT call `self._coordinator.set_close_reason(symbol, close_trigger)`. When the WS exec event arrives, `pop_close_reason` finds nothing and returns the mode-aware default `bybit_demo_sl_tp` — losing the real trigger (sniper, watchdog, time_decay, callb).

**Live evidence today**:
- 13 `closed_by=bybit_demo_sl_tp` events (fallback fires).
- 9 `COORD_DOUBLE_CLOSE` events (the race the report cited: WS dispatch records `bybit_demo_sl_tp` first; the watchdog's redundant `on_trade_closed` with the correct trigger lands second and is dropped as duplicate).

**Fix**: in `close_position` (adapter.py:~360), right after `_record_close_trigger`, add:
```python
if self._coordinator is not None and hasattr(self._coordinator, "set_close_reason"):
    try:
        self._coordinator.set_close_reason(symbol, close_trigger)
    except Exception as _e:
        self._log.warning(
            f"COORD_SET_CLOSE_REASON_FAIL | sym={symbol} "
            f"trigger={close_trigger} err='{str(_e)[:120]}' | {ctx()}"
        )
```

After fix: the WS subscriber's `pop_close_reason` returns the real trigger. The watchdog's redundant `on_trade_closed` becomes a clean dedup at `COORD_DOUBLE_CLOSE` — but with the correct attribution already recorded.

**`reduce_position` follow-up**: `reduce_position` does not yet accept `close_trigger`. Adding it requires updating the caller (sniper). Out of scope for T3-3 minimal fix; tracked as follow-up.

## T3-2 — orders blank-PK INSERT OR REPLACE loss

**Root cause**: `_build_close_order` (adapter.py:1535-1547) hardcodes `order_id=""`. Every close `save_order(close_order)` UPSERT clobbers the prior blank-PK row. 23 of 24 close rows lost today.

**Fix**: `_build_close_order` accepts an optional `order_id` parameter with non-empty fallback:

```python
def _build_close_order(
    symbol: str, side: Side, qty: float, exit_price: float,
    order_id: str = "",
) -> Order:
    if not order_id:
        # Fallback when the adapter could not resolve the Bybit orderId
        # (rare — see CLOSE_FILL_FALLBACK branches). Deterministic format
        # so audit pivots can recognise the synthetic origin.
        order_id = f"bd-close-{symbol}-{int(time.time() * 1000)}"
    return Order(order_id=order_id, ...)
```

Update the two call sites:
- `close_position` at line ~461: pass `order_id=order_id` (already captured at line 412 from envelope).
- `reduce_position` at line ~634: capture the envelope's `orderId` (current code discards it) and pass it through.

## Aim preservation

Both fixes are observability + audit-correctness. No trade-decision logic changes. Aggressive-exploitation philosophy preserved entirely.

## Operator decision

Both fixes are mechanical and the report's own recommendations. Default approve unless operator wants a different format for the synthetic order_id.
