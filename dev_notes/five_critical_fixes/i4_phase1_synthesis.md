# Issue 4 Phase 1 — Partial-close PnL Inflation: Root-Cause Investigation

## WHERE the inflation enters

Today's FILUSDT trade (09:35:36 → eventual full close at 10:11:23) is the canonical case. Full event sequence reconstructed from `data/logs/workers.log`:

```
09:35:36.264  BYBIT_DEMO_ORDER_RECEIVED  | FILUSDT side=Sell qty=4430.2
09:35:36.338  BYBIT_DEMO_ORD_SEND        | FILUSDT side=Sell qty=4430.2 sl=1.1388 tp=1.0950
09:35:36.482  BYBIT_DEMO_ORD_RESP        | FILUSDT oid=e2252729-… fill=1.1286 Filled
09:35:36.490  COORD_REG                  | FILUSDT order_id=e2252729 (state.size=4430.2)
09:35:36.514  THESIS_OPEN                | id=2185 sym=FILUSDT dir=Sell ent=1.1286

[13.5 minutes of M4_DECISION action=tighten/hold; FILUSDT hovers around +0.2% PnL]

09:49:07.427  L4P_CHECK                  | FILUSDT close_reason='mode4_partial'
09:49:07.572  Mode4 PARTIAL FILUSDT      | 50% closed, pnl=+0.12% score=62.2
09:49:07.573  M4_ACT_PARTIAL             | FILUSDT pct=50% src=score pnl=+0.12%
09:49:07.575  BYBIT_DEMO_WS_CLOSE_EVENT  | FILUSDT oid=9244a879 side=Buy exec_price=1.1274
                                           exec_qty=2215.1 closed_size=2215.1
                                           closed_by=bybit_demo_sl_tp     ← misattributed
09:49:07.577  COORD_PNL_BACK_DERIVED     | FILUSDT pnl_pct=+0.1063% win=Y
09:49:07.577  COORD_CLOSE_START          | FILUSDT pnl$=+5.3162 held=811s ent=1.1286 ext=1.1274
                                           cbs=15        ← treated as full close
09:49:07.578  ProfitSniper closed FILUSDT (buffer retained 176 points for counterfactual)
09:49:07.583  DL_TRADE                   | tid=t-FILUSDT-1778492947 pnl=+0.1063% pnl$=+5.3162
                                           rsn=bybit_demo_sl_tp                ← inflated
09:49:07.594  THESIS_CLOSE               | FILUSDT pnl$=+5.3162 rsn=bybit_demo_sl_tp
09:49:07.604  Strategy perf updated      | claude_trader/FILUSDT WIN +0.11%   ← inflated
09:49:07.620  TIAS_SAVE                  | id=1406 FILUSDT pnl=+0.11% WIN regime=trending_down

09:49:13.446  ProfitSniper _on_position_opened | FILUSDT — sees residual as NEW position

[residual continues for ~22 minutes as ghost — Issue 2 territory]

10:11:23.334  SNIPER_STALL_ESCAPE        | FILUSDT escalated_to=full_close
10:11:23.361  BYBIT_DEMO_POSITION_CLOSE  | FILUSDT close_trigger=mode4_stall_valve
10:11:23.507  BYBIT_DEMO_WS_CLOSE_EVENT  | FILUSDT exec_qty=2215.1 closed_size=2215.1
10:11:23.510  COORD_DOUBLE_CLOSE         | FILUSDT — already closed, skipping  ← Issue 5
10:11:23.668  COORD_DOUBLE_CLOSE         | FILUSDT — already closed, skipping
10:11:23.782  WD_CLOSE_PRICE_FALLBACK    | FILUSDT — Issue 3 territory
10:11:23.783  WD_CLOSE corrupted         | FILUSDT pnl$=0 ent=$0 dir=
```

## The bug — pinned to a single check

`src/bybit_demo/bybit_demo_websocket_subscriber.py:337-351`:

```python
# Only fully-flatting closes trigger on_trade_closed. Partial
# fills (leaves_qty > 0) leave the position open; the next
# execution event will arrive when the rest fills.
if closed_size <= 0:
    log.debug(...); return
if leaves_qty > 0:
    log.info(BYBIT_DEMO_WS_EXEC_PARTIAL ...); return
# falls through to dispatch on_trade_closed
```

The comment's intent is correct. The check is wrong. For a `reduceOnly=True` market order with `qty < position_size`:
- Bybit fills the order in one shot
- `closedSize` = order qty (e.g. 2215.1)
- `leavesQty` = 0 (the ORDER is fully filled)
- Position size after fill = `original_pos_size - closedSize > 0`

The `leaves_qty == 0` test is satisfied, so the partial fill passes through to `_dispatch_close → coordinator.on_trade_closed` as if it were a full position flat. The coordinator pops the TradeState (which still has the FULL entry qty 4430.2), runs `COORD_PNL_BACK_DERIVED` against `state.entry_price × state.size`, fans out to all 15 callbacks.

Two distinct data corruptions then occur:

### Corruption 1 — qty / pnl_usd inflated to full notional

In `src/workers/manager.py:2040-2042` (the `_trade_history_close_callback`):

```python
qty = float(record.get("size", 0.0) or 0.0)        # ← state.size = 4430.2 (FULL)
pnl_usd = float(record.get("pnl_usd", 0.0) or 0.0)  # ← back-derived on 4430.2 notional
```

Trade_history row written with `qty=4430.2, pnl=$5.32` for a close that actually moved only 2215.1 at $0.0012 delta. Actual realized PnL was approximately $2.66, not $5.32.

trade_log (`src/core/data_lake.py:write_trade`) gets the same inflated PnL via `pnl_usd=+5.3162`.

### Corruption 2 — close_by misattributed

The WS execution event has `stopOrderType=""` (it was a system reduceOnly market order, not an SL/TP trigger). Lines 367-381:

```python
if stop_order_type in ("StopLoss", "Stop"):
    closed_by = "bybit_sl_hit"
elif stop_order_type == "TakeProfit":
    closed_by = "bybit_tp_hit"
else:
    pop_reason = self._coordinator.pop_close_reason(symbol)
    closed_by = pop_reason or "bybit_external"
```

The coordinator's `pop_close_reason` was supposed to hold "mode4_partial" — set by sniper's `_execute_partial_close` somewhere. But the close_by ended up as `bybit_demo_sl_tp` in the actual event. That came from elsewhere; need to confirm. The label is wrong but the value matches the suffix of `pop_close_reason`'s actual content: "bybit_demo_sl_tp" probably leaked from a stale `set_close_reason` left by a previous trade.

### Corruption 3 — residual becomes orphaned (Issue 5 root)

After the spurious close at 09:49:07, the coordinator state is popped (`trade_coordinator.py:666`). The position on Bybit still has 2215.1 open. Sniper notices at 09:49:13 via `_on_position_opened` and re-tracks. But there is no `TradeCoordinator.register_trade` for the residual — it lives only in sniper's local buffer.

When the residual eventually closes at 10:11:23 (MODE4_STALL_ESCALATE → close_position), the WS_CLOSE_EVENT dispatches to `coordinator.on_trade_closed`. The coordinator finds no state for FILUSDT, hits the silent-skip path at `trade_coordinator.py:671`:

```
COORD_DOUBLE_CLOSE | sym=FILUSDT by=bybit_demo_sl_tp | already closed — skipping duplicate
```

No second trade_history row. No second trade_log row. No second THESIS_CLOSE. **This is Issue 5 in its entirety, caused by Issue 4.**

## Why the WS subscriber's check is structurally wrong

`leaves_qty` describes the ORDER's residual qty (limit-order semantics). For market orders with reduceOnly=True, it's effectively always 0 — the matching engine fills the entire requested qty (or rejects). It does NOT describe the position's residual.

To detect "is this fill closing the position", the correct signal is either:
1. The position size AFTER the fill (Bybit V5 WS `position` channel reports this on every change)
2. Comparing `closedSize` to the known prior position size (requires local tracking)
3. A side-channel flag set by reduce_position before the order is sent

## Coupling to Issue 5

Issue 5 is a direct downstream consequence of Issue 4. The residual orphan-tracking + COORD_DOUBLE_CLOSE silent-skip exists precisely because the partial close erroneously popped the state. Fix Issue 4 correctly → Issue 5 disappears.

## Solution options

### Option A — Side-channel flag (smallest blast radius)

`reduce_position` (in `bybit_demo_adapter.py:514-582`) takes a `partial_close_qty` value and stamps a flag on the coordinator: `coordinator.mark_partial_close_pending(symbol, partial_qty, fill_price_estimate)`.

In `_call_coordinator_close` (WS subscriber line 465-507), before dispatching, check `coordinator.pop_partial_close_pending(symbol)`. If set, call a new `coordinator.on_partial_close(symbol, closed_qty, exec_price)` which:
- Writes a partial-close trade_history row with `qty=closed_qty`, PnL on `closed_qty × delta`
- Writes a partial-close trade_log row similarly
- Does NOT pop coordinator state
- Updates state.size = state.size - closed_qty
- Emits a `MODE4_PARTIAL_RECORDED` event

When the residual eventually closes, the original TradeState is still there; the coordinator's existing on_trade_closed path handles it correctly.

**Pros:** No reliance on Bybit's WS field semantics; explicit signal of partial vs full. Backward-compatible (default `partial_qty=None` falls back to existing behavior). Minimal surface.

**Cons:** Requires sniper to invoke the flag-setting helper before reduce_position. Two new methods on TradeCoordinator. The trade_history row count grows: partial + full close = 2 rows per partial trade (consumers may need to aggregate). Existing dashboards/scripts assume 1 row per trade — need to inspect.

### Option B — Position-state consultation in WS subscriber

In `_handle_one_execution` (WS subscriber line 317-409), after the `leaves_qty == 0` check passes, call `position_service.get_position(symbol)`. If position still has size > 0, treat as partial: emit `BYBIT_DEMO_WS_EXEC_PARTIAL_CLOSE`, update coordinator state.size = new_size, write a partial-close trade row (similar to Option A's `on_partial_close`), return without full dispatch.

**Pros:** Self-contained in the WS subscriber. No new coordinator API. Works without sniper modifying its call sequence.

**Cons:** Extra DB/service hit on every close event. Subject to a race with the WS `position` channel (which may not have updated by the time we query). Bybit-side position can lag by tens of ms behind the execution event. The race could mis-classify a true full close as partial if the position channel hasn't caught up yet.

### Option C — Position-channel-driven close (most architecturally correct)

Refactor: the `position` WS channel (already wired at `_handle_position`) becomes the authoritative "position closed" trigger. The `execution` channel only records fill details. When `_handle_position` reports size=0 for a symbol, dispatch to coordinator.on_trade_closed. When `_handle_one_execution` fires, only record the fill price/qty into a per-symbol cache that the close path consults.

**Pros:** Architecturally clean. Bybit's position channel is the source of truth for position state. Partial fills don't trigger close paths regardless of order type. Single concept, single trigger.

**Cons:** Largest refactor. Touches two WS channel handlers, the coordinator dispatch, and the test surface. Higher chance of regression. Requires careful ordering: ensure the position event arrives within a reasonable window after execution (Bybit's docs don't guarantee tight ordering between channels).

## Recommendation

**Option A** with one explicit caveat to the operator: trade_history will now contain partial-close rows. Aggregating scripts must be updated. The partial row's `trade_id` should distinguish from the eventual final row (suggest `bd-{oid}-partial-{n}`).

If the operator prefers no schema-shape evolution, Option B is the next pick — but the race condition is a real concern in volatile WS conditions.

Option C is the long-term right answer but too invasive for the current sequential-fix window.

## Phase 2 → Operator Decision

Awaiting operator's choice. After this report, I would also surface a clarifying question: should partial-close rows write into `trade_history` and `trade_log` as separate rows, or only update aggregate fields on a single row? The first is cleanest for TIAS learning; the second is cleanest for existing dashboards.
