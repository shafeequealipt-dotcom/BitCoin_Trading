# G4 — Last 7 ScannerWorker Cycles

## Capture metadata

- **Capture timestamp:** 2026-04-27 23:00:00 UTC
- **Sources:** `data/logs/workers.log` (current), `data/logs/workers.2026-04-27_01-31-00_169356.log` (rotated, contains pre-22:53 ticks). The current `workers.log` was rotated/started at 22:06:01 UTC after a worker restart so cycles 21:50-22:00 come from the rotated file.
- **Cycles selected:** the 7 most recent cycles ending at scanner sweet-spot 4:00 within each 5-minute window where SCANNER_FILTER_AGGREGATE was emitted (i.e., cycle ran rather than skipped). Process restart at 22:53:35 means no further qualifying cycle ran within the capture window (the 22:29, 22:34, 22:39, 22:44, 23:04, 23:14 ticks all logged `LAYER1D_TICK_SKIP | reason=cycle_inactive`).
- **Per-cycle log tags grepped:** `SCANNER_FILTER_AGGREGATE`, `SCANNER_PACKAGE_BUILD_START`, `PACKAGE_VALIDATE`, `SCANNER_PACKAGE_BUILD_DONE`, `PACKAGE_VALIDATE_SUMMARY`, `CYCLE_FRESHNESS`, `SCANNER_SELECT`, `SCANNER_TICK_SUMMARY`, `LAYER1D_TICK_DONE`.

---

## Cycle 1 — `c-2026-04-27-21:50` (tick 21:54:00 UTC)

**Source:** `workers.2026-04-27_01-31-00_169356.log:57687-57699`

Per-coin qualification (aggregate only):
- `total=50 qualified=0 fail_no_xray=50 fail_setup_none=0 fail_consensus=0 fail_regime=0 fail_rr=0 fail_blockers=0 pass_xray=0 pass_consensus_strong=0 pass_consensus_good=0`
- **Why:** `fail_no_xray=50` — every coin failed the X-RAY presence check. StructureCache had `xray_keys=0` per the CYCLE_FRESHNESS line (`xray_age_p50_ms=unknown`).

Bucket counts: see qualification line — single bucket (`fail_no_xray=50`).

Selection result:
```
SCANNER_SELECT | qualified=0 selected=2 forced=2 watch_list=50
```
- 0 qualified, 2 forced (BTCUSDT + ETHUSDT — both held open positions).

Package validation:
```
PACKAGE_VALIDATE | sym=BTCUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed'] stale=[]
PACKAGE_VALIDATE | sym=ETHUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed'] stale=[]
PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=0 warn=2 fail_quarantined=0
```

Cycle freshness: `klines_keys=0 xray_keys=0 packages_keys=3` (cold caches — kline_worker had not yet ticked).

Total elapsed (LAYER1D_TICK_DONE line 57699): `elapsed_ms=66 drift_ms=1`. SCANNER_TICK_SUMMARY: `el=64ms`.

---

## Cycle 2 — `c-2026-04-27-21:55` (tick 21:59:00 UTC)

**Source:** `workers.2026-04-27_01-31-00_169356.log:58658-58669`

Aggregate:
- `total=50 qualified=0 fail_no_xray=25 fail_setup_none=5 fail_consensus=19 fail_regime=0 fail_rr=1 fail_blockers=0 pass_xray=20 pass_consensus_strong=0 pass_consensus_good=1`

Selection: `qualified=0 selected=2 forced=2 watch_list=50`

Packages built: 2
- BTCUSDT completeness=0.89 verdict=ok missing=['price_data.regime', 'alt_data.fear_greed']
- ETHUSDT completeness=0.94 verdict=ok missing=['alt_data.fear_greed']

`PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=2 warn=0 fail_quarantined=0`

Cycle freshness: `klines_age_p50_ms=190410 xray_age_p50_ms=194691 packages_age_p50_ms=0 klines_keys=200 xray_keys=25 packages_keys=3`.

Total elapsed: `elapsed_ms=19 drift_ms=1` (LAYER1D_TICK_DONE), `el=18ms` (SCANNER_TICK_SUMMARY). `mean_score=0.000 top=BTCUSDT(0.000)`.

---

## Cycle 3 — `c-2026-04-27-22:00` (tick 22:04:00 UTC)

**Source:** `workers.2026-04-27_01-31-00_169356.log:59446-59457`

Aggregate:
- `total=50 qualified=0 fail_no_xray=25 fail_setup_none=10 fail_consensus=13 fail_regime=0 fail_rr=2 fail_blockers=0 pass_xray=15 pass_consensus_strong=2 pass_consensus_good=0`

Selection: `qualified=0 selected=2 forced=2 watch_list=50`

Packages:
- BTCUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed']
- ETHUSDT completeness=0.73 verdict=warn missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed']

`PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=0 warn=2 fail_quarantined=0`

Cycle freshness: `klines_keys=200 xray_keys=50 packages_keys=3`.

Total elapsed: `elapsed_ms=22 drift_ms=1`, `mean_score=0.027 top=BTCUSDT(0.055) protected=1`.

---

## Cycle 4 — `c-2026-04-27-22:05` (tick 22:09:00 UTC)

**Source:** `workers.log:369-381`

Aggregate:
- `total=50 qualified=0 fail_no_xray=25 fail_setup_none=6 fail_consensus=15 fail_regime=1 fail_rr=3 fail_blockers=0 pass_xray=19 pass_consensus_strong=4 pass_consensus_good=0`

Selection: `qualified=0 selected=2 forced=2 watch_list=50`

Packages:
- BTCUSDT completeness=0.89 verdict=ok missing=['price_data.regime', 'alt_data.fear_greed']
- ETHUSDT completeness=0.94 verdict=ok missing=['alt_data.fear_greed']

`PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=2 warn=0 fail_quarantined=0`

Cycle freshness: `klines_age_p50_ms=209947 xray_age_p50_ms=194959 klines_keys=200 xray_keys=50 packages_keys=3`.

Total elapsed: `elapsed_ms=31 drift_ms=2`, `mean_score=0.123 top=BTCUSDT(0.247) protected=1`. PACKAGE size: `total_size_bytes=1977`.

---

## Cycle 5 — `c-2026-04-27-22:10` (tick 22:14:00 UTC)

**Source:** `workers.log:1252-1263`

Aggregate:
- `total=50 qualified=0 fail_no_xray=25 fail_setup_none=10 fail_consensus=12 fail_regime=2 fail_rr=1 fail_blockers=0 pass_xray=15 pass_consensus_strong=3 pass_consensus_good=0`

Selection: `qualified=0 selected=2 forced=2 watch_list=50`

Packages:
- ETHUSDT completeness=0.73 verdict=warn missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed']
- BTCUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed']

`PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=0 warn=2 fail_quarantined=0`

Cycle freshness: `klines_age_p50_ms=202235 xray_age_p50_ms=194966 packages_age_p50_ms=0 klines_keys=200 xray_keys=50 packages_keys=3`.

Total elapsed: `elapsed_ms=20 drift_ms=1`, `mean_score=0.196 top=ETHUSDT(0.392) protected=1`. Size: `total_size_bytes=1876`.

---

## Cycle 6 — `c-2026-04-27-22:15` (tick 22:19:00 UTC)

**Source:** `workers.log:2090-2101`

Aggregate:
- `total=50 qualified=0 fail_no_xray=25 fail_setup_none=6 fail_consensus=14 fail_regime=2 fail_rr=3 fail_blockers=0 pass_xray=19 pass_consensus_strong=4 pass_consensus_good=1`

Selection: `qualified=0 selected=2 forced=2 watch_list=50`

Packages:
- BTCUSDT completeness=0.89 verdict=ok missing=['price_data.regime', 'alt_data.fear_greed']
- ETHUSDT completeness=0.94 verdict=ok missing=['alt_data.fear_greed']

`PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=2 warn=0 fail_quarantined=0`

Cycle freshness: `klines_age_p50_ms=201980 xray_age_p50_ms=194985 klines_keys=200 xray_keys=50 packages_keys=3`.

Total elapsed: `elapsed_ms=22 drift_ms=3`, `mean_score=0.000 top=BTCUSDT(0.000) protected=0`. Size: `total_size_bytes=1956`.

---

## Cycle 7 — `c-2026-04-27-22:20` (tick 22:24:00 UTC)

**Source:** `workers.log:2799-2810`

Aggregate:
- `total=50 qualified=0 fail_no_xray=25 fail_setup_none=10 fail_consensus=12 fail_regime=2 fail_rr=1 fail_blockers=0 pass_xray=15 pass_consensus_strong=3 pass_consensus_good=0`

Selection: `qualified=0 selected=2 forced=2 watch_list=50`

Packages:
- BTCUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed']
- ETHUSDT completeness=0.73 verdict=warn missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed']

`PACKAGE_VALIDATE_SUMMARY | packages_built=2 ok=0 warn=2 fail_quarantined=0`

Cycle freshness: `klines_age_p50_ms=209932 xray_age_p50_ms=194995 klines_keys=200 xray_keys=50 packages_keys=3`.

Total elapsed: `elapsed_ms=32 drift_ms=1`, `mean_score=0.000 top=BTCUSDT(0.000) protected=0`. Size: `total_size_bytes=1894`.

---

## After 22:24

The next 5 scheduled scanner sweet-spots (22:29, 22:34, 22:39, 22:44, **22:53 process restart**, 23:04, 23:14) all logged:
```
LAYER1D_TICK_SKIP | sub=scanner_worker reason=cycle_inactive drift_ms=N rate_limited=true
```
The L3 cycle gate flipped to inactive (LayerManager.is_cycle_active()=False) after 22:24. Per `WORKER_LIVENESS_HEARTBEAT` lines, `cycle_active=False` was first observed at 2026-04-27 22:27:59.775 (workers.log:3655). No SCANNER_FILTER_AGGREGATE cycles ran past 22:24 within the capture window.

---

## Cross-cycle aggregate table

| # | Cycle | Tick UTC | total | qualified | fail_no_xray | fail_setup_none | fail_consensus | fail_regime | fail_rr | fail_blockers | pass_xray | pass_str | pass_good | selected | forced | el_ms | top |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | c-21:50 | 21:54:00 | 50 | 0 | 50 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 2 | 66 | BTCUSDT(0.000) |
| 2 | c-21:55 | 21:59:00 | 50 | 0 | 25 | 5 | 19 | 0 | 1 | 0 | 20 | 0 | 1 | 2 | 2 | 19 | BTCUSDT(0.000) |
| 3 | c-22:00 | 22:04:00 | 50 | 0 | 25 | 10 | 13 | 0 | 2 | 0 | 15 | 2 | 0 | 2 | 2 | 22 | BTCUSDT(0.055) |
| 4 | c-22:05 | 22:09:00 | 50 | 0 | 25 | 6 | 15 | 1 | 3 | 0 | 19 | 4 | 0 | 2 | 2 | 31 | BTCUSDT(0.247) |
| 5 | c-22:10 | 22:14:00 | 50 | 0 | 25 | 10 | 12 | 2 | 1 | 0 | 15 | 3 | 0 | 2 | 2 | 20 | ETHUSDT(0.392) |
| 6 | c-22:15 | 22:19:00 | 50 | 0 | 25 | 6 | 14 | 2 | 3 | 0 | 19 | 4 | 1 | 2 | 2 | 22 | BTCUSDT(0.000) |
| 7 | c-22:20 | 22:24:00 | 50 | 0 | 25 | 10 | 12 | 2 | 1 | 0 | 15 | 3 | 0 | 2 | 2 | 32 | BTCUSDT(0.000) |

Across all 7 cycles: `qualified=0` every cycle. Selection mechanism: forced packages (BTCUSDT + ETHUSDT — both held open positions) for all 7. No coin ever passed the qualitative checklist (Phase 5 gate: STRONG/GOOD consensus + RR≥2.0 + regime alignment + no blockers).

Per-cycle WHY summary:
- **Cycle 1 (21:50):** 100% fail_no_xray — StructureCache empty (kline_worker hadn't ticked yet).
- **Cycles 2-7:** ~25/50 fail_no_xray (the structure_worker batches half the universe per tick — see G3.F where `cached=50` but each tick analyzes only `symbols=25`); 5-10 fail_setup_none; 12-19 fail_consensus; 0-3 fail_regime; 1-3 fail_rr; **0 blockers** in any cycle. `pass_consensus_strong` ranges 0-4 per cycle; `pass_consensus_good` ranges 0-1.
