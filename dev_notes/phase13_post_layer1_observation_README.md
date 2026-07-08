# Phase 13 — Live Observation README

**Status:** harness shipped; live run pending operator-driven workers restart
**Date:** 2026-04-26

This file accompanies `scripts/observation_4h.py` and documents how to
run the post-Layer-1 4-hour observation window after the workers
process is restarted to pick up the post-Layer-1 code (commits
`48aa288 → 040c03b`).

## Why 4 h, not 24 h

Plan-mode user decision: 24 h was the spec target; 4 h captures one
credential refresh cycle, multiple WAL checkpoints, the dominant
trading hour, and ample kline + strategy + brain ticks. If any tail
behavior surfaces in the 4 h window that needs more samples, extend
later.

## Pre-requisites

1. The systemd unit at `systemd/trading-workers.service` (with
   `After=shadow.service` / `Wants=shadow.service`) is installed:

   ```bash
   sudo cp systemd/trading-workers.service /etc/systemd/system/trading-workers.service
   sudo systemctl daemon-reload
   ```

2. The current workers process (running 4 h+ at planning time) is
   restarted so the post-Layer-1 code is active:

   ```bash
   sudo systemctl restart trading-workers
   ```

3. Wait ~30 s for the boot grace window to clear, then verify:

   ```bash
   journalctl -u trading-workers --since "1 minute ago" | \
     grep -E "FUND_POOLS|Capital pools updated|WORKER_FIRST_TICK"
   ```

   You should see `Capital pools updated: active=<real-balance>` (not
   0.00) and `WORKER_FIRST_TICK | name=...` lines for every worker.

## Running the observation

Default 4-hour window (no service restart from the script — it just
observes):

```bash
.venv/bin/python scripts/observation_4h.py
```

With explicit mid-run restart at t=2 h (requires sudo NOPASSWD or
operator at the keyboard):

```bash
.venv/bin/python scripts/observation_4h.py --restart-at-midpoint
```

Shorter test runs:

```bash
.venv/bin/python scripts/observation_4h.py --window 30   # 30 minutes
.venv/bin/python scripts/observation_4h.py --window 1    # 1-minute smoke test
```

## What it samples

Every 5 minutes, scrapes new lines appended to `data/logs/workers.log`
and updates counters/percentile buffers for:

- `KLINE_FETCH` — kline_worker tick latency, error counts, quality
- `KLINE_WRITE_LAG` — stale-symbol counts
- `KLINE_FETCH_FAIL` — per-symbol failure attribution (Phase 3)
- `KLINE_STRAGGLER` — coins persistently failing 3+ ticks (Phase 3)
- `DB_LOCK_WAIT` — slow lock acquires with holder/caller identity
- `DB_LOCK_HIST` — periodic p50/p95/max lock-wait percentiles
- `WAL_CHECKPOINT` — Phase 2 hourly checkpoints
- `ORDER_START` — Phase 5 idempotency: with vs without `link_id=`,
  unique link_ids
- `ORDER_RETRY` / `ORDER_DEDUPED` — Phase 5 retry & recovery counts
- `CLAUDE_PREFLIGHT_REFRESH` / `CLAUDE_CALL_OK` / `CLAUDE_NONRETRY` /
  `WORKER_DEGRADATION_CASCADE` — Phase 6 brain reliability
- `SHADOW_CALL_FAIL` / `FUND_MGR_BALANCE_FAIL` — Phase 1 boot-grace
- `FINNHUB_COVERAGE` — Phase 7 sentiment funnel breakdown

Every 30 minutes, snapshots:

- `data/trading.db-wal` size
- `MAX(published_at)` from `news_articles`
- `MAX(created_at)` from `aggregated_sentiment`

## Output

Final report at `dev_notes/phase13_post_layer1_observation_report.md`
with:

- A pass/fail checklist against every phase's verification criteria
- Detailed per-metric counts in JSON
- A verdict line: PASS or ATTENTION REQUIRED with explicit failures

The script is idempotent: re-running it overwrites the report with
the new window's data.

## Critical regression to watch for

The single most important checklist item:

> **Duplicate ORDER_START events (legacy fmt) | 0**

If this is non-zero, the Phase 5 fix is NOT running on the live
process — every order is at risk of being submitted twice on Bybit.
The script flags this with 🚨 ROLLBACK in the verdict. Stop trading,
verify the deploy, and either roll forward or revert.

## Deferred items (filed as follow-ups)

These were documented as deferred during the post-Layer-1 work and
should be picked up after Phase 13 if observation surfaces a need:

- **Subprocess streaming + stall detection** in
  `claude_code_client.py` (Phase 6 follow-up). Adds the missing
  `CLAUDE_PROC_STALL` log line at 60 s of stdout silence.
- **Pre-kill diagnostics** (`/proc/<pid>/status` + `wchan`) on
  `_kill_process_group`. Same follow-up as stall detection.
- **`WORKER_DEGRADATION_CASCADE`** event correlation. Information
  derivable from existing logs; observability nicety.
- **`modify_order` / `cancel_order` idempotency keys** (Phase 5
  follow-up). Cancel is idempotent on Bybit; amend benefits from
  `orderLinkId` matching but the safety risk is much lower than
  duplicate-place.
- **`PositionService` direct Bybit calls** at
  `position_service.py:131,233` use no `orderLinkId` — close paths
  bypass `OrderService`. Reduce-only flag bounds the risk.
- **`_ZERO_COVERAGE_TTL_SECONDS` config knob** (Phase 7 follow-up).
  Hardcoded 30 min is fine for typical workloads; tune via config
  only if observation reveals a specific need.
- **`KLINE_WRITE_LAG` candle-aware threshold rebase** (O-2). Refines
  an already-working metric.
- **`PRICE_WS_HEALTH` heartbeat** (O-8). Larger change requiring
  per-second internal counters in PriceWorker.
- **`STRAT_PNL_GATE pnl/streak` fields** (O-14).
- **Loguru rotation `tail -F` check** — Phase 0 found this is likely
  not actually a problem (loguru renames the old file, `tail -F`
  follows the path). Validate during Phase 13 if any operator
  reports missing log lines after a rotation.
- **Low-priority cleanups** in observability batch 3 (O-7, O-11,
  O-19, G6-G8) — defer indefinitely.

## Rollback path

Each Phase 1-12 commit can be reverted independently. If Phase 13
surfaces a regression that's tied to a specific phase, `git revert
<commit>` cleanly reverses just that phase's changes.

For Phase 5 specifically (the safety-critical commit), rollback
restores `@retry(max_attempts=2, delay=0.5)` on `place_order` —
which re-introduces the duplicate-order risk. Don't roll back unless
the new behavior is actively breaking trades; the duplicate exposure
is greater than any plausible Phase 5 bug.
