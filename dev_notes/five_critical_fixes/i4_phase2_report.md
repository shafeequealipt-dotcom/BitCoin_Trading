# Issue 4 Phase 2 Report — Partial-Close PnL Inflation (covers Issue 5)

# 1. Executive Summary

A 50 percent partial close fires correctly on Bybit but the system records it as a full close: trade_history qty 4430.2 (full entry) and pnl 5.32 USD (full notional), when the actual closed quantity was 2215.1 and the actual realized PnL was approximately 2.66 USD. The remainder becomes orphaned in coordinator state; the eventual full close 22 minutes later hits the COORD_DOUBLE_CLOSE silent-skip path and writes nothing — that is Issue 5 in entirety.

Single check is wrong: `bybit_demo_websocket_subscriber.py` line 346 gates on `leaves_qty == 0`, which is the order's residual qty, not the position's. For a reduceOnly partial market order, the order fills entirely (leaves_qty == 0) while the position remains open.

# 2. What The Reports Said vs Current Code

## 2.1 Report claim

"When the sniper does a 50% partial close, the trade_history row records the FULL entry qty (4430.2) and PnL computed on full notional ($5.32) when only HALF actually closed (2215.1, ~$2.66). The remainder is treated by sniper as a NEW position."

## 2.2 Verified

Today's FILUSDT trace at 09:49:07 produces exactly this. Confirmed by reading `bybit_demo_websocket_subscriber.py:317-409` and `trade_coordinator.on_trade_closed:639-850`. The partial-fill execution event dispatches to `coordinator.on_trade_closed`; the coordinator pops the full TradeState and runs the existing CRITICAL-1 back-derive on the full size; all 15 callbacks fan out (trade_history, trade_log, thesis_close, perf, TIAS) writing full-notional values.

Issue 5 is downstream: at 10:11:23 the residual closes, but the coordinator already popped the state at 09:49:07, so the silent-skip at `trade_coordinator.py:671` fires twice (once from WS, once from sniper M4 stall). No second `trade_log` row.

## 2.3 Coupling

Fix Issue 4 correctly and Issue 5 disappears at the same commit. Per the directive's stated order, Issue 4 ships first; Issue 5 verification confirms it.

# 3. Evidence

Full FILUSDT trace today (extracted from `data/logs/workers.log`):

```
09:35:36.482  BYBIT_DEMO_ORD_RESP  fill=1.1286 Filled (qty=4430.2 entry order)
09:35:36.490  COORD_REG            state.size=4430.2 entered coordinator
09:49:07.427  L4P_CHECK            close_reason='mode4_partial'
09:49:07.572  Mode4 PARTIAL FILUSDT 50% closed pnl=+0.12%
09:49:07.575  BYBIT_DEMO_WS_CLOSE_EVENT
                                   exec_qty=2215.1 closed_size=2215.1 leaves=0
09:49:07.577  COORD_PNL_BACK_DERIVED ent=1.1286 ext=1.1274 pnl_pct=+0.1063% win=Y
                                   (back-derived on full state.size=4430.2)
09:49:07.577  COORD_CLOSE_START    pnl$=+5.3162 cbs=15 — full-close fan-out fires
09:49:07.583  DL_TRADE             pnl$=+5.3162 (inflated)
09:49:13.446  ProfitSniper _on_position_opened FILUSDT — residual seen as NEW
                                   (no coordinator.register_trade)
10:11:23.510  COORD_DOUBLE_CLOSE   by=bybit_demo_sl_tp — silent-skip
10:11:23.668  COORD_DOUBLE_CLOSE   by=mode4_stall_valve — silent-skip
10:11:23.783  WD_CLOSE             corrupted (Issue 3 territory)
```

# 4. Where The Bug Lives

`src/bybit_demo/bybit_demo_websocket_subscriber.py:337-351`:

```python
if closed_size <= 0:
    log.debug(BYBIT_DEMO_WS_EXEC_NON_CLOSE); return
if leaves_qty > 0:
    log.info(BYBIT_DEMO_WS_EXEC_PARTIAL); return
# falls through to dispatch on_trade_closed
```

The intent of the `leaves_qty == 0` test is "is this fill closing the full position?" but the check measures the wrong thing — `leaves_qty` describes the ORDER's residual qty, not the POSITION's residual qty. For a `reduceOnly=True` market partial, the order fills fully and the position remains open.

Three solutions follow.

# 5. Solution Options

## 5.1 Option A — Side-channel partial flag (Recommended)

`reduce_position` in `bybit_demo_adapter.py:514-582` stamps a flag on the coordinator before sending the order: `coordinator.mark_partial_close_pending(symbol, partial_qty)`. The WS subscriber, before dispatching on_trade_closed, calls `coordinator.pop_partial_close_pending(symbol)`. If set, the event routes to a new `coordinator.on_partial_close(symbol, closed_qty, exec_price)` which:

- Writes a partial-close trade_history row with `qty=closed_qty` and PnL on `closed_qty × delta` (Shape A) OR
- Updates an in-memory state.realized_pnl_accumulated without writing a row (Shape B)
- Updates `state.size = state.size - closed_qty`
- Does NOT pop coordinator state
- Emits `MODE4_PARTIAL_RECORDED | sym=… closed_qty=… pnl_pct=… pnl_usd=…`

When the residual eventually closes (no flag set), the existing `on_trade_closed` path runs with the reduced state.size, writes the correct final row, pops state.

**Pros:** No reliance on Bybit's WS field semantics. Explicit signal. Backward-compatible (default flag absent falls back to existing behavior). Race-free (the flag is set BEFORE the order is sent; the WS event arrives AFTER).

**Cons:** New API on TradeCoordinator (`mark_partial_close_pending`, `pop_partial_close_pending`, `on_partial_close`). Two-row shape requires aggregating dashboards to adapt (Shape A); single-row shape requires careful aggregation logic (Shape B). 

## 5.2 Option B — WS subscriber consults position state

In `_handle_one_execution`, after the existing `leaves_qty == 0` check, query `position_service.get_position(symbol)`. If the position still has size > 0, the fill was a partial: emit `BYBIT_DEMO_WS_EXEC_PARTIAL_CLOSE`, update the coordinator's state.size, write a partial-close row similar to Option A's `on_partial_close`, return without full dispatch.

**Pros:** Self-contained in the WS subscriber. No new coordinator API. Works without modifying sniper.

**Cons:** Extra HTTP / DB hit on every close event (~50-200 ms — meaningful on the close path). Race-sensitive: Bybit's position state can lag tens of ms behind the execution event; a true full close that hasn't updated position yet could be misclassified as partial. Mitigation requires retry-with-timeout — more complexity.

## 5.3 Option C — Position-channel-driven close

Refactor: the `position` WS channel (`_handle_position`) becomes the authoritative "position closed" trigger. The `execution` channel only records fill details into a per-symbol cache. When `_handle_position` reports size=0, dispatch to `coordinator.on_trade_closed` with the cached fill price.

**Pros:** Architecturally clean. Bybit's position channel IS the source of truth for position state. Partial fills cannot misfire close logic regardless of order type or qty.

**Cons:** Largest refactor. Touches two channel handlers, the coordinator dispatch, and the test surface. Bybit does not strictly order execution before position events; ordering uncertainty introduces a window where a fill is recorded but the close trigger has not yet fired (potential for missed-close if the position event is dropped).

# 6. Recommendation

**Option A, with Shape A persistence (two rows per partial trade).**

Reasoning:

- Smallest behavior risk for the close-detection logic (no race on position-state queries).
- Explicit signaling: the system knows it sent a partial, doesn't have to infer it.
- TIAS learning quality is higher with one outcome per partial than with aggregated rows (more training signal per trade).
- Shape A's two-row pattern is a clean schema evolution — `trade_id` distinguishes via suffix (`bd-{oid}-partial-1`, `bd-{oid}-final`).

# 7. Open Questions

1. **Persistence shape.** Two rows per partial trade (Shape A) or single aggregate row (Shape B)? Shape A is cleanest for TIAS, but consumers (dashboard, /apex_last reports, performance enforcer) need to know whether to aggregate or display each row independently.
2. **Multiple partials per trade.** The current sniper only emits 50 % partials, but the design should support multiple in case future modes do scaled-out exits. Shape A naturally supports N rows; Shape B requires careful accumulator logic.
3. **trade_log size_usd field.** Observed today as 0.0 in all rows (the A4 audit-report concern, MEDIUM priority). Out of scope for Issue 4 but worth noting — the partial fix should at minimum set `size_usd = closed_qty * entry_price` on partial rows.
4. **Telemetry.** Should `MODE4_PARTIAL_RECORDED` fire a Telegram or Loguru alert, or stay log-only?

# 8. Phase 3 Implementation Plan (Conditional On Operator Approval)

If Option A + Shape A is chosen:

1. `feat(i4/phase3a)`: TradeCoordinator gains `_partial_close_pending` dict, `mark_partial_close_pending`, `pop_partial_close_pending`, `on_partial_close` methods. Plus state.size mutability under partial.
2. `feat(i4/phase3b)`: `bybit_demo_adapter.reduce_position` calls `coordinator.mark_partial_close_pending(symbol, qty)` before sending the order.
3. `feat(i4/phase3c)`: WS subscriber's `_dispatch_close` checks pending-partial flag and routes to `on_partial_close` instead of `on_trade_closed`. New log `BYBIT_DEMO_WS_PARTIAL_DISPATCH`.
4. `feat(i4/phase3d)`: `manager.py` close-callback fan-out adds `_partial_trade_history_callback` and `_partial_trade_log_callback` that write rows with the partial qty and computed PnL. Existing full-close callbacks unchanged.
5. `test(i4/phase3)`: unit tests covering: partial flag set → routed to on_partial_close; partial without flag → routed to on_trade_closed (legacy); state.size decremented correctly; residual final close uses reduced state.size for PnL.

Verification (Phase 4):

- Trigger a partial via sniper M4 (or operator-induced).
- Confirm trade_history row #1 has `qty=closed_qty`, `pnl` computed on partial notional.
- After residual closes, confirm trade_history row #2 has `qty=residual_qty`, `pnl` on residual notional.
- Confirm zero `COORD_DOUBLE_CLOSE` events for partial+full lifecycles.
- Confirm Issue 5's metric `trade_log row count == close event count` over 4-6 h.
- Shadow path unaffected (the reduce_position-side-channel mechanism is bybit_demo only).

# 9. Stop For Operator Decision

Awaiting operator's choice of option (A / B / C / variant), choice of persistence shape (A / B) if Option A is selected, and answers to open questions.
