# G6 — ERROR / CRITICAL events in last 24h

## Capture metadata

- **Capture timestamp:** 2026-04-27 23:00:00 UTC
- **Window:** 2026-04-26 23:00:00 → 2026-04-27 23:00:00 UTC (24h)
- **Sources:**
  - `data/logs/workers.log` (current — covers 22:06 → 23:12 from new PID, then split by 22:53 restart)
  - `data/logs/workers.2026-04-27_01-31-00_169356.log` (prior PID — covers 01:31 → 22:06)
  - `data/logs/brain.log` (continuous; cumulative ERRs back to 2026-04-13)
  - `data/logs/general.log` (continuous; cumulative ERRs back to 2026-04-23)
- **Method:** `awk '/^2026-04-26 23:|^2026-04-27 / && /( ERROR | CRITICAL )/' <files>` then aggregate by event tag.
- **Total raw lines in window:** 79 (71 ERROR + 8 CRITICAL)

---

## Aggregate by event tag (24h)

| Count | Tag | Source file:line (sample location) |
|---|---|---|
| 20 | `ORDER_GATE_LM_DEADLINE_EXCEEDED` | `src.trading.services.order_service:_enforce_layer3_gate:240` |
| 20 | `ORDER_BLOCKED` | `src.trading.services.order_service:_emit_order_blocked:182` |
| 11 | `DB_PROTECT_BLOCKED` | `src.database.protected_tables:assert_not_protected_destructive:135` |
| 8 | `WORKER_SHUTDOWN` (CRITICAL) | `workers:_sync_emit` and `__main__:_atexit_log:82` |
| 7 | `STRAT_PREFETCH_CRITICAL` | `src.workers.strategy_worker:tick:418` (prior log) and `:tick:460` (current log) |
| 4 | `STRAT_CALL_A_FAIL` | `src.brain.strategist:create_trade_plan:407` |
| 4 | `Claude trade failed for ...: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'` | `src.core.layer_manager:_execute_new_trades:888` |
| 3 | `DB_ERR` (`no such table: cycle_metrics`) | `src.database.connection:execute:314` |
| 2 | `ORDER_RETRY_EXHAUSTED` | `src.trading.services.order_service:_place_order_with_idempotent_retry:576` |

(Counts verified by `grep -oE "\| [A-Z][A-Z_0-9]+ "` on the windowed line set.)

---

## Detail per pattern

### 1. ORDER_GATE_LM_DEADLINE_EXCEEDED (20×)

- **Source:** `src/trading/services/order_service.py:240` (`_enforce_layer3_gate`)
- **Sample full line (workers.2026-04-27_01-31-00_169356.log):**
  ```
  2026-04-27 11:19:32.667 | ERROR    | src.trading.services.order_service:_enforce_layer3_gate:240 | ORDER_GATE_LM_DEADLINE_EXCEEDED | link_id=ti-bfdb675042834456befef3e8 sym=ETHUSDT purpose=mcp_tool elapsed_s=4872.8 deadline_s=60.0 action=block | no_ctx
  ```
- **Context:** Layer 3 gate flips fail-close once `OrderService.init_elapsed_s > lm_attach_deadline_sec=60.0`. All 20 fires occurred between 11:19 and 19:11 UTC, all marked `purpose=mcp_tool` (operator-driven manual close attempts), `sym=ETHUSDT` (10×) or `sym=BTCUSDT` (10×). `elapsed_s` ranged 4872 → 33213 seconds (= LayerManager attach lifetime).

### 2. ORDER_BLOCKED (20×)

- **Source:** `src/trading/services/order_service.py:182` (`_emit_order_blocked`)
- **Sample:**
  ```
  2026-04-27 11:19:32.668 | ERROR    | src.trading.services.order_service:_emit_order_blocked:182 | ORDER_BLOCKED | link_id=ti-bfdb675042834456befef3e8 sym=ETHUSDT side=Sell purpose=mcp_tool reason=lm_deadline_exceeded force=False deadline_s=60.0 elapsed_s=4872.8 | no_ctx
  ```
- **Context:** Paired 1:1 with each ORDER_GATE_LM_DEADLINE_EXCEEDED. Same 20 events, downstream emission step.

### 3. DB_PROTECT_BLOCKED (11×)

- **Source:** `src/database/protected_tables.py:135` (`assert_not_protected_destructive`)
- **Sample (general.log):**
  ```
  2026-04-26 23:18:11.061 | ERROR    | src.database.protected_tables:assert_not_protected_destructive:135 | DB_PROTECT_BLOCKED | sql_kind=DELETE table=trade_thesis sql='DELETE FROM trade_thesis WHERE opened_at < ?' | no_ctx
  ```
- **Context:** Hourly. Each fire is a `DELETE FROM trade_thesis WHERE opened_at < ?` from CleanupWorker; the protected-tables guard rejects it. 11 fires correspond to 11 hours that emitted (some hours skipped).

### 4. WORKER_SHUTDOWN (CRITICAL, 8×)

- **Sources:** `workers:_sync_emit` (4) + `__main__:_atexit_log:82` (4)
- **Sample (workers.log):**
  ```
  2026-04-27 22:45:52.782 | CRITICAL | workers:_sync_emit | WORKER_SHUTDOWN | reason=atexit | clean exit recorded
  2026-04-27 22:45:52.781 | CRITICAL | __main__:_atexit_log:82 | WORKER_SHUTDOWN | reason=atexit | clean exit recorded
  ```
- **Context:** 4 process restarts in the 24h window: 06:16:42, 09:56:47, 22:45:52, plus the prior shutdown sequence. Each restart emits one CRITICAL from each of the two sync paths (8 total).

### 5. STRAT_PREFETCH_CRITICAL (7×)

- **Source (prior log):** `src/workers/strategy_worker.py:418`
- **Source (current log):** `src/workers/strategy_worker.py:460` (line shifted post-rebuild)
- **Sample:**
  ```
  2026-04-27 22:16:38.601 | ERROR    | src.workers.strategy_worker:tick:460 | STRAT_PREFETCH_CRITICAL | el=8571ms db=1087ms h1_db=774ms coins=50 | sid=s-1777328190020
  ```
  ```
  2026-04-27 04:01:42.561 | ERROR    | src.workers.strategy_worker:tick:418 | STRAT_PREFETCH_CRITICAL | el=12504ms db=1249ms h1_db=747ms coins=50 | sid=s-1777262490009
  ```
- **Context:** StrategyWorker prefetch >8000 ms threshold (per G2: hardcoded `if _section_ms["prefetch"] > 8000`). 7 fires in 24h. `el` ranged 8571 → 17030 ms; `db` ranged 570 → 1503 ms; `h1_db` ranged 521 → 2010 ms; coins=50 always.

### 6. STRAT_CALL_A_FAIL (4×)

- **Source:** `src/brain/strategist.py:407` (`create_trade_plan`)
- **Sample (brain.log):**
  ```
  2026-04-27 15:25:47.418 | ERROR    | src.brain.strategist:create_trade_plan:407 | STRAT_CALL_A_FAIL | err='Cannot extract JSON from response:
  ```
  (the err= field is multi-line — only the first line is shown above)
- **Context:** All 4 fires used identical `err='Cannot extract JSON from response:`. Timestamps: 15:25:47, 16:16:06, 16:22:01, 16:53:44 (afternoon window, all on 2026-04-27).

### 7. Claude trade failed (4×)

- **Source:** `src/core/layer_manager.py:888` (`_execute_new_trades`)
- **Sample (workers.2026-04-27_01-31-00_169356.log):**
  ```
  2026-04-27 06:34:50.866 | ERROR    | src.core.layer_manager:_execute_new_trades:888 | Claude trade failed for DYDXUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
  2026-04-27 06:41:50.715 | ERROR    | src.core.layer_manager:_execute_new_trades:888 | Claude trade failed for DYDXUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
  2026-04-27 06:48:20.718 | ERROR    | src.core.layer_manager:_execute_new_trades:888 | Claude trade failed for RUNEUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
  2026-04-27 06:48:20.805 | ERROR    | src.core.layer_manager:_execute_new_trades:888 | Claude trade failed for ETHUSDT: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'
  ```
- **Context:** Signature mismatch — `_execute_new_trades` calls `ShadowOrderService.place_order(..., purpose=...)` but the Shadow wrapper does not accept `purpose`. All 4 fires inside one window 06:34-06:48 UTC. Affects 4 distinct symbol attempts (DYDXUSDT×2, RUNEUSDT, ETHUSDT). Process restarted at 06:16:42; this regression was hit on the next strategist cycle and continued on retries until the next process restart at 09:56:47 (per WORKER_SHUTDOWN above).

### 8. DB_ERR no such table: cycle_metrics (3×)

- **Source:** `src/database/connection.py:314` (`execute`)
- **Sample (general.log):**
  ```
  2026-04-27 07:18:06.986 | ERROR    | src.database.connection:execute:314 | DB_ERR | err='no such table: cycle_metrics' sql='INSERT OR REPLACE INTO cycle_metrics (hour_ts, cycles_count,  layer1a_p50_ms, la' | no_ctx
  ```
- **Context:** Hourly cycle-metrics flush from `CycleTracker` (config: `[observability].cycle_metrics_flush_seconds = 3600`). Table missing → 3 fires at 07:18, 08:18, 09:18 UTC. Disappears after 09:56 restart (the migration presumably ran).

### 9. ORDER_RETRY_EXHAUSTED (2×)

- **Source:** `src/trading/services/order_service.py:576` (`_place_order_with_idempotent_retry`)
- **Sample (workers.2026-04-27_01-31-00_169356.log):**
  ```
  2026-04-27 06:27:15.121 | ERROR    | src.trading.services.order_service:_place_order_with_idempotent_retry:576 | ORDER_RETRY_EXHAUSTED | link_id=ti-04caef00f3354840ba03b3b6 sym=ETHUSDT attempts=2 purpose=mcp_tool err=ab not enough for new order (ErrCode: 110007) (ErrTime: 06:27:15).
  2026-04-27 06:27:16.040 | ERROR    | src.trading.services.order_service:_place_order_with_idempotent_retry:576 | ORDER_RETRY_EXHAUSTED | link_id=ti-c9ce7c6a9fc84233b067f74b sym=BTCUSDT attempts=2 purpose=mcp_tool err=ab not enough for new order (ErrCode: 110007) (ErrTime: 06:27:15).
  ```
- **Context:** Bybit ErrCode 110007 ("ab not enough for new order"). Both fires within one second at 06:27:15-16. Sym=ETHUSDT and BTCUSDT, both purpose=mcp_tool.

---

## Distribution over time

| Hour bucket | ERRORs |
|---|---|
| 2026-04-26 23:00-00:00 | 1 |
| 2026-04-27 00:00-01:00 | 1 |
| 2026-04-27 01:00-02:00 | 1 |
| 2026-04-27 02:00-03:00 | 1 |
| 2026-04-27 03:00-04:00 | 1 |
| 2026-04-27 04:00-05:00 | ~5 (4× STRAT_PREFETCH_CRITICAL + 1× DB_PROTECT) |
| 2026-04-27 05:00-06:00 | 2 (1× STRAT_PREFETCH + 1× DB_PROTECT) |
| 2026-04-27 06:00-07:00 | ~10 (2× ORDER_RETRY_EXHAUSTED, 4× Claude trade failed, 2× WORKER_SHUTDOWN, 1× DB_PROTECT, 1× STRAT_PREFETCH likely) |
| 2026-04-27 07:00-09:00 | 3× DB_ERR cycle_metrics + 2× DB_PROTECT |
| 2026-04-27 09:00-10:00 | 2× WORKER_SHUTDOWN + 1× DB_PROTECT |
| 2026-04-27 11:00-12:00 | 2× ORDER_GATE + 2× ORDER_BLOCKED |
| 2026-04-27 12:00-13:00 | 2× ORDER_GATE + 2× ORDER_BLOCKED |
| 2026-04-27 13:00-14:00 | 2× ORDER_GATE + 2× ORDER_BLOCKED |
| 2026-04-27 15:00-16:00 | 1× STRAT_CALL_A_FAIL |
| 2026-04-27 16:00-17:00 | 3× STRAT_CALL_A_FAIL + 6× ORDER_GATE/BLOCKED + 1× DB_PROTECT |
| 2026-04-27 18:00-19:00 | 4× ORDER_GATE/BLOCKED |
| 2026-04-27 19:00-20:00 | 2× ORDER_GATE/BLOCKED (ETHUSDT) + ... |
| 2026-04-27 22:00-23:00 | 2× STRAT_PREFETCH_CRITICAL + 2× WORKER_SHUTDOWN |

(The exact per-hour count was not exhaustively bucketed by `awk` — distribution above is reconstructed from sample reads.)

---

## Notes

- **No DB_LOCK_WAIT errors** in the 24h window (the `WARNING` lines for DB_LOCK_WAIT are below the ERROR threshold; e.g., `2026-04-27 22:53:46.036 | WARNING ... DB_LOCK_WAIT | wait_ms=3621`). Per config, threshold is 1000 ms.
- **No BrainError / Claude CLI timed out / Cannot extract JSON failures in workers.log/general.log** — only in `brain.log` (4 fires, all 15:25-16:53).
- **No Reddit / Finnhub / Bybit-WS errors** in the 24h window.
- **No ShadowKlineReader async errors** — the 2026-04-26 fix (per memory) appears to have held.
- **No regime / signal / structure worker ERROR-level emissions** in the 24h window — these workers logged WARNING-and-below only.
