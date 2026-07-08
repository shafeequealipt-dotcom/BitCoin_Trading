#!/usr/bin/env bash
# scripts/run_db_concurrency_stress.sh
#
# Phase conn-pool/p3-5 (db-concurrency-refactor 2026-05-14).
#
# Run the database concurrency stress-test scenarios against a copy of
# the production trading.db. Produces a metrics table that helps the
# operator pick the reader_pool_size default for the Phase 3.7 cutover.
#
# Usage:
#     scripts/run_db_concurrency_stress.sh           # short scenarios 1-3 only
#     STRESS_LONG=1 scripts/run_db_concurrency_stress.sh  # +scenarios 4 (5min), 5 (30min)
#
# The script copies data/trading.db to data/trading_stress_test.db at
# the start of each test (per-test fixture). The original is never opened.

set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f data/trading.db ]; then
    echo "ERROR: data/trading.db not found. Run from project root with the live DB present." >&2
    exit 1
fi

LOG_DIR="dev_notes/db_concurrency/phase3_5_stress_runs"
mkdir -p "$LOG_DIR"
TS=$(date -u +%Y-%m-%dT%H-%M-%SZ)
LOG_FILE="$LOG_DIR/stress_run_${TS}.log"

echo "Running DB concurrency stress tests…"
echo "Source DB: data/trading.db ($(stat -c %s data/trading.db) bytes)"
echo "Log file:  $LOG_FILE"
echo

# Run scenarios 1-3 always, 4-5 only if STRESS_LONG=1
if [ "${STRESS_LONG:-0}" = "1" ]; then
    SCENARIOS="scenario1 or scenario2 or scenario3 or scenario4 or scenario5"
else
    SCENARIOS="scenario1 or scenario2 or scenario3"
fi

python3 -m pytest tests/stress/test_db_concurrency_stress.py \
    -v -m stress \
    --tb=short \
    -k "$SCENARIOS" \
    2>&1 | tee "$LOG_FILE"

echo
echo "Done. Log saved to $LOG_FILE"
echo "Compare elapsed times by (model, pool_size) to pick reader_pool_size for Phase 3.7."
