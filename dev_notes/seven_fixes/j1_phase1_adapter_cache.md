# J1 Phase 1 Step 1.1.2 — Adapter Cache Symmetry Deep Read

Captured 2026-05-14 22:35 UTC. Read-only.

## Question

Does the bybit_demo adapter symmetrically write and prune the `positions` cache, or does it write only? If write-only, what produces the prune events that should keep the cache in sync?

## Findings

### Adapter touch-sites on `positions` table

`src/bybit_demo/bybit_demo_adapter.py` interacts with the `positions` table via `self._trading_repo` at the following sites (verified by grep):

```
line 276-280  get_positions_with_confirmation  → save_position(exchange_mode='bybit_demo')   [WRITE only]
line 565-578  place_order                       → save_order                                 [orders table; not relevant]
line 603-617  close_position                    → save_position(size=0, exchange_mode='bybit_demo')  [DELETE-on-zero]
line 1340-1350 OrderService place_order         → save_order                                 [orders table; not relevant]
```

There are **two** positions-table touch-sites in the adapter:
- `get_positions_with_confirmation` — INSERT OR REPLACE per live position
- `close_position` — DELETE-on-zero via `save_position(size=0)`

### The actual prune chain

The adapter itself does not prune stale rows. Pruning happens via a separate cascade:

1. **Bybit-side close fires** (SL fill, TP fill, liquidation, or `close_position`).
2. **Watchdog tick** calls `get_positions_with_confirmation`. The previously-open symbol is missing from the response.
3. **`_detect_and_record_closes`** at `src/workers/position_watchdog.py:3229-3237` computes `vanished = self._last_known_symbols - open_symbols`. Symbols in the vanished set were tracked last tick but are absent now.
4. For each vanished symbol, calls **`coordinator.on_trade_closed`** (multiple call sites at lines 1564, 1697, 1738, 1807, 1905, 1954, 2050, 2091, 2730 of position_watchdog.py).
5. **`coordinator.on_trade_closed`** dispatches 17 registered callbacks (per audit-log `cbs_fired=17`).
6. One of those callbacks is **`_positions_table_cleanup_on_close`** at `src/workers/manager.py:2198+` — hardened by commit `c4eef5c` to read `record.exchange_mode` (the trade's mode at register time) and call `trading_repo.delete_position(sym)` via `get_running_loop()`.
7. **`trading_repo.delete_position`** at `src/database/repositories/trading_repo.py:263-281` executes `DELETE FROM positions WHERE symbol = ?`. Idempotent.

This chain works **as long as** the symbol was in `_last_known_symbols` at the prior tick. Verified in production: 61 `POSITION_ROW_DELETED` events in the latest worker log; sample at 21:47:26-27 cleaned 9 symbols including DYDXUSDT.

### The structural asymmetry that remains

The adapter writes via INSERT OR REPLACE for every confirmed-true response. The prune path is OUTSOURCED to the watchdog's vanished-detection. This is a structural asymmetry in two scenarios:

**Scenario A — first-tick blindness.** On the very first watchdog tick after a worker boot, `_last_known_symbols` is `set()` (initialised empty at `position_watchdog.py:337`). If at boot time the `positions` table already contains rows for symbols no longer on Bybit (the 2026-05-13 stale rows are exactly this case), the first tick's vanished set is `set() - open_symbols = set()`. No close callbacks fire. The stale rows stay forever unless `close_position` is later called or the backfill script is run.

**Scenario B — symbol that was never in `_last_known_symbols`.** If a position exists on Bybit but somehow never entered `_last_known_symbols` on any prior tick (transient API issue, watchdog start-up race, manual operator entry on Bybit UI followed by immediate close before watchdog catches it), then later closes on Bybit, the vanished-detection cannot catch it.

In both scenarios the adapter has already done its INSERT for the position when first seen, but the prune chain depends on the watchdog having tracked it previously.

### Live PositionService — symmetric pattern check

`src/trading/services/position_service.py:54-91` is the live-mode counterpart. It also does INSERT-only on every `get_positions` call (`save_position(pos, exchange_mode="shadow")` at line 87). It also does not prune.

The live path therefore has the same first-tick blindness, but it is the live mode (Bybit production) which the operator never uses for trading. The audit-relevant mode is bybit_demo, which is where the four current stale rows live.

### Shadow adapter — no asymmetry because no cache

`src/shadow/shadow_adapter.py:151-210` `ShadowPositionService.get_positions()` does **not** call `save_position`. Shadow does not maintain a local cache of positions in the `positions` table; the watchdog reads positions live from the Shadow API every tick. There is no cache to drift, and no asymmetry to address.

### Implication

The c4eef5c + 06672f0 fix chain closes the asymmetry **for symbols the watchdog has tracked at least once**. The remaining gap is for symbols that exist in `positions` but were never tracked by the watchdog. The four stale rows are evidence of this: they were created on 2026-05-13 when (presumably) the watchdog had them in `_last_known_symbols`, then closed on Bybit while the pre-fix mode-gating bug silently swallowed the cleanup. They have remained stale because no subsequent watchdog tick sees them in Bybit's response (so they can never appear in `_last_known_symbols` again, so they can never be "vanished").

## Implication For The J1 Fix

Three architectural choices are open:

- **Choice 1 — Run the existing backfill (no new code).** `scripts/backfill_orphan_positions.py` cleans the four historic rows. This addresses today's residue.

- **Choice 2 — Adapter-level symmetric prune (new code, structural).** After `get_positions_with_confirmation` finishes with `confirmed=True`, query `SELECT symbol FROM positions WHERE exchange_mode='bybit_demo'` and DELETE any row not in the response set. Emit `POSITIONS_CACHE_PRUNE` per delete. This makes the adapter the single source of truth for the cache — bytes-equivalent symmetry between write and prune. The watchdog's vanished-detection becomes a redundancy.

- **Choice 3 — Boot-time reconciliation (new code, narrow).** On worker startup, after `recover_state_from_db` runs, force one `get_positions_with_confirmation` call and DELETE any `positions` row for the active mode that is not in the response. Cleans residual rows on every restart. Emit `POSITION_REGISTRY_BACKFILL` per delete (already the planned event for boot-time work).

Choice 2 is the strongest structural fix; Choice 3 is the narrow one-shot at boot; Choice 1 is operational only. The fix-option report (Step J1.1.9) will compare them against the master prompt's forbidden band-aid list.

## What Operator Approves

Whether to evaluate Choices 1, 2, 3 in Step J1.1.9 or to fix the scope earlier. My current recommendation pending the rest of Phase 1: **Choice 2 + Choice 1 (backfill the historic rows as a separate one-shot)**. Choice 3 is partial coverage compared to Choice 2; it only catches startup, not the steady-state asymmetry.
