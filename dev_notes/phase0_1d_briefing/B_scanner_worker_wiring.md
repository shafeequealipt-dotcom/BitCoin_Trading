# Phase 0.B — Scanner Worker Wiring (read-only audit)

**File:** `src/workers/scanner_worker.py` (1380 lines)
**Last commit touching:** `4223910 xray-counter: real-project end-to-end pipeline verification`

## Class

```python
class ScannerWorker(SweetSpotWorker):
    worker_tier = WorkerTier.LAYER1D    # line 57
    cycle_gated = True                   # line 59

    def __init__(self, settings, db, scanner: MarketScanner, services=None):
        # line 61-76; sweet_spot=settings.workers.sweet_spots.scanner_worker
```

## Entry: `tick()`

`async def tick(self)` at line 867 fires per 5-min sweet-spot. Run loop is `SweetSpotWorker.start()` at `base_worker.py:574-650`.

## The 5-criterion gate (`_qualifies` lines 743-863)

```
Crit 1: structure.setup_type != "none"   (lines 774-789)
        sources: structure_worker._cache.get(coin)
        on miss: reasons_failed.append("no_xray_analysis" or "no_xray_setup_type")

Crit 2: consensus in {STRONG, GOOD}      (lines 791-801)
        threshold: settings.scanner.qualitative.min_consensus = "GOOD"
        source: lm.get_strategy_consensus(symbol)["consensus"]

Crit 3: _regime_aligns(regime, direction) is True   (lines 803-824)
        source: regime_worker.get_regime(symbol).regime.value
        helper: lines 342-355
            long  → trending_up | ranging
            short → trending_down | ranging
            volatile/dead → fail

Crit 4: directional_rr >= 1.1            (lines 826-852)
        source: structure.structural_placement.rr_long / rr_short
        threshold: settings.scanner.qualitative.min_rr_ratio = 1.1
        fallback to legacy rr_ratio if direction-aware missing

Crit 5: _check_blockers(...) returns []  (lines 854-861)
        see C_brain_gate_wiring.md for blockers detail
```

Short-circuits at first failure. Aggregate counter logs FIRST failure only.

## Aggregate counter (`SCANNER_FILTER_AGGREGATE`)

Computed lines 939-979, emitted line 1004:
```
agg = {
  fail_no_xray, fail_setup_none, fail_consensus,
  fail_regime, fail_rr, fail_blockers,
  pass_xray, pass_consensus_strong, pass_consensus_good
}
```

## Continuous score (`_compute_opportunity_score` lines 248-318)

6 weighted normalized components, weights from `[scanner.scoring_weights]`:

```python
score = w.structure * struct_norm    # 0.27
      + w.strategy  * strat_norm     # 0.27
      + w.signal    * sig_norm       # 0.13
      + w.regime    * regime_norm    # 0.13
      + w.funding   * funding_norm   # 0.10
      + w.rr        * rr_norm        # 0.10
```

Returns `(score, breakdown)`. `breakdown` keys consumed by `SCANNER_SELECTED` log:
`structure, structure_raw, structure_conf, strategy, signal, regime, funding, rr`.

## Helpers

| Helper | Lines | Returns | Source |
|---|---|---|---|
| `_get_setup_score` | 80-95 | float (0-100) | structure_worker._cache[coin].setup_score |
| `_get_setup_type_confidence` | 97-117 | float [0.5, 1.0] | structure_worker._cache[coin].setup_type_confidence |
| `_get_strategy_score` | 119-130 | float (0-100) | strategy_worker.get_score(coin) |
| `_get_signal_confidence` | 132-146 | float (0-1) | signal_worker.get_signal(coin).confidence |
| `_get_regime_alignment` | 148-174 | float [-1, +1] | regime_worker.get_regime(coin).regime.value |
| `_get_funding_strength` | 176-192 | float [0, ∞) | abs(altdata_worker.get_funding(coin)) |
| `_get_directional_rr` | 194-244 | float [0, ∞) | structure.structural_placement.rr_long/rr_short |

## Package builder (`_build_package` lines 524-741)

Inputs: `coin, score, record, forced, fg_value, position`.

Sub-blocks built (in order):
1. blockers_observed = list(record.get("blockers", []))   # line 567
2. XrayBlock from structure_worker._cache.get(symbol)       (lines 569-630)
3. StrategiesBlock from lm.get_strategy_consensus + strategy_worker.get_score (lines 632-653)
4. SignalsBlock from signal_worker.get_signal(coin) (lines 655-668)
5. AltDataBlock from altdata_worker.get_funding + fg_value (lines 670-691)
6. PriceDataBlock from market.get_ticker_cached(symbol) (lines 693-718)
7. open_position dict (only if forced=True) (lines 720-727)
Returns CoinPackage with qualification_reasons=record["reasons_passed"] (line 733).

## Top-N selection (lines 1034-1045)

```python
qualified_records.sort(key=lambda r: r[1], reverse=True)   # by score desc
n_max = cfg_q.max_selection   # 15
n_min = cfg_q.min_selection   # 0
selected = qualified_records[:n_max] if len >= n_max else qualified_records
```

## Output handoff (lines 1162-1175)

```python
lm._coin_packages = packages     # line 1164
self.scanner.set_active_universe(symbols)   # line 1302
INSERT INTO active_universe ...   # lines 1271-1291
```

## 5-tuple structure (line 929)

```
list[tuple[str, float, dict, dict, bool]]
       coin   score breakdown record forced
```

Unpacked at lines: 1047 (forced count), 1049 (symbols list), 1118 (per-coin loop, package build), 1273 (DB insert loop), 1322 (SCANNER_SELECTED log loop).

**Any change to this tuple shape breaks 4 unpack sites — preserve exactly.**

## Log tags emitted (per cycle, INFO unless noted)

| Tag | Line | Fields |
|---|---|---|
| LAYER1D_CYCLE_START / DONE | cycle_tracker.py:166, 202 | cycle_id, elapsed_ms |
| SCANNER_FILTER_RESULT | 986/993 (DEBUG) | sym, qualified, forced, score, reasons |
| SCANNER_FILTER_AGGREGATE | 1004 | cycle_id, total, qualified, 6×fail_*, 3×pass_* |
| SCANNER_PACKAGE_BUILD_START | 1067 | cycle_id, packages_to_build |
| SCANNER_PACKAGE_BUILD_DONE | 1177 | cycle_id, packages, total_size_bytes, elapsed_ms |
| PACKAGE_VALIDATE | 1134 | cycle_id, sym, completeness, verdict, missing, stale |
| PACKAGE_QUARANTINED | 1142 (WARN) | cycle_id, sym, completeness, missing, stale |
| PACKAGE_VALIDATE_SUMMARY | 1184 | cycle_id, packages_built, ok, warn, fail_quarantined |
| CYCLE_FRESHNESS | 1215 | cycle_id, klines_age_p50/p95, xray_age_p50/p95, packages_age_p50/p95, key counts |
| SCANNER_SELECTED | 1323 | rank, coin, score, forced, src=structure:.., struct_raw, struct_conf |
| SCANNER_SELECT | 1349 | cycle_id, qualified, selected, forced, watch_list |
| SCANNER_TICK_SUMMARY | 1359 | watch_list, protected, scored, selected, top_n, forced_in, mean_score, top, el_ms, drift_ms |

These are the **invariant tags** that Phase 5+ must preserve.

## Service dependencies

| Service key | Method called | Purpose |
|---|---|---|
| `structure_worker` | `_cache.get(coin)`, `get_setup_score`, `get_setup_type_confidence` | XRAY block |
| `strategy_worker` | `get_score(coin)` | Strategy total_score |
| `signal_worker` | `get_signal(coin)` | Signal confidence + components |
| `regime_worker` | `get_regime(symbol)` | Per-coin regime |
| `altdata_worker` | `get_funding(symbol)` | Funding rate |
| `layer_manager` | `get_strategy_consensus(symbol)` | Consensus dict |
| `market` / `market_service` | `get_ticker_cached(symbol)` | Price data |
| `fear_greed` | `get_latest()` (async) | F&G index |
| `position` / `position_service` | `get_position(sym)`, `get_positions()` (async) | Open positions |
| `cycle_tracker` | `start_cycle("layer1d")`, `end_cycle`, `record_qualified` | Cycle markers |

Defensive access: every `services.get(key)` is None-checked; method calls use `hasattr()`. On failure: log DEBUG `SERVICE_ACCESSOR_FAIL` + return None.

## Error handling pattern

Per CLAUDE.md and existing code: per-item try/except, log error, continue, never crash. Examples at lines 84-95, 110-117, 123-130, 382-397, 572-630, 1118-1161.
