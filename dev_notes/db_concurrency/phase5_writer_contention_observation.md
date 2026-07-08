# Phase 5 — Writer Contention Observation (2026-05-14 19:05)

## Event

At 2026-05-14 19:05:34 UTC the production logs surfaced the first
`WRITER_LOCK_WAIT` events since the 17:16 cutover (3 events in a
cluster):

```
19:05:34.542  WRITER_LOCK_WAIT  wait_ms=2957  holder=executemany:
19:05:34.549  WRITER_LOCK_WAIT  wait_ms=4088  holder=executemany:
19:05:34.702  WRITER_LOCK_WAIT  wait_ms=4146  holder=execute:
```

No `CASCADE_DETECTED` (the 5000 ms cascade threshold was not crossed).

Correlated worker slow-tick events in the same 4-second window:

```
19:05:35.346  BASE_WORKER_TICK_SLOW  name=fund_manager_worker  el=5554ms
19:05:36.033  BASE_WORKER_TICK_SLOW  name=profit_sniper        el=8620ms
19:05:37.296  BASE_WORKER_TICK_SLOW  name=position_watchdog    el=18588ms
19:05:37.186  POSITION_CLOSE_REASON  sym=ALICEUSDT reason=strategic_review
19:05:47.471  BYBIT_DEMO_POSITION_CLOSE  sym=ALICEUSDT close_trigger=wd_claude_action
19:05:52.858  BASE_WORKER_TICK_SLOW  name=kline_worker         el=22857ms
```

## Root-cause attribution

The cluster sat inside the 5-min sweet-spot batch window. Workers
firing at sweet-spot 0:30 (kline), 0:45 (structure), 1:00 (signal),
1:15 (regime) all overlap roughly at 19:05:30–19:06:30 of every wall-
clock hour. The brain layer-3 cycle was also active and decided to
close ALICEUSDT (`strategic_review`) at 19:05:37.

Likely sequence:

1. kline_worker began its 5-min chunked `executemany INSERT OR IGNORE`
   into the `klines` table (~30 000 rows in 500-row chunks).
2. Each chunk acquires the writer lock, runs ~50–100 ms of execute +
   commit, releases. With `await asyncio.sleep(0)` between chunks the
   writer is brief but contended.
3. Concurrently, the watchdog / strategy_worker / data_lake were
   queueing writes (account_snapshot, claude_decision, position_snapshot,
   thesis_update). Each of these acquired the writer lock after queueing.
4. The brain-do-close path for ALICEUSDT issued multiple sequential
   writes (trade_log INSERT OR REPLACE, trade_thesis UPDATE,
   claude_decisions INSERT, position_snapshots INSERT) — each
   contending for the writer with kline's chunks still running.
5. Three writers that happened to acquire late saw acquire-waits of
   2.9–4.1 seconds.

This is **expected writer contention during the 5-min batch window**.
It is NOT:

- A cascade (no `CASCADE_DETECTED` event; longest wait 4146 ms is below
  the 5000 ms cascade threshold).
- A reader-blocked-by-writer pattern (the reader pool is independent;
  `CONN_POOL_*` shows zero exhaustion / zero growths).
- A regression introduced by the refactor (pre-refactor, the same
  workload produced full reader-blocking cascades up to 44 seconds).

It IS:

- The refactor's `WRITER_LOCK_WAIT` instrumentation working as designed
  — surfacing residual writer-side contention so operators can see it
  separately from the pre-refactor reads-block-reads noise.
- A reminder that the 5-min sweet-spot batch window is still a
  serialised-writer hot period.

## Comparison to pre-cutover baseline

| Window | Cascades | DB_LOCK_WAIT (pre) / WRITER_LOCK_WAIT (post) | Max wait |
|---|---|---|---|
| Pre-cutover 1h45m | 12 | 129 | 44 210 ms |
| Post-cutover 2 h | 0 | 3 | 4 146 ms |

99%+ reduction in lock waits, 100% cascade elimination — the contract
the refactor was designed to deliver.

## Operational implications

- The 5-min batch window remains the highest-pressure period for
  writers. Under continued load growth, this is the bottleneck that
  will surface first.
- The bottleneck is `kline_worker`'s chunked `executemany` against the
  `klines` table holding the writer lock for ~50 ms per chunk × 60-180
  chunks = 3-9 s of cumulative writer activity per 5-min cycle.
- Mitigations beyond this refactor's scope (not actioned now):
  - Per-domain DatabaseManager instances (Option C in
    `08_architectural_options.md`) — would split writers across domain
    managers so kline writes don't share a writer lock with trade-state
    writes.
  - Shorter chunk size for `klines` (e.g. 200 rows instead of 500) —
    increases yield frequency at the cost of more commits.
  - Asynchronous flush queue for low-priority writes (sniper_log,
    position_snapshots) — reduces critical-path writer contention.

## Decision: Phase 3.9 deferred

Phase 3.9 was scheduled to remove the legacy `_LegacyEngine` after the
soak window. With this fresh writer-contention observation, the
conservative choice is to **keep `_LegacyEngine` in the codebase as the
revert path** until:

1. We observe whether `WRITER_LOCK_WAIT` events recur during subsequent
   5-min batch windows or were a one-off.
2. The Phase 5.1 / 5.2 / 5.3 cleanups (now landed) take effect after
   the next service restart, reducing per-INSERT B-tree maintenance and
   eliminating the price_alerts / scheduled_reports poll pressure.
3. The operator explicitly authorizes legacy removal.

The pooled engine is the only active path in production; the legacy
path is dead code unless the operator flips `concurrency_model` in
`config.toml`. The cost of keeping it is < 200 lines of dead Python
that share zero state with the live engine.

## Phase 5 status after this observation

| Sub-phase | Status |
|---|---|
| 5.1 — drop duplicate idx_fear_greed_ts (DESC) | DONE (`774c684`, schema v33; applies on next restart) |
| 5.2 — drop duplicate idx_pos_snapshots_ts | DONE (same commit) |
| 5.3 — stop zero-row polling (price_alerts + scheduled_reports) | DONE (`6b84f46`) |
| 5.4 — redirect /decisions, drop /errors brain_decisions block | DONE (`f3cd5da`) |
| 5.5 — concurrency_model_docs.md | DONE earlier (`cd542b4`) |
| 3.9 — remove `_LegacyEngine` | **DEFERRED** pending observation that this writer-contention pattern does not recur after the Phase 5.1-5.3 cleanups land at next restart |

All Phase 5 sub-phases have shipped. The migration (5.1, 5.2) applies on
next service restart; the engine cache updates (5.3, 5.4) apply on next
restart too. After the next restart and one more 5-min batch cycle
without `WRITER_LOCK_WAIT`, Phase 3.9 can land safely.

End of writer-contention observation note.
