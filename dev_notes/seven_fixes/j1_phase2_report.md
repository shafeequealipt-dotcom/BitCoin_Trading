# J1 Phase 2 Report — Orphan Position Bug — Operator Decision Required

Written 2026-05-14 23:15 UTC. Read-only investigation complete. No code has been changed. The operator's decision on the fix path is required before any source edits.

## Executive Summary

The audit's orphan-position bug has been investigated in depth across ten Phase 1 deliverables. The structural root cause (pre-`c4eef5c` mode-gating bug in the cleanup callback) is **already fixed in production**. Four residual stale rows remain from before the fix shipped (2026-05-14 10:54 UTC). The remaining work is operational cleanup plus an early-warning reconciler so future drift becomes visible.

## What The Audit Said

LIVE_OBSERVATIONS.md OBS-09 reported 13 positions in the local `positions` table at peak with only 8 tracked by workers during 2026-05-14 20:35-21:46 UTC. Five symbols (AAVE, EGLD, RUNE, SAND, DYDX) were called orphans persisting for the 2-hour window. An $81.7K margin gap was attributed to invisible orphan margin on Bybit.

The audit hypothesised two things:

1. The boot-time discovery path misses positions that existed in a prior session.
2. The in-memory registry is keyed by `trade_id` and orphans have NULL/stale trade_id.

## What The Investigation Found

### Hypothesis 2 is wrong

The in-memory registry is `TradeCoordinator._trades: dict[str, TradeState]` at `src/core/trade_coordinator.py:146`, keyed by **symbol**. No `trade_id` key exists. The audit's `_open_positions` dict does not exist in production code outside the offline simulator.

### Hypothesis 1 is directionally correct but mis-targeted

The boot path is `recover_state_from_db` at `src/core/trade_coordinator.py:183-288`. It reads `SELECT ... FROM trade_thesis WHERE status='open'` only. It does not read the `positions` table. This is the H2 hypothesis in my plan ranking, and it is real but theoretical (the forward race that would trigger it is not observed in the audit window).

### The actual root cause was already identified and fixed

The pre-`c4eef5c` cleanup callback `_positions_table_cleanup_on_close` in `src/workers/manager.py` had three gaps:

1. Read `transformer.current_mode` at dispatch time; silently returned if the transformer was not yet attached or in mid-switch.
2. Used `asyncio.get_event_loop()` which returns a closed loop after shutdown.
3. No success-side observability.

Commit `c4eef5c fix(i2): orphan-positions — TradeState.exchange_mode + cleanup-callback fix + backfill` shipped on 2026-05-14 at 10:54 UTC. It:

- Added `exchange_mode` field to `TradeState`; captured at register-time.
- Changed the cleanup callback to read `record.exchange_mode` (the trade's mode), not the global at dispatch.
- Switched to `asyncio.get_running_loop()` (loud failure on missing loop).
- Added `POSITION_ROW_DELETED`, `POSITION_ROW_DELETE_FAIL`, `POSITION_ROW_DELETE_SKIP` log events.
- Shipped `scripts/backfill_orphan_positions.py` for operator-supervised one-shot cleanup of historic stale rows.

### Verification that c4eef5c is working

61 `POSITION_ROW_DELETED` events in the latest 6-hour worker log. Sample at 21:47:26-27 cleaned 9 symbols including DYDXUSDT (the audit's J2 case — DYDX was correctly cleaned on close, post-fix).

### Why four rows remain

The four residual rows (SAND, EGLD, RUNE, AAVE) have `updated_at=2026-05-13`. They predate the fix by hours. The fix prevents NEW orphan accumulation but does not retroactively clean residual rows. The backfill script must be run.

### The reconciler dimension gap

`fund_reconciler` watches `total_equity` drift only. Per the current 22:27 UTC reading, `bybit_total=local_total=183,666` so `drift_pct=+0.00` — but Bybit `available=99,951` vs local `available=91,833`, an $8K margin-allocation gap that the reconciler is structurally blind to. The audit's $81.7K claim was based on a different (potentially historical) data point; the dimension blindness it identified is real and is unaddressed.

### Why the audit observed orphans on 2026-05-14 20:35-21:46

The four (then more) stale rows from 2026-05-13 had never been cleaned. The c4eef5c fix shipped at 10:54 on 2026-05-14; the audit started at 20:35. In between, NEW closes were cleaning correctly via the fix, but historic rows did not get retroactively cleaned. The audit observed those legacy stale rows as orphans because workers' live API result (Bybit truth) did not include them.

## Root-Cause Summary

The fundamental architectural gap is **adapter-level cache asymmetry**: `BybitDemoPositionService.get_positions_with_confirmation` INSERTs every present position into the local `positions` cache but never prunes rows for symbols missing from the response. Pruning is outsourced to the watchdog's vanished-detection + close-callback chain, which the c4eef5c fix made reliable for new closes but cannot retroactively heal pre-fix residue.

A second structural gap is the **reconciler dimension**: equity-only comparison cannot catch position-count or margin-allocation drift.

A third (theoretical) gap is **boot-recovery scope**: `recover_state_from_db` reads only `trade_thesis`, so a position open on Bybit without an open thesis row is invisible to the brain.

## Proposed Fix Path

Detailed evaluation in `dev_notes/seven_fixes/j1_phase1_fix_options.md`. Headline recommendation:

### Option D — ship A + B with operator-supervised backfill

1. **Phase 3 Step 0 — cleanup the four residual rows.** Run `scripts/backfill_orphan_positions.py --dry-run` to enumerate; operator approves; run `--yes`. No code change. This is the explicit, operator-supervised, one-shot tool already shipped by `c4eef5c` for exactly this purpose.

2. **Phase 3 Step A — adapter-level symmetric prune.** Branch `fix/j1-orphan-positions`, commit `j1/phase3-a-cache-prune`. After `BybitDemoPositionService.get_positions_with_confirmation` finishes with `confirmed=True`, prune any `exchange_mode='bybit_demo'` row whose symbol is not in the response set. Skip the prune on `confirmed=False`. Add a dwell-time guard so a single `confirmed=True, positions=()` response does NOT prune everything (two consecutive empty-confirmed ticks required). New event: `POSITIONS_CACHE_PRUNE`. Unit tests for the four cases (confirmed-true-with-prune, confirmed-false-without-prune, confirmed-empty-once-no-prune, confirmed-empty-twice-prune).

3. **Phase 3 Step B — `PositionReconciler` worker.** Commit `j1/phase3-b-position-reconciler`. New sibling worker to `fund_reconciler`, 60s cadence. Per tick compares `positions` table count for the active mode against the last confirmed `get_positions_with_confirmation` length. Emits `POSITION_RECONCILE` per tick and `POSITION_RECONCILE_DRIFT` on detected drift (2-tick dwell). Optionally adds a `FUND_INUSE_DRIFT` event for available-balance gaps above `max($1000, 0.5% * bybit_total)`.

4. **Phase 3 Step E (optional defence-in-depth) — pagination loop.** Commit `j1/phase3-e-pagination`. Adds `nextPageCursor` loop to `BybitDemoPositionService.get_positions_with_confirmation` with a 5-page hard cap. New `BYBIT_DEMO_POSITIONS_PAGINATION_CAP` warning when capped. Today's volumes are well under Bybit's default limit=20, so this is purely future-proofing.

### Defer for now

- **Option C (boot-recovery backfill)**: Closes the H2 forward-race gap. Not observed in the audit window. Skip unless operator wants belt-and-suspenders coverage.
- **Option F (zombie reconciler dwell-time guard)**: Closes the zombie forward-race vulnerability. Not observed. Defer unless `BYBIT_DEMO_POSITIONS_UNKNOWN_STATE` rises in J1 verification.

## What This Does Not Address

- J2 (cross-direction) gets a pre-order check in its own issue. J1's fix removes the primary trigger (stale cache rows) but the J2 fix still adds the defence-in-depth pre-order check.
- J3 (DIR_LOCK precedence), J4 (Claude CLI stalls), J5 (APEX sizing), J6 (re-entry learning), J7 (sentinel direction-blind) are separate issues. Phase 0 baseline captures their current metrics for later use.

## Master-Prompt Rule Compliance

- Rule 1 (investigation first): all ten Phase 1 deliverables written.
- Rule 3 (no band-aids): every fix option evaluated against the forbidden list.
- Rule 4 (understand before touch): every in-scope file read end-to-end.
- Rule 5 (no assumptions): audit hypothesis 2 explicitly falsified.
- Rule 6 (observability): mandatory events identified per option.
- Rule 8 (aim preservation): no fix slows trade frequency or biases toward capital preservation.
- Rule 10 (no Shadow regression): every option mode-scoped to `bybit_demo`.

## Aim Preservation

After J1 ships, the system trades the same number of positions (or more), and trades the same way. The only behavioural change is operational: position cleanup happens reliably, and drift becomes visible. The brain's decision logic, sizing, direction selection, and risk model are untouched.

## Decisions Required

The following six questions await operator answer. I will not start Phase 3 until they are answered.

### Q1 — Approve running `scripts/backfill_orphan_positions.py` to clean the four current stale rows as the first step of Phase 3?

Recommendation: yes. The script is the explicit, operator-supervised tool for this exact case.

### Q2 — Approve Option A (adapter-level symmetric prune)?

Recommendation: yes. Closes the structural asymmetry that produced the orphans.

### Q3 — Approve Option B (PositionReconciler worker) with the proposed dimensions and thresholds?

Recommendation: yes. Position-count drift with 2-tick dwell. Optionally add `FUND_INUSE_DRIFT` for available-balance gaps with threshold `max($1000, 0.5% * bybit_total)`.

### Q4 — Approve Option E (pagination loop)?

Recommendation: yes. Small, low-risk hardening.

### Q5 — Include Option C (boot-recovery backfill) for belt-and-suspenders coverage, or skip it?

Recommendation: skip for now. Add only if Phase 4 verification surfaces an H2 case.

### Q6 — Include Option F (zombie reconciler dwell-time guard), or skip it?

Recommendation: skip for now. Add only if `BYBIT_DEMO_POSITIONS_UNKNOWN_STATE` count rises.

## After Approval

When the operator approves the fix path, Phase 3 begins:

1. Create branch `fix/j1-orphan-positions` from current tip (`fix/db-concurrency-refactor`).
2. Step 0: run backfill script under operator supervision.
3. Step A, B, (E) — implement, test, commit atomically.
4. Phase 4: deploy, monitor 24 hours, capture verification metrics, write `j1_phase4_verification.md`.
5. Sign-off from operator → J2 Phase 1 begins.
