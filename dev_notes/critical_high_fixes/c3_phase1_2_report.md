# CRITICAL-3 Phase 1+2 — Investigation and Operator Discussion

## Audit reference

CRITICAL-3 — TradeHistory persistence covers only system-initiated closes; 86 of 116 bybit_demo trades lack history rows (74 percent gap, worse than audit's 67 percent).

## Phase 0 confirmation + new finding

Verified 2026-05-09:
- `trade_log` bybit_demo: 116
- `trade_history` rows total: **30** (audit said 26)
- All 30 rows use the **`bd-{symbol}-close` collision pattern** — every existing row is a fallback ID. `close_order.order_id` is always falsy because `_build_close_order` at `bybit_demo_adapter.py` hardcodes `order_id=""`. So the actual gap is even worse than the audit suggested: 30 unique symbols × N closes per symbol → 30 latest-overwrite rows. System-initiated repeat closes for the same symbol are LOST.

Coverage gap: at least 86 of 116 = 74 percent missing. Plus an unknown number of overwrites within the 30 stored rows.

## Investigation

### Where coordinator.on_trade_closed is called

Grepped `src/`. All paths through coordinator that fire callbacks:

| Source | Call sites | Use case |
|---|---|---|
| `bybit_demo_websocket_subscriber:494` | 1 | WS execution event (SL/TP hit, manual close, fill confirm) |
| `position_watchdog.py` | 10 sites (lines 1312, 1430, 1471, 1540, 1638, 1687, 1767, 1808 + others) | Poll-detected closes when WS missed |
| `profit_sniper` (likely) | TBD | Sniper-driven exits |

The new close-callback covers ALL of these paths automatically — including the watchdog-poll fallback that protects against WS outages.

### Adapter's current path (the duplicate writer)

`bybit_demo_adapter.close_position:385-422` builds a TradeRecord inline and calls `self._trading_repo.save_trade(trade)` directly. The trade_id is `close_order.order_id or f"bd-{symbol}-close"` — but `_build_close_order` hardcodes `order_id=""`, so the fallback always wins. Result: 30 collision-overwritten rows.

### Trade_id collision evidence

```
sqlite> SELECT trade_id, COUNT(*) FROM trade_history WHERE trade_id LIKE 'bd-%' GROUP BY trade_id ORDER BY COUNT(*) DESC LIMIT 5;
bd-ADAUSDT-close|1
bd-RENDERUSDT-close|1
...
```

Each unique trade_id has only 1 row because `INSERT OR REPLACE` overwrites earlier rows with the same key. The 116 system-initiated closes for these 30 symbols collapse into 30 rows.

### Consumers of trade_history

Read-only consumers (none care about WHO writes the rows, only that they exist):
- `alert_manager.py:250` — alert summary `get_trade_history(limit=50)`
- `mcp/tools/memory_tools.py:27, 51` — MCP tool exposure
- `fund_manager/momentum_allocator.py:57` — `SELECT pnl FROM trade_history`
- `fund_manager/manager.py:537` — `SELECT pnl FROM trade_history ORDER BY exit_time DESC LIMIT 20`
- `telegram/handlers/portfolio.py:90` — Telegram /history command
- `database/cleanup.py:56` + `protected_tables.py:52` — cleanup-protected

None of them depend on adapter-vs-WS-callback origin. They just read rows.

### Required record additions

The new close-callback needs `size` (qty for trade_history). Currently the coordinator record dict (`trade_coordinator.py:751-805`) does NOT include `size`. One-line add: `"size": state.size if state else 0.0,`. The record already includes `order_id` (line 762, from `state.order_id`).

## Three options considered

### Option A — New WS-side callback as SOLE writer (recommended)

Remove the adapter's direct `save_trade` call (lines 385-422); add a new `_trade_history_close_callback` registered in `workers/manager.py`. Single writer eliminates duplicate-writer race entirely. Trade_id derived from `state.order_id` (open-side, unique per trade) with epoch-ms fallback.

Pros:
- Single writer — no race, no idempotency check needed
- Covers ALL coordinator paths (WS event, watchdog poll, sniper, etc.) automatically
- Mode-gated: only fires for `bybit_demo` (shadow's behavior unchanged)
- Trade_id is unique (no more collisions)
- Matches the existing pattern of trade_log/intelligence/thesis (all coordinator-callback driven)
- Fixes the 86-row gap AND the collision over the 30 existing rows in one design

Cons:
- Slight asymmetry with live `PositionService` which still self-persists at lines 210/322 (live mode is out of scope per prompt)
- WS-down windows: if WS drops AND watchdog hasn't yet detected, no row is written. Acceptable — same window as trade_log/intelligence/thesis already have

### Option B — Keep adapter writer, add idempotent callback

Keep adapter's save_trade; add new callback that checks `SELECT 1 FROM trade_history WHERE trade_id=?` before insert.

Pros:
- Minimal adapter change

Cons:
- Two writers — race window where both fire concurrently
- Adapter and callback would generate different trade_ids (open-side vs close-side orderId), so the existence check would fail and BOTH rows would land
- More complex; idempotency check is a per-close DB hit

### Option C — Move write entirely to a new helper, called by both

Refactor save_trade into a deduplication-aware helper invoked from adapter, callback, and (someday) live PositionService.

Pros:
- Long-term clean

Cons:
- Larger blast radius; touches live code
- Out of scope per prompt's "live trading is NOT enabled"

## Recommendation

**Option A.** Single writer is the cleanest design. It matches the existing 14-callback fan-out pattern that already governs trade_log/intelligence/thesis. The "two writers + idempotency" approach is fragile because the trade_id sources differ.

## Implementation plan

Single atomic commit. Files modified:

1. `src/core/trade_coordinator.py`: add `"size": state.size if state else 0.0,` to record dict.
2. `src/bybit_demo/bybit_demo_adapter.py:385-422`: remove the inline TradeRecord build + save_trade call. Add a clarifying comment that trade_history is now written by `_trade_history_close_callback` in workers/manager.py for ALL bybit_demo closes (not just system-initiated).
3. `src/workers/manager.py`: add `_trade_history_close_callback` near the existing data_lake/thesis callback registration site (around line 1899).

Tests added:
- 1 unit test: callback writes trade_history for bybit_demo close
- 1 unit test: callback skips when mode is shadow
- 1 unit test: trade_id derivation uses state.order_id when present
- 1 unit test: trade_id falls back to `bd-{symbol}-{epoch_ms}` when state.order_id absent
- 1 unit test: TradeRecord fields populated correctly from coordinator record (post-CRITICAL-1+2)

5 new tests.

## Open questions

None blocking. Existing 30 rows in trade_history are NOT touched (Rule 12 default = leave). Callback fires for fresh closes only; the 86 missing rows will not be backfilled in this commit.
