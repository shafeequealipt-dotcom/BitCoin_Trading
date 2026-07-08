# Phase 6 — Live trial period (5-7 days)

**Goal:** Observe all phases shipped together. Critically, measure APEX/XRAY flip survival and outcomes. The trial unblocks the operator's question: "is APEX flipping working or wasteful?".

**Trial start:** TBD — operator runs `sudo systemctl restart trading-workers trading-mcp-sse` to load the post-fix code.

## Pre-trial — boot signs of life (within 30s of restart)

Tail `data/logs/workers.log` and look for these one-shot sentinels:

```
STRAT_CALL_B_REFRAMED | system_prompt_version=2 close_rules_removed=2 contract=aggressive_management
DB_PRAGMAS | journal_mode=WAL cache_size=64MiB synchronous=NORMAL busy_timeout=10000ms foreign_keys=ON
DB_PRAGMA | wal_autocheckpoint=2000 jsize_lim=100MiB temp_store=MEMORY mmap_size=256MiB
SENT_CONSUMPTION_DISABLED | reason=operator_decision_2026-05-06 effect=signal_generator_skip_sentiment_branch
SENTIMENT_DEGRADED_MODE | reason=consumption_disabled source=operator_decision_2026-05-06 effect=per_coin_log_suppressed
SENTIMENT_DEGRADED_MODE | reason=no_reddit source=fear_greed_only
ENFORCER_STATE | ... el=0|1|2|3 ...
```

If any of these are missing, the corresponding phase didn't take. Investigate before continuing the trial.

## Daily monitor queries

Run these once per day during the trial. Compare each metric against the Phase 0 baseline (`phase0_baseline.md`).

### Monitor 1 — STRAT_ACTION_CLOSE rate (target: ≥70% reduction)

```bash
WLOGS="data/logs/workers.log $(ls -t data/logs/workers.*.log 2>/dev/null | head -3)"
echo "Total STRAT_ACTION_CLOSE (24h)"; grep -hc "STRAT_ACTION_CLOSE " $WLOGS | awk -F: '{s+=$NF}END{print s}'
echo "BLOCKED (min-hold guardrail)"; grep -hc "STRAT_ACTION_CLOSE_BLOCKED" $WLOGS | awk -F: '{s+=$NF}END{print s}'
echo "Actual closes (not blocked)"; grep -h "STRAT_ACTION_CLOSE " $WLOGS | grep -v BLOCKED | wc -l
echo "Sample reasons (last 20):"; grep -h "STRAT_ACTION_CLOSE " $WLOGS | grep -v BLOCKED | tail -20 | sed -n "s/.*rsn='\([^']*\).*/\1/p"
```

Compare with Baseline 1: total CLOSE was 63 (16 actual + 21 BLOCKED). Target post-fix: 6 actual + 7 BLOCKED (70% reduction). Reasons should be SL/TP/structure-invalidation, NOT regime-mismatch / thesis-broken.

### Monitor 2 — APEX/XRAY flip survival

```bash
sqlite3 -header data/trading.db "
SELECT
  symbol, direction,
  COALESCE(xray_flip_source, CASE WHEN apex_flipped THEN 'apex' ELSE '' END) AS source,
  COALESCE(xray_flip_ratio, 0) AS ratio,
  ROUND(actual_pnl_pct, 2) AS pnl,
  close_reason,
  CAST(strftime('%s', closed_at) - strftime('%s', opened_at) AS INTEGER) / 60 AS held_min,
  closed_at
FROM trade_thesis
WHERE status != 'open'
  AND closed_at >= datetime('now', '-24 hours')
  AND (apex_flipped = 1 OR xray_flip_source != '')
ORDER BY closed_at DESC LIMIT 50;
"
```

Compare with Baseline 2: 38/50 last closed trades were flipped, average held_min < 5 (sniper-killed). Target post-fix: ≥80% of NEW flipped trades survive past 30 minutes (held_min > 30). Closing reasons should now be SL/TP, not strategic_review / thesis-broken.

### Monitor 3 — Flipped vs unflipped win-rate (the question)

```bash
sqlite3 data/trading.db "
WITH last_50 AS (
  SELECT actual_pnl_pct,
         CASE WHEN apex_flipped = 1 OR xray_flip_source != '' THEN 'flipped' ELSE 'unflipped' END AS bucket
  FROM trade_thesis WHERE status != 'open' AND actual_pnl_pct IS NOT NULL
  ORDER BY closed_at DESC LIMIT 50
)
SELECT bucket,
       COUNT(*) AS n,
       SUM(CASE WHEN actual_pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
       ROUND(AVG(actual_pnl_pct), 3) AS avg_pnl,
       ROUND(SUM(CASE WHEN actual_pnl_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS wr_pct
FROM last_50 GROUP BY bucket;
"
```

This is the answer to "is APEX/XRAY flipping working or wasteful?".
- Flipped WR similar to unflipped → flipping captures real RR asymmetry that translates to outcomes.
- Flipped WR significantly worse → flipping is wasteful; recalibrate.
- Flipped WR significantly better → flipping is producing edge.

### Monitor 4 — survival_block elimination

```bash
WLOGS="data/logs/workers.log $(ls -t data/logs/workers.*.log 2>/dev/null | head -3)"
echo "TRADE_SKIP rsn=survival_block (24h)"; grep -h "TRADE_SKIP" $WLOGS | grep -c survival_block
echo "ENFORCER_RR_ADJUSTED (24h)"; grep -hc "ENFORCER_RR_ADJUSTED" $WLOGS
echo "Sample adjustments:"; grep -h "ENFORCER_RR_ADJUSTED" $WLOGS | tail -5
```

Compare with Baseline 3: 12 survival_block events. Target: ≤2 (only when adjustment was infeasible per Phase 2B).

### Monitor 5 — DB_LOCK_WAIT (target: stay 0 or very low)

```bash
WLOGS="data/logs/workers.log $(ls -t data/logs/workers.*.log 2>/dev/null | head -3)"
grep -hc "DB_LOCK_WAIT" $WLOGS
```

Currently 0. Should remain 0 or below 1/min.

### Monitor 6 — SIG_DOWNGRADE rate + non-destructive evidence

```bash
WLOGS="data/logs/workers.log $(ls -t data/logs/workers.*.log 2>/dev/null | head -3)"
echo "SIG_DOWNGRADE count (24h)"; grep -hc "SIG_DOWNGRADE" $WLOGS
```

The count should remain similar (the rate is a signal-quality issue, not a fix target). What changes is downstream: ScannerWorker / strategist now see `original_signal_type` and `confidence_floor_failed` in `signal.components`.

Spot-check downstream usage by tailing `STRAT_CALL_A_CTX` for any references to original_signal_type or confidence_floor_failed in future enhancements.

### Monitor 7 — Sentiment availability (target: per-coin SENT_DEGRADED_MODE = 0)

```bash
WLOGS="data/logs/workers.log $(ls -t data/logs/workers.*.log 2>/dev/null | head -3)"
echo "Per-coin SENT_DEGRADED_MODE (24h)"
grep -h "SENT_DEGRADED_MODE | sym=" $WLOGS | wc -l
echo "Init-time SENTIMENT_DEGRADED_MODE (count = restarts)"
grep -hc "SENTIMENT_DEGRADED_MODE" $WLOGS
echo "SENT_UNKNOWN_CACHE_HIT (cache still warm)"
grep -hc "SENT_UNKNOWN_CACHE_HIT" $WLOGS
```

Per-coin should drop to 0 (suppressed). Init-time fires once per restart.

### Monitor 8 — Overall win rate (now measurable)

```bash
sqlite3 data/trading.db "
SELECT COUNT(*) AS n,
       SUM(CASE WHEN actual_pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
       ROUND(AVG(CASE WHEN actual_pnl_pct > 0 THEN actual_pnl_pct END), 3) AS avg_win,
       ROUND(AVG(CASE WHEN actual_pnl_pct < 0 THEN actual_pnl_pct END), 3) AS avg_loss,
       ROUND(SUM(CASE WHEN actual_pnl_pct > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS wr_pct
FROM (SELECT actual_pnl_pct FROM trade_thesis
      WHERE status != 'open' AND actual_pnl_pct IS NOT NULL
      ORDER BY closed_at DESC LIMIT 100);
"
```

Baseline (last 50, pre-fix): WR=28%, avg_win=+0.22%, avg_loss=-0.24%. Expectancy negative.

If post-fix WR ≥35% with similar win/loss sizes: **strategy edge exists** (graduation candidate).
If 28%-35%: marginal; observe longer.
If <25%: strategies need work, not the system.

### Monitor 9 — System stability (target: no new errors)

```bash
WLOGS="data/logs/workers.log $(ls -t data/logs/workers.*.log 2>/dev/null | head -3)"
echo "ERROR-level events (24h)"; grep -hc " | ERROR" $WLOGS
echo "Sample errors:"; grep -h " | ERROR" $WLOGS | tail -5
echo "Worker stalls"; grep -hc "BASE_WORKER_TICK_SLOW" $WLOGS
```

No new error patterns. No crashes. Stalls similar to pre-fix baseline.

## Trial pass / fail criteria

Trial succeeds if:
- Monitor 1 (STRAT_ACTION_CLOSE) drops ≥70%.
- Monitor 2 (flip survival) ≥80% of new flipped trades survive past 30 min.
- Monitor 3 (flipped vs unflipped WR) provides clear signal.
- Monitor 4 (survival_block) drops ≥80%.
- Monitor 7 (sentiment per-coin spam) at 0.
- Monitor 8 (overall WR) measurable.
- Monitors 5, 6, 9 do not regress.

Trial fails if:
- CALL_B still closing at high rate (Phase 1 didn't take).
- Flipped trades still dying early via other paths.
- New error patterns emerge.
- WR collapse <25%.

## Phase 7 trigger

When trial succeeds, write `phase7_verification_report.md` with the 9 sections per spec.
When trial fails, document failure mode and propose Phase 1.5 / Phase 2.5 corrections.
