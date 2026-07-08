# G3 — Live Cache Snapshots (reconstructed from logs)

## Capture metadata

- **Capture timestamp:** 2026-04-27 23:00:00 UTC (file write)
- **Log read window:** 2026-04-27 22:05 - 23:12 UTC
- **Process state:** workers process restarted at 2026-04-27 22:53:35 UTC (line `4338` of current `workers.log`). The 22:05-22:26 cycles in the log come from the prior PID that exited at 22:45:52 (line `4248` `Worker 'scanner_worker' stopped (total_ticks=7, errors=0)`), and were captured while running.
- **Snapshot mechanism:** **NOT FOUND — searched** `src/workers/scanner_worker.py`, `src/workers/strategy_worker.py`, `src/workers/structure_worker.py`, `src/workers/regime_worker.py`, `src/workers/signal_worker.py`, `src/strategies/scanner.py`, `src/core/coin_package.py`, `src/server.py` — no in-process cache-dump endpoint or file-write mechanism for `_score_cache`, `_strategy_consensus`, `_signal_cache`, `_per_coin_regimes`, `_coin_packages`, `StructureCache`. The only available signal is the aggregate emitted by each worker on every tick (`STRAT_L4_HANDOFF`, `STRAT_CONSENSUS_SUMMARY`, `SIG_BATCH_STATS`, `XRAY_TICK_SUMMARY`, `REGIME_TICK_SUMMARY`, `PACKAGE_VALIDATE` per package, `SCANNER_PACKAGE_BUILD_DONE`, `CYCLE_FRESHNESS`). Reconstructed below from the latest log lines.

---

## A. `_score_cache` (StrategyWorker) — reconstructed from `STRAT_L4_HANDOFF` aggregates

Source: `src/workers/strategy_worker.py:819`

Logged shape: total entries only. Per-coin contents not in the aggregate log.

| Cycle (sid time) | score_cache_size | consensus_size | consensus_summary_size | hints_top20_size | handoff el_ms |
|---|---|---|---|---|---|
| 2026-04-27 22:06:33.372 | **16** | 16 | 7 | 7 | 2 |
| 2026-04-27 22:11:33.807 | **18** | 18 | 7 | 9 | 2 |
| 2026-04-27 22:16:38.778 | **18** | 18 | 7 | 9 | 8 |
| 2026-04-27 22:21:37.786 | **19** | 19 | 6 | 7 | 1 |
| 2026-04-27 22:26:39.110 | **19** | 19 | 7 | 7 | 1 |

Most recent: **19 of 50 watch_list coins have a score cache entry** at 22:26:39 UTC. Per-coin score values **NOT FOUND in logs** — only the count is emitted. The 50-coin target is not met; ~31/50 coins missing.

---

## B. `_strategy_consensus` — reconstructed from `STRAT_CONSENSUS_SUMMARY` and `STRAT_CONSENSUS_CHANGE`

Source: `src/workers/strategy_worker.py:743/755/771`

Most recent summary (cycle sid=s-1777328790019, 2026-04-27 22:26:39.109 UTC):
```
STRAT_CONSENSUS_WRITE | full_count=9 filtered_count=7 setups_in=10 cache_size_after=19 mode=NORMAL threshold=50
STRAT_CONSENSUS_SUMMARY | total=9 GOOD=5 STRONG=1 WEAK=3
```

Per-coin transitions logged this cycle (full coverage of changes only — coins whose consensus did not change are not re-logged):
- `BSBUSDT from=STRONG to=GOOD votes=38 score=0.75`
- `AAVEUSDT from=STRONG to=GOOD votes=38 score=0.75`
- `HYPERUSDT from=LEAN to=WEAK votes=38 score=0.30`

Prior cycle summary (22:21:37.786, sid=s-1777328490002):
```
STRAT_CONSENSUS_SUMMARY | total=10 GOOD=3 LEAN=1 STRONG=3 WEAK=3
```
Per-coin transitions in that cycle:
- `AAVEUSDT from=WEAK to=STRONG votes=38 score=1.00`
- `AEROUSDT from=WEAK to=GOOD votes=38 score=0.75`
- `HYPERUSDT from=WEAK to=LEAN votes=38 score=0.50`
- `DYDXUSDT from=NONE to=WEAK votes=38 score=0.30`

**Reconstructed snapshot at 2026-04-27 22:26:39 UTC:**

- Cache size: **19** entries (out of 50 watch_list target)
- Filtered (mode=NORMAL threshold=50): **7** entries above threshold; **9** entries above 0
- Distribution (most recent): `STRONG=1, GOOD=5, WEAK=3` (sums to 9; LEAN missing means LEAN=0 this cycle)
- Vote_count per coin: **38** (constant in all logs — number of registered strategies)
- `score` field: STRONG=1.00, GOOD=0.75, LEAN=0.50, WEAK=0.30 (per `ensemble.py:99` map, see G2)

Per-coin direction field: **NOT FOUND in logs.** Direction lives in the in-memory consensus dict but is not emitted.

`last_updated`: implicit — equals the `STRAT_CONSENSUS_WRITE` timestamp `2026-04-27 22:26:39.109`.

---

## C. `_strategy_consensus_summary`

Source: same `STRAT_CONSENSUS_SUMMARY` line above. The "summary" cache referenced by `STRAT_L4_HANDOFF.consensus_summary_size=7` matches the `filtered_count=7` count in the WRITE line — only coins with score above the mode=NORMAL threshold=50 land here. **7 entries at 22:26:39 UTC.**

---

## D. `_signal_cache` (SignalWorker) — reconstructed from `SIG_TICK_SUMMARY` + `SIG_BATCH_STATS`

Source: `src/workers/signal_worker.py:115/129/143/163`

Per-tick aggregate (most recent 5 ticks):

| Timestamp | universe | signals | mean_conf | conf_min | conf_max | conf_std | strongest |
|---|---|---|---|---|---|---|---|
| 22:06:01.889 | 50 | **50** | 0.25 | 0.203 | 0.429 | 0.054 | (n/a in line shown) |
| 22:11:00.940 | 50 | **50** | 0.24 | 0.203 | 0.343 | 0.048 | — |
| 22:16:01.296 | 50 | **50** | 0.27 | 0.203 | 0.344 | 0.058 | — |
| 22:21:00.537 | 50 | **50** | 0.22 | 0.203 | 0.344 | 0.035 | — |
| 22:26:03.374 | 50 | **50** | 0.21 | 0.203 | 0.335 | 0.025 | ORCAUSDT type=neutral conf=0.33 |

Per-coin sample lines from cycle ending 22:26:03.374:
```
22:26:02.549 Signal for FILUSDT: neutral (confidence: 0.20)
22:26:02.575 Signal for MNTUSDT: neutral (confidence: 0.20)
22:26:02.591 Signal for MONUSDT: neutral (confidence: 0.20)
22:26:02.849 Signal for SKRUSDT: neutral (confidence: 0.20)
22:26:02.868 Signal for PLUMEUSDT: neutral (confidence: 0.24)
22:26:02.887 Signal for EGLDUSDT: neutral (confidence: 0.20)
22:26:02.906 Signal for ALGOUSDT: neutral (confidence: 0.20)
22:26:03.062 Signal for BSBUSDT: neutral (confidence: 0.25)
22:26:03.078 Signal for KATUSDT: neutral (confidence: 0.20)
22:26:03.096 Signal for HYPERUSDT: neutral (confidence: 0.20)
22:26:03.114 Signal for ORCAUSDT: neutral (confidence: 0.33)
22:26:03.136 Signal for BLURUSDT: neutral (confidence: 0.20)
22:26:03.292 Signal for OPUSDT: neutral (confidence: 0.20)
22:26:03.313 Signal for APTUSDT: neutral (confidence: 0.20)
22:26:03.330 Signal for LTCUSDT: neutral (confidence: 0.24)
22:26:03.350 Signal for BCHUSDT: neutral (confidence: 0.20)
22:26:03.374 Signal for ALICEUSDT: neutral (confidence: 0.20)
```

Reconstructed snapshot at **2026-04-27 22:26:03 UTC**:
- 50 entries (full universe coverage)
- All 50 in the sample window classified `signal_type=neutral`
- Confidence range 0.203 - 0.335; mean 0.214; std 0.025
- Direction: **`neutral`** for all 50 coins this cycle (no buy/sell signals fired)
- Strongest: ORCAUSDT @ conf=0.335

---

## E. `_per_coin_regimes` (RegimeDetector) — reconstructed from `REGIME_TICK_SUMMARY`

Source: `src/workers/regime_worker.py:293`

Per-tick aggregate (most recent 5 ticks):

| Timestamp | universe | global | per_coin_size | el_ms |
|---|---|---|---|---|
| 22:06:19.059 | 50 | ranging | **49** | 4057 |
| 22:11:19.046 | 50 | ranging | **49** | 4044 |
| 22:16:24.883 | 50 | ranging | **49** | 9863 |
| 22:21:21.657 | 50 | ranging | **49** | 6655 |
| 22:26:24.807 | 50 | ranging | **49** | 9789 |

Most recent reading: 49 of 50 coins have a per-coin regime. Per-coin regime values and confidences **NOT FOUND in logs** — only the count is emitted; per-coin lines such as `REGIME_DETECTED | sym=...` were not present in the searched window. `last_updated` per coin: implicit ≈ `REGIME_TICK_SUMMARY` timestamp.

Brain prompt reads (sample reference for direction values, from `STRAT_DIRECTIVE` lines 14601 in brain.log): `ETHUSDT [TRENDING_DOWN 64%]`, `BSBUSDT [TRENDING_UP 99%]` — confirms the cache keys regime + confidence per coin even though the worker tick log emits only aggregates.

---

## F. `StructureCache` — reconstructed from `XRAY_TICK_SUMMARY` and `XRAY_CLASSIFY`

Source: `src/workers/structure_worker.py:268`, `src/workers/structure_worker.py:183`, `src/analysis/structure/setup_scanner.py:84`

### F.1 Per-tick aggregate

| Timestamp | universe | batch | symbols | analyzed | errors | cached | session | setups | skips | el_ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 22:10:45.614 | 50 | 0/2 | 25 | 25 | 0 | 50 | late_ny(mid) | 12 | 16 | 613 |
| 22:15:45.821 | 50 | 1/2 | 25 | 25 | 0 | 50 | late_ny(mid) | 12 | 13 | 819 |
| 22:20:45.581 | 50 | 0/2 | 25 | 25 | 0 | 50 | late_ny(mid) | 12 | 18 | 579 |
| 22:25:47.329 | 50 | 1/2 | 25 | 25 | 0 | 50 | late_ny(mid) | 12 | 13 | 2303 |

`cached=50` confirms the StructureCache has **50 entries** at 22:25:47.329 UTC. Each tick refreshes 25 (one of two batches).

### F.2 Setup-type distribution from XRAY_CLASSIFY (cycle ending 22:25:47)

Distribution from grep `setup_type=` between 22:25:00 and 22:26:00 (19 entries seen — partial; only 25 are refreshed per tick):
- `bearish_fvg_ob`: **18**
- `bullish_fvg_ob`: **1** (INJUSDT)
- `none`: **NOT FOUND in this window** (would emit `XRAY_CLASSIFY | sym=X setup_type=none confidence=...` per code at structure_worker.py:152-154)

Per-coin sample lines (cycle ending 22:25:47.329):

```
22:25:45.078 XRAY_CLASSIFY | sym=BTCUSDT  setup_type=bearish_fvg_ob confidence=0.55 score=64  direction=short
22:25:45.109 XRAY_CLASSIFY | sym=ETHUSDT  setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:45.381 XRAY_CLASSIFY | sym=BNBUSDT  setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:45.566 XRAY_CLASSIFY | sym=XRPUSDT  setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:45.591 XRAY_CLASSIFY | sym=ADAUSDT  setup_type=bearish_fvg_ob confidence=0.55 score=30  direction=short
22:25:45.659 XRAY_CLASSIFY | sym=DOGEUSDT setup_type=bearish_fvg_ob confidence=0.55 score=64  direction=short
22:25:45.782 XRAY_CLASSIFY | sym=AVAXUSDT setup_type=bearish_fvg_ob confidence=0.55 score=64  direction=short
22:25:45.805 XRAY_CLASSIFY | sym=LINKUSDT setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:45.866 XRAY_CLASSIFY | sym=ARBUSDT  setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:45.887 XRAY_CLASSIFY | sym=NEARUSDT setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:46.088 XRAY_CLASSIFY | sym=ATOMUSDT setup_type=bearish_fvg_ob confidence=0.55 score=64  direction=short
22:25:46.134 XRAY_CLASSIFY | sym=INJUSDT  setup_type=bullish_fvg_ob confidence=0.55 score=38  direction=long
22:25:46.392 XRAY_CLASSIFY | sym=RENDERUSDT setup_type=bearish_fvg_ob confidence=0.55 score=64 direction=short
22:25:46.584 XRAY_CLASSIFY | sym=ONDOUSDT setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:46.660 XRAY_CLASSIFY | sym=PYTHUSDT setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
22:25:46.797 XRAY_CLASSIFY | sym=SEIUSDT  setup_type=bearish_fvg_ob confidence=0.70 score=100 direction=short
22:25:47.061 XRAY_CLASSIFY | sym=GALAUSDT setup_type=bearish_fvg_ob confidence=0.55 score=100 direction=short
22:25:47.089 XRAY_CLASSIFY | sym=MANAUSDT setup_type=bearish_fvg_ob confidence=0.55 score=64  direction=short
22:25:47.111 XRAY_CLASSIFY | sym=SANDUSDT setup_type=bearish_fvg_ob confidence=0.55 score=49  direction=short
```

### F.3 Setup-score distribution

From the 19-coin sample at 22:25:
- score=100: 2 (SEIUSDT, GALAUSDT)
- score=64: 6
- score=49: 9
- score=38: 1
- score=30: 1

### F.4 RR ratio + age

**NOT FOUND.** RR is not emitted in the XRAY_CLASSIFY line. The `[XRAY_SCANNER]` line emits top-3 by score but not RR per coin. Age in seconds **NOT FOUND** in any structure_worker log emission — only in scanner_worker's CYCLE_FRESHNESS aggregate (`xray_age_p50_ms=194995` at 22:24, `xray_age_p95_ms=494936`). Cache TTL is 300 s per `[analysis.structure].cache_ttl_seconds = 300` (config.toml:897).

### F.5 XRAY_SCANNER aggregate (most recent 4 cycles)

```
22:10:45.612 XRAY_SCANNER | total=28 qualified=26 skipped=16 | #1=DYDXUSDT(76) #2=LDOUSDT(73) #3=BLURUSDT(73)
22:15:45.820 XRAY_SCANNER | total=25 qualified=24 skipped=13 | #1=RUNEUSDT(73) #2=GALAUSDT(73) #3=LDOUSDT(73)
22:20:45.580 XRAY_SCANNER | total=30 qualified=28 skipped=18 | #1=DYDXUSDT(76) #2=GALAUSDT(73) #3=LDOUSDT(73)
22:25:47.326 XRAY_SCANNER | total=25 qualified=24 skipped=13 | #1=RUNEUSDT(73) #2=GALAUSDT(73) #3=LDOUSDT(73)
```

---

## G. `_coin_packages` (last cycle)

Source: `src/workers/scanner_worker.py:826/861/899/905`

### G.1 Most recent cycle (cycle_id=c-2026-04-27-22:20, tick at 22:24:00 UTC)

```
22:24:00.014 SCANNER_FILTER_AGGREGATE | cycle_id=c-2026-04-27-22:20 total=50 qualified=0 fail_no_xray=25 fail_setup_none=10 fail_consensus=12 fail_regime=2 fail_rr=1 fail_blockers=0 pass_xray=15 pass_consensus_strong=3 pass_consensus_good=0
22:24:00.016 SCANNER_PACKAGE_BUILD_START | cycle_id=c-2026-04-27-22:20 packages_to_build=2
22:24:00.017 PACKAGE_VALIDATE | cycle_id=c-2026-04-27-22:20 sym=BTCUSDT completeness=0.67 verdict=warn missing=['price_data.current', 'xray.setup_type', 'price_data.regime', 'alt_data.fear_greed'] stale=[]
22:24:00.017 PACKAGE_VALIDATE | cycle_id=c-2026-04-27-22:20 sym=ETHUSDT completeness=0.73 verdict=warn missing=['price_data.current', 'xray.setup_type', 'alt_data.fear_greed'] stale=[]
22:24:00.018 SCANNER_PACKAGE_BUILD_DONE | cycle_id=c-2026-04-27-22:20 packages=2 total_size_bytes=1894 elapsed_ms=2
22:24:00.019 PACKAGE_VALIDATE_SUMMARY | cycle_id=c-2026-04-27-22:20 packages_built=2 ok=0 warn=2 fail_quarantined=0
22:24:00.020 CYCLE_FRESHNESS | cycle_id=c-2026-04-27-22:20 klines_age_p50_ms=209932 klines_age_p95_ms=1704546 xray_age_p50_ms=194995 xray_age_p95_ms=494936 packages_age_p50_ms=1 packages_age_p95_ms=1 klines_keys=200 xray_keys=50 packages_keys=3
22:24:00.031 SCANNER_SELECT | cycle_id=c-2026-04-27-22:20 qualified=0 selected=2 forced=2 watch_list=50
22:24:00.031 SCANNER_TICK_SUMMARY | watch_list=50 protected=0 scored=2 selected=2 top_n=15 forced_in=2 mean_score=0.000 top=BTCUSDT(0.000) el=29ms drift_ms=1
```

Per-package field detail (`PACKAGE_VALIDATE` lines):
- **BTCUSDT:** completeness=**0.67** verdict=warn; missing fields = `price_data.current, xray.setup_type, price_data.regime, alt_data.fear_greed`; stale=[]
- **ETHUSDT:** completeness=**0.73** verdict=warn; missing fields = `price_data.current, xray.setup_type, alt_data.fear_greed`; stale=[]

Cycle-level totals: `packages_built=2 ok=0 warn=2 fail_quarantined=0`. **Both packages qualified=False (forced via open-position rule).**

### G.2 packages_keys=3

`packages_keys=3` in CYCLE_FRESHNESS but `packages=2` in BUILD_DONE. Three packages live in `_coin_packages`; one extra key is from a previous cycle that hasn't been replaced (typically `__stage1_summary__` or similar — exact key name **NOT FOUND** in this log window).

### G.3 Bucket / stale

`packages_age_p50_ms=1` and `packages_age_p95_ms=1` confirm packages are written fresh on every cycle.

---

## Summary of gaps

| Cache | Snapshot mechanism | What's logged | What's missing |
|---|---|---|---|
| `_score_cache` | NOT FOUND — only aggregate count | size, e.g. 19 | per-coin scores, last_write timestamp per coin |
| `_strategy_consensus` | NOT FOUND — only aggregate + transitions | size, distribution counts | per-coin (consensus, score, vote_count, direction, last_updated) for stable rows |
| `_strategy_consensus_summary` | NOT FOUND — only count | filtered_count, threshold | full filtered set |
| `_signal_cache` | per-coin per-cycle log lines | full coverage available | none significant |
| `_per_coin_regimes` | NOT FOUND — only aggregate | size only | per-coin regime, confidence, last_updated |
| `StructureCache` | per-coin XRAY_CLASSIFY lines | setup_type, confidence, score, direction per coin | RR, age, structural_levels.suggested_sl/tp |
| `_coin_packages` | per-package PACKAGE_VALIDATE | completeness, missing/stale, qualified | inner field values (current price, regime string, etc.) |
