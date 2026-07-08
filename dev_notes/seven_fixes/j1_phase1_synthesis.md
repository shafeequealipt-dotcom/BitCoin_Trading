# J1 Phase 1 — Synthesis Report

Completed 2026-05-14 23:10 UTC. Read-only investigation. No code changes.

## What The Audit Said vs What Current Code Shows

### Audit claim (LIVE_OBSERVATIONS.md OBS-09)

- 13 positions in `positions` table at peak, only 8 tracked by workers; 5 orphans (AAVE, EGLD, RUNE, SAND, DYDX) for the entire 2-hour 2026-05-14 20:35-21:46 window.
- "$81.7K of margin used on Bybit invisible to local in_use."
- "`drift_pct` reconciler only compares equity totals, never alarms on margin gap."
- Audit hypothesised: registry keyed by `trade_id`, orphans have NULL/stale trade_id.
- Audit hypothesised: boot-time discovery loads positions but misses some.

### Current-code reality (verified file:line, log:line)

**The registry**: `TradeCoordinator._trades: dict[str, TradeState]` at `src/core/trade_coordinator.py:146`, keyed by **symbol** (not trade_id). The audit's `_open_positions` dict does not exist in production code. Audit hypothesis 2 is false.

**The boot path**: `recover_state_from_db` at `src/core/trade_coordinator.py:183-288` reads `trade_thesis WHERE status='open'` only. It does NOT read the `positions` table. The audit's "loads positions but misses some" framing was close but wrong about the source — it reads trade_thesis, not positions.

**WD_TICK n**: emitted at `src/workers/position_watchdog.py:536`, counts `len(positions)` from `position_service.get_positions()`. In bybit_demo mode the call routes through the Transformer to `BybitDemoPositionService.get_positions_with_confirmation()` at `src/bybit_demo/bybit_demo_adapter.py:181-290`, which calls `/v5/position/list` with `settleCoin=USDT`. WD_TICK n is Bybit's reported live count, not the cache count and not the registry count.

**Audit-window peak n**: 410 ticks total; n=9 modal peak (92 occurrences); n=10 highest observed (49 occurrences). The audit's "n=8 max" was conservative.

**The five orphan symbols' WD_TICK presence in the audit window**: 0 ticks each. They appear in intelligence-layer logs (XRAY structure scans) but never in WD_TICK. This means Bybit's API was NOT reporting them during the audit — they existed only in the local `positions` cache.

**Currently (2026-05-14 22:30 UTC)**: WD_TICK shows n=0 for hundreds of ticks. The four remaining stale rows (SAND, EGLD, RUNE, AAVE) have `updated_at=2026-05-13` and `trade_thesis` rows closed on 2026-05-13 by `bybit_demo_sl_tp` or `zombie_reconciler`. These rows do not represent live Bybit positions; they are pre-fix residue.

**The $81.7K margin gap claim**: `FUND_RECONCILE` at 22:27 UTC reads `bybit_available=99,951 / local_avail=91,833 / drift_pct=+0.00`. The reconciler is structurally blind because it compares `total_equity` only (`bybit_total=local_total=183,666`). The audit's gap claim was directionally correct but the actual current gap is ~$8K, not $81.7K. The reconciler structural gap is real and is the H4 problem.

## The Verified Root Cause

**H1 (primary)** — Pre-c4eef5c mode-gating bug in `_positions_table_cleanup_on_close`. Pre-fix the cleanup callback read `transformer.current_mode` at dispatch time and silently returned if not exactly `"bybit_demo"`. Every close event during boot, mid-mode-switch, or SEGV recovery silently leaked the `positions` row. The four current stale rows are exactly this pattern: their `trade_thesis` was correctly closed on 2026-05-13 (by Bybit SL/TP) but the positions-table row was never deleted because the cleanup callback silently skipped.

**Status of H1**: Already fixed by commit `c4eef5c` (2026-05-14 10:54 UTC). The fix:
- Records `exchange_mode` on `TradeState` at register time (not at dispatch).
- Cleanup callback reads `record.exchange_mode` (the trade's mode), not the global at dispatch.
- Uses `asyncio.get_running_loop()` (raises if loop closed) instead of `asyncio.get_event_loop()` (silent failure).
- Emits `POSITION_ROW_DELETED`, `POSITION_ROW_DELETE_FAIL`, `POSITION_ROW_DELETE_SKIP`.

**Verification of c4eef5c working**: 61 `POSITION_ROW_DELETED` events in the latest 6-hour log window. Sample at 21:47:26-27 cleaned 9 symbols including DYDXUSDT (the audit's J2 case). The fix functions correctly.

**Why the four rows remain**: They predate the fix. `scripts/backfill_orphan_positions.py` is the supplied operator-supervised one-shot cleanup tool. It has not been run since the four rows were created.

## Supporting Hypotheses

**H4 (real, must fix)** — `fund_reconciler` watches only `total_equity` drift, not position-count and not margin-availability. Even after H1's fix, the operator has no early-warning signal for future drift. The fix adds a `PositionReconciler` worker (sibling to fund_reconciler) that compares `positions` count to live API count and emits `POSITION_RECONCILE_DRIFT` on drift.

**H2 (theoretical, conditional)** — Boot-recovery reads only `trade_thesis WHERE status='open'`. If `zombie_reconciler` closes a thesis while Bybit still has the position open (forward race), the next worker boot wakes up with the brain unaware of the position. The watchdog still ticks it (reads live API), but the brain's `_trades` is missing. The forward race is not observed in the audit window; the fix (Option C) is defence-in-depth.

**H3 (latent, low priority)** — `/v5/position/list` pagination not implemented. Current account well under default `limit=20`, so no tail-loss today. Fix as a small hardening commit (Option E).

**Adapter cache asymmetry (architectural concern)** — The bybit_demo adapter writes the cache on every `get_positions_with_confirmation` but never prunes. Pruning is outsourced to the watchdog's vanished-detection plus the close-callback chain (which c4eef5c hardened). On first tick after boot or on transient API errors that return `confirmed=True, positions=()`, the pruning chain can miss. Fix (Option A) is adapter-level symmetric prune.

**Zombie reconciler forward race (theoretical)** — Could fire if the watchdog's first tick after boot returns transient empty. Not observed. Fix (Option F) is dwell-time guard; low priority.

## Forbidden Band-Aid Rejection

Each option above is checked against the master prompt's forbidden list (Rule 3):

- "Adding a periodic sweep that scoops up missed positions" — Option A is symmetric write/prune at the adapter level, NOT a periodic sweeper. Option B is signal-only (no auto-delete).
- "Auto-deleting positions table rows that aren't in registry" — Option A deletes rows that are not in Bybit's confirmed response (true source of truth). Option B never deletes. Option C never deletes.
- "Force-closing orphans on startup" — Option C REGISTERS missing positions in `_trades` so the brain manages them; it does not close them.
- "Hiding orphans from dashboard" — opposite of B; the reconciler surfaces drift.
- "Treating `tracked` and `in DB` as equivalent without understanding the boot path" — investigated explicitly; the boot path reads trade_thesis, not positions. Option C is the principled bridge.

## Hard-Constraint Verification

| Constraint | A | B | C | E |
|------------|---|---|---|---|
| positions count == registry count after fix | yes | signals if not | yes | n/a |
| Watchdog ticks every open position | unchanged (watchdog uses live API) | unchanged | unchanged | unchanged |
| Sniper sees every open position | unchanged | unchanged | unchanged | unchanged |
| No Shadow regression | scoped to `exchange_mode='bybit_demo'` | mode-aware | mode-aware | bybit_demo only |
| No double-registration | n/a | n/a | uses `if sym in _trades: continue` (idempotent) | n/a |
| Backfill only with status=open and recent updated_at | n/a | n/a | only registers symbols confirmed-live by API | n/a |

All constraints satisfied for the recommended option set.

## Recommendation

**Ship Option D (A + B + operator-supervised backfill)** as the primary J1 fix. **Ship Option E** as a separate small hardening commit. **Defer Option C and Option F** unless operator decides belt-and-suspenders coverage is wanted.

### Recommended commit sequence on `fix/j1-orphan-positions`

1. `j1/phase3-0-backfill` — operator runs `scripts/backfill_orphan_positions.py --dry-run`, then `--yes`. No code change. Establishes a clean starting state.
2. `j1/phase3-a-cache-prune` — adapter-level symmetric prune in `BybitDemoPositionService.get_positions_with_confirmation`. New `delete_positions_bulk` helper in `trading_repo.py`. New `POSITIONS_CACHE_PRUNE` log event. Dwell-time guard for `confirmed=True, positions=()` case. Unit tests covering: confirmed=True with prune; confirmed=False without prune; confirmed-empty-once (no prune); confirmed-empty-twice (prune).
3. `j1/phase3-b-position-reconciler` — new `src/workers/position_reconciler.py` worker, 60s cadence. New `POSITION_RECONCILE`, `POSITION_RECONCILE_DRIFT`, `FUND_INUSE_DRIFT` events. Boot wiring in `manager.py`. Unit tests.
4. `j1/phase3-e-pagination` — pagination loop with 5-page cap and `BYBIT_DEMO_POSITIONS_PAGINATION_CAP` warning. Unit tests.

Phase 4 verification spans 24+ hours and confirms: positions count matches WD_TICK n; POSITION_RECONCILE_DRIFT fires only on genuine drift; no Shadow regression; cascade absence maintained.

## Verification Metrics

For Phase 4 verification of J1:

- `POSITIONS_CACHE_PRUNE` events fire when expected (a Bybit-side close happens and the next tick prunes the stale row before the close-callback chain). The two delete paths now race-correctly because both are idempotent.
- `POSITION_RECONCILE_DRIFT` count: 0 during normal operation; non-zero if and only if drift exists.
- Daily `SELECT COUNT(*) FROM positions WHERE exchange_mode='bybit_demo' AND size > 0` matches the latest `WD_TICK n=` within a 1-tick skew.
- Shadow smoke test: open/close a Shadow trade; verify Shadow path unchanged; verify `positions` table is unaffected.
- Cascade absence: `CASCADE_DETECTED` count remains 0 throughout the verification window.

## Open Questions For The Phase 2 Operator Report

The Phase 2 report will present these for explicit operator approval:

1. Run the four-row backfill (existing tool) as Step 0 of Phase 3?
2. Ship Option A (adapter cache prune)?
3. Ship Option B (PositionReconciler worker)?
4. Ship Option E (pagination loop)?
5. Skip Option C (boot backfill) for now, or include it as defence-in-depth?
6. Skip Option F (zombie dwell-time guard), or include it?
7. Reconciler dimensions: position-count only, or include `FUND_INUSE_DRIFT` for available-balance gap?
8. Reconciler thresholds: confirm 2-tick dwell for position-count drift and `max($1000, 0.5% * bybit_total)` for available-balance drift?

## What This Investigation Did Not Do

- No code changes.
- No new dependencies installed.
- No tests run (Phase 1 is read-only; the test plan is for Phase 3).
- No commits.

## Compliance With Master Prompt + Project CLAUDE.md

- **Rule 1 (investigation first)**: Done. Ten Phase 1 deliverables.
- **Rule 4 (understand before touch)**: All in-scope files read end-to-end (`trade_coordinator.py`, `bybit_demo_adapter.py`, `fund_reconciler.py`, `position_watchdog.py`, `thesis_manager.py`, `trading_repo.py`, `shadow_adapter.py`).
- **Rule 5 (no assumptions)**: Each audit claim verified against current code. Audit hypothesis 2 (trade_id NULL) explicitly falsified.
- **Project CLAUDE.md grep-before-touch**: Grep was performed for `_open_positions`, `on_trade_opened`, `register_trade`, `save_position`, `delete_position` before any commitment.
- **Screen-reader friendliness**: h2/h3 structure, no emoji, complete sentences.
