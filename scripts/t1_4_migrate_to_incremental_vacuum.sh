#!/usr/bin/env bash
# T1-4 / F4 one-time database migration script
# Six-tier-fixes 2026-05-11
#
# Purpose:
#   Switch trading.db from auto_vacuum=NONE (0) to auto_vacuum=INCREMENTAL (2)
#   so the cleanup_worker's hourly PRAGMA incremental_vacuum(N) calls can
#   reclaim freelist pages without taking the long exclusive lock that the
#   legacy daily full VACUUM took (up to 21 s on the production DB today).
#
# Pre-conditions:
#   - The trading worker AND the MCP server MUST be stopped before running.
#     SQLite VACUUM holds an EXCLUSIVE lock; concurrent processes will
#     fail or be blocked. Verify with:
#       sudo systemctl stop trading-workers trading-mcp-sse
#       ps -ef | grep -E "workers\.py|server\.py" | grep -v grep
#   - A current DB backup must exist. Tier 0 took one at
#     data/trading.db.bak-pre-six-tier-fixes-<timestamp>.
#
# Post-conditions:
#   - PRAGMA auto_vacuum returns 2 (INCREMENTAL).
#   - DB file may shrink (the single VACUUM rewrites the file with freelist
#     pages reclaimed).
#   - On worker restart, cleanup_worker's hourly tick will emit
#     `VACUUM | mode=incremental pages=1000 success=Y` instead of
#     `DB_VACUUM_MIGRATION_REQUIRED`.
#
# Usage (from project root):
#   bash scripts/t1_4_migrate_to_incremental_vacuum.sh
#
# Exit codes:
#   0 — migration succeeded; auto_vacuum=2 confirmed.
#   1 — DB locked / sqlite3 unavailable / migration verification failed.
#

set -euo pipefail

DB_PATH="${1:-data/trading.db}"

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "ERROR: sqlite3 CLI not found in PATH. Install sqlite3 first."
    exit 1
fi

if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: $DB_PATH not found. Run from project root or pass explicit path."
    exit 1
fi

echo "=== T1-4 incremental_vacuum migration ==="
echo "Target DB: $DB_PATH"
echo "Pre-migration size: $(du -h "$DB_PATH" | awk '{print $1}')"
echo

current_mode=$(sqlite3 "$DB_PATH" "PRAGMA auto_vacuum;" 2>/dev/null || echo "?")
echo "Current auto_vacuum mode: $current_mode (expected: 0 = NONE; we are migrating to 2 = INCREMENTAL)"

if [ "$current_mode" = "2" ]; then
    echo "auto_vacuum is already INCREMENTAL. Nothing to do."
    exit 0
fi

# Detect locks. fuser may not detect aiosqlite ephemeral connections, but
# any persistent holder will be flagged here.
if fuser "$DB_PATH" >/dev/null 2>&1; then
    echo "ERROR: $DB_PATH appears to be in use by another process."
    echo "Stop trading-workers and trading-mcp-sse, then re-run."
    exit 1
fi

echo
echo "Running migration (this takes one full VACUUM — expect a brief pause):"
start_ts=$(date +%s)
sqlite3 "$DB_PATH" <<SQL
PRAGMA auto_vacuum=INCREMENTAL;
VACUUM;
SQL
end_ts=$(date +%s)
echo "Migration completed in $((end_ts - start_ts)) seconds."

post_mode=$(sqlite3 "$DB_PATH" "PRAGMA auto_vacuum;" 2>/dev/null || echo "?")
echo
echo "Post-migration auto_vacuum mode: $post_mode"
echo "Post-migration size:   $(du -h "$DB_PATH" | awk '{print $1}')"

if [ "$post_mode" != "2" ]; then
    echo "ERROR: migration appears to have failed. Expected mode=2, got $post_mode."
    exit 1
fi

echo
echo "Migration OK. Restart workers when ready:"
echo "  sudo systemctl start trading-workers trading-mcp-sse"
echo
echo "After restart, cleanup_worker's first hourly tick should emit:"
echo "  VACUUM | mode=incremental pages=1000 success=Y"
echo "Confirm with: tail -F data/logs/workers.log | grep VACUUM"
