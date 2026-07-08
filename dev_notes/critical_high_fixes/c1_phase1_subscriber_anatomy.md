# CRITICAL-1 Phase 1 — WS Subscriber Anatomy

## Purpose

Document `src/bybit_demo/bybit_demo_websocket_subscriber.py` (502 lines) end-to-end. Identify exactly what the close-event dispatch passes to the coordinator and what fields it leaves at zero.

## File summary

- 502 lines. Public class `BybitDemoWebSocketSubscriber`.
- Owns the bybit_demo private-WS subscription and the close-event dispatch into `TradeCoordinator.on_trade_closed`.
- Constructed by `BybitDemoWSWorker` (`src/workers/bybit_demo_ws_worker.py`).

## Dispatch chain (close path only)

```
_handle_execution(message)                         line 225
  → _handle_one_execution(fill)                    line 317
    → _is_duplicate_close(symbol, order_id)        line 409
    → _dispatch_close(...)                         line 428
      → _call_coordinator_close(...) [coroutine]   line 463
        → coordinator.on_trade_closed(...)         line 489
```

## What the dispatch populates (per `_call_coordinator_close`, lines 463-502)

The coroutine receives five named arguments from `_dispatch_close`:

| Arg | Source | Notes |
|---|---|---|
| `symbol` | `fill.get("symbol")` | string from WS fill payload |
| `exit_price` | `float(fill.get("execPrice"))` | Bybit's authoritative fill price |
| `closed_by` | `_handle_one_execution:362-381` | One of `"bybit_sl_hit"`, `"bybit_tp_hit"`, the `coordinator.pop_close_reason(symbol)` value, or fallback `"bybit_external"` |
| `exec_fee` | `float(fill.get("execFee"))` | Captured but NOT yet threaded into coordinator (P3 deferred) |
| `order_id` | `fill.get("orderId")` | Used for dedup and logging |

## What the coordinator call passes (line 489-497)

```python
self._coordinator.on_trade_closed(
    symbol=symbol,
    pnl_pct=0.0,        # back-derived by coordinator from state + exit_price
    pnl_usd=0.0,        # back-derived by coordinator
    was_win=False,      # back-derived in coordinator from state.entry_price + exit_price
    closed_by=closed_by,
    exit_price=exit_price,
    price_source="bybit_ws_authoritative",
)
```

The three "back-derived" comments at lines 491-493 form the contract assumption that this fix series invalidates.

## Comment-vs-reality gap

The subscriber's docstrings explicitly state coordinator back-derives:

- Line 390-400 (in `_handle_one_execution`): "the coordinator computes pnl_pct from state.entry_price + exec_price, and pnl_usd from state.size + entry_price + pnl_pct (existing logic at trade_coordinator.py lines 612-638)"
- Line 474-481 (in `_call_coordinator_close`): "the coordinator back-derives pnl_pct from state.entry_price + exit_price and pnl_usd from state.size + entry_price + pnl_pct"

The audit's reference to "lines 612-638" is stale. The actual current back-derive logic is at `trade_coordinator.py:684-707`. **It only back-derives close_price (already covered by the new exit_price kwarg) and pnl_usd. It NEVER back-derives pnl_pct.** The subscriber's contract assumption is false in the present code.

## Idempotency layers (informational)

Subscriber implements L1 dedup only:
- L1: `_processed_closes` TTL dedup keyed by `(symbol, orderId)` for 5 seconds (lines 41, 102, 409-426)
- L2: TradeCoordinator atomic `_trades.pop(symbol, None)` first-writer-wins (lines 666-675)
- L3: PositionWatchdog `is_symbol_cooled_down` skips poll-side processing of WS-handled closes

The subscriber does NOT touch `trade_history`, `trade_intelligence`, or `trade_thesis`. All persistence happens in coordinator-registered close callbacks.

## Other dispatch handlers (informational)

- `_handle_position` (line 246): logs `BYBIT_DEMO_WS_POS_FLAT` when size=0; does NOT call coordinator.
- `_handle_order` (line 272): logs `BYBIT_DEMO_WS_ORDER` for Filled/Cancelled/Rejected; does NOT call coordinator.

The execution-stream handler is the canonical close source.

## Findings

1. The subscriber never computes pnl_pct. It explicitly delegates to the coordinator with two layers of comments asserting the coordinator back-derives.
2. The subscriber does pass `exit_price` correctly (Bybit's authoritative `execPrice` from the fill).
3. The subscriber does pass `closed_by` correctly (one of the four documented strings).
4. The subscriber's `was_win=False` is hardcoded — even after pnl_pct fix, downstream consumers reading `was_win` (e.g., `tias/collector.py:149`) will see False unless the coordinator flips it from the back-derived pnl_pct.
5. Line 478-481 has a deferred TODO: "exec_fee is not yet threaded through (P3 will widen the coordinator signature to accept fee-inclusive net PnL)". Out of scope for CRITICAL-1, but the fee data is captured at the WS boundary and would be needed for fee-inclusive PnL.

## Implication for the fix

If the coordinator gets a back-derive branch for pnl_pct (computed from `state.entry_price`, `exit_price`, `state.side`), the subscriber needs no code change for CRITICAL-1. The contract becomes truthful: subscriber passes `pnl_pct=0` as a sentinel meaning "compute it"; coordinator computes it.

Alternative: change subscriber to compute pnl_pct itself (it has `exit_price` and could fetch `state.entry_price` via `coordinator._trades[symbol]`). This couples subscriber to coordinator state, which the existing design explicitly avoided.

The choice is a Phase 2 decision; the subscriber's anatomy is fully documented for either path.
