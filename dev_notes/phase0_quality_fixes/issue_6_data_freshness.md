# Phase 0 — Quality Issue 6: Cross-Cycle Data Freshness Measurement

## A — Current observed behaviour

The Layer 1 pipeline has a deterministic timing chain (5-min window, sweet-spot offsets):

```
:00:30 → kline_worker writes klines (typical 14-22s elapsed)
:00:45 → structure_worker reads klines, computes XRAY (~12-21s)
:01:00 → signal_worker reads sentiment + TA, generates signals (~50ms)
:01:15 → regime_worker reads kline-derived TA, classifies (~variable)
:01:30 → strategy_worker reads everything, produces consensus (~37-200ms)
:01:45 → altdata_worker fetches funding + OI + F&G (~5-9s)
:04:00 → scanner_worker reads all caches, builds packages (~variable)
[next window]
:00:00 → brain reads packages, decides → Stage 2
```

If `kline_worker` slows from 14s to 30s, all downstream data is 16s staler. **No log captures this.** Operators have no way to detect pipeline timing degradation other than by parsing per-worker latency logs and computing the chain manually.

**Currently NOT logged:**
- When each cache was last written (per symbol)
- Read-time vs write-time delta at each handoff
- End-to-end cycle freshness

## B — Expected behaviour

Per cycle:
- `CACHE_WRITE | name=<cache> sym=<s> ts=<unix>` — DEBUG, sampled
- `CACHE_READ | name=<cache> sym=<s> freshness_ms=<m> reader=<worker>` — DEBUG, sampled
- `CYCLE_FRESHNESS | cycle_id={id} klines_to_xray_p50={m} ... end_to_end_p50={m}` — INFO, once per cycle

End-to-end p50 in healthy system: ~90s (kline at :30, brain reads at next :00 = 90s window aligning to 5-min cycle).

`/health` Telegram command shows the freshness section:
```
Data Freshness (last 10 cycles):
  Klines→XRAY: p50=15s p95=22s
  XRAY→Strategy: p50=58s p95=72s
  ...
  End-to-end: p50=88s p95=117s
```

## C — Root cause

**Pure new instrumentation.** No existing code is broken. The fix adds a lightweight cache-freshness helper + cycle_tracker integration + /health rendering.

Key constraint: the helper must add < 1 ms per read overhead. Implementation = module-level singleton dict storing `{(cache_name, key): write_time}`; lookups are O(1) dict gets.

## D — Verification approach (post-fix)

| Metric | Measure | Target |
|---|---|---|
| `CYCLE_FRESHNESS` event per cycle | grep workers.log for 1 hour | 12 events (1 per 5-min cycle) |
| End-to-end p50 ~90s in healthy state | numbers in CYCLE_FRESHNESS log | 75–105s |
| Slow chain detection | manually delay kline_worker by 15s; verify `klines_to_xray_p50` reflects | freshness rises ~15s |
| `/health` shows freshness | Telegram /health output | "Data Freshness" section visible |
| Negligible overhead | strategy_worker tick latency unchanged | within 5% of baseline |

## E — Rollback path

Phase 6 changes are additive: new `cache_freshness.py` + read/write hooks at each cache + new `CYCLE_FRESHNESS` emit + /health section. Each commit revertable independently. If any cache hook proves expensive, revert that one only.

## Files end-to-end mapped

| File | Lines | Role |
|---|---|---|
| `src/core/cache_freshness.py` | NEW | Lightweight singleton: `record(name, key)` writes timestamp; `read_age(name, key)` returns delta |
| `src/workers/kline_worker.py` | (cache write site) | Add `cache_freshness.record("klines", symbol)` post-write |
| `src/workers/structure_worker.py` | (cache write + read sites) | Record on write; emit read-age into CACHE_READ log |
| `src/workers/signal_worker.py` | (cache write + read sites) | Same pattern |
| `src/workers/regime_worker.py` | (cache write + read sites) | Same |
| `src/workers/strategy_worker.py` | (cache write + read sites) | Same |
| `src/workers/scanner_worker.py` | (read sites) | Read freshness when building packages |
| `src/core/cycle_tracker.py` | (existing) | Add `record_handoff_freshness(...)` + emit `CYCLE_FRESHNESS` per cycle |
| `src/telegram/handlers/system.py` | (`/health`) | Render last-10-cycle p50/p95 freshness section |
| `tests/test_cache_freshness.py` | NEW | Verify timestamp-on-write + read-time delta + aggregation |

## Phase 6 fix outline (preview)

4 atomic commits:
1. Add `cache_freshness.py` helper (~80 lines).
2. Instrument every cache write/read across the 6 workers.
3. Cycle_tracker emits `CYCLE_FRESHNESS` per cycle with all handoff p50/p95s.
4. /health Data Freshness section.
