# Phase 9 — Layer 1 Restructure Observation Period (template)

**Status:** in progress (1-2 weeks calendar)
**Start date:** 2026-04-27
**End date:** TBD (target 2026-05-04 to 2026-05-11)

This document is filled in incrementally as observation data accumulates. Final go/no-go recommendation lands at the bottom once the 15-item checklist runs.

## How to read this file

- **Verification checklist (15 items)**: pass/fail per item with evidence link.
- **Observation sets (7)**: weekly rollup queries.
- **Hotfixes**: any commit that landed during observation; documented + linked.

## Pre-condition note

Pre-condition #2 (1-week soak post the 5-fixes engagement) was waived per operator decision on 2026-04-27. D-3 fix shipped only 2026-04-26. Baseline metrics in this period therefore reflect a less-stable steady state than the prompt specifies. Any "regression" called out below should be cross-referenced against this caveat.

## Baseline (pre-Phase-1, captured 2026-04-26 from `dev_notes/five_fixes_final_summary.md`)

| Metric | Pre-restructure baseline |
|---|---|
| kline_worker tick p50 | 13s → <5s (D-3 fix) |
| kline_worker tick p95 | 20s → <10s |
| StrategyWorker coins/tick | 5 of 50 → 50 of 50 |
| Universe rotations / hour | ~14 → <4 |
| Brain CLI hang frequency | ~50% → <5% |

## Verification checklist (target ≥12/15 = success)

| # | Check | Threshold | Evidence | Status |
|---|---|---|---|---|
| 1 | Layer 1A p95 | <5s | cycle_metrics.layer1a_p95_ms | ⏳ |
| 2 | Layer 1B p95 | <15s | cycle_metrics.layer1b_p95_ms | ⏳ |
| 3 | Layer 1C p95 | <10s | cycle_metrics.layer1c_p95_ms | ⏳ |
| 4 | Layer 1D p95 | <500ms | cycle_metrics.layer1d_p95_ms | ⏳ |
| 5 | Total cycle p95 | <30s | cycle_metrics.total_p95_ms | ⏳ |
| 6 | Avg qualified 5-25 | yes | SCANNER_SELECT logs | ⏳ |
| 7 | Avg selected 10-15 | yes | SCANNER_SELECT logs | ⏳ |
| 8 | Setup type variety | NONE<70%, ≥4 types | XRAY_CLASSIFY_SUMMARY | ⏳ |
| 9 | Consensus variety | STRONG+GOOD ≥20% | STRAT_CONSENSUS_SUMMARY | ⏳ |
| 10 | Prompt size reduction | median 6-9 KB | PROMPT_BUILD_DONE | ⏳ |
| 11 | Cold-start works | 5/5 toggles fired CYCLE_RESUME_WAIT | manual | ⏳ |
| 12 | All 5 toggles work | yes | manual | ⏳ |
| 13 | Trading perf ≥ baseline | yes | closed_trades | ⏳ |
| 14 | Zero new error patterns | yes | log diff | ⏳ |
| 15 | No regression in L3/4/5 | yes | order success rate | ⏳ |

Legend: ⏳ pending • ✅ pass • ❌ fail

## Observation sets (filled weekly)

### 1. Cycle latency

Run `python3 scripts/observe_phase9.py 168` (7-day window) at start of week 2. Paste output below.

### 2. Selection quality

Grep `SCANNER_SELECT` from `data/logs/workers.log`, parse qualified/selected/forced. Report mean + p95.

### 3. Setup type distribution

Grep `XRAY_CLASSIFY_SUMMARY`, parse counts. Compute NONE % + count of distinct types appearing.

### 4. Consensus distribution

Grep `STRAT_CONSENSUS_SUMMARY`, parse counts. Compute STRONG+GOOD %.

### 5. Stage 2 prompt characteristics

Grep `PROMPT_BUILD_DONE`, parse size_bytes. Median + p95.

### 6. Cold-start behavior

Manually run `/layer 2 off` then `/layer 2 on` 5 times across the period. Verify each toggle pair produces a `CYCLE_RESUME_WAIT` + `CYCLE_RESUME` log line. Record timestamps.

### 7. Trading performance

Query `closed_trades` for win rate, avg win, avg loss, total PnL across the period. Compare to baseline (1-2 weeks pre-Phase-1).

## Hotfixes (if any)

None yet. List one per row when applicable: `<sha> | <date> | <one-line description>`.

## Final recommendation (filled at end)

To be decided based on verification checklist:
- **GO** (≥12/15 pass): mark restructure complete; consider real-money transition.
- **ITERATE** (<12/15 pass): list deficient items + remediation plan.
- **REVERT** (catastrophic): roll back per the `pre-layer1-restructure` git tag.

## Notes for next session

- Phase 8 deferred a full live renumber (LayerManager constants stay at 1/2/3). Once observation confirms stability, the renumber can land via a one-shot commit + `python3 scripts/migrate_layer_state_to_v2.py`.
- Phase 7 is additive (prepends packages); the per-coin loop replacement that would actually shrink the prompt is deferred to a follow-up commit once Phase 9 measures the prepend impact.
- 22 commits landed today (2026-04-27): Phase 0 setup + Phases 1-8 across 22 atomic commits, plus the pre-condition setup commit `8dca492`.
