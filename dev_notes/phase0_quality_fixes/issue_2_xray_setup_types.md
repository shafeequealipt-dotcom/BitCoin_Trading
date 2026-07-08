# Phase 0 — Quality Issue 2: XRAY setup_type Distribution

## A — Current observed behaviour

**Live measurement from log tail (last 100 cycles, ~8 hours):**

Quality grade distribution (the `quality=` field in `XRAY_ANALYZE`):
- A+ : 28-35% (high confluence)
- A  : 8-12%
- B  : 15-22%
- C  : 8-15%
- SKIP : 25-35% (insufficient confluence OR R:R < 0.5)

**Critical gap: zero `XRAY_CLASSIFY_SUMMARY` and zero `XRAY_CLASSIFY_DETAIL` log events found.** The setup classifier runs (line 523-532 in `structure_engine.py`) but emits no per-cycle distribution log. Operators cannot determine whether classification produces variety or all-NONE without parsing every individual `XRAY_ANALYZE` line.

The `XRAY_CLASSIFY_FAIL` warning fires only on classifier exceptions — silent on successful classifications.

## B — Expected behaviour

- `XRAY_CLASSIFY_SUMMARY` event emitted once per cycle with per-type counts
- Distribution should reflect market: NONE 30–60%, BULLISH 15–30%, BEARISH 15–30%
- For each NONE classification, `XRAY_NONE_REASON` shows closest_type + missed_by — gives operators tuning evidence
- ScannerWorker criterion 1 (must have setup_type) passes 30–70% of universe (was 0–10% if all NONE)

## C — Root cause

The `classify_setup()` method at `src/analysis/structure/structure_engine.py:676-803` is **logically correct**:
- Reads MTF score, SMC confluence, structural placement, FVG/OB/sweep/range patterns
- Top-down conservative first-match-wins decision tree (8 setup types + NONE)
- Confidence scored as bounded minimum of contributing scores

Thresholds at lines 698-710 (with `config.toml [analysis.structure.setup_types]` overrides at lines 341-346):
- `fvg_ob_min_confluence = 0.7`
- `structural_break_require_retest = true`
- `sweep_min_displacement_pct = 0.5`
- `range_breakout_min_compression_bars = 20`
- `mtf_alignment_required = true`

**The gap is observability, not logic.** Without the summary event, operators cannot calibrate thresholds with evidence. Phase 0 cannot prove thresholds are wrong; Phase 1 of XRAY (this phase) adds the observability that enables that calibration.

## D — Verification approach (post-fix)

| Metric | Measure | Target |
|---|---|---|
| `XRAY_CLASSIFY_SUMMARY` per cycle | grep workers.log for 1 hour | 12 events (1 per 5-min cycle) |
| Setup type variety | counts in summary log | NONE 30–60%, BULLISH+BEARISH ≥30% combined |
| Classification confidence calibration | sample 5 high-conf (>0.8) and 5 low-conf (<0.4) — visual chart inspection | high-conf patterns clearly present; low-conf borderline |
| `XRAY_NONE_REASON` per NONE | one event per NONE classification | per-coin reason (closest_type + missed_by + weakest_input) |
| ScannerWorker criterion 1 pass rate | `SCANNER_FILTER_AGGREGATE` | 30–70% of universe (was 0–10%) |
| XRAY phase logic unchanged | grep `XRAY_ANALYZE` content fields, compare distributions of S/R, FVG, OB, sweep counts to current baseline | no regression |

## E — Rollback path

Phase 2 changes are additive observability. No threshold calibration unless evidence requires it. If summary log proves too verbose, revert the per-coin emit and keep only the per-cycle aggregate. Rollback: `git revert <phase2-commits>`.

## Files end-to-end mapped

| File | Lines | Role |
|---|---|---|
| `src/analysis/structure/structure_engine.py` | 47-982 (StructureEngine), **676–803 (classify_setup — fix target for return value)** | The classifier; logic is correct, return shape needs `closest_type` + `missed_by` for NONE |
| `src/analysis/structure/models/structure_types.py` | 13-36 | SetupType enum (9 values: NONE + 4 BULLISH + 4 BEARISH) |
| `src/workers/structure_worker.py` | (where analyze() is called per cycle) | **Fix target — emit XRAY_CLASSIFY_SUMMARY here** |
| `config.toml` | 341-346 | `[analysis.structure.setup_types]` thresholds |

## Phase 2 fix outline (preview)

Two atomic commits:
1. Modify `classify_setup()` to also return `(closest_type, missed_by, weakest_input)` when verdict is NONE — used for the per-coin reason log.
2. In `structure_worker` after batch classify, accumulate per-type counts; emit `XRAY_CLASSIFY_SUMMARY | cycle_id={id} total=50 NONE={n} BULLISH_FVG_OB={n} ...`. For each NONE result, emit `XRAY_NONE_REASON | sym={s} closest_type={t} missed_by={feature:value} weakest_input={phase}`.
3. (Conditional) Threshold calibration only if evidence shows mis-calibration after the new logs land.
