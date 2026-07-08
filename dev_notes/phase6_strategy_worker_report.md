# Phase 6 — StrategyWorker Universe Audit + Cosmetic Log Promotion

**Date:** 2026-04-26
**Engagement:** `IMPLEMENT_SEVEN_WORKERS_UNIVERSE_INTEGRATION_PROFESSIONAL.md`
**File:** `src/workers/strategy_worker.py` (1,221 LOC)

---

## Findings (from Phase 0 audit)

StrategyWorker is **stateless across rotations.** All four layers (Layer 1 signals → Layer 2 scorer → Layer 3 ensemble → Layer 4 hints) operate on per-tick `candles_map` / `ta_map` working sets that are local variables, discarded at function-end. External caches consumed (TACache, StructureCache, RegimeDetector's `_per_coin_regimes`) are owned and pruned by their respective owners — StrategyWorker is a read-only consumer.

The only finding from the audit was cosmetic:

- **HR-3 cosmetic gap.** `tick()` line 123 (HEAD) emitted `log.debug("Strategy worker: no active coins")` on empty universe — invisible at default log level. Other workers' empty-universe handlers all emit `*_UNIVERSE_EMPTY` at warning level for operator visibility.

## Changes Made

### A. Promote empty-universe log to warning + structured tag

```python
universe = await self.scanner.get_active_universe()
if not universe:
    log.warning(
        f"STRAT_UNIVERSE_EMPTY | reason=scanner_returned_empty | {ctx()}"
    )
    return
```

Now appears in operator log searches alongside `KLINE_UNIVERSE_EMPTY`, `PRICE_UNIVERSE_EMPTY`, `ALTDATA_UNIVERSE_EMPTY`, `REGIME_PERCOIN_EMPTY`, `SIGNAL_UNIVERSE_EMPTY`, and `XRAY_UNIVERSE_EMPTY`.

### B. No functional changes

The audit explicitly verified, by file:line trace:

| Concern | Audit result |
|---|---|
| Universe call cadence | Once per tick at line 121 (now 128). Result is local; no cache. |
| Prefetch query parameterization | `market_repo.get_klines_batch(list(universe), ...)` — universe-bound (lines 176, 194). |
| Stale-skip rule (>5 min) | Layer 1 Phase 4 has Shadow streaming the entire 50-coin watch_list, so any rotation-in candidate already has fresh klines in `klines` table. Stale-skip is correct; no grace period needed. |
| Per-coin internal state | None. `_tick_times` is the only instance dict, and it's a flat list of timestamps for STRAT_HEALTH (cleared every 10 ticks). No per-symbol dicts. |
| TACache lifecycle | TTL-managed (120s) by TACache class itself. StrategyWorker reads, doesn't write to it. |
| StructureCache lifecycle | TTL-managed by StructureCache. StrategyWorker reads via `services["structure_cache"]`. |
| Regime cache | `getattr(self.regime_detector, '_per_coin_regimes', {})` — read-only access. RegimeWorker (Phase 5) owns lifecycle. |
| DailyPnLManager | Global gate, not universe-aware. Confirmed correct by line 94 read. |

## Verification (static)

- `.venv/bin/python -c "from src.workers.strategy_worker import StrategyWorker"` → `OK`
- `ast.parse` of file → OK
- `grep -n "STRAT_UNIVERSE_EMPTY"` → 1 occurrence in code, 1 in inline comment.
- `grep -nE "self\._[a-z]+\[" src/workers/strategy_worker.py` → only `self._tick_times[...]` (rolling timing window). No per-coin instance dicts.

## Verification (runtime — covered by Phase 8 60-min observation)

- `STRAT_CYCLE_DONE | coins=30` per `scan_interval_seconds`.
- `STRAT_REGIME_DIST | total=30` matching active universe size minus stale-skipped coins.
- `STRAT_SKIP_STALE` count = 0 in steady state (Shadow pre-stream guarantee). Transient acceptable on rotation tick.
- `STRAT_PNL_GATE | halted=N` — gate is global, not universe-driven.
- 0 occurrences of `STRAT_UNIVERSE_EMPTY` after the first 30 s of startup.

## Commit

`phase6: strategy worker — empty-universe log promoted to warning`
