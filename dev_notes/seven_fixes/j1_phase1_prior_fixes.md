# J1 Phase 1 Step 1.1.1 — Prior Orphan-Position Fix History

Captured 2026-05-14 22:30 UTC. Read-only.

## Why This Matters

J1 is not a greenfield investigation. Four prior commits shipped substantial work in the orphan-position area, and assuming today's audit observations are caused by a still-unaddressed gap risks regressing the existing fixes. This document maps what shipped, what each fix did, and what gap (if any) remains.

## Commit Inventory

### `c4eef5c` — fix(i2): orphan-positions — TradeState.exchange_mode + cleanup-callback fix + backfill (2026-05-14 10:54 UTC)

Authored less than 12 hours before the audit window started. Substantial.

What it shipped:

- **TradeState gains `exchange_mode` field** at `src/core/trade_coordinator.py`. `register_trade` captures the transformer's `current_mode` at entry time and stores it on the state object. `on_trade_closed` prefers `state.exchange_mode` over `transformer.current_mode` at dispatch time, with a fallback for legacy pre-I2 trades.
- **`_positions_table_cleanup_on_close` callback hardened** at `src/workers/manager.py:2198-2222`. Pre-fix three gaps:
  1. Mode-gating silent return — read `transformer.current_mode` at dispatch; if transformer wasn't attached or mode was briefly wrong, callback silently returned. Every close during the window leaked.
  2. Used `asyncio.get_event_loop()` (deprecated) which returns a closed loop after shutdown.
  3. No success-side observability.
- **Fix**: read `record.get("exchange_mode", "")` from the close record (set by register_trade), use `asyncio.get_running_loop()`, emit `POSITION_ROW_DELETED` on success and `POSITION_ROW_DELETE_SKIP` on mode-gate trip.
- **New structured emissions**: `POSITION_ROW_DELETED`, `POSITION_ROW_DELETE_FAIL`, `POSITION_ROW_DELETE_SKIP`, `POSITION_ORPHAN_BACKFILL_*`.
- **Backfill script**: `scripts/backfill_orphan_positions.py` — operator-supervised one-shot. Identifies rows in `positions` table that are not in coordinator's `_trades` map and have no open `trade_thesis`. Prompts before delete; supports `--dry-run` and `--yes`.

This fix corresponds nearly 1:1 with H1 of my plan's hypothesis ranking. **The H1 structural fix is already in place.**

### `06672f0` — feat(i2-i3/phase3): positions-table cleanup + WD_CLOSE recovery (2026-05-11 12:25 UTC)

The precursor to c4eef5c. What it shipped:

- `trading_repo.delete_position(symbol)` — new explicit cleanup entry, idempotent (DELETE on missing row is no-op).
- `workers/manager.py`: registered `_positions_table_cleanup_on_close` as the 16th close-callback. Fires on every `coordinator.on_trade_closed` dispatch (WS, watchdog, sniper, manual). Pre-fix the positions table relied on `close_position` calling `save_position(size=0)` to trigger DELETE-on-zero; **external SL/TP closes (which fire on Bybit's matching engine, NOT through `close_position`) bypassed that DELETE path**. All 6 zombies observed at the time were externally-closed positions — 100 percent leak rate for external closes.

This is the **architectural recognition** that exchange-side closes need an explicit cleanup hook. c4eef5c hardened the callback after it shipped with the mode-gating bug.

`06672f0` also extended `position_watchdog.py` with two SELECT fallbacks (`trade_thesis` and `orders`) that populate missing `entry_price` and `direction` fields in WD_CLOSE records — closes another silent-success path.

### `f13fbee` — docs(i2-i3/phase1): zombie positions + corrupted WD_CLOSE investigation (2026-05-10)

Investigation document only, no code changes. Established the dual problem: (1) external closes leak positions table rows, (2) WD_CLOSE emits ent=$0 / dir="" / pnl$=0 whenever coordinator state is absent. Both addressed by `06672f0`.

### `0a1d825` — fix(p5): re-target close_thesis to overwrite zombie-reconciler rows (L9-G3 + L9-G4)

What it shipped: `close_thesis` WHERE clause extended to also catch and overwrite zombie-reconciled rows. The race it addressed: zombie_reconciler closes a thesis with pnl=0; the authoritative close path arrives milliseconds later but the WHERE clause matched only `status='open'` and skipped the now-`status='closed'` row, leaving the zombie record as the durable truth. Post-fix the authoritative close can overwrite the zombie close, preserving real pnl and exit price.

This does **not** address the forward race (zombie closes a thesis while Bybit position is still open), only the reverse race.

## Are The Prior Fixes Actually Working?

Yes. Verified in `data/logs/workers.2026-05-14_*.log` (61 `POSITION_ROW_DELETED` events across the post-fix window). Sample:

```
21:29:13  POSITION_ROW_DELETED  sym=ARBUSDT  src=close_callback
21:29:39  POSITION_ROW_DELETED  sym=ICPUSDT  src=close_callback
21:30:54  POSITION_ROW_DELETED  sym=AXSUSDT  src=close_callback
21:40:09  POSITION_ROW_DELETED  sym=DOGEUSDT src=close_callback
21:47:26  POSITION_ROW_DELETED  sym=DYDXUSDT src=close_callback
21:47:26  POSITION_ROW_DELETED  sym=IMXUSDT  src=close_callback
21:47:27  POSITION_ROW_DELETED  sym=ARBUSDT  src=close_callback
21:47:27  POSITION_ROW_DELETED  sym=XRPUSDT  src=close_callback
21:47:27  POSITION_ROW_DELETED  sym=ENAUSDT  src=close_callback
21:47:27  POSITION_ROW_DELETED  sym=MNTUSDT  src=close_callback
```

DYDX is in the deletion list (21:47:26) — that is the audit's "cross-direction Buy" being cleaned up correctly on close. The cleanup path is firing for new closes.

## Why The 4 Currently-Stale Rows Remain

The four rows in `positions` table (SAND, EGLD, RUNE, AAVE — all with `updated_at=2026-05-13`) **predate the c4eef5c fix**, which shipped at 10:54 on 2026-05-14. They were created on 2026-05-13 by:

1. **`bybit_demo_sl_tp` closes on Bybit** (Bybit's matching engine triggered the stop). The pre-c4eef5c cleanup callback's mode-gating silently skipped these closes, leaving rows in the cache.
2. **`zombie_reconciler` close** (AAVE has this signature) — the reconciler closed the thesis but had no positions-table delete hook before `06672f0` shipped.

The c4eef5c fix prevents NEW orphan accumulation but does not retroactively clean residuals. The `scripts/backfill_orphan_positions.py` script is the supplied tool for that one-shot cleanup; it has not been run since the four rows were created.

This is operationally important: **the J1 implementation may not require new code at all to clean these four rows — running the existing backfill script may suffice**, with a follow-up reconciler enhancement (H4) to detect future drift.

## What This Means For J1's Scope

The structural H1 fix is already shipped (c4eef5c + 06672f0). The remaining J1 work is:

1. **Operational cleanup** — run `scripts/backfill_orphan_positions.py` (operator-supervised dry-run first, then `--yes`) to clean the 4 historic rows. No new code required.
2. **Reconciler enhancement (H4)** — `fund_reconciler` still watches only equity totals. The structural blindness to margin/position-count drift means future divergences will be invisible. New code required: add a position-count check to `fund_reconciler` (or a new sibling worker) that compares `positions` table count for the active mode against the last confirmed `get_positions_with_confirmation` size. Emit `POSITION_RECONCILE_DRIFT` on mismatch.
3. **Boot recovery extension (H2)** — `TradeCoordinator.recover_state_from_db` reads only `trade_thesis WHERE status='open'`. If `zombie_reconciler` closes a thesis while Bybit still has the position (forward race or external operator action), the brain wakes up unaware of that position. New code: optionally extend `recover_state_from_db` to also check the `positions` table for the active mode. Emit `POSITION_REGISTRY_BACKFILL` per restored row. **Pending verification in Step J1.1.4 that the forward race actually fires.**
4. **Defence-in-depth (H3)** — Bybit V5 `/v5/position/list` pagination loop. Below the default page-size today, so optional.

## Compliance With Master Prompt Rules

- **Rule 3 (no band-aids)**: The c4eef5c fix targeted the actual gap (mode-gating bug), not a sweeper. The backfill script is explicitly one-shot. Reconciler enhancement (H4) is structural early-warning, not a periodic sweep.
- **Rule 6 (observability)**: `POSITION_ROW_DELETED`, `POSITION_ROW_DELETE_FAIL`, `POSITION_ROW_DELETE_SKIP`, `POSITION_ORPHAN_BACKFILL_*` are already in production. J1's new work adds `POSITION_RECONCILE_DRIFT` and (if H2 confirmed) `POSITION_REGISTRY_BACKFILL`.
- **Rule 10 (do not break Shadow)**: All prior fixes are mode-gated to `bybit_demo`. The Shadow path is untouched. Future J1 work must preserve this.

## Open Question For Operator

**Should the four historic stale rows be cleaned with the existing backfill script as the first action of J1 Phase 3, before any new code is written?** A separate option is to leave them in place until the H4 reconciler enhancement lands so the reconciler can fire `POSITION_RECONCILE_DRIFT` on them as a live test. Both are defensible. Recommendation: clean them first (the rows do not represent live positions and the backfill is the explicit tool); use the H4 reconciler tests to verify drift detection separately.
