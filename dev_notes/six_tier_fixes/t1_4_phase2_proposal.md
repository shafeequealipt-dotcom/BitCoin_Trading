# T1-4 Phase 2 — F4 VACUUM cascade proposal

## 1. Confirmed diagnosis

- Daily VACUUM at `cleanup_worker.py:205` holds EXCLUSIVE lock for up to 21 s (live evidence today: 3924 / 16384 / **21009** / 4114 ms).
- DB is `journal_mode=wal` (good), `wal_autocheckpoint=1000` (good), but `auto_vacuum=0` (NONE).
- Without `auto_vacuum=INCREMENTAL`, the only way to reclaim freelist pages is a full VACUUM, which is exclusive-locking.
- Cascade affects F2 (kline slow ticks), F3 (profit_sniper slow ticks), F5 (SL gateway rate-limit thrash), F8 (trail step), and any other writer during the freeze.

## 2. Recommended solution: Option A (incremental_vacuum)

Two-stage rollout:

### Stage 1 — One-time migration (operator-initiated maintenance step)

Operator runs once (system stopped):

```bash
sqlite3 data/trading.db "PRAGMA auto_vacuum=INCREMENTAL; VACUUM;"
```

This sets the mode flag AND performs the one final full VACUUM required by SQLite to start tracking freelist pages. The freeze is acceptable since the system is stopped. Verify after with `PRAGMA auto_vacuum` returning 2.

I will provide the exact command in a script (`scripts/t1_4_migrate_to_incremental_vacuum.sh`) so the operator can run it explicitly with confidence.

### Stage 2 — Code changes

**`src/workers/cleanup_worker.py`** — replace the daily full VACUUM with hourly `PRAGMA incremental_vacuum(N)`:

```python
# Old: once-per-day full VACUUM with 3-attempt retry loop.
# New: every-cleanup-tick (hourly) incremental_vacuum(N_pages).
# Default 1000 pages = ~4 MB reclaimed per hour; completes in <1s.
# Skips silently when auto_vacuum != INCREMENTAL (e.g. fresh-clone DB
# that hasn't run the one-time migration yet) — operator sees a
# DB_VACUUM_MIGRATION_REQUIRED warning at boot.
try:
    pages = int(getattr(self.settings.cleanup, "incremental_vacuum_pages", 1000))
    await self.db.execute(f"PRAGMA incremental_vacuum({pages})")
    log.info(f"VACUUM | mode=incremental pages={pages} success=Y | {ctx()}")
except Exception as e:
    log.warning(f"VACUUM_FAIL | mode=incremental err='{str(e)[:120]}' | {ctx()}")
```

**`src/workers/manager.py:1075`** — boot VACUUM removed. The hourly incremental in cleanup_worker covers steady-state reclamation; boot VACUUM was redundant (incremental_vacuum handles it on the first tick).

**`src/database/connection.py`** — at connect-time, run an idempotent `PRAGMA auto_vacuum=INCREMENTAL` (no-op when already set; warns if `auto_vacuum=0` is detected on an existing DB so operator notices the migration is needed). This ensures new DB files (e.g. shadow tests, future deploys) default to INCREMENTAL from the start.

## 3. Three solution options — recap

| Option | LOC | Pros | Cons |
|--------|-----|------|------|
| **A (recommended)** | ~50 + 1 script | Eliminates 21-s freezes. Standard SQLite production pattern. | One-time migration freeze (acceptable: system stopped). |
| B (schedule full VACUUM at idle hour) | ~10 | Simplest. Keeps current mechanic. | Freeze still happens, just at 04:00 UTC. Doesn't eliminate cascade. |
| C (split high-volume tables to separate DB) | ~300+ | Architectural. Cleanest separation. | Large refactor. Two connection pools. Out of scope. |

## 4. Aim preservation

Option A preserves aggressive-exploitation philosophy:
- No trade decision logic changes.
- Workers complete ticks faster (no 21-s freeze) so trade reaction time is BETTER, not worse.
- Aggressive opportunity exploitation needs fast worker ticks; eliminating the cascade directly helps.

## 5. Hard constraints satisfied

- DB continues to reclaim space (PRAGMA incremental_vacuum does this).
- No write freezes longer than ~1 s during normal operation (incremental at 1000 pages completes in milliseconds).
- Existing DB state not corrupted (auto_vacuum is a metadata-only change; data rows unchanged).
- Pre-fix DB backup already taken in Tier 0 (`data/trading.db.bak-pre-six-tier-fixes-20260511_1444`).

## 6. Observability additions

- `VACUUM | mode=incremental pages=N success=Y` — INFO, on each cleanup tick.
- `VACUUM_FAIL | mode=incremental err='...'` — WARN if PRAGMA call fails.
- `DB_VACUUM_MIGRATION_REQUIRED | current_mode=0 expected=INCREMENTAL` — WARN at boot if migration hasn't run.
- Existing daily VACUUM log line removed (now incremental).

## 7. Test plan (smoke, ≤10 min)

`tests/test_t1_4_vacuum_migration.py` — 3 tests against an in-memory SQLite DB:

1. After `PRAGMA auto_vacuum=INCREMENTAL; VACUUM`, `PRAGMA auto_vacuum` returns 2.
2. After setting incremental mode, `PRAGMA incremental_vacuum(N)` executes without error and frees pages when there's a freelist.
3. Cleanup-worker config knob `incremental_vacuum_pages` defaults to 1000.

(The migration script is shell, not Python; it gets a manual operator step.)

## 8. Operator decision required

Please choose:

- **A (recommended)**: incremental_vacuum + one-time migration. Eliminates 21-s freezes.
- **B**: schedule full VACUUM at 04:00 UTC. Simplest; freeze still happens.
- **C**: split high-volume tables. Architectural; out of scope for T1-4.
- **Defer**: T1-4 closed without fix; F2/F3/F5/F8 cascade continues during daily VACUUM.

Then state any non-default thresholds (e.g. incremental_vacuum_pages if not 1000; cleanup tick interval if not 3600 s).

When you reply, I proceed to Phase 3 implementation.
