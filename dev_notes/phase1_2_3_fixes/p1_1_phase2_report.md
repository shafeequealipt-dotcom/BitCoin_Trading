# P1-1 Phase 2 — Operator Decision Report

## Summary

The Phase 1 investigation confirms:

- Live `data/trading.db` (197.5 MB) is in `auto_vacuum=0` (NONE); needs `2` (INCREMENTAL).
- All code is correctly wired: `connection.py` warns on mismatch; `cleanup_worker.py` calls `PRAGMA incremental_vacuum(1000)` hourly once mode is 2.
- Freelist is small today (858 pages / 3.35 MB), but the growth is unbounded and the migration is overdue.
- Disk: 6.9 GB free; sudo (passwordless) works for systemctl on this VM.
- No fresh production backup exists; the latest backup is a March testnet snapshot.

The migration is a one-shot operation: stop services → backup → run script → restart services → verify. Total expected downtime ≈ 30–60 seconds.

## Three Orthogonal Decisions

### Decision A — Backup strategy

| Option | Behavior | Trade-off |
|--------|----------|-----------|
| A1 | Take a fresh `data/trading.db.bak-p1-1-<UTC-timestamp>` copy before VACUUM. | +5 s downtime, +200 MB disk; recoverable to exact pre-migration state. **Recommended.** |
| A2 | Run without fresh backup; rely on the March 2026 testnet backup. | Faster (no extra step) but offers no production-state recovery if the migration leaves the file unusable. |
| A3 | Take a GCP-disk-level snapshot. | Heavier (slower), captures full disk state; overkill for a 197 MB SQLite file. |

**Recommendation: A1.** Small cost, gives clean rollback.

### Decision B — Maintenance window

| Option | Behavior | Trade-off |
|--------|----------|-----------|
| B1 | Stop `trading-workers` + `trading-mcp-sse`, run migration, restart both. | ~30–60 s total downtime. Operator's Telegram dashboard, MCP server, and trade execution all paused for the window. Cleanest. **Recommended.** |
| B2 | Stop only `trading-workers`, leave MCP SSE up. | MCP SSE holds a DB handle too — `fuser` will reject the run. Not viable. |
| B3 | Wait for a low-activity window (overnight). | Defers fix; current symptom is active. Unnecessary given downtime is short. |

**Recommendation: B1, run now (Asia Pacific evening — low operator load).**

### Decision C — Observability touch-up

| Option | Behavior | Trade-off |
|--------|----------|-----------|
| C1 | Ship migration only. Existing `VACUUM | mode=incremental pages=1000 success=Y` log is sufficient for verification. | Zero code change; just an operational run. |
| C2 | Ship migration + one tiny commit adding `pages_freed=<delta>` and `elapsed_ms=<wall>` to the hourly log line to exactly match prompt Rule 6's `DB_INCREMENTAL_VACUUM_OK` tag. | One commit, < 15 LOC, one unit test. Better long-term observability — operator can see how much is being reclaimed hour-to-hour. **Recommended if the operator wants strict Rule 6 compliance.** |

**Recommendation: C2 — small commit, large observability win for future regression detection.**

## Combined Recommended Sequence

1. `cp data/trading.db data/trading.db.bak-p1-1-$(date -u +%Y%m%dT%H%M%SZ)` (decision A1).
2. `sudo systemctl stop trading-mcp-sse trading-workers` (decision B1).
3. Verify no stale processes hold the file (`fuser data/trading.db` should return empty).
4. `bash scripts/t1_4_migrate_to_incremental_vacuum.sh data/trading.db`.
5. Confirm script exited 0 and printed `Post-migration auto_vacuum mode: 2`.
6. `sudo systemctl start trading-workers trading-mcp-sse`.
7. (Decision C2 only) On a new `fix/p1-1-auto-vacuum-migration` branch, add `pages_freed` + `elapsed_ms` to the cleanup-worker log line, commit + verify.
8. Watch first hourly cleanup tick (~60 min) for `VACUUM | mode=incremental ...`.

## Rollback if Step 4 fails non-zero

```
sudo systemctl stop trading-workers trading-mcp-sse
rm -f data/trading.db-wal data/trading.db-shm
cp data/trading.db.bak-p1-1-<timestamp> data/trading.db
sudo systemctl start trading-workers trading-mcp-sse
```

DB returns to pre-migration state. The existing `DB_VACUUM_MIGRATION_REQUIRED` warning continues to fire harmlessly. Investigate the failure cause before retry.

## Risks Mitigated

- Data loss → fresh backup taken (Decision A1).
- Process interference → services stopped via systemd before VACUUM (Decision B1).
- Disk space exhaustion during VACUUM → 6.9 GB free vs ~400 MB peak need; no risk.
- Verification ambiguity → existing structured logs + optional observability commit (Decision C).
- Aim regression → no trade-logic touched; trading resumes within ~60 s.
- Shadow regression → Shadow is a separate process tree on its own DB; not affected.

## What I will NOT do without operator approval

- Run the migration.
- Stop any service.
- Modify any source file.
- Force a `VACUUM` outside the script's guarded path.

Waiting for operator's three decisions.
