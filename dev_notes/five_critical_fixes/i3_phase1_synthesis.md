# Issue 3 Phase 1 — Corrupted WD_CLOSE Data: Root-Cause Investigation

## Where the corruption is produced

`src/workers/position_watchdog.py:3046-3085` (`_detect_and_record_closes`):

```python
pnl_pct = 0.0
pnl_usd = 0.0
entry_price = 0.0
direction = ""
if self.coordinator:
    plan = self.coordinator.get_trade_plan(symbol)
    state = self.coordinator._trades.get(symbol)
    if plan and hasattr(plan, "entry_price") and plan.entry_price > 0:
        entry_price = plan.entry_price
        direction = getattr(plan, "direction", "")
    if not direction and state:
        direction = getattr(state, "side", "")

    if entry_price > 0 and exit_price > 0:
        # compute pnl from entry/exit
        ...
    else:
        # final fallback: cached pct, zero notional
        pnl_pct = self._last_pnls.get(symbol, 0.0)
        ...
```

The writer's entry/direction/pnl resolution depends on the **coordinator** still having either the trade plan or the trade state. When both are absent (because some prior path already popped the state), the fallback emits zeros.

Today's FILUSDT WD_CLOSE at 10:11:23.783 reads:
```
ent=$0.00000000 dir= price_src=ticker_fallback pnl$=+0.0000 pnl=-0.2747%
```

`pnl_pct=-0.2747%` came from `self._last_pnls.get(symbol, 0)` — the cached tick-level PnL from a previous tick when the residual was still tracked. Everything else is zero or empty because `plan = None` and `state = None`.

## Why the coordinator state is missing

The coordinator was popped 22 minutes earlier (09:49:07) by Issue 4's bug (partial-as-full-close). When the watchdog detected the residual's external close at 10:11:23, the trade plan/state had been gone since 09:49:07.

So Issue 3's corrupted WD_CLOSE is **partially** caused by Issue 4. But the watchdog's writer would produce the same zero-corruption for any other reason the coordinator state could be absent:
- WS-driven close already processed the trade (no Issue 4 needed; pure race)
- Manual close via Telegram or other tooling that bypasses the coordinator
- A bug where some other path popped state without notifying

So Issue 3 deserves a defensive fix regardless of whether Issue 4 is fixed.

## What the CRITICAL-1 fix did NOT cover

CRITICAL-1 (in `trade_coordinator.on_trade_closed:716-731`) added a "sentinel-zero" back-derive: when callers pass `pnl_pct=0` with a valid `exit_price`, the coordinator back-derives PnL from `state.entry_price + close_price + state.side`. This works for the WS-driven close path (subscriber dispatches `on_trade_closed` with the right sentinels and the coordinator does the back-derive).

It does NOT work for the WD_CLOSE path because:
- WD_CLOSE is written DIRECTLY by the watchdog (line 3148) — not by routing through `coordinator.on_trade_closed`
- The watchdog's PnL computation happens in its own code, not in the coordinator's back-derive helper

So the CRITICAL-1 fix didn't reach the watchdog path. Issue 3 is the second half of the same data-integrity work.

## Solution options

### Option A — Defensive entry/direction lookup from the orders table

When `plan` and `state` are both absent, query the most recent filled order for the symbol from the `orders` table:

```sql
SELECT side, qty, avg_fill_price FROM orders
WHERE symbol = ? AND status = 'Filled' AND date(created_at) >= date('now', '-1 day')
ORDER BY created_at DESC LIMIT 1
```

Use that row's `avg_fill_price` as entry, `side` as direction, `qty` as size for notional. Compute PnL with entry from orders + exit from current path.

**Pros:** No coordinator-state mutation needed; reads from authoritative orders table. Works for ALL late-detected closes regardless of upstream cause.

**Cons:** Extra DB query in the close-record path (adds 1-2 ms). Edge case: multiple consecutive trades on the same symbol — the lookup must filter to the open trade, not a previously closed one. The orders table doesn't currently flag "this order's position is closed" so the most-recent-filled-order heuristic may be wrong for fast re-entries.

### Option B — Don't write WD_CLOSE when coordinator state is missing

When `plan = None and state = None`, skip the WD_CLOSE write entirely. Rely on the WS-driven close path to have already written the record (or, if not, accept the data loss).

**Pros:** Simplest possible change.

**Cons:** Loses data for genuine late-detected closes where WS missed the event. Doesn't fix the trade autopsy / TIAS data quality concern. Effectively a band-aid that the directive explicitly forbids: "Filtering out WD_CLOSE events with empty fields (data still corrupt, now invisible)."

### Option C — Persist trade state in `trade_thesis` and read from there

The `trade_thesis` table already has `direction`, `entry_price`, `size_usd`, `leverage`, and the row stays around (status='open') until thesis closes. The watchdog could query `trade_thesis WHERE status='open' AND symbol=?` to recover entry/direction when coordinator state is missing.

**Pros:** Authoritative source (the thesis was the source of truth for the trade at open time). Doesn't depend on orders table heuristics.

**Cons:** Same extra-query cost as Option A. The thesis may already have been closed by a previous (failed) close attempt — need to be careful about timing.

### Option D — Read both orders and thesis, prefer thesis if both exist (Recommended)

Combined: try thesis_manager.get_open_thesis(symbol) first (cheaper, in-memory cache likely), fall back to orders query, fall back to current zero-emission only if both fail.

**Pros:** Most robust; minimal new code (already have thesis_manager DI). Captures the operator's intent that "the trade is real even if state was lost."

**Cons:** Two DB queries in the worst case. Mitigation: cache the (entry, direction, size) tuple in `self._last_close_lookup_cache` keyed by `(symbol, opened_at)`.

## Recommendation

**Option D** (thesis → orders → fail). The thesis table is the operator-visible record of open trades; if it has the data, use it. Falling back to orders keeps the fix correct for the rare case where thesis save failed but the order placed. The two-query worst case adds ~3-5 ms per WD_CLOSE — well under the 500 ms warning threshold for watchdog ticks.

## Test coverage

Phase 3 should add tests for:

- Coordinator state present + valid → existing behavior preserved
- Coordinator state absent, thesis row present → recovers entry/direction/PnL from thesis
- Coordinator state absent, thesis absent, orders row present → recovers from orders
- All three absent → no row written; defensive WARNING log emitted (`WD_CLOSE_RECOVERY_FAIL`); no zero-corruption in `trade_log`
- CRITICAL-1 WS path unchanged

## Implementation note — schema and locking

The watchdog already reads from the same SQLite database via aiosqlite. Adding two more SELECT statements is fine. No schema changes.
