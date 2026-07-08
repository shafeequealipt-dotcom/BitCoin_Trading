# CRITICAL-1 Phase 1 — System-Initiated Close Path

## Purpose

Document `bybit_demo_adapter.close_position` (lines 248-438). Verify that this path computes correct PnL inline and writes to trade_history independently of the coordinator. Verify whether (and how) this path interacts with `coordinator.on_trade_closed`.

## Function signature (line 248-280)

```python
async def close_position(
    self,
    symbol: str,
    *,
    purpose: str = "layer4_close",
    close_trigger: str = "system_close",
) -> Order:
```

`close_trigger` is a recent (Phase 12.7 lifecycle-logging-audit) addition for HIGH-3 propagation. Has documented enum values: "sniper_p9", "callb_close", "wd_hard_stop", etc.

## Flow

| Lines | Action |
|---|---|
| 281-283 | Log `BYBIT_DEMO_POSITION_CLOSE \| sym=... close_trigger=...` |
| 287-292 | `pos = await self.get_position(symbol)`. If no position, log `BYBIT_DEMO_CLOSE_NO_POSITION` and return rejected. |
| 294-306 | Build opposite-side market reduceOnly order body |
| 308-316 | POST `/v5/order/create`. If TradingMCPError, log `BYBIT_DEMO_CLOSE_REJECT` and return rejected. |
| 318-363 | P3 of P1-P10: resolve actual fill via `_resolve_close_fill` → `/v5/order/realtime`. Falls back to `pos.mark_price` if orderId missing or zero avg price. Logs `CLOSE_FILL_CONFIRMED` (success) or `BYBIT_DEMO_CLOSE_FILL_FALLBACK` (fallback). |
| 365 | Build close_order via `_build_close_order(symbol, close_side, pos.size, exit_price)` |
| 373-436 | P7 wiring: persist via `_trading_repo` if available |
| 438 | Return `close_order` |

## Inline PnL computation (lines 385-422)

This is the system-initiated path's PnL computation, which the audit calls out as the only correct one in the codebase:

```python
from src.core.types import TradeRecord
pnl_price_delta = (exit_price - pos.entry_price) * pos.size
if pos.side == Side.SELL:
    pnl_price_delta = -pnl_price_delta
pnl_pct_val = (
    ((exit_price - pos.entry_price) / pos.entry_price) * 100.0
    if pos.entry_price > 0
    else 0.0
)
if pos.side == Side.SELL:
    pnl_pct_val = -pnl_pct_val
trade = TradeRecord(
    trade_id=close_order.order_id or f"bd-{symbol}-close",
    symbol=symbol,
    side=pos.side,
    entry_price=pos.entry_price,
    exit_price=exit_price,
    qty=pos.size,
    pnl=pnl_price_delta,
    pnl_pct=pnl_pct_val,
    strategy=purpose,
)
await self._trading_repo.save_trade(trade)
self._log.info(
    f"BYBIT_DEMO_PERSIST_OK | sym={symbol} table=trade_history "
    f"trade_id='{trade.trade_id}' pnl_pct={pnl_pct_val:.2f} | {ctx()}"
)
```

This is the canonical PnL formula used elsewhere in the codebase. It confirms:

- **Buy**: `pnl_pct = ((exit - entry) / entry) * 100`
- **Sell**: `pnl_pct = -((exit - entry) / entry) * 100` (which equals `((entry - exit) / entry) * 100`)
- The formula uses `pos.side == Side.SELL` to identify short trades. `Side.SELL` is an enum value from `src/core/types.py`.
- `qty` and `size` are interchangeable here (`pos.size`).

**This is the formula the coordinator-side back-derive must mirror to keep the two paths producing identical numbers.**

## trade_id format

Line 403: `trade_id=close_order.order_id or f"bd-{symbol}-close"`.

**Audit's ISSUE 1.4-A** flagged this as a collision risk: when `close_order.order_id` is falsy, multiple closes for the same symbol use the same fallback ID `bd-{symbol}-close` and collide on `INSERT OR REPLACE`. CRITICAL-3 will need to address this. Confirmed: the fallback ID is non-unique.

## Does close_position invoke the coordinator?

Reading the function in full from line 248 through line 438:

- It calls `_trading_repo.save_order`, `_trading_repo.save_trade`, `_trading_repo.save_position`. Three direct repo writes.
- It does NOT call `coordinator.on_trade_closed`.
- It does NOT call any callback registered with the coordinator.

So the coordinator pathway is bypassed entirely on the adapter's direct write — but the adapter's market order DOES eventually fill, which triggers a Bybit WS execution event. That event lands in `_handle_one_execution` and dispatches to the coordinator. **System-initiated closes therefore produce TWO write events:**

1. Direct `save_trade` to `trade_history` with correct inline PnL (this path only)
2. Indirect WS execution → `coordinator.on_trade_closed` → 14 callbacks → corrupt pnl=0 in trade_log/intelligence/thesis (this path is shared with WS-only closes)

## How this matches the database state

| Table | Rows | Source path |
|---|---|---|
| `trade_history WHERE trade_id LIKE 'bd-%'` | 30 | Direct `save_trade` from `close_position` (system-initiated only) |
| `trade_log WHERE exchange_mode='bybit_demo'` | 116 | Indirect from WS dispatch through coordinator (BOTH system-initiated AND WS-only closes) |
| Coverage gap | 86 | WS-only closes (SL hit, TP hit, manual UI close) — never touch `close_position` |

The 30 in trade_history are the system-initiated subset of the 116 in trade_log. The 86 missing are WS-only closes. CRITICAL-3 fix needs to write trade_history for those 86 too, while CRITICAL-1 fix needs to correct pnl in the 116 trade_log rows (going forward).

## Cross-table evidence (5 sample trades, oldest to newest in same close batch at 2026-05-09 19:52:30-32 UTC)

From the cross-check query in Phase 0:

| Symbol | trade_log pnl_pct | trade_history pnl_pct | Diff source |
|---|---|---|---|
| ADAUSDT (Sell, 0.272 → 0.2721) | 0.0 (corrupt) | -0.0367 (correct loss) | trade_log via coordinator (bug); trade_history via adapter inline (correct) |
| IMXUSDT (Sell, 0.18976 → 0.18974) | 0.0 (corrupt) | +0.0105 (correct win) | same |
| ARBUSDT (Sell, 0.14207 → 0.14208) | 0.0 (corrupt) | -0.0070 (correct loss) | same |
| NEARUSDT (Sell, 1.5585 → 1.5582) | 0.0 (corrupt) | +0.0192 (correct win) | same |
| KATUSDT (Sell, 0.01031 → 0.01031) | 0.0 (correct — flat) | 0.0 (correct — flat) | tie; both right by coincidence |

Notable: AEROUSDT shows `trade_log exit=0.5147` vs `trade_history exit=0.5156`. The adapter's `_resolve_close_fill` POST-resolved a different exit price than the WS execution event reported. **For consistency, CRITICAL-1's coordinator-side fix should treat the WS-supplied exit_price as authoritative for the coordinator path, since that's what's already populated in the record's `close_price` field — the inline adapter computation can keep its own value in trade_history (different price source by design).** This is a documented design seam, not a defect.

## reduce_position (line 440-491, partial)

Mirrors `close_position` but for partial reduces. Falls back to full close on rejection (REDUCE_FALLBACK alert — HIGH-7). Same inline PnL pattern. Out of scope for CRITICAL-1.

## Findings

1. The system-initiated close path computes correct PnL with a clear formula. This formula is the test oracle.
2. `close_position` does not call the coordinator. The coordinator is invoked indirectly by the WS execution event.
3. System-initiated closes produce one correct trade_history row AND one corrupt trade_log/intelligence/thesis triple. The corruption is caused by the coordinator path, not by the adapter.
4. The two paths can produce slightly different exit prices (adapter's `/v5/order/realtime` poll vs WS `execPrice`). This is a minor design seam — CRITICAL-1 fix preserves the WS price for coordinator-routed records.
5. The trade_id collision in the fallback `bd-{symbol}-close` is real and observable. Will be addressed by CRITICAL-3.
6. The fix for CRITICAL-1 cannot break this path because the adapter does not depend on the coordinator's record dict at all.
