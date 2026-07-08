# Issue 2 Phase 1 — Zombie Positions in DB: Root-Cause Investigation

## Root cause — pinned to a single missing call

`src/bybit_demo/bybit_demo_websocket_subscriber.py:246-270` (`_handle_position`):

```python
def _handle_position(self, message: dict[str, Any]) -> None:
    """Pybit position-stream callback. ...
    Logs position state changes for observability. Does NOT trigger
    coordinator.on_trade_closed directly — the execution-stream
    handler is the canonical close source. Position events are
    secondary confirmation that a position fully flatted.
    """
    ...
    for pos in positions:
        sym = pos.get("symbol", "")
        size_str = pos.get("size", "0")
        try:
            size = float(size_str)
        except (TypeError, ValueError):
            size = 0.0
        if sym and size == 0.0:
            log.info(
                f"BYBIT_DEMO_WS_POS_FLAT | sym={sym} | {ctx()}"
            )
```

When Bybit's WS reports a position state with `size=0`, the handler **only logs `BYBIT_DEMO_WS_POS_FLAT`** — it does not call `trading_repo.save_position(pos)` (which would trigger the DELETE-on-size==0 path at `trading_repo.py:182`).

The positions-table cleanup currently relies exclusively on `BybitDemoOrderService.close_position` (`bybit_demo_adapter.py:480-510`) calling `save_position(pos, size=0)`. But:

- External closes (`bybit_sl_hit`, `bybit_tp_hit`) trigger on Bybit's matching engine — our adapter's `close_position` is NEVER called.
- All 6 zombie positions today closed externally (per `trade_history.close_reason`): ATOMUSDT, NEARUSDT, CRVUSDT, GMTUSDT, APTUSDT, PYTHUSDT all closed via `bybit_sl_hit` or `bybit_tp_hit`.

So every external close leaks a zombie. Today's ghost rate: **6/6 of externally-closed positions remained in the table** (100 % leak rate for external closes).

## Why brain Call B sees zombies

The brain's Call B reads from the `positions` table indirectly via `position_service.get_positions` (per `strategist.py:3423-3425`). The position service for bybit_demo (`bybit_demo_adapter.get_positions`) queries Bybit's HTTP API for live positions and side-saves them to the table. So as long as the position_service is queried fresh, brain sees only real positions.

The corruption surfaces when:
- A downstream consumer reads directly from the table (per the directive's concern about Call B's prompt construction)
- Or when state-divergence checks compare table count to live count (positions table inflated by zombies)
- Or when the watchdog's `_detect_and_record_closes` reconciles positions table vs Bybit (more rows in table than Bybit knows about → reconciliation churn)

## Coupling to other issues

- **Issue 4** (partial-close inflation): If a partial close happens correctly per the proposed Issue 4 fix, the position size after partial is non-zero. The positions table still has the row with the updated (smaller) size — no zombie created. Only the eventual full close needs cleanup, which this fix also handles.
- **Issue 3** (corrupted WD_CLOSE) and Issue 2 are partially coupled — both touch the watchdog's view of "what's actually open." Fixing Issue 2 reduces the watchdog's reconciliation noise but doesn't fix the WD_CLOSE corruption directly.

## Solution options

### Option A — `_handle_position` calls `save_position(size=0)` on size=0 events

Smallest possible patch. In the handler, when `size == 0`, dispatch (via `run_coroutine_threadsafe`, since pybit's thread isn't on the asyncio loop) a call to `trading_repo.save_position(zero_position, exchange_mode='bybit_demo')`. The DELETE fires; the row is gone.

**Pros:** Single-file change. Zero risk to other subsystems. Mirrors the contract of `close_position`'s cleanup. Race-free because pybit's position events are serialized per-symbol and `INSERT OR REPLACE`/`DELETE` are idempotent.

**Cons:** Requires the trading_repo to be reachable from the WS subscriber (need to verify the DI wiring — it's likely already in `self._services`). Need to construct a `Position` dataclass with size=0 from the WS payload (existing parser must already do this for non-zero positions).

### Option B — Coordinator close-callback DELETE

Add a `_positions_table_cleanup_close_callback` to the 15-callback fan-out in `workers/manager.py:1999+`. When any close path fires `coordinator.on_trade_closed`, the callback runs `save_position(pos, size=0)` for the symbol.

**Pros:** Single trigger point for ALL close paths (WS, watchdog, sniper M4). Architecturally consistent with the existing callback pattern.

**Cons:** Depends on `on_trade_closed` firing — but for external SL/TP hits the WS subscriber DOES dispatch on_trade_closed correctly (the `closed_by="bybit_sl_hit"` path works). So Option B would cover external closes via the same callback that already writes trade_history. Subtle: Option B does NOT cover the case where the coordinator's `COORD_DOUBLE_CLOSE` silent-skip fires (state already gone). In that case no callback fires — but the position was already popped, so it shouldn't be in the table anyway. Edge case worth thinking about.

### Option C — Both

Belt-and-suspenders: install both Option A (in the WS handler) and Option B (as a callback). Idempotent DELETE makes the double-fire harmless.

## Recommendation

**Option B** — adds the cleanup to the close-callback fan-out alongside trade_history, trade_log, thesis_close, perf_update, TIAS_save. The architectural symmetry is the right call. Performance impact is one DELETE per close (trivial). Race-free because:
- The callback is async-loop-scheduled by `on_trade_closed`
- DELETE is idempotent (no row → no-op)
- The position re-emission case (rare: WS sees size>0 between close and callback fire) is handled by `INSERT OR REPLACE` semantics

Option A is the smaller change; if the operator wants minimum-surface, Option A delivers correctness with one less callback registration.

## Verification

After implementation:
- Sample 20+ external closes
- Confirm `positions` table row count after close == Bybit's live open position count
- Ghost rate target: 0 / 20+

Today's failure case to spot-check post-fix:
- Trigger a paper-trade SL/TP hit (or use Bybit demo to manually close)
- Confirm WS emits `BYBIT_DEMO_WS_POS_FLAT`
- Confirm positions table row for that symbol disappears within seconds (target: < 5 s)
