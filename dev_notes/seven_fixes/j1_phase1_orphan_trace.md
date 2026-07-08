# J1 Phase 1 Step 1.1.6 — Orphan Trace (Five Audit Symbols)

Captured 2026-05-14 22:55 UTC. Read-only.

## Method

For each of the audit's five orphan symbols, cross-reference:
- `positions` table (Bybit cache, what the audit called "View 1")
- `trade_thesis` (decision history, the brain's source of truth)
- audit-window log `/home/inshadaliqbal786/SESSION_LOGS_2026-05-14_20-35_to_21-46.log`
- DYDXUSDT-specific cross-direction trace

## AAVEUSDT

**Current state (22:30 UTC)**:
- `positions`: Sell, size 9.04, entry 99.54, updated_at 2026-05-13T10:31, exchange_mode=bybit_demo
- `trade_thesis`: Latest row Sell, closed 2026-05-13 17:31:14 by `zombie_reconciler`
- 22 total historical thesis rows since 2026-03-27 — heavily traded symbol

**Audit window** (2026-05-14 20:35-21:46): 0 appearances in `WD_TICK`. Appears in intelligence layer only (XRAY_NONE_REASON at 20:35:45.482, sentiment aggregation at 20:46:00.272). First mention: line 173. Last mention: line 3052.

**Interpretation**: Trade opened at some point before 2026-05-13 10:31. Position closed on Bybit (SL/TP fill) between 10:31 and the next watchdog tick that didn't include it. Pre-c4eef5c mode-gating bug skipped the cleanup callback. Zombie_reconciler at 17:31 then closed the still-open thesis with pnl=0. The `positions` row was never deleted — it sat at the 10:31 snapshot ever since.

This is the canonical pre-fix orphan signature.

## EGLDUSDT

**Current state**:
- `positions`: Buy, size 42.0, entry 4.761, updated_at 2026-05-13T10:13, exchange_mode=bybit_demo
- `trade_thesis`: Latest row Buy, closed 2026-05-13 10:00:34 by `bybit_demo_sl_tp`
- 24 total thesis rows since 2026-05-02

**Audit window**: 0 `WD_TICK` appearances. XRAY scan at line 167 (20:35:45.434).

**Interpretation**: Bybit SL fired at 10:00:34. The cleanup callback should have run after `bybit_demo_sl_tp` close but the pre-c4eef5c bug silently skipped it. `positions.updated_at` is later than the thesis `closed_at` (10:13 > 10:00:34) — the adapter kept upserting until Bybit stopped returning the symbol.

## RUNEUSDT

**Current state**:
- `positions`: Sell, size 2209.8, entry 0.6109, updated_at 2026-05-13T10:31, exchange_mode=bybit_demo
- `trade_thesis`: Latest row Sell, closed 2026-05-13 10:00:33 by `bybit_demo_sl_tp`
- 16 total thesis rows since 2026-04-28

**Audit window**: 0 `WD_TICK` appearances. XRAY scan at line 149 (20:35:45.360). Last mention at line 706 (REGIME_DIVERGE 20:36:17).

**Interpretation**: Same pattern as EGLD. SL fired at 10:00:33. Cleanup callback skipped. Row stuck at 10:31 cache state.

## SANDUSDT

**Current state**:
- `positions`: Sell, size 11155.0, entry 0.08068, updated_at 2026-05-13T07:50, exchange_mode=bybit_demo
- `trade_thesis`: Latest row Sell, closed 2026-05-13 07:38:39 by `bybit_demo_sl_tp`
- 18 total thesis rows since 2026-04-30

**Audit window**: 0 `WD_TICK` appearances. XRAY scan at line 158 (20:35:45.396).

**Interpretation**: Same pattern. SL fired at 07:38:39. Cleanup skipped. Row stuck at 07:50.

## DYDXUSDT — The J2 Cross-Direction Case

**Current state**: NOT in current `positions` table (it was cleaned at 21:47:26 by the post-c4eef5c cleanup callback during the audit window).

**Thesis history**: 17 rows since 2026-04-24. The most relevant ones:
- 2026-05-13 22:08:01 → 22:10:25 — Sell, closed by `bybit_demo_sl_tp`. Pre-fix.
- 2026-05-13 22:33:25 → 22:53:09 — Sell, closed by `zombie_reconciler`. Pre-fix.
- **2026-05-14 21:09:12 → 21:47:26 — Buy, closed by `system_close`. This is the audit's J2 case, properly cleaned post-fix.**

**Audit window**: 0 `WD_TICK` appearances BEFORE 21:09. The audit's stated "orphan Sell" at 20:38 with qty 3599.7 @ 0.15 is the stale `positions` row from the prior day (`updated_at` would have been 2026-05-13). It existed in the cache but not in Bybit's response.

**Cross-direction sequence (verified in audit log)**:
- Line 10606 — 21:08:58.035 — `APEX_DIR_LOCK | sym=DYDXUSDT dir=Buy regime=trending_up`
- Line 10730 — 21:09:10.921 — `APEX_DIR_LOCK_OVERRIDE | qwen_tried=Sell locked_to=Buy regime=trending_up`
- Line 10784 — 21:09:12.105 — `BYBIT_DEMO_ORDER_RECEIVED | side=Buy qty=1270.0`
- Line 10793 — 21:09:12.387 — `COORD_REG | sym=DYDXUSDT src=claude_direct side=Buy qty=1270.0 entry_price=0.15748`
- 21:47:26 — Position closed (system_close). `POSITION_ROW_DELETED | sym=DYDXUSDT src=close_callback` fired correctly.

The new Buy at 21:09:12 did INSERT OR REPLACE into the existing stale Sell row, overwriting it. After 38 minutes, the new Buy closed and the row was deleted by the c4eef5c cleanup callback. The audit's framing ("orphan Sell on Bybit conflicts with new Buy") was based on the local cache state, not on Bybit truth. On Bybit truth there was only one direction at a time (the prior Sell had closed on 2026-05-13).

This corroborates the H1 stale-cache root-cause story. **J2 (cross-direction) is largely a downstream consequence of the J1 stale-cache root cause.** Once stale rows can't accumulate (post-c4eef5c) and reconciler drift fires (post-J1 fix), the J2 trigger disappears. The J2 pre-order check should still be added as defence-in-depth (a malicious operator could manually open opposing positions on Bybit), but the audit's specific J2 firing is explained.

## Why The Five Symbols Specifically

These five symbols share a common signature:
- All have `positions.updated_at` < 24 hours before the c4eef5c fix shipped (2026-05-14 10:54).
- All have `trade_thesis` closures pre-c4eef5c.
- All are symbols the strategy has historically traded heavily (15-24 thesis rows each).

It is not coincidence. They are the survivors of the pre-fix mode-gating bug. The fix shipped at 10:54; the audit started at 20:35. In the 9.5-hour gap, other orphans likely closed cleanly (via post-fix cleanup callback) leaving just these five as the residue. Today (2026-05-14 22:30 UTC) only four remain — DYDX was overwritten by the new Buy that itself was correctly cleaned post-close.

## Connection To `trade_log`

`trade_log` for bybit_demo shows 506 historical rows total but 0 with `closed_at IS NULL`. The 301 shadow-mode rows with NULL `closed_at` are stale residue from a different time period.

This is the audit's "View 3 is empty" observation. The explanation: `trade_log` is written ONLY at close time in this codebase (`src/core/data_lake.py:56-177`). There is no `write_trade_opened` path. The audit's framing of trade_log as a "view of open positions" was incorrect — it is a closed-position write-only table. The empty count for bybit_demo with NULL closed_at is expected, not a bug.

## What This Trace Establishes

1. **The four current stale rows are pre-c4eef5c residue.** They are not actively misbehaving today (no margin really tied up on Bybit for them — WD_TICK n=0).
2. **The c4eef5c fix is working** — DYDX was cleaned correctly when the post-fix Buy closed.
3. **The audit's J1 framing was correct in spirit** — the four (then 13) stale rows DID exist and DID cause J2's cross-direction trigger via the cache overwrite path. The exact root cause is "pre-fix cleanup callback skipped" rather than "no cleanup mechanism exists."
4. **The architectural concern remains** — the cache still does not have symmetric write/prune (the prune chain depends on the watchdog detecting a vanish). H4's reconciler enhancement would catch any future drift.

## Recommended J1 Phase 3 Sequence (Updated)

1. Run `scripts/backfill_orphan_positions.py --dry-run` to enumerate the four current stale rows.
2. Operator approves; run `scripts/backfill_orphan_positions.py --yes`.
3. New code: PositionReconciler worker (H4) to catch future drift.
4. New code (optional, defence-in-depth): boot-recovery `_trades` backfill for positions-table-vs-trade_thesis mismatch on the active mode.
5. Optional: zombie-reconciler dwell-time guard (low priority).
