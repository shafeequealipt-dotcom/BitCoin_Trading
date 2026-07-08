# Phase 0 Baseline — Seven Application-Layer Fixes (J1 to J7)

Captured 2026-05-14 22:30 UTC. No code changes. Read-only.

## H1 Pre-Conditions

### B1a regime detector fix present

Commit `6938c69 docs(regime-investigation): end-to-end pipeline check against real production data` is present in the current branch history (`fix/db-concurrency-refactor`). The substantive B1a fix landed earlier; this commit is the verification doc that confirmed it works.

### DB cascade absence

Across the five most recent workers log files (covering 2026-05-13 05:19 through 2026-05-14 22:00 UTC), `CASCADE_DETECTED` occurrences: **0**. The cascade pattern that dominated earlier monitoring sessions remains absent.

### Working tree status

Branch `fix/db-concurrency-refactor`. Modified: `data/layer_state.json`, `data/logs/layer1c_full.jsonl`, `systemd/trading-workers.service`. No `src/` changes pending. Multiple untracked dev_notes from prior investigations. Safe to start a new branch off this tip.

### Workers liveness

Most recent log file `data/logs/workers.2026-05-14_21-24-42_919966.log` is actively written; FUND_RECONCILE is emitting once per minute as of 22:27 UTC. Workers are healthy.

## J1 Baseline — Orphan Positions

### Three-view snapshot, 22:30 UTC

```
positions table (size > 0, exchange_mode='bybit_demo'): 4 rows
  SANDUSDT Sell 11155.0 @ 0.08068   updated_at=2026-05-13T07:50
  EGLDUSDT Buy   42.0   @ 4.761     updated_at=2026-05-13T10:13
  RUNEUSDT Sell  2209.8 @ 0.6109    updated_at=2026-05-13T10:31
  AAVEUSDT Sell  9.04   @ 99.54     updated_at=2026-05-13T10:31

trade_thesis status='open': 0 rows

Recent WD_TICK n distribution (workers.2026-05-14_21-24-42_919966.log):
  n=0   238 ticks  ←  current state
  n=6    12
  n=7    10
  n=8    10
  n=9    64
  n=10   34
```

### Live reconciler readout (smoking gun for H4)

`FUND_RECONCILE` at 22:27:07 UTC:

```
bybit_total = 183,666.46
bybit_available = 99,951.34
local_total = 183,666.46
local_cap = 91,833.23
local_avail = 91,833.23
drift_pct = +0.00
auto_correct = false
```

Bybit reports ~$83.7K margin in use; local view believes $0 in use. `drift_pct` is `+0.00` because it compares `total_equity` (matched on both sides) and is structurally blind to the available-balance gap.

### Audit-window baseline (2026-05-14 20:35-21:46, `/home/inshadaliqbal786/SESSION_LOGS_2026-05-14_20-35_to_21-46.log`)

```
WD_TICK n distribution across 410 ticks:
  n=2    41
  n=3     9
  n=5    50
  n=6    42
  n=7    44
  n=8    83
  n=9    92   ← modal peak
  n=10   49

Five orphan symbols' WD_TICK presence: 0 ticks each
  AAVEUSDT, EGLDUSDT, RUNEUSDT, SANDUSDT, DYDXUSDT

BYBIT_DEMO_PERSIST_POSITION_FAIL:   0
BYBIT_DEMO_POSITIONS_UNKNOWN_STATE: 0
CASCADE_DETECTED:                   0
ZOMBIE_RECONCILE_FAIL:              0
zombie / reconcile_with_shadow:     0 calls during window
GHOST_RECONCILED:                   1 (ICPUSDT only)

FUND_RECONCILE ticks:               38, drift_pct range -0.03% to +0.03%, no alerts
```

The audit's claim of "8 max" was slightly conservative — peak observed n in the log was 10. The five orphan symbols never appeared in any WD_TICK during the window, which matches the H1 stale-cache root-cause hypothesis: the rows exist only in the local cache table, not in Bybit's response.

## J2 Baseline — Cross-Direction

From the audit-window log:

```
APEX_DIR_LOCK_OVERRIDE: 1 event total
  21:09:10  DYDXUSDT  qwen_tried=Sell  locked_to=Buy  regime=trending_up
```

DYDXUSDT was the only cross-direction trigger and is the case the audit highlights for J2. The orphan Sell was stale-cache only; on Bybit truth there was no Sell to conflict with.

## J3 Baseline — XRAY ↔ DIR_LOCK Precedence

The combined J2+J3+J7 count `CLAUDE_PROC_STALL + APEX_DIR_LOCK_OVERRIDE + XRAY_FLIP_SUPPRESSED_BY_LOCK + SENTINEL_ADVISOR_SKIP` across the audit window is 43 (full distribution to be broken out in J3's Phase 0 once that issue starts). The audit's per-event counts will be re-verified in each issue's Step 1.1 before any fix is proposed.

## J4 Baseline — Claude CLI Stalls

Full distribution to be broken out at the start of J4's Phase 0. Audit summary: 20 `CLAUDE_PROC_STALL` events in 1h45m; one ERROR-level 240s stall; one CALL_A at 268 seconds.

## J5 Baseline — APEX Sizing

To be re-verified at the start of J5's Phase 0. Audit summary: 15 of 18 APEX_SIZING events clamped to $1200 (83 percent).

## J6 Baseline — Re-Entry Without Learning

To be re-verified at the start of J6's Phase 0. Audit cases: MNT (-$10.75), XRP (-$20.36), ICP (-$21.20, this one won).

## J7 Baseline — Sentinel Direction-Blind

To be re-verified at the start of J7's Phase 0. Audit: 3 `SENTINEL_ADVISOR_SKIP` "not tighter" events on short positions during the window.

## Conditions To Watch Throughout J1-J7

- Every phase confirms `CASCADE_DETECTED` count remains 0 in the most recent 6h window. If cascades return, pause and escalate.
- Every phase confirms the workers process is healthy at the start.
- Every phase confirms the B1a regime fix is still in place (commit `6938c69` reachable from HEAD).
- The DB concurrency refactor (a separate prompt) is in progress on `fix/db-concurrency-refactor`. J1's branch will be cut from this tip and must not undo any pool-related changes.

## Approval Gate Status

Phase 0 baseline is complete. Proceeding to J1 Phase 1 investigation. No code changes have been made. The operator will be asked to approve the Phase 1 synthesis (and the recommended fix path) before any J1 source edits.
