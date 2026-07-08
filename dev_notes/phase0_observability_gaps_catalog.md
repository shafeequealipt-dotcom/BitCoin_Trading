# Phase 0 — Observability Gaps Catalog (Group F, ~19 items)

This catalog enumerates every observability gap identified in the spec and the post-Layer-1 observation reports. Each gap has: current state (file:line), desired state, severity (high/medium/low), and the phase it lands in.

## Tick summary gaps (5 missing — spec claimed 8, only 5 truly missing)

| # | Worker | File | Current state | Phase |
|---|---|---|---|---|
| 1 | backtest_worker | `src/workers/backtest_worker.py` | Only DEBUG `Backtest worker: would backtest {name}` per strategy | 8 |
| 2 | discovery_worker | `src/workers/discovery_worker.py:45-81` | INFO pattern + strategy counts; no aggregated TICK_SUMMARY | 8 |
| 3 | live_monitor_worker | `src/workers/live_monitor_worker.py:35-50` | Logs only when hot patterns found; nothing when empty | 8 |
| 4 | scheduled_report_worker | `src/workers/scheduled_report_worker.py:17-20` | DEBUG `Scheduled reports: {n} due` only | 8 |
| 5 | trial_monitor_worker | `src/workers/trial_monitor_worker.py:33-41` | Per-result INFO; no aggregate | 8 |

**Already have structured tick summary (no work needed):**
- kline_worker (`KLINE_FETCH | klines={...} expected={...} ...` at line 179-182)
- signal_worker (`SIG_BATCH` + `SIG_BATCH_STATS` at lines 122, 138-141)
- altdata_worker (`ALTDATA | fg={...} funding={...} oi={...}` at line 130)
- scanner_worker (`SCANNER | coins={...} top={...} score={...}` at line 53)
- strategy_worker (`STRAT_CYCLE_DONE | ...` at lines 317-335 + `STRAT_HEALTH` at 372)
- regime_worker (`REGIME_GLOBAL` at 141 + `REGIME_PERCOIN` at 181)
- price_worker (`PRICE_WS_CONN` / `PRICE_WS_DISC` events; no per-tick because the worker is event-driven, not polling — needs `PRICE_WS_HEALTH` heartbeat (Phase 10))

## High-value gaps (Phase 10 — batch 1)

| ID | Description | Current | Desired | Severity |
|---|---|---|---|---|
| G1 | SCAN_NOOP for silent scanner cycles | scanner_worker silent on no-change ticks | Log `SCAN_NOOP | universe_unchanged size={N} | {ctx}` to confirm scanner is alive | high |
| G2 | STRAT_SKIP_STALE rollup | Per-symbol blast (one log per skipped symbol) | One rollup `STRAT_SKIP_STALE_SUMMARY | n_skipped={N} reasons={dict} | {ctx}` | high |
| G3 | KLINE_FETCH `quality=skipped_cooldown` | When all timeframes are within cooldown, total_fetched=0 logs at INFO with no context | Add `quality=skipped_cooldown` reason so operators don't read silence as failure | high |
| G4 | Worker state-size heartbeat | Internal dicts (`_last_fetch`, `_consecutive_fails`) grow silently | Every 50 ticks, log `WORKER_STATE_SIZE | name={n} dicts={...} | {ctx}` | medium |
| G5 | WORKER_FIRST_TICK milestone | No log when each worker hits first tick post-boot | BaseWorker emits `WORKER_FIRST_TICK | name={n} el_to_first_tick_ms={ms}` once | medium |
| O-8 | PRICE_WS_HEALTH heartbeat | Only reconnect/disconnect events | `PRICE_WS_HEALTH | status=connected msg_per_min={r} subscribed={n}` every 60s | high |
| O-15 | Per-worker TICK_SLOW thresholds | Static `_BASE_WORKER_TICK_SLOW_SECONDS = 2.0` (base_worker.py:26) | Per-worker override (kline default 8s, strategy 10s, others 2s) via `config.toml` | high |
| O-18 | Throttle shadow_adapter boot ERROR loop | Covered by Phase 1.2 — verify here | Already part of Phase 1; verify no regression | (deferred to Phase 1) |

## Medium-value gaps (Phase 11 — batch 2)

| ID | Description | Current | Desired |
|---|---|---|---|
| O-1 | Structured KLINE_TICK improvements | Phase 3 covers most | Add any remaining fields (e.g., `lag_max_s`, `lag_p95_s`) |
| O-2 | KLINE_WRITE_LAG threshold rebase | Static 180s threshold | Candle-close-aware threshold (M5 candle just closed → expect 0-200s lag); avoid false positives |
| O-4 | `el=Xms` on SIG_BATCH | `signal_worker.py:122` lacks timing | Add `el={ms}ms` to existing line |
| O-6 | `el=Xms` on ALTDATA | `altdata_worker.py:130` lacks timing | Add `el={ms}ms` to existing line |
| O-10 | REGIME_PERCOIN_FAIL structured | Aggregated only | Per-failure structured `REGIME_PERCOIN_FAIL | coin={c} err={...}` |
| O-12 | XRAY_TICK_ERR DEBUG → WARNING | `structure_worker.py` logs at DEBUG | Promote to WARNING so it shows at default INFO+ |
| O-13 | XRAY_SESSION_ERR DEBUG → WARNING | Same pattern | Promote to WARNING |
| O-14 | STRAT_PNL_GATE pnl/streak fields | Gate decision logged without context | Add `pnl={p} streak={n}` for analyzability |
| (Loguru) | Rotation tail-F check | `LOG_ROTATION = "10 MB"` at `src/core/logging.py:18` with auto-rename | **VERIFY** before fixing — loguru may rotate the *new* file; tail -F follows the path. May not be a real issue. |

## Low-value cleanups (Phase 12 — batch 3)

| ID | Description | Current | Desired |
|---|---|---|---|
| O-7 | ALTDATA_SOURCE_FAIL structured | Generic exception log | Per-source: `ALTDATA_SOURCE_FAIL | src={name} err={...}` (fear_greed, funding, oi) |
| O-11 | REGIME_RESTORE_FAIL DEBUG → WARNING | DEBUG-level | Promote |
| O-19 | CLEANUP_PROTECTED structured | When `protected_tables.py` blocks a destructive query, raises but no structured log | Log `CLEANUP_PROTECTED_BLOCKED | table={t} sql={...:80}` before raise |
| O-9 | PriceWorker per-tick aggregate | None (event-driven) | `PRICE_TICK_SUMMARY | reconnects={r} ws_msgs={n} subscribed={s}` per 60s |
| G6 | Truncation cap documentation | `str(e)[:120]` scattered | Document constant in `src/core/log_context.py`, centralize as `MAX_ERR_LEN = 120` |
| G7 | Same as G6 for shorter caps | `str(e)[:80]` etc. | Centralize via `MAX_ERR_LEN_SHORT = 80` |
| G8 | UNIVERSE_EMPTY rollup escalation | Empty line every tick, correct per-cycle | Counter; after 30 consecutive empty cycles, escalate one line to ERROR |

## DB lock-wait instrumentation (Phase 9, separate)

| ID | Description |
|---|---|
| Phase 9 | DB lock acquire/release wrapping in `connection.py` to capture `wait_ms`, `holder`, `caller`. Periodic `DB_LOCK_HIST` histogram. The single biggest blind spot in current observability — see plan Phase 9 for full design. |

## Pragma mismatch (overlaps Phase 2)

Already documented in `phase0_issue_d3_cluster.md` Section A.4. Diagnosed in Phase 2.0 before any contention work.

## Severity summary

- **High value, ship in Phase 10**: G1, G2, G3, O-8, O-15
- **Medium**: G4, G5, O-1, O-2, O-4, O-6, O-10, O-12, O-13, O-14, loguru-rotation-check
- **Low / cleanup**: O-7, O-11, O-19, O-9, G6, G7, G8

## Known false leads to NOT fix

- **Loguru rotation breaking `tail -F`**: spec assumes this; may not be true. Validate before changing config.
- **"5 workers missing tick summary"** — spec says 8, only 5 are truly missing. Don't add redundant summaries to workers that already have structured logs.
- **Shadow adapter boot ERROR rate-limiting** — should be done as part of Phase 1, not duplicated in Phase 10. Mark O-18 as Phase 1.

## Verified citations

| Claim | File:Line |
|---|---|
| Backtest worker no TICK_SUMMARY | `src/workers/backtest_worker.py:42-60` |
| Discovery worker no aggregate | `src/workers/discovery_worker.py:45-81` |
| Live monitor conditional log | `src/workers/live_monitor_worker.py:35-50` |
| Scheduled report DEBUG only | `src/workers/scheduled_report_worker.py:17-20` |
| Trial monitor per-result | `src/workers/trial_monitor_worker.py:33-41` |
| Static TICK_SLOW | `src/workers/base_worker.py:26` |
| KLINE_FETCH structured | `src/workers/kline_worker.py:179-182` |
| SIG_BATCH lacks `el=` | `src/workers/signal_worker.py:122` |
| ALTDATA lacks `el=` | `src/workers/altdata_worker.py:130` |
| Loguru rotation config | `src/core/logging.py:18, 115-125` |
| XRAY logs at DEBUG | `src/workers/structure_worker.py` (verified via grep) |
| `_ZERO_COVERAGE_TTL_SECONDS` hardcoded | `src/intelligence/sentiment/aggregator.py:31` |
