# Phase 9 — Live Observation + Final Report

**Engagement:** Layer 1 corrected migration.
**Date drafted:** 2026-04-26
**Operator action required:** restart `trading-workers` and run the 24-hour observation procedure below.
**Phases complete (commits):**
- Phase 0 — `bca18d0` (investigation)
- Phase 1 — `b14ac0d` (sweet-spot config + scheduler + SweetSpotWorker)
- Phase 2 — `e118eec` (KlineWorker)
- Phase 3 — `c54819b` (structure_worker)
- Phase 4a — `0da6ae6` (SignalWorker)
- Phase 4b — `7ff6fce` (RegimeWorker)
- Phase 4c — `a0735ba` (StrategyWorker)
- Phase 5a — `84f6606` (AltDataWorker)
- Phase 5b — `252c9c6` (PriceWorker)
- Phase 6 — `bb75115` (ScannerWorker → cycle trigger)
- Phase 7 — `d8f6d5b` (cleanup obsolete rotation handlers)
- Phase 8 — `4e07504` (cycle code review)

## What was verified from code (during Phases 0-8)

- All 7 data workers + ScannerWorker now subclass `SweetSpotWorker` (or `BaseWorker` for PriceWorker).
- All 7 data workers read `settings.universe.watch_list` directly. Zero `scanner.get_active_universe()` reads remain on the worker side.
- ScannerWorker's new path bypasses `MarketScanner.scan_market()` raw-ticker fetching; it composes opportunity scores from the 5 worker accessors (`get_setup_score`, `get_score`, `get_signal`, `get_regime`, `get_funding`) and writes to the `active_universe` table.
- Force-include for open-position coins preserved (HR-3) via `position_service.get_positions()` in `_open_position_symbols`.
- BTC/ETH reference-pair force-include preserved.
- Master rotation-callback dispatcher and 5 worker `_on_universe_change` handlers deleted.
- Cycle code (`strategist.py:592` and `:1250`) reads `active_universe` correctly under both old and new architectures — no changes needed.
- MCP, telegram, factory, fund_manager: zero `active_universe` or `watch_list` references — layer-clean.
- `SweetSpotsSettings.__post_init__` rejects malformed MM:SS, out-of-window minutes, and chain-order violations at startup. 26 unit tests pass.

## What needs the live observation period

Sustained-load behaviors that don't reproduce in unit tests or code audits:

1. **Sweet-spot drift under contention.** Target p95 < 1000 ms for every worker.
2. **Cache freshness.** Each of the 50 watch_list coins should have warm caches (TACache, StructureCache, signal cache, regime cache, funding cache) at every cycle moment.
3. **Cycle quality.** No `STRAT_SKIP_STALE` storms (the symptom of the pre-corrected architecture under universe flapping).
4. **No regressions** on D-3 lock contention, brain reliability, order placement, sentiment.
5. **Resource usage.** RSS, FDs, Bybit rate, DB WAL.
6. **Selection quality.** ScannerWorker's composite score should produce sensible top-30 ranks (not all-zero, not BTC/ETH only).

## Operator runbook for the 24-hour observation

### Step 1 — Restart trading-workers cleanly
```bash
sudo systemctl restart trading-workers
sleep 30
sudo journalctl -u trading-workers --since="1 minute ago" | grep -E 'SWEET_SPOT_REGISTERED|WORKER_FIRST_TICK'
```
Expected: 7 `SWEET_SPOT_REGISTERED` lines (kline_worker, structure_worker, signal_worker, regime_worker, strategy_worker, altdata_worker, scanner_worker) + `WORKER_FIRST_TICK` lines as each fires its first sweet spot.

### Step 2 — Watch the first chain (within 5 minutes of restart)
```bash
tail -f logs/workers.log | grep -E 'SWEET_SPOT_FIRED|TICK_SUMMARY' --line-buffered
```
Expected within the first 5-min window:
- `SWEET_SPOT_FIRED worker=kline_worker offset=0:30 drift_ms=...` then `KLINE_TICK_SUMMARY universe=50 ...`
- `SWEET_SPOT_FIRED worker=structure_worker offset=0:45 drift_ms=...` then `XRAY_TICK_SUMMARY universe=50 ...`
- `SWEET_SPOT_FIRED worker=signal_worker offset=1:00 ...` then `SIG_TICK_SUMMARY universe=50 ...`
- `SWEET_SPOT_FIRED worker=regime_worker offset=1:15 ...` then `REGIME_TICK_SUMMARY universe=50 ...`
- `SWEET_SPOT_FIRED worker=strategy_worker offset=1:30 ...` then `STRAT_CYCLE_DONE coins=50 ...`
- `SWEET_SPOT_FIRED worker=altdata_worker offset=1:45 ...` then `ALTDATA_FUNDING_TICK universe=50 ...`
- `SWEET_SPOT_FIRED worker=scanner_worker offset=4:00 ...` then `SCANNER_TICK_SUMMARY watch_list=50 selected=30 ...`

### Step 3 — Cache health (10 minutes after restart)
```bash
# X-RAY cache — should be 50 entries after 2 sweet-spot fires (10 min)
grep 'XRAY_CACHE_HEALTH' logs/workers.log | tail -3

# Active universe table — should have 30 rows
sqlite3 data/trading.db 'SELECT COUNT(*) FROM active_universe; SELECT symbol, opportunity_score FROM active_universe ORDER BY opportunity_score DESC LIMIT 5;'

# Klines table — should have ~50 distinct symbols within the last 10 minutes
sqlite3 data/trading.db "SELECT COUNT(DISTINCT symbol) FROM klines WHERE timestamp > strftime('%s', 'now') * 1000 - 600000;"
```

### Step 4 — Drift measurements (1 hour after restart)
```bash
# Pull all SWEET_SPOT_FIRED lines, extract drift_ms, compute mean/max per worker.
grep 'SWEET_SPOT_FIRED' logs/workers.log | awk -F'worker=' '{print $2}' | awk -F' ' '{
  split($1, w, " "); worker=w[1];
  for (i=1; i<=NF; i++) if ($i ~ /^drift_ms=/) drift=substr($i, 10);
  count[worker]++; sum[worker]+=drift; if (drift>max[worker]) max[worker]=drift;
} END { for (w in count) printf "%s: n=%d mean_drift=%.0fms max_drift=%.0fms\n", w, count[w], sum[w]/count[w], max[w]; }'
```
Expected: every worker p50 < 200 ms, p95 < 1000 ms.

### Step 5 — STRAT_SKIP_STALE storm check (1 hour)
```bash
grep -c 'STRAT_SKIP_STALE' logs/workers.log
```
Pre-corrected baseline: regularly 50+ per hour during universe flapping. Post-corrected target: < 5 per hour (intermittent only).

### Step 6 — KLINE_FRESHNESS_WARN tally (1 hour)
```bash
grep 'KLINE_FRESHNESS_WARN' logs/workers.log | wc -l
grep 'KLINE_FRESHNESS_WARN' logs/workers.log | tail -10
```
Target: 0-2 per hour. Stragglers identified here are candidates for watch_list pruning.

### Step 7 — Bybit API rate (1 hour)
Rough estimate: KlineWorker fires once per 5-min × 50 syms × ~1.5 tf-per-tick ≈ 15/min. Compare against the pre-corrected ~60/min baseline. Bybit's 600/sec window is never approached.

### Step 8 — Memory / RSS (24 hours)
```bash
ps -o rss,etime -p $(systemctl show -p MainPID trading-workers | cut -d= -f2)
```
Track over 24 h. Expected steady-state RSS at 50 coins: ~+1 MB above the 30-coin baseline (per Phase 0 §4.5 estimate).

### Step 9 — Final verification checklist (after 24 hours)

| # | Check | Target | Pass? |
|---|---|---|---|
| 1 | All 7 workers operate on 50 coins | `universe=50` in every TICK_SUMMARY | |
| 2 | Sweet spots fire at configured offsets | drift p95 < 1000 ms per worker | |
| 3 | Chain order verified | KLINE_TICK_SUMMARY < XRAY_TICK_SUMMARY < SIG_TICK_SUMMARY < REGIME_TICK_SUMMARY < STRAT_CYCLE_DONE < ALTDATA_FUNDING_TICK < SCANNER_TICK_SUMMARY in every window | |
| 4 | ScannerWorker selects 30 from warm 50 | `SCANNER_TICK_SUMMARY watch_list=50 selected=30` | |
| 5 | Cycle reads only the 30, with fresh data | `STRAT_CYCLE_DONE coins=50 ... scored=>0` (no zero-coin cycles) | |
| 6 | No KLINE_BACKFILL on universe rotations | `grep -c KLINE_BACKFILL` = 0 | |
| 7 | No STRAT_SKIP_STALE storms | < 5/hour cumulative | |
| 8 | Open positions force-included | `forced_in=N` in SCANNER_TICK_SUMMARY when N > 0 (positions exist) | |
| 9 | Memory stable over 24 hours | RSS Δ < 200 MB over the window | |
| 10 | No new error patterns | `grep -E 'CRITICAL|ERROR' logs/workers.log` shows no novel patterns | |
| 11 | No Bybit rate-limit hits | no `429` or rate-limit warnings | |
| 12 | Layer 2 (Brain + execution) regressions | trades placed at expected cadence | |
| 13 | All 9 phase reports complete | this file + phase0-8 in dev_notes/ | YES |

If all 13 pass, the corrected Layer 1 migration is live and verified.

## What to do if a phase fails

Per HR-6 (per-phase atomic commits), each phase has a rollback target:

| If failure pattern is... | Revert | Then... |
|---|---|---|
| Workers crash on startup config-validation error | `git revert b14ac0d` (Phase 1 + scheduler infra) — but everything else falls with it | Inspect ConfigError, fix config.toml, restart |
| Workers don't fire at sweet spots | `git revert e118eec` (Phase 2 KlineWorker first), see if drift normalizes; if not, revert successive phases | |
| ScannerWorker selects wrong coins | `git revert bb75115` (Phase 6 only) — ScannerWorker reverts to legacy raw-ticker scoring while all 7 data workers still use watch_list (forward-compatible mid-state) | Tune scoring weights in `[scanner.scoring_weights]` and re-apply Phase 6 |
| Migration causes brain or trade-execution regression | revert from Phase 8 backwards until trades stabilize | |

## Conclusion

The migration code is complete and verified at the unit/integration/audit level. The 24-hour live observation is the final verification gate — the operator runs the runbook above, fills the checklist, and the migration is declared successful (or surfaces a specific failure to address).

If checklist passes: this engagement is closed. Future Layer-1 changes follow the corrected architecture defined in `LAYER1_CORRECTED_ARCHITECTURE_BLUEPRINT.md`.
