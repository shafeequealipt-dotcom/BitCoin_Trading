# Five Priority Cascade Fixes — Combined Final Verification

This document is the operator-facing summary of the cascade-fix series.
Phase 0 baseline + per-issue investigation reports + per-issue
implementation commits are filed under `dev_notes/cascade_fixes/`.

The deploy-and-measure portion (Phase 4 of each issue) is operator-led —
the live cascade only manifests under sustained load (5+ open positions
for hours). The operator runs the fixed branch in production and
captures comparison metrics. Section "Live verification protocol" below
is the runbook.

---

## Executive summary

| # | Issue | Branch | Commit | Tests | Status |
|---|-------|--------|--------|------:|--------|
| 1 | fear_greed_index defensive cleanup | `fix/cascade-i1-fear-greed` | `edaacd9` | 10 | shipped |
| 3 | profit_sniper concurrent modification race | `fix/cascade-i3-sniper-race` | `3c9d3c4` | 4 | shipped |
| 5 | Layer4ProtectionService late-wire | `fix/cascade-i5-l4-late-wire` | `13206ad` | 4 | shipped |
| 2 | ticker_cache write storm batching | `fix/cascade-i2-ticker-batch` | `64166dc` | 12 | shipped |
| 4 | positions table parity + exchange_mode | `fix/cascade-i4-positions-parity` | `f2116b7` | 10 | shipped |

All branches stack on `feature/bybit-demo-adapter`. The tip
`fix/cascade-i4-positions-parity` carries all 5 fixes plus baseline
docs. **62 cascade-fix-series and neighboring tests pass; no regressions.**

---

## What changed (per-issue)

### Issue 1 — fear_greed_index defensive cleanup

Root cause flagged by the report did NOT match current evidence. Phase 0 baseline showed:
- ticker_cache: 99.7% of all `DB_LOCK_WAIT` holders
- fear_greed_index: 0% of holders

Defensive cleanup applied to the only remaining unbounded query:
- `AltDataRepository.get_fear_greed_history` accepts `limit` kwarg (default 10,000)
- `FearGreedClient.get_history` clamps `days` to [1, 365] and `limit` to [1, 10000]
- Schema v31: `idx_fear_greed_ts_asc` so `ORDER BY timestamp ASC` is index-served (not scan-and-sort against TEXT timestamp)
- Debug log `FEAR_GREED_HISTORY_QUERY` for instrumentation

### Issue 3 — profit_sniper concurrent modification race

Confirmed in current `workers.log` (2026-05-10 17:25:39 XRPUSDT crash; prior 2026-05-09 15:38:43 MONUSDT). Lines 649 and 689 of `profit_sniper.py` were already snapshot-iterated; line 327 was missed.

- `profit_sniper.py:327` now iterates `list(self._tracked.items())`
- `trade_coordinator.py:937` `get_status` defensive snapshot

### Issue 5 — Layer4ProtectionService late-wire

Confirmed in current logs: 130 `services_unwired` events in a 2-hour window with a perfect 1:1 match to 130 `TIME_DECAY_STRUCT_GUARD blocked=true` events.

- `manager.py:~1494` adds the missing late-wire block (mirrors the watchdog/profiler/scanner late-wires above)
- L4's `regime_detector` and `structure_cache` attached after `RegimeDetector` is built
- `L4_LATE_WIRE` log line for observability

### Issue 2 — ticker_cache write storm batching (the actual cascade fix)

Phase 0 baseline confirmed 99.7%+ of cascade waits held by per-message ticker_cache writes; max wait 63.6s.

- New `src/workers/ticker_cache_buffer.py` — `TickerCacheBuffer` with thread-safe `put`, async drainer flushing every 500ms via `executemany`
- `MarketRepository.save_tickers_batch` mirrors the existing `save_klines` chunking pattern
- `PriceWorker._handle_ticker_update` puts into the buffer instead of scheduling per-message `save_ticker`
- `Transformer._get_local_price` and `MarketRepository.get_ticker` consult buffer first (fresher than DB)
- WorkerManager constructs the buffer + wires it to PriceWorker and Transformer
- `TICKER_BUFFER_START` / `TICKER_BUFFER_HEARTBEAT` logs for observability

### Issue 4 — positions table parity + exchange_mode

Confirmed 0 rows in `positions` table during active bybit_demo trading. Schema v32 closes the parity gap.

- `migrations.py` SCHEMA_VERSION 32: `ALTER TABLE positions ADD COLUMN exchange_mode TEXT NOT NULL DEFAULT 'shadow'` + `idx_positions_mode`
- `TradingRepository.save_position` accepts `exchange_mode` kwarg (matches `save_order` / `save_trade` pattern)
- `BybitDemoPositionService.get_positions` calls `save_position(pos, exchange_mode='bybit_demo')` per non-zero open position
- `PositionService.get_positions` (live) passes `exchange_mode='shadow'` explicitly
- `scripts/backfill_positions_exchange_mode.py` for parity (vacuous on current DB; mirrors v30 pattern)

---

## Live verification protocol (operator runbook)

After deploying `fix/cascade-i4-positions-parity` (which carries all 5 fixes), follow this protocol.

### Pre-deploy checklist

1. Backup the production DB:

       cp data/trading.db data/trading.db.bak-pre-cascade-fixes-$(date -u +%Y%m%d_%H%M%S)

2. Snapshot Phase 0 baseline metrics from `dev_notes/cascade_fixes/phase0_baseline.md` so the comparison is apples-to-apples.

3. Notify yourself: Issue 5 (L4 gate restoration) may release a backlog of force-closes for positions that were silently held past structural validity. Watch the first 1-2 hours of `TIME_DECAY_FORCE_CLOSE` events and confirm they look appropriate. None of the 5 fixes block trades or downgrade pacing — aim-preservation is satisfied.

### Deploy

1. Restart workers:

       sudo systemctl restart trading-mcp-workers

2. Tail the workers log for the first 30 seconds:

       tail -f data/logs/workers.log | grep -E "L4_LATE_WIRE|TICKER_BUFFER_START|MIGRATIONS_SUMMARY"

   Expected lines:
   - `MIGRATIONS_SUMMARY` (or `Schema upgrade: 30 -> 32`)
   - `L4_LATE_WIRE | regime_detector=ok structure_cache=ok`
   - `TICKER_BUFFER_START | flush_interval_ms=500`

### Verification metrics — collect after 4-6 hours of live trading with 5+ positions

| Metric | Phase 0 baseline | Target | How to measure |
|--------|-----------------:|-------:|----------------|
| ticker_cache % of `DB_LOCK_WAIT` holders | 99.7% | <50% | `awk` script across `data/logs/general*.log` (see `phase0_baseline.md`) |
| Max `wait_ms` | 63,648 | <1,000 | `grep -aoE "wait_ms=[0-9]+" data/logs/general*.log \| sort -t= -k2 -rn \| head -1` |
| Count of `wait_ms > 10,000` per ~9-min window | 27,208 | <50 | `grep -aoE "wait_ms=[0-9]{5,}" data/logs/general*.log \| wc -l` |
| `services_unwired` count per 2h | 130 | **0** | `grep -ac "services_unwired" data/logs/workers.log` |
| `TIME_DECAY_STRUCT_GUARD blocked=true reason='no_data:services_unwired'` | 130 | **0** | `grep -ac "no_data:services_unwired" data/logs/workers.log` |
| profit_sniper `WORKER_TICK_FAIL` count per 2h | 1 | **0** | `grep -aE "WORKER_TICK_FAIL.*profit_sniper" data/logs/workers.log \| wc -l` |
| `positions` row count when bybit_demo open | 0 | >0 | `sqlite3 data/trading.db "SELECT exchange_mode, COUNT(*) FROM positions GROUP BY exchange_mode;"` |
| `WD_TICK_SLOW` count per 2h | 14 | substantial drop | `grep -ac "WD_TICK_SLOW" data/logs/workers.log` |
| `BASE_WORKER_TICK_SLOW` count per 2h | 230 | substantial drop | `grep -ac "BASE_WORKER_TICK_SLOW" data/logs/workers.log` |
| `TICKER_BUFFER_HEARTBEAT` lines | (new) | one per ~30s | `grep -ac "TICKER_BUFFER_HEARTBEAT" data/logs/workers.log` |

### Cascade load test (after the 4-6h run passes)

Open 8-10 positions and sustain for several hours. Verify:
- watchdog tick stays near the ~80 ms baseline
- profit_sniper `restart_count` stays 0
- No `WORKER_TICK_FAIL`
- Force-closes for structural invalidation fire when warranted (no perpetual `services_unwired`)
- Telegram `/positions` returns the actual open positions (not empty)

### Shadow-mode regression check

Switch `[general] mode = "shadow"` in `config.toml` and let one cycle run. Verify:
- PriceWorker still ticks; `TICKER_BUFFER_HEARTBEAT` continues to fire (buffer is mode-agnostic)
- shadow rows continue to be written: `sqlite3 data/trading.db "SELECT COUNT(*) FROM positions WHERE exchange_mode='shadow';"` > 0
- Layer4 gate functions correctly for shadow positions

### Backfill (one-shot)

After deploy:

    python -m scripts.backfill_positions_exchange_mode

Vacuous on current DB but logged for audit symmetry with the v30 backfills.

---

## Test coverage

```
tests/test_altdata_fear_greed_history.py       6 tests   (Issue 1 repo)
tests/test_fear_greed_client_clamps.py         4 tests   (Issue 1 client)
tests/test_profit_sniper_iteration_race.py     4 tests   (Issue 3)
tests/test_layer4_protection/test_late_wire.py 4 tests   (Issue 5)
tests/test_ticker_cache_buffer.py             12 tests   (Issue 2)
tests/test_positions_exchange_mode.py         10 tests   (Issue 4)
                                            ─────────
                                              40 tests   (cascade-fix series)
```

Plus 22 prior-existing neighboring tests still pass:
```
tests/test_layer4_protection/test_protection_service.py    10
tests/test_layer4_protection/test_sniper_integration.py     4
tests/test_altdata_repo_oi_delta.py                         6
tests/test_profit_sniper_partial_cap.py                     2
                                                          ────
                                                            22
```

**Total 62 passes, 0 regressions.**

---

## Out of scope (per the spec)

These remain open and are NOT addressed by this fix series:

- The 43% win rate / -7% session PnL pattern (separate issue: trade quality)
- Brain force-closing on noise (Bug #14, #32)
- APEX SL distance for low-vol coins
- WS reconnect storm during idle window
- The orders table missing Bybit-initiated closes (A9)
- CALL_A latency (separate brain timing)
- The remaining ~37 monitoring-report findings

This series restores **operational stability**, not strategy edge. Profitability is unchanged.

---

## Operator sign-off

After the live verification protocol above passes, please update this file with:

- [ ] Phase 4.1 metrics (4-6h post-deploy) — _to be filled by operator_
- [ ] Cascade load-test results — _to be filled by operator_
- [ ] Shadow-mode regression check — _to be filled by operator_
- [ ] New operational ceiling (was 3-4 positions; now ?) — _to be filled by operator_
- [ ] Sign-off — _to be filled by operator_

When signed off, this project is complete.
