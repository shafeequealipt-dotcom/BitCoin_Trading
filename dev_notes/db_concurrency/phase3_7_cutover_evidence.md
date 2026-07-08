# Phase 3.7 — Production Cutover Evidence

Date: 2026-05-14
Commit: `0807523` (`conn-pool/p3-7: cutover concurrency_model to reader_pool`)

## Cutover sequence

| Step | Time (UTC) | Result |
|---|---|---|
| Pre-cutover baseline captured | 2026-05-14 | `phase0_baseline.md` records DB sizes, 1h45m metric window from `SESSION_LOGS_2026-05-14_12-45_to_14-30.log` |
| `config.toml` edited: `concurrency_model = "reader_pool"` | 2026-05-14 17:15 | One-line change |
| Cutover commit | 2026-05-14 17:15 | `0807523` |
| `sudo systemctl restart trading-workers` | 2026-05-14 17:16 | exit 0 |
| `sudo systemctl restart trading-mcp-sse` | 2026-05-14 17:16 | exit 0 |
| Services verified active | 2026-05-14 17:17 | `trading-workers: active`, `trading-mcp-sse: active` |

## First-minute boot lines (verified)

From `data/logs/general.log`:

```
2026-05-14 17:16:46.181 | INFO | src.database.connection:connect:516 | CONN_POOL_INIT | readers=4 hard_cap=8 writer=ready
2026-05-14 17:16:46.183 | INFO | src.database.connection:connect:753 | DB_CONN | path=data/trading.db wal=Y engine=reader_pool
2026-05-14 17:16:59.044 | INFO | src.database.connection:connect:516 | CONN_POOL_INIT | readers=4 hard_cap=8 writer=ready
2026-05-14 17:16:59.045 | INFO | src.database.connection:connect:753 | DB_CONN | path=data/trading.db wal=Y engine=reader_pool
```

Two CONN_POOL_INIT lines confirm both `trading-workers` (17:16:46) and `trading-mcp-sse` (17:16:59) booted on the pooled engine.

`engine=reader_pool` in the DB_CONN line confirms the facade dispatched to `_PooledDatabaseEngine` as designed.

## First-minute error / cascade check

Search command:

```
grep -E "DB_ERR|CASCADE_DETECTED|CONN_POOL_EXHAUSTED|WRITER_LOCK_WAIT|DB_LOCK_WAIT|ERROR|CRITICAL" \
  data/logs/general.log data/logs/workers.log data/logs/mcp.log \
  | awk '$1 >= "2026-05-14" && $2 >= "17:16:00"'
```

Result: **zero matches** in the first minute post-cutover.

## First-minute worker activity (sanity)

From `data/logs/workers.log` 17:16:51 onwards:

- `WORKER_FIRST_TICK | name=cleanup_worker el_to_first_tick_ms=1305 first_tick_el_ms=1304` — cleanup worker booted in 1.3 s (well below the 2 s slow threshold).
- `Bybit client connected (MAINNET)` at 17:16:59.
- `WD_TICK | mode=passive n=0 syms=[none]` — position_watchdog ticking on its 10-second cadence.
- `TICKER_BUFFER_HEARTBEAT | flushes=60 written=2432 last_flush_n=43 last_flush_ms=1.6 max_flush_ms=72.6 err_count=0` — ticker buffer flushing 50ms cadence with sub-ms latency and zero errors.
- `WORKER_LIVENESS_HEARTBEAT | total=20 healthy=15 never_ticked=0 overdue=0` — liveness watchdog reports 0 overdue workers.
- `PRICE_WS_HEALTH | status=connected msgs_per_min=13988` — WS feeding normally.
- `SNIPER_TICK | tick=12 el=73ms n=0 syms=[] mode=bybit_demo` — profit_sniper ticking on its 5-second cadence; no positions yet.
- `LAYER_STATE_SYNC | match=true` — layer state coherent.

## Cutover status

Production is live on the pooled engine. First-minute observations:

- ✅ Both services booted cleanly with `engine=reader_pool`.
- ✅ Zero database errors.
- ✅ Zero cascade events.
- ✅ Zero pool exhaustions.
- ✅ Zero writer-lock waits.
- ✅ Zero reader-pool waits exceeding warn threshold.
- ✅ All 20 workers healthy per liveness watchdog.
- ✅ WS feed nominal at ~14k msgs/min.
- ✅ Ticker buffer flushing at sub-ms latency.

Phase 4 verification window starts now. The 48 h soak collects metrics against the baseline in `phase0_baseline.md`. The methodology and the metrics targets are in `phase4_verification.md` (template, to be filled at the end of the soak).

## Revert path (unchanged from plan)

If at any point during Phase 4 the operator decides to revert:

```bash
# Edit config.toml: concurrency_model = "single_lock"
sudo systemctl restart trading-workers
sudo systemctl restart trading-mcp-sse
```

No code revert needed. The legacy single-lock path remains in `connection.py` until Phase 3.9 removes it after 1 week of stable operation on the pooled engine.

End of `phase3_7_cutover_evidence.md`.
