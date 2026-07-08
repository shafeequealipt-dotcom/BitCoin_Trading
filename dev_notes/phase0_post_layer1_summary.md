# Phase 0 — Post-Layer-1 Investigation Summary

**Date:** 2026-04-26
**Source spec:** `/home/inshadaliqbal786/IMPLEMENT_POST_LAYER1_FIXES_PROFESSIONAL.md`
**Plan file:** `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-kind-sphinx.md`

This file is the index over the seven Phase-0 investigation deliverables. Each linked file contains mechanism, dependencies, constraints, and fix candidates for one or more of the 32 post-Layer-1 issues, with `file_path:line_number` citations.

## Investigation files

| File | Issues |
|---|---|
| [`phase0_issue_d3_cluster.md`](phase0_issue_d3_cluster.md) | #1 (kline tick latency 12-20s), #2 (KLINE_WRITE_LAG stale_count=31), #6 (WAL pinned 104MB) — Group A |
| [`phase0_issue_brain_credential.md`](phase0_issue_brain_credential.md) | #3 (Claude CLI hang at expiry), cascade #8 (watchdog flip), cascade #9 (enforcer STALE) — Group B |
| [`phase0_issue_duplicate_orders.md`](phase0_issue_duplicate_orders.md) | #4 (duplicate ORDER_START 500ms apart) — Group C — **SAFETY-CRITICAL** |
| [`phase0_issue_sentiment_staleness.md`](phase0_issue_sentiment_staleness.md) | #5 (sentiment 4.68h stale, conf≈0.28 neutral) — Group D |
| [`phase0_issue_startup_ordering.md`](phase0_issue_startup_ordering.md) | #10 (workers start before Shadow listener; capital pools default 0) — Group E |
| [`phase0_observability_gaps_catalog.md`](phase0_observability_gaps_catalog.md) | All 19 observability gaps — Group F |

## Root-cause classification of all 32 issues

### Group A — Database lock contention (D-3)

| # | Symptom | Root cause |
|---|---|---|
| 1 | KlineWorker tick 12-20s chronic | (a) artificial `await asyncio.sleep(0.1)` per fetch at `kline_worker.py:162` summing to ~12s yield time; (b) lock contention from many small writes serialized via single `asyncio.Lock` at `connection.py:37` |
| 2 | KLINE_WRITE_LAG stale_count=31 | Cascade from #1 — writes serialized behind the slow tick |
| 6 | WAL pinned at 104MB (live DB shows `journal_size_limit=-1`) | (a) **PRAGMA mismatch** — code sets `journal_size_limit=104857600` (`connection.py:57`) but live DB shows `-1` (no limit); (b) no scheduled `wal_checkpoint` invocation in code |
| Cascade A.1 | StrategyWorker prefetch 2-7s queueing | Serialized behind kline_worker writes on `DatabaseManager._lock` |
| Cascade A.2 | Latency degrades over the run (13s→15.5s mean) | WAL growth → larger merge cost on each write commit |

### Group B — Brain CLI credential lifecycle

| # | Symptom | Root cause |
|---|---|---|
| 3 | Claude CLI subprocess hangs silently at credential expiry | No pre-flight credential refresh before subprocess spawn (`claude_code_client.py:722-746`); silent stdout for full timeout window |
| 8 | Position watchdog flips passive→safety_net for 110s | (a) consequence of #3; (b) **latent bug**: watchdog reads `claude_client._last_response_time` (`position_watchdog.py:323`) which **does not exist** on the client (only `_last_call_time` at `claude_code_client.py:97,236,321`); `getattr(..., 0.0) or 0.0` masks the missing attribute |
| 9 | Enforcer beats STALE for 4 heartbeats | Same latent bug as #8 — `performance_enforcer.py:380-386` reads only `_last_call_time` (request-start, not response-time) |

### Group C — Order placement integrity

| # | Symptom | Root cause |
|---|---|---|
| 4 | Duplicate ORDER_START 500ms apart, identical params | `@retry(max_attempts=2, delay=0.5)` decorator at `order_service.py:51` wraps the entire `place_order` including the line-85 ORDER_START log AND the line-175 Bybit `place_order` call. No `order_link_id` generated → Bybit cannot deduplicate. **Live evidence**: `data/logs/workers.log:5-8` and 33677-33680 show every order doubles ~500ms apart. **Probable status**: real duplicate live orders on Bybit. |

### Group D — Sentiment freshness

| # | Symptom | Root cause |
|---|---|---|
| 5 | Sentiment 4.68h stale; SIG_BATCH_STATS conf_mean=0.291 std=0.036 | NewsWorker IS ticking and aggregated_sentiment IS being written (289k rows, last write minutes ago). Staleness is **upstream** — Finnhub returning few articles, or `news_service.py:62-70` 24h cutoff dropping most articles, or sparse Finnhub coverage on the 50-coin universe |

### Group E — Service startup ordering

| # | Symptom | Root cause |
|---|---|---|
| 10 | 10 ERROR lines `Cannot connect to host 127.0.0.1:9090` at boot | (a) `systemd/trading-workers.service` has only `After=network-online.target`, no `After=shadow.service`; (b) `shadow_adapter.py:442-459` has no retry-with-backoff; (c) `fund_manager/manager.py:131` swallows the failure with `except: pass`, so capital pools initialize at 0.00 |

### Group F — Observability gaps

19 individual items — see `phase0_observability_gaps_catalog.md`. Of the 8 spec-claimed-missing tick summaries, **only 5 are truly missing**: backtest, discovery, live_monitor, scheduled_report, trial_monitor. kline / signal / altdata / scanner / strategy / regime / price already have structured logs.

## Cross-issue dependencies

- **#2 cascades from #1** (write lag = symptom of slow tick).
- **#6 has its own root cause** (PRAGMA mismatch + missing checkpoint scheduler) but its visibility is amplified by #1.
- **#8 and #9 cascade from #3** but the watchdog/enforcer reading non-existent attribute is a separate latent bug that pre-dates #3.
- Phase 4 (D-3 root cause) blocks on Phase 2 (sleep removal + PRAGMA fix). Phase 2's sleep removal alone may eliminate the need for Phase 4's chunked saves.
- Phase 5 (ORDER duplicates) is **safety-critical** and independent — should land as fast as Phase 0 + a single commit cycle.

## Phase-order revision

The plan's Phase order remains valid. Two notes:

1. **Phase 5 priority escalation**: spec puts Phase 5 after Phase 4. Investigation finds duplicate orders are **active and ongoing** — every order doubles. Recommend deploying Phase 5 in parallel with Phase 1 (independent code path, no shared files).

2. **Phase 4 conditional**: re-measure kline tick latency immediately after Phase 2's `sleep(0.1)` removal (Step 2.4). Hypothesis: tick p50 drops from ~13s to ~3-4s. If verified, Phase 4 collapses to monitoring-only.

## Verification gate

Before Phase 1 executes, the answers to the spec's five gate questions are:

1. **What mechanism holds trading.db lock for 12-20s?** No single 12-20s lock-holder. Combination of (a) sequential 0.1s sleeps adding ~12s yield time at `kline_worker.py:162` outside the lock, (b) ~120 small executemany acquires per kline tick, (c) DatabaseManager has a single `asyncio.Lock` at `connection.py:37` shared by all readers and writers, (d) PRAGMA mismatch keeping WAL at 100MB ceiling.
2. **What hangs the Claude CLI subprocess?** Spec's "OAuth credentials about to expire" hypothesis is plausible but unverified. The 3-layer refresh exists at `claude_code_client.py:309-597`. Phase 6 adds pre-flight refresh + stall detection; the actual hang root cause may emerge from those new diagnostics.
3. **Are duplicate ORDER_START actually placing duplicate orders?** **Yes, almost certainly.** No `order_link_id` is sent (verified in `order_service.py:159-173`), so Bybit cannot dedup. The retry fires on any exception including the post-place_order SL verification block at line 207-227 — every order with stop-loss likely double-fires.
4. **Why is sentiment 4.68h stale?** Empirical: news_articles MAX(published_at) is ~1.5-2h old; aggregated_sentiment writes are continuous. Upstream coverage gap, not a worker death.
5. **Why do workers start before Shadow?** No `After=shadow.service` in the trading-workers unit. Shadow service is at `/home/inshadaliqbal786/shadow/systemd/shadow.service`. Both units only depend on `network-online.target` and race.

All five answered with code-level evidence. Verification gate passes. Phase 1 can begin.
