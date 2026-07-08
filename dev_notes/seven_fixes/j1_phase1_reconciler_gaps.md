# J1 Phase 1 Step 1.1.5 — Reconciler Gap (H4)

Captured 2026-05-14 22:50 UTC. Read-only.

## What `fund_reconciler` Watches Today

`src/workers/fund_reconciler.py`, end-to-end:

- Cadence: `settings.fund_manager.reconcile_interval_seconds`, default 60s. Verified: 38 `FUND_RECONCILE` events in the 71-minute audit window — once per minute.
- Inputs: `account_service.get_wallet_balance()` (Bybit truth) and `fund_manager._account_state` (local view).
- Comparison axis (line 143-150):

  ```python
  if bybit_total > 0:
      drift_pct = ((local_total - bybit_total) / bybit_total) * 100.0
  else:
      drift_pct = 0.0
  ```

  **Only `total_equity` is compared.** `available_balance` is read (`bybit_available`, `local_avail`) and logged but not used in the drift calculation.
- Output (line 152-158): `FUND_RECONCILE | bybit_total=... bybit_available=... local_total=... local_cap=... local_avail=... drift_pct=+/-X.XX auto_correct=true|false`.
- Alert: `FUND_RECONCILE_DRIFT` WARNING fires only when `abs(drift_pct) > threshold_pct` (default 5%). Optional Telegram alert + opt-in auto-correct.

## Why The $81.7K Margin Gap Is Invisible

The audit observed Bybit `in_use` margin ~$84.6K vs local `in_use` $2.9K, an $81.7K gap. The reconciler's formula:

```
drift_pct = (local_total - bybit_total) / bybit_total
```

Equity components: total_equity = available + in_use. If Bybit has in_use=$84.6K and local has in_use=$2.9K but BOTH total_equity values match (because Bybit's accounting still includes the margin'd positions' notional in total_equity), then drift_pct = 0.

This is exactly the live reading at 22:27 UTC (Phase 0 baseline):

```
bybit_total = 183,666.46
bybit_available = 99,951.34   →  ~$83.7K in_use on Bybit
local_total = 183,666.46
local_avail = 91,833.23       →  ~$0 in_use locally (full trading capital available)
drift_pct = +0.00
```

The reconciler is structurally blind to this gap. It is doing exactly what the design says — compare totals — but the design does not catch the dimension that fails in the orphan scenario.

The four current stale rows are not actively eating margin (Bybit's WD_TICK n=0 means no positions are open on Bybit demo right now), so the $83.7K Bybit in_use is something else — likely unrelated holdings on the account. But the architectural point stands: if those four rows DID represent live margin, the reconciler would not notice.

## What The Fix Needs To Add

A position-count and margin-availability cross-check. Specifically:

### Dimension 1 — Position-count drift

```
positions_db = SELECT COUNT(*) FROM positions
                WHERE size > 0 AND exchange_mode = current_mode
positions_bybit = last get_positions_with_confirmation length (confirmed=True)

if positions_db != positions_bybit:
    emit POSITION_RECONCILE_DRIFT |
        mode={current_mode}
        db_count={positions_db}
        bybit_count={positions_bybit}
        diff={positions_db - positions_bybit}
```

This catches the four current stale rows immediately: `db_count=4, bybit_count=0, diff=+4`. Operator sees the discrepancy in the dashboard / Telegram and runs the backfill script.

Dwell-time: alert only when the drift persists for two consecutive reconciler ticks (60s minimum), to avoid noise during fast open/close churn. Use `_last_drift_count` instance state.

### Dimension 2 — Available-balance drift

```
in_use_bybit = bybit_total - bybit_available
in_use_local = local_total - local_avail
in_use_drift = in_use_bybit - in_use_local

if abs(in_use_drift) > $X (e.g., $1000) and abs(in_use_drift / bybit_total) > Y%:
    emit FUND_RECONCILE_INUSE_DRIFT |
        in_use_bybit=...
        in_use_local=...
        in_use_diff=...
```

This catches the margin-allocation divergence that today's `drift_pct=+0.00` hides. Threshold should be operator-tunable (Bybit's $99,951 vs local $91,833 is $8,118 — a real but small gap that does not need to alarm; the audit's $81.7K does need to alarm).

## Architectural Choice

Two options:

- **Option B1 — Extend `fund_reconciler`.** Add the two new dimensions to its `tick()` method. Single worker, single point of audit. Slightly larger blast radius (one method does more), but matches the original design intent (reconciliation lives here).
- **Option B2 — New sibling worker `position_reconciler`.** Single-responsibility: only checks position-count and margin drift. Cleaner separation but adds boot-time configuration and another worker in the heartbeat census.

The fund_reconciler module's own header comment (line 12-23) explicitly advocates **separate workers for separate concerns**:

> Why a separate worker instead of folding the comparison into `FundManager.update_state`? ... Single-responsibility: ... Adding drift detection there would make a hot-path method also responsible for cross-source reconciliation, alerting, and operator opt-in semantics. Three concerns in one method invites future regressions.

By the same logic, position-count reconciliation should NOT be folded into `fund_reconciler` — it should be its own sibling worker.

**Recommendation**: Option B2 (new `PositionReconciler` worker). Same cadence as fund_reconciler (60s) but separate file, separate boot wiring, separate alert path. Reuses the same `account_service` and reads `positions` table directly. Emits `POSITION_RECONCILE` (info) per tick and `POSITION_RECONCILE_DRIFT` (warning) on detected drift.

## Compliance With Master Prompt Rules

- **Rule 3 (no band-aids)**: A new dimension to the reconciler is structural early-warning, not a periodic sweeper. It does NOT delete or modify rows — it emits a signal for the operator to act (or for the operator-approved auto-correct path).
- **Rule 6 (observability)**: `POSITION_RECONCILE` per tick, `POSITION_RECONCILE_DRIFT` on alarm, `FUND_RECONCILE_INUSE_DRIFT` for the available-balance dimension if extending the existing reconciler.
- **Rule 8 (aim preservation)**: Pure observability addition. Does not slow trade frequency or bias toward capital preservation. Operator sees more; behaviour unchanged.

## Open Questions For Operator

1. **Sibling worker vs extension** — confirm Option B2 (sibling) over Option B1 (extend). My recommendation: B2.
2. **Thresholds** — for position-count drift, alert at any non-zero diff (with 2-tick dwell), or at diff > N? For available-balance drift, alert at $X or Y%? My recommendation: position-count `diff != 0` with 2-tick dwell; available-balance `abs(diff) > max($1000, 0.5% * bybit_total)`.
3. **Auto-correct behaviour** — should `POSITION_RECONCILE_DRIFT` trigger an auto-prune (DELETE stale rows automatically) under operator opt-in? My recommendation: NO. Drift signal only; cleanup remains operator-supervised via the existing backfill script. Auto-prune is too close to a band-aid sweeper (Rule 3) and risks deleting a row mid-race.
