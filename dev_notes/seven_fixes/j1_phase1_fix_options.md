# J1 Phase 1 Step 1.1.9 — Fix-Option Evaluation

Captured 2026-05-14 23:05 UTC. Read-only.

## Framework

Each option is evaluated against:
- Master prompt Rule 3 — no band-aid choices (per the forbidden list)
- Hard constraints (from J1 spec): positions count == registry count after fix; watchdog ticks every open position; sniper sees every open position; no Shadow regression; no double-registration; backfill only registers positions with status=open AND recent updated_at
- Master prompt Rule 6 — observability events mandatory
- Master prompt Rule 8 — aggressive-exploitation philosophy preserved (no behaviour-blocking conservatism)
- Master prompt Rule 10 — Shadow must continue to work

## Option A — Adapter-Level Symmetric Cache Prune

**Mechanic**: After `BybitDemoPositionService.get_positions_with_confirmation` finishes with `confirmed=True`, query `SELECT symbol FROM positions WHERE exchange_mode='bybit_demo'`, compute diff against current response, DELETE rows not in the response. Skip the prune entirely when `confirmed=False` to avoid phantom-close cascades.

**Code site**: `src/bybit_demo/bybit_demo_adapter.py:181-290` and `src/database/repositories/trading_repo.py` (new bulk `delete_positions(symbols, exchange_mode)` method).

**New observability**: `POSITIONS_CACHE_PRUNE | mode=bybit_demo sym=X reason=missing_from_response`.

**Pros**:
- Makes the adapter the single source of truth for the cache. Bytes-equivalent write/prune symmetry.
- Closes the structural asymmetry that produced the four current stale rows.
- Independent of the watchdog's vanished-detection — works even on first tick.

**Cons**:
- Overlaps with the existing close-callback cleanup chain (`POSITION_ROW_DELETED`). Double-delete is idempotent so no harm, but the observability now has two deletion sources.
- If for any reason the response is `confirmed=True, positions=()` due to an unhandled error path (per the documented follow-up in `i1_phase1_investigation.md`), the prune would still delete everything. The Issue I1 fix only guards the watchdog's downstream path against this; the adapter-level prune is exposed.

**Risk mitigations**:
- Skip prune on `confirmed=False` (already in the design).
- Add additional guard: skip prune if `confirmed=True AND len(positions)==0 AND len(db_rows)>0`. Require two consecutive empty-confirmed ticks before pruning (dwell time). This addresses the Issue I1 follow-up.
- Test: simulate `confirmed=True, positions=()` for one tick and verify no prune fires; simulate two consecutive and verify prune fires.

**Forbidden band-aid check**:
- "Adding a periodic sweep that scoops up missed positions" — NOT this. The prune fires at the exact moment the adapter writes; it is symmetric with the write, not a periodic sweeper.
- "Auto-deleting positions table rows that aren't in registry" — NOT this. The criterion is "not in Bybit's confirmed response," which IS the source of truth.

**Verdict**: STRONG. Closes the structural asymmetry. Includes the dwell-time guard as a no-band-aid hardening.

## Option B — PositionReconciler Worker (H4)

**Mechanic**: New worker `src/workers/position_reconciler.py`, 60s cadence. Per tick:
1. Query `SELECT COUNT(*) FROM positions WHERE size > 0 AND exchange_mode = ?` for the active mode.
2. Read the last confirmed `get_positions_with_confirmation` result (cached on the watchdog or service container).
3. Compare counts. If different for 2 consecutive ticks, emit `POSITION_RECONCILE_DRIFT`.
4. Optionally compute Bybit `in_use` vs local `in_use` and emit `FUND_INUSE_DRIFT` if the gap exceeds `max($1000, 0.5% * bybit_total)`.

**Code sites**: New worker file. Boot wiring in `src/workers/manager.py` adjacent to `fund_reconciler` registration.

**New observability**: `POSITION_RECONCILE` per tick, `POSITION_RECONCILE_DRIFT` on alert, `FUND_INUSE_DRIFT` on margin alert.

**Pros**:
- Catches future drift from any source (not just adapter-level — Bybit API anomalies, manual operator entries, regressions).
- Single-responsibility worker per the project's own architectural principle (see `fund_reconciler.py` header comment).
- Pure observability — does not modify data, does not auto-correct, does not slow trading.
- Aligns with master prompt Rule 6 (mandatory new events for the corrected behaviour).

**Cons**:
- Adds a new worker; one more thing to monitor and start.
- Threshold tuning may require operator iteration.

**Forbidden band-aid check**:
- "Auto-deleting positions table rows that aren't in registry" — NOT this. Pure signal, no auto-delete.
- "Hiding orphans from dashboard" — opposite; the reconciler explicitly surfaces them.

**Verdict**: STRONG. Required regardless of whether A or C ships. Without B, future drift remains invisible.

## Option C — Boot-Recovery `_trades` Backfill From `positions`

**Mechanic**: After `TradeCoordinator.recover_state_from_db` reads `trade_thesis WHERE status='open'`, additionally query `positions` table for the active mode. For each row in `positions` whose symbol is not already in `_trades` AND that appears in `get_positions_with_confirmation(confirmed=True)`, register a minimal `TradeState` with `source="boot_position_backfill"`.

**Code sites**: `src/core/trade_coordinator.py:183-288` (recover_state_from_db) and `src/workers/manager.py:579` area (the call site).

**New observability**: `POSITION_REGISTRY_BACKFILL | sym=X side=Y entry=Z source=positions_table mode=bybit_demo`.

**Pros**:
- Closes the H2 boot-recovery scope gap (brain wakes up unaware of positions Bybit still has).
- Idempotent — only registers symbols not already in `_trades`.
- Cross-verified against live API truth — only registers symbols that confirm-true on Bybit.

**Cons**:
- Requires synchronous live API call during boot — adds boot latency.
- The minimal TradeState lacks rich context (decision_id, brain reasoning, original SL/TP) — the watchdog has to enrich.
- May surprise the operator: positions appear in the dashboard without a recent thesis row.

**Forbidden band-aid check**:
- "Force-closing orphans on startup" — NOT this. Backfill REGISTERS them so the brain manages them properly.
- "Hiding orphans from dashboard" — opposite.

**Verdict**: USEFUL but conditional. Only needed if the audit-window forward-race or external operator entry is realistic. Pending Step J1.1.4 conclusion: the forward race is theoretical, not observed. Recommend including C only if the operator wants belt-and-suspenders coverage.

## Option D — Combination A + B + C

**Recommended sequencing**:

1. Run `scripts/backfill_orphan_positions.py --dry-run` first (no code change).
2. Operator approves the four-row cleanup; run `--yes`.
3. Ship A (adapter-level symmetric prune) as commit `j1/phase3-a-cache-prune`.
4. Ship B (PositionReconciler worker) as commit `j1/phase3-b-position-reconciler`.
5. Optionally ship C (boot backfill) as commit `j1/phase3-c-boot-backfill`. Skip if Phase 2 operator decision is "diagnostic only."

Each commit atomic, on branch `fix/j1-orphan-positions`.

**Verdict**: My recommendation. A is structural, B is observability, C is conditional defence-in-depth.

## Option E — Bybit V5 Pagination Loop (H3)

**Mechanic**: Add `nextPageCursor` loop in `BybitDemoPositionService.get_positions_with_confirmation` capped at 5 pages.

**Code site**: `src/bybit_demo/bybit_demo_adapter.py:181-290`.

**New observability**: `BYBIT_DEMO_POSITIONS_PAGINATION_CAP` if cap hit; benign because today's volumes are 1-14 positions and the default Bybit limit is 20.

**Pros**: Removes a latent failure mode at scale.

**Cons**: Adds latency to every `get_positions` call (one extra request per ~20 positions).

**Verdict**: Low priority. Include as defence-in-depth if the operator wants it; else defer to a separate hardening commit later.

## Option F — Zombie Reconciler Forward-Race Guard

**Mechanic**: Per Step J1.1.4 recommendation, require two consecutive `confirmed=True` ticks before allowing the zombie reconciler to run on an empty/missing-symbol set.

**Code site**: `src/workers/position_watchdog.py:542-552`.

**Verdict**: Low priority. The race is theoretical and not observed in the audit. Defer.

## Recommendation Summary

- **Ship D (= A + B with operator-approved backfill)** as the primary J1 fix.
- **Ship E** as a separate small commit in the same branch.
- **Ship C** only if operator wants belt-and-suspenders coverage.
- **Defer F** unless `BYBIT_DEMO_POSITIONS_UNKNOWN_STATE` count rises in the J1 verification window.

## Open Questions For Operator

1. Approve the four-row backfill (existing tool, no new code)?
2. Approve A (adapter-level symmetric prune) as the structural fix?
3. Approve B (PositionReconciler worker) as the observability fix?
4. Approve C (boot-recovery backfill) — yes/no?
5. Approve E (pagination loop) — yes/no?
6. Approve F (zombie dwell-time guard) — yes/no/defer?
