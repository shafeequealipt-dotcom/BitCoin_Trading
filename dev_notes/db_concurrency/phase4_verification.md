# Phase 4 — Verification (48 h Production Soak)

Status: **IN PROGRESS** — soak window started 2026-05-14 17:16 UTC (cutover commit `0807523`).

Compare-against baseline: `phase0_baseline.md` (1 h 45 m window from `SESSION_LOGS_2026-05-14_12-45_to_14-30.log`, pre-cutover).

This file is filled at the end of the soak. Until then, the operator can append intermediate observations as the window progresses.

## 1. Methodology

48 h after cutover, capture the same metrics from `data/logs/workers.log` and `data/logs/mcp.log` (or a `tail`-derived SESSION_LOGS file equivalent to the baseline). Compute the same percentiles. Compare against the targets table below.

Grep commands:

```bash
LOG_GLOB="data/logs/workers.log data/logs/mcp.log data/logs/general.log"

# Event counts (post-cutover window; replace timestamps as needed)
grep -E "DB_LOCK_WAIT|WRITER_LOCK_WAIT" $LOG_GLOB \
  | awk '$1 >= "2026-05-14" && $2 >= "17:16:00"' | wc -l

grep "CASCADE_DETECTED" $LOG_GLOB \
  | awk '$1 >= "2026-05-14" && $2 >= "17:16:00"' | wc -l

grep "CONN_POOL_EXHAUSTED" $LOG_GLOB \
  | awk '$1 >= "2026-05-14" && $2 >= "17:16:00"' | wc -l

grep "WORKER_TICK_OVERDUE" $LOG_GLOB \
  | awk '$1 >= "2026-05-14" && $2 >= "17:16:00"' | wc -l

grep "BASE_WORKER_TICK_SLOW" $LOG_GLOB \
  | awk '$1 >= "2026-05-14" && $2 >= "17:16:00"' | wc -l

# wait_ms distribution
grep -oE "wait_ms=[0-9]+" $LOG_GLOB | awk -F= '{print $2}' | sort -n \
  | awk 'BEGIN{c=0} {a[c++]=$1; sum+=$1} END{print "n="c, "p50="a[int(c*0.5)], "p95="a[int(c*0.95)], "p99="a[int(c*0.99)], "max="a[c-1], "avg="sum/c}'

# Per-worker slow-tick frequency
grep "BASE_WORKER_TICK_SLOW" $LOG_GLOB \
  | awk '$1 >= "2026-05-14" && $2 >= "17:16:00"' \
  | grep -oE "name=[a-zA-Z_]+" | sort | uniq -c | sort -rn
```

## 2. Targets vs baseline

| Metric | Baseline (1 h 45 m) | Target post-refactor | Tolerance | Actual (fill at 48 h) |
|---|---|---|---|---|
| `DB_LOCK_WAIT` + `WRITER_LOCK_WAIT` (1 h 45 m equivalent) | 129 | ≤ 13 (90 % reduction) | ≤ 26 | _TBD_ |
| `CASCADE_DETECTED` | 12 | 0 | ≤ 1 | _TBD_ |
| `CONN_POOL_EXHAUSTED` | n/a (new metric) | 0 | ≤ 2 | _TBD_ |
| `WORKER_TICK_OVERDUE` | 92 | ≤ 9 | ≤ 18 | _TBD_ |
| `BASE_WORKER_TICK_SLOW` | 126 | ≤ 25 | ≤ 50 | _TBD_ |
| `wait_ms` p95 | 26 436 ms | < 500 ms | < 1 000 ms | _TBD_ |
| `wait_ms` p99 | 43 108 ms | < 2 000 ms | < 5 000 ms | _TBD_ |
| `wait_ms` max | 44 210 ms | < 5 000 ms | < 10 000 ms | _TBD_ |
| `profit_sniper` TICK_SLOW | 33 | ≤ 3 | ≤ 6 | _TBD_ |
| `position_watchdog` TICK_SLOW | 19 | ≤ 2 | ≤ 4 | _TBD_ |
| Trade-throughput | reference window | unchanged | ≥ baseline rate | _TBD_ |
| Telegram dashboard P95 | reference | unchanged or better | unchanged | _TBD_ |

## 3. Pool-stat snapshot (read at 48 h)

Captured from `CONN_POOL_STATS` emits (`cleanup_worker` hourly):

| Field | 1 h | 24 h | 48 h |
|---|---|---|---|
| `acquires` total | _TBD_ | _TBD_ | _TBD_ |
| `peak_in_use` observed | _TBD_ | _TBD_ | _TBD_ |
| `exhausted_count` | _TBD_ | _TBD_ | _TBD_ |
| `growths` | _TBD_ | _TBD_ | _TBD_ |
| `reconnects` | _TBD_ | _TBD_ | _TBD_ |
| `avg_wait_ms` | _TBD_ | _TBD_ | _TBD_ |

If `peak_in_use` consistently exceeds 4 OR `growths` accumulates more than ~50 over the 48 h window, the operator should consider bumping `reader_pool_size` from 4 to 8 (one-line `config.toml` edit + restart). If `peak_in_use` stays below 4, the default is right-sized.

## 4. Stress sweep with production-sized rows (optional)

If the operator wants definitive sizing data before declaring Phase 4 green, run:

```bash
STRESS_KLINES_ROWS=5000 scripts/run_db_concurrency_stress.sh
```

This matches the spec's 50 000-row klines burst (10 writers × 5 000 rows). Logs land in `dev_notes/db_concurrency/phase3_5_stress_runs/`.

## 5. Decision gate at end of soak

After 48 h:

- All targets within tolerance → Phase 4 GREEN → proceed to Phase 5 cleanups → Phase 3.9 cleanup commit after one more week stable.
- One metric outside tolerance → investigate root cause; consider bumping pool size; do NOT revert blindly.
- Multiple metrics outside tolerance OR clear regression → revert via config flip; capture evidence; resume investigation.

## 6. Sign-off

| Item | Verified |
|---|---|
| Cutover commit `0807523` deployed | ✅ 2026-05-14 17:16 |
| Both services on `engine=reader_pool` per `DB_CONN` log | ✅ |
| Zero errors / cascades / exhaustions in first minute | ✅ |
| 48 h soak completed | _TBD_ |
| Metrics within tolerance | _TBD_ |
| Stress sweep (optional) confirms sizing | _TBD_ |
| Operator sign-off to proceed to Phase 5 | _TBD_ |

End of `phase4_verification.md` template.
