# Phase 5 — Cleanup Targets (Survey)

Per `IMPLEMENT_DB_CONCURRENCY_REFACTOR.md` Part E §5 and the operator's 2026-05-14 decision to include zero-row polling stops in this refactor. Phase 5 commits run AFTER Phase 4 verification confirms the new concurrency model is stable in production.

## Target 1 — Drop duplicate fear_greed_index indexes (`conn-pool/p5-1`)

Current state (verified via `sqlite3 data/trading.db ".schema"`):

```
CREATE INDEX idx_fear_greed_ts ON fear_greed_index(timestamp DESC);
CREATE INDEX idx_fear_greed_ts_asc ON fear_greed_index(timestamp ASC);
```

Two indexes on the same column differing only by direction. SQLite walks B-tree indexes in either direction at O(1) for `LIMIT 1` queries, so one is functionally redundant.

Active read sites:

- `altdata_repo.py:44` — `SELECT * FROM fear_greed_index ORDER BY timestamp DESC LIMIT 1`. EXPLAIN QUERY PLAN reports `SCAN ... USING INDEX idx_fear_greed_ts_asc`.
- `altdata_repo.py:83` — `SELECT * FROM fear_greed_index WHERE timestamp > ?`.
- `tias/collector.py` (various sites) — `ORDER BY timestamp DESC LIMIT 1`.

Decision: drop `idx_fear_greed_ts` (DESC). Keep `idx_fear_greed_ts_asc`.

Rationale: the audit (Chapter 4 query 4) confirmed the planner already prefers `idx_fear_greed_ts_asc` for the LIMIT-1 reads. Removing the DESC index cuts insert-time cost in half on this 600/hr write path with no read-time penalty.

Verification step before drop:

```
sqlite3 data/trading.db <<'EOF'
EXPLAIN QUERY PLAN SELECT * FROM fear_greed_index ORDER BY timestamp DESC LIMIT 1;
EXPLAIN QUERY PLAN SELECT * FROM fear_greed_index ORDER BY timestamp ASC LIMIT 1;
EXPLAIN QUERY PLAN SELECT * FROM fear_greed_index WHERE timestamp > '2026-05-01';
EOF
```

If `idx_fear_greed_ts_asc` covers all three plans, the DESC index can be safely dropped via a migration.

## Target 2 — Drop duplicate position_snapshots indexes (`conn-pool/p5-2`)

Current state:

```
CREATE INDEX idx_pos_snapshots_ts ON position_snapshots(ts_epoch);
CREATE INDEX idx_position_snapshots_ts ON position_snapshots(ts_epoch DESC);
CREATE INDEX idx_position_snapshots_symbol ON position_snapshots(symbol);
```

Two indexes on `position_snapshots.ts_epoch` (one ASC default, one DESC).

Decision: drop `idx_pos_snapshots_ts` (the older one without direction); keep `idx_position_snapshots_ts` (DESC, used by recent-N queries).

Verification step before drop:

```
sqlite3 data/trading.db <<'EOF'
EXPLAIN QUERY PLAN SELECT * FROM position_snapshots ORDER BY ts_epoch DESC LIMIT 10;
EXPLAIN QUERY PLAN SELECT * FROM position_snapshots WHERE ts_epoch > 1700000000 ORDER BY ts_epoch DESC LIMIT 100;
EOF
```

If `idx_position_snapshots_ts` covers both plans, drop the duplicate.

## Target 3 — Stop zero-row table polling (`conn-pool/p5-3`)

### price_alerts (0 rows for the duration of all observed sessions)

Poll cadence: every 10 s from `src/workers/price_alert_worker.py:45`:

```python
active = await self.alert_engine.repo.get_active_alerts()
```

→ `telegram_repo.py:32` — `SELECT * FROM price_alerts WHERE triggered = 0`.

This was the audit's top cascade-holder query. With the pooled engine, it no longer blocks other workers, but it still serializes on the reader pool and is operational noise.

Fix shape: replace the unconditional poll with a check-on-demand pattern. The Telegram bot can write a sentinel row in `price_alerts` or set an in-memory flag when an alert is created via `/setalert`. The poller skips the DB read until the flag is set.

Detail: keep the poll path operational but gate it on `if PriceAlertEngine.has_active_alerts(): ...` where `has_active_alerts()` uses an in-memory boolean updated on alert create/delete. Periodic re-sync (every 5 min) catches any out-of-band insertions.

### scheduled_reports (0 rows for the duration of all observed sessions)

Poll cadence: every 300 s from `src/workers/scheduled_report_worker.py` via `ScheduledReportEngine.fire_due_reports()` → `telegram_repo.get_active_reports()`.

Same shape as price_alerts. Fix: gate the read on an in-memory `has_active_reports()` flag set on create/delete.

## Target 4 — Stop brain_decisions reads in Telegram handlers (`conn-pool/p5-4`)

Read sites (zero-row table; the active strategist writes to `claude_decisions`, not `brain_decisions`):

- `src/telegram/handlers/brain.py:34` — `SELECT action_taken, trigger, cost_usd, created_at FROM brain_decisions ...`.
- `src/telegram/handlers/system.py:101` — same SELECT pattern with `LIKE '%error%' OR LIKE '%fail%' OR LIKE '%skip%'`.

Decision options for the operator at p5-4:

1. **Redirect to claude_decisions.** Both queries are operator-facing diagnostics. The shape that returns useful data today is `claude_decisions`. The migration is a SELECT-target replacement.
2. **Remove the handlers entirely.** If the operator no longer uses these Telegram commands, remove the routes. Lower-risk because no schema change.

The plan defers the decision to operator at p5-4 time. The survey records the two read sites for the change.

## Target 5 — Write `concurrency_model_docs.md` (`conn-pool/p5-5`)

Documentation page in `dev_notes/db_concurrency/concurrency_model_docs.md` covering:

- The new concurrency model (reader pool + writer connection).
- How to add a new worker that needs DB access.
- How to add a new repository.
- Best practices for transaction scoping (hold the writer lock for one atomic write group; never hold it across awaits on external services).
- Pool sizing tuning guidance.
- Operator runbook: how to flip `concurrency_model` between `single_lock` and `reader_pool` with zero downtime.

Will be written at p5-5 time once Phase 4 metrics confirm the final pool size and any tuning lessons.

## Scheduling

These five cleanups land in order p5-1 → p5-5, each as its own atomic commit, after Phase 4 verification reports green. None of them is required for the Phase 3.7 cutover or the Phase 4 verification window.

If Phase 4 reveals that targets 1 or 2 (duplicate indexes) cause any unexpected planner regression, they are deferred. The polling stops (targets 3 and 4) are independent and can land any time after the cutover.

End of `phase5_cleanup_targets.md`.
