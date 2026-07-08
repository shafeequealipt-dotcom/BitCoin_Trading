# CRITICAL/HIGH Fix Series — Complete (Phases 0-3 of 14 Issues)

## Status

All 14 audit-flagged issues from `IMPLEMENT_CRITICAL_HIGH_FIXES.md` have shipped through Phase 3 (implementation). Phase 4 (live verification) is deferred to a single combined end-of-series trial per operator decision.

## Commit log

12 atomic commits on `feature/bybit-demo-adapter`, all on top of pre-series HEAD `d2250c1`:

| Commit | Issue | Description |
|---|---|---|
| `bd8134f` | n/a | chore(state): runtime state checkpoint pre-critical-high-fixes |
| `4b121d3` | CRITICAL-1 | back-derive pnl_pct from prices in coordinator |
| `34c2965` | CRITICAL-2 | populate opened_at in coordinator close record |
| `4940260` | CRITICAL-3 | close trade_history coverage gap via coordinator callback |
| `4425420` | CRITICAL-5 | wrong-side SL/TP rejection (multi-layer defense) |
| `a56c73b` | CRITICAL-4 | numeric-normalized alert dedup |
| `8b9b57f` | HIGH-9 | tid_scope context manager kills cross-symbol bleed |
| `45d92e6` | HIGH-1 | account_snapshots writes for both modes |
| `4de6882` | HIGH-2 | exchange_mode columns on orders / account_snapshots / trade_history |
| `884291d` | HIGH-3 | close_trigger propagates through get_last_close |
| `0ce5558` | HIGH-7 | structured ret_code/ret_msg/op in REDUCE_FALLBACK |
| `1742cbe` | HIGH-4 | CLAUDE_PROC_STALL observability (root cause deferred) |

HIGH-5, HIGH-6, HIGH-8 auto-resolve from CRITICAL-1+CRITICAL-4 fixes — verified-only in Phase 4.

## Test coverage added

103 new tests across 11 new test files. Final suite (excluding pre-existing broken `tests/test_phase7/`): **2601 passed, 8 skipped, 1 pre-existing failure** (`test_apex_direction_lock.py::test_system_prompt_still_has_rsi_caution` — verified to fail identically on the pre-series baseline).

| Test file | Tests | Coverage |
|---|---|---|
| `test_critical1_pnl_back_derive.py` | 23 | 5 Sell parametrized real-trade fixtures, 3 Buy synthetic, 6 was_win flip cases, 4 negative controls, 1 pnl_usd cascade, 2 cooldown timing, 1 callback fan-out, 1 double-close guard |
| `test_critical2_opened_at.py` | 4 | record dict carries opened_at, format matches closed_at, callback forwarding, fallback when state None |
| `test_critical3_trade_history_callback.py` | 9 | trade_id derivation (state.order_id, fallback, collision elimination, time uniqueness), TradeRecord construction, side mapping, coordinator record `size` field |
| `test_critical4_alert_dedup.py` | 9 | identical hash, KATUSDT retry pair dedups, 5-burst collapses, distinct symbols/tags/err preserved, float ordering, scientific notation, raw hash preserved |
| `test_critical5_sl_wrong_side.py` | 21 | sniper wrong-side formula (10 parametrized), adapter SL/TP wrong-side rejection (8), 34040 idempotent (3) |
| `test_high1_account_snapshots.py` | 3 | bybit_demo saves snapshot, shadow still enriches, no-op robustness |
| `test_high2_exchange_mode_columns.py` | 8 | schema v30 has columns, idempotent migrations, orders backfill, trade_history backfill, save_order/save_trade with kwarg, save_account_snapshot |
| `test_high3_close_trigger_propagation.py` | 9 | cache record/get/expire/prune, get_last_close cached vs fallback |
| `test_high4_proc_stall_observability.py` | 4 | prompt size attributes, log format, sequence ordering |
| `test_high7_reduce_fallback_context.py` | 4 | structured ret_code/ret_msg/op fields, missing details defensive, qty_exceeds_size visibility, no_position unchanged |
| `test_high9_tid_scope.py` | 9 | tid_scope sets/restores, exception safety, async propagation, concurrent isolation, iteration pattern |
| **Total** | **103** | |

## Per-issue impact summary

### CRITICAL-1 — Universal pnl=0 corruption
- **Files**: `src/core/trade_coordinator.py`, `src/bybit_demo/bybit_demo_websocket_subscriber.py`
- **Effect**: New `COORD_PNL_BACK_DERIVED` log per WS-driven close. trade_log/intelligence/thesis now write correct pnl from sentinel-zero contract. 116 → ~0 corrupt rows expected post-deploy. Auto-resolves HIGH-5 (back-derive precondition) and HIGH-6 (trade_thesis pnl=0 race).

### CRITICAL-2 — opened_at NULL on every trade_log row
- **Files**: `src/core/trade_coordinator.py`, `src/workers/manager.py`
- **Effect**: Two-line change. Both bybit_demo (116/116) and shadow (1597/1597) future trades populate `opened_at` ISO string from `state.opened_at_dt`. Existing rows untouched per Rule 12.

### CRITICAL-3 — TradeHistory coverage gap (74%)
- **Files**: `src/core/trade_coordinator.py`, `src/bybit_demo/bybit_demo_adapter.py`, `src/workers/manager.py`
- **Effect**: Adapter's direct `save_trade` removed; new `_trade_history_close_callback` becomes single writer for ALL bybit_demo coordinator paths (WS event, watchdog poll, sniper, time-decay). trade_id uses `state.order_id` with epoch-ms fallback — eliminates collision pattern. 86 missing rows backfill = forward-only.

### CRITICAL-5 — SL on wrong side for Sell
- **Files**: `src/workers/profit_sniper.py`, `src/bybit_demo/bybit_demo_adapter.py`, `src/observability/bybit_demo_alert_relay.py`
- **Effect**: Multi-layer defense. Sniper `SNIPER_WRONG_SIDE_GUARD` blocks the audit's KATUSDT bug at root. Adapter `BYBIT_DEMO_SET_SL_DIRECTION_BUG` and `_TP_DIRECTION_BUG` defensive validation rejects locally before Bybit roundtrip. Mirror logic for TP. ICPUSDT 34040 "not modified" now treated as idempotent success.

### CRITICAL-4 — Alert spam structural defense
- **Files**: `src/alerts/throttle.py`, `src/alerts/alert_manager.py`
- **Effect**: New `normalized_content_hash` replaces digit-runs with `#NUM` before SHA256, so retry storms (5x KATUSDT SET_SL_FAIL with drifting base_price) collapse to one dedup hash. CRITICAL-1+5 already eliminate the audit's specific spam sources; CRITICAL-4 prevents future regressions. Auto-resolves HIGH-8 (alert spam volume).

### HIGH-9 — Cross-symbol tid bleed
- **Files**: `src/core/log_context.py`, `src/workers/profit_sniper.py`, `src/workers/position_watchdog.py`
- **Effect**: New `tid_scope(symbol, role)` context manager with token-restore semantics. Applied to sniper Loop 2/3 + watchdog data_lake / emergency / dup loops + watchdog main monitoring loop top-of-body. Eliminates RENDERUSDT/ATOMUSDT-style bleed.

### HIGH-1 — account_snapshots dormant
- **Files**: `src/core/transformer.py`
- **Effect**: Snapshot save moved out of the `is_shadow` gate. Both modes write equity history. Enrichment stays shadow-only (correct).

### HIGH-2 — exchange_mode columns
- **Files**: `src/database/migrations.py`, `src/database/repositories/trading_repo.py`, `src/core/transformer.py`, `src/workers/manager.py`, `src/bybit_demo/bybit_demo_adapter.py`
- **Effect**: Schema v30. ALTER TABLE on orders, account_snapshots, trade_history with idempotent backfill UPDATEs. Writers updated to pass `exchange_mode` kwarg. trade_intelligence already had it (P4) — audit corrected.

### HIGH-3 — close_trigger hardcoded "exchange_match"
- **Files**: `src/bybit_demo/bybit_demo_adapter.py`
- **Effect**: Per-symbol close_trigger cache in PositionService. close_position stashes the caller's trigger (60s TTL). get_last_close reads from cache; falls back to "exchange_match" for genuinely exchange-initiated closes (legitimate semantic).

### HIGH-7 — REDUCE_FALLBACK swallows context
- **Files**: `src/bybit_demo/bybit_demo_adapter.py`
- **Effect**: Structured ret_code, ret_msg, op fields extracted from `e.details`. New REDUCE_FALLBACK log for `qty_exceeds_size` silent degrade case (was invisible in audit history).

### HIGH-4 — CLAUDE_PROC_STALL on every Stage-2 brain call
- **Files**: `src/brain/claude_code_client.py`
- **Effect**: ROOT CAUSE EXTERNAL (Anthropic API latency on complex prompts). Per Risk 5, structural fix deferred. Observability added: prompt_chars / sys_prompt_chars / cmd_argc on CLAUDE_PROC_SPAWNED; prompt_chars / sys_prompt_chars on every CLAUDE_PROC_STALL_*S event. Operators can now correlate stall rate with prompt complexity.

## Operator's pre-Phase-4 checklist

Before running the combined live trial:

1. **Restart all systemd services**:
   ```
   sudo systemctl restart trading-workers trading-brain trading-mcp-sse
   ```
2. **Verify migration ran**:
   ```
   sqlite3 data/trading.db "SELECT MAX(version) FROM schema_version;"
   # Expected: 30
   sqlite3 data/trading.db "PRAGMA table_info(orders);" | grep exchange_mode
   sqlite3 data/trading.db "PRAGMA table_info(account_snapshots);" | grep exchange_mode
   sqlite3 data/trading.db "PRAGMA table_info(trade_history);" | grep exchange_mode
   ```
3. **Verify backfill applied**:
   ```
   sqlite3 data/trading.db "SELECT exchange_mode, COUNT(*) FROM orders GROUP BY exchange_mode;"
   # Expected: bybit_demo: 88 (or higher) | shadow: 0
   sqlite3 data/trading.db "SELECT exchange_mode, COUNT(*) FROM trade_history GROUP BY exchange_mode;"
   # Expected: bybit_demo: 30 | shadow: 0
   ```
4. **Run live for 4-6 hours** to accumulate 10-20 fresh bybit_demo closes.
5. **Capture logs** — recommend a dedicated log file rotation for the verification window.

## Phase 4 verification queries (run after the soak)

Per CRITICAL-1 verification:
```sql
SELECT COUNT(*) FROM trade_log
WHERE exchange_mode='bybit_demo'
  AND closed_at > '<deploy_ts>'
  AND pnl_usd != 0;
-- Expected: most/all post-deploy bybit_demo rows have non-zero pnl_usd
```

Per CRITICAL-2 verification:
```sql
SELECT exchange_mode, COUNT(*) FILTER(WHERE opened_at = '') / COUNT(*)::FLOAT
FROM trade_log
WHERE closed_at > '<deploy_ts>'
GROUP BY exchange_mode;
-- Expected: 0% empty for both modes
```

Per CRITICAL-3 verification:
```sql
SELECT COUNT(*) FROM trade_log WHERE exchange_mode='bybit_demo' AND closed_at > '<deploy_ts>';
SELECT COUNT(*) FROM trade_history WHERE exchange_mode='bybit_demo' AND exit_time > '<deploy_ts>';
-- Expected: counts match (both rise together with each fresh close)
```

Per CRITICAL-4 verification:
```
grep -c DL_TRADE_SUSPECT logs.txt  # post-deploy
# Expected: ~0 in 3h window (was 49 pre-deploy)

grep "ALERT_SENT" logs.txt | grep -c "level=critical"  # post-deploy
# Expected: well below 50/hour
```

Per CRITICAL-5 verification:
```
grep -c BYBIT_DEMO_SET_SL_FAIL logs.txt  # post-deploy
# Expected: ~0 (was 8/3h pre-deploy; 7 wrong-side eliminated by sniper guard, 1 ICPUSDT idempotent now success)

grep -c BYBIT_DEMO_SET_SL_DIRECTION_BUG logs.txt  # post-deploy
# Expected: ~0 (sniper guard catches before adapter)
grep -c SNIPER_WRONG_SIDE_GUARD logs.txt  # post-deploy
# Expected: small but non-zero — confirms the guard is firing on real attempts
```

Per HIGH-9 verification:
```
grep "tid=t-" logs.txt | awk '...' # cross-symbol bleed sample
# Expected: ~0 distinct (sym, tid_prefix) mismatches (was 8+ in 2.85h pre-deploy)
```

Per HIGH-1 verification:
```sql
SELECT exchange_mode, COUNT(*), MAX(updated_at) FROM account_snapshots GROUP BY exchange_mode;
-- Expected: bybit_demo count > 0 with MAX > deploy_ts (was dormant since 2026-05-08T11:19:21)
```

Per HIGH-3 verification:
```
grep "close_trigger=" logs.txt | grep -v "exchange_match" | head
# Expected: real triggers (sniper_p9, callb_close, wd_emergency, etc.) appearing
```

Per HIGH-4 verification:
```
grep CLAUDE_PROC_STALL_60S logs.txt | head
# Each line now shows prompt_chars=N — operator can correlate
# Stall RATE itself unchanged (deferred per Risk 5)
```

## Closing notes

- **Aggressive-exploitation philosophy preserved**: nothing in this series blocks trades, downgrades pacing, or biases toward capital preservation.
- **Risk Register Risk 8** applies to CRITICAL-1: Performance Enforcer will start receiving real (often negative) PnL signal for the first time. Operator should expect mode-transition events immediately post-deploy. This is expected, not a regression.
- **Backfill scope**: HIGH-2 backfill IS applied (heuristics provably correct). Existing 116 trade_log + 120 trade_thesis + 1322 trade_intelligence corrupted rows from CRITICAL-1+2 are NOT backfilled per Rule 12 default; can be addressed in a separate scoped task if operator chooses.
- **Pre-existing test failure**: `test_apex_direction_lock.py::test_system_prompt_still_has_rsi_caution` fails identically on the pre-series baseline (verified via `git stash` + re-run). Not caused by this series.
- **Pre-existing broken test directory**: `tests/test_phase7/` has 3 import errors from deprecated `brain.executor` / `brain.scheduler` / `brain.prompt_builder` modules (renamed to `.deprecated`). Out of scope for this series.

Awaiting operator restart + 4-6h live trial → Phase 4 verification reports per issue → final sign-off.
