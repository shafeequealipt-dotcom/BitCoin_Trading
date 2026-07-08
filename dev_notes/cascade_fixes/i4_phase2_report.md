# Issue 4 — Phase 2 Operator Discussion Report

## Summary

`BybitDemoPositionService.get_positions` does not call `save_position`, so the `positions` cache table stays at 0 rows in bybit_demo mode while the watchdog actively trades positions in memory. The CRITICAL/HIGH series (schema v30) added the `exchange_mode` column to orders/trade_history/account_snapshots but missed positions.

The fix:
1. Schema v32 — add `exchange_mode` column to `positions` + supporting index.
2. `save_position` accepts an optional `exchange_mode` kwarg matching the `save_order` / `save_trade` pattern.
3. `BybitDemoPositionService.get_positions` calls `save_position(pos, exchange_mode='bybit_demo')` per non-zero open position.
4. `PositionService.get_positions` (live) passes `exchange_mode='shadow'` for symmetry.
5. `scripts/backfill_positions_exchange_mode.py` for parity with the v30 backfills.

Operator confirmed in plan-mode: add `exchange_mode` column, recommendation Sub A + Sub C (column + index, PK unchanged).

## Evidence

- Live `PositionService.get_positions:54-80` saves at line 76. `BybitDemoPositionService.get_positions:131-166` does not save. Verified.
- Schema check: `PRAGMA table_info(positions)` returns 12 columns, no `exchange_mode`. `PRAGMA table_info(orders)` returns 14 columns including `exchange_mode` at index 13.
- `SELECT COUNT(*) FROM positions` = 0 at Phase 0 baseline.
- `save_position` at `trading_repo.py:159` (PRE-FIX) had no `exchange_mode` kwarg, unlike `save_order` (line 34) and `save_trade` (line 223).
- `_handle_position` in the WS subscriber is logging-only.
- `close_position:455-468` already calls `save_position(pos)` post-zero, so the wiring exists for the close path; only the open-position INSERT is missing.

## Solution chosen

**Sub A + Sub C (column + index, PK unchanged)**:

1. **`migrations.py` — schema v32**:
   - `ALTER TABLE positions ADD COLUMN exchange_mode TEXT NOT NULL DEFAULT 'shadow'` (idempotent via the existing pre-flight column-exists check in `run_migrations`).
   - `CREATE INDEX IF NOT EXISTS idx_positions_mode ON positions(exchange_mode)`.

2. **`save_position(self, position, *, exchange_mode: str = "")`** — matches `save_order` / `save_trade` contract. Empty default = legacy behavior (column DEFAULT 'shadow').

3. **`BybitDemoPositionService.get_positions`** — `await self._trading_repo.save_position(pos, exchange_mode='bybit_demo')` for each non-zero position. Per-position try/except so a save failure logs but does not interrupt the return.

4. **`BybitDemoPositionService.close_position`** — existing `save_position` call (zero-size delete) now also passes `exchange_mode='bybit_demo'` for symmetry; ignored on the delete path but pin against future contract changes.

5. **`PositionService.get_positions` and `close_position`** — pass `exchange_mode='shadow'` explicitly.

6. **`scripts/backfill_positions_exchange_mode.py`** — flips rows with `updated_at >= '2026-05-08T11:19:26'` from 'shadow' to 'bybit_demo'. Idempotent. Vacuous on the current DB (positions count was 0).

PK stays `symbol`. Both modes don't currently coexist — if they ever do (e.g., simultaneous shadow shadow + bybit_demo testing), the latest-write wins. This is captured by an explicit pin test.

## Trade-offs

### Pros
- Closes the parity gap — DB consumers (Telegram /positions, MCP tools, post-mortem queries) see open positions in bybit_demo mode
- Symmetric with the v30 pattern (orders/trade_history/account_snapshots all have exchange_mode)
- Migration is online + reversible
- Tagging makes future cross-mode queries clean
- `save_position` per-position failure is non-fatal (matches existing close_position pattern)

### Cons
- ~1 extra DB write per position per watchdog tick (~10s); at 8 positions ≈ 50/min — small relative to Issue 2's 100-200/sec ticker storm (now batched to ~2/sec)
- Sequencing-after-Issue-2 is intentional: the new writes land in a less contended DB
- PK stays `symbol`, so cross-mode coexistence (not currently a requirement) overwrites by latest-write — explicit pin test documents the behavior

### Risks
- None significant. Backfill is vacuous on production. Schema migration is fast (table currently empty).

## Verification plan

After deploy:
1. Deploy with bybit_demo open positions
2. `SELECT exchange_mode, COUNT(*) FROM positions GROUP BY exchange_mode;` — `bybit_demo` count > 0 (was 0)
3. Telegram `/positions` and MCP `get_positions` return correct data
4. Shadow mode: switch mode=shadow, observe `shadow` rows continue to be written
5. DB write load: per-position write every ~10s × N positions — verify Issue 2's batching has reduced contention enough that this is safe (DB_LOCK_WAIT count remains low after deploy)
6. `scripts/backfill_positions_exchange_mode.py` runs cleanly (vacuous output on current DB)

Tests: 10 test cases pin the contract — kwarg honored, legacy default, zero-size delete, mode distribution query, BybitDemoPositionService.get_positions persistence with mocked repo, save-failure non-fatal, source-level pins on all four call sites and the migration, cross-mode overwrite semantics.
