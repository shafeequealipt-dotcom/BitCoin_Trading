# Phase 2 — XRAY setup_type Observability Fix Report

**Date:** 2026-04-27
**Commits:**
- `a7b8834` — XRAY_NONE_REASON + confidence percentiles in summary

## Bug summary

Phase 0 evidence: `classify_setup()` at `structure_engine.py:676-803` is logically correct and config-driven. The XRAY_CLASSIFY_SUMMARY event already exists at `structure_worker.py:170-173`. **The gap was not the classification but the absence of evidence to tune thresholds.** Operators saw "27/50 NONE" but no indication WHICH threshold blocked classification — guesswork to calibrate.

## Fix summary

Two complementary diagnostics added without touching classify_setup():

| Diagnostic | Purpose | Frequency |
|---|---|---|
| `XRAY_NONE_REASON` per-coin (INFO) | Explains WHY each NONE classification landed in NONE: closest_type, missed_by (specific threshold gap), weakest_input | One per NONE result per cycle (~27/50 typical) |
| `XRAY_CLASSIFY_SUMMARY` confidence percentiles | Reveals whether non-NONE classifications are high-conf (real patterns) or low-conf (borderline) | Once per cycle (1/5min) |

## Files changed

| File | Change |
|---|---|
| `src/analysis/structure/structure_engine.py` | NEW `diagnose_none()` method (~120 lines): walks the same decision tree as `classify_setup` but reports the closest branch + miss reason + weakest input. classify_setup signature UNCHANGED (back-compat). |
| `src/workers/structure_worker.py` | Per-coin loop: when NONE, call `diagnose_none()` and emit `XRAY_NONE_REASON` at INFO. Cycle summary: accumulate `setup_type_confidence` values; compute p50/p95 once; append to `XRAY_CLASSIFY_SUMMARY`. |
| `tests/test_setup_classifier_diagnose.py` (NEW) | 6 cases covering bare analysis, FVG_OB partial-fit, structural break missing BOS, weakest_input identification, partial sweep, API contract. |

## New observability

```
XRAY_NONE_REASON | sym=BTCUSDT closest_type=BULLISH_FVG_OB
                   missed_by='mtf_score=0.40<fvg_ob_min=0.70'
                   weakest_input=mtf mtf=0.40 smc=0.50
                   direction=long structure=uptrend | did=...

XRAY_CLASSIFY_SUMMARY | total=50 NONE=27 BULLISH_FVG_OB=10 BEARISH_FVG_OB=5
                        BULLISH_STRUCTURAL_BREAK=4 BEARISH_LIQUIDITY_SWEEP=3
                        BULLISH_LIQUIDITY_SWEEP=1 conf_p50=0.65 conf_p95=0.83 | did=...
```

## Verification — automated

```
pytest tests/test_setup_classifier_diagnose.py — 6 passed
Full regression — 99 passed (signal_generator + state_sync + persistence
                              + watchdog + corrected_layer1 + universe + log_routing)
```

## Verification — operator-driven (post-deploy, 1 hour)

| # | Trial | Pass criterion |
|---|---|---|
| 2.1 | `XRAY_NONE_REASON` per NONE | one event per NONE classification; structured fields present |
| 2.2 | `XRAY_CLASSIFY_SUMMARY` per cycle | now includes `conf_p50=` + `conf_p95=` fields |
| 2.3 | Distinct miss reasons aggregated | grep `missed_by=` over 1h, count distinct reasons; the dominant reason is the threshold to consider tuning |
| 2.4 | Confidence percentiles meaningful | p50 in [0.4, 0.8] indicates healthy mix; p95 > 0.85 indicates strong patterns are present |
| 2.5 | No XRAY phase regression | grep `XRAY_ANALYZE` content fields (S/R, FVG, OB, sweep) — same shape and counts as pre-deploy baseline |

## Phase 2.3 (threshold calibration) — conditional

The plan reserved a third optional commit for threshold calibration if Phase 0 evidence showed clear mis-calibration. **Decision: defer.** Phase 0 alone cannot prove thresholds are wrong without the new `XRAY_NONE_REASON` + percentile logs. Once those land in production, operators get evidence-driven calibration material; calibration becomes an operator config-only change (no commit needed) or a small follow-up commit if any threshold is unanimously cited as the bottleneck.

## Out of scope for this phase

- Strategy categorisation
- XRAY phase logic itself (only setup classification observability)
- Per-NONE-reason rate-limiting (the diagnostic is one log per NONE per cycle, ~27/cycle = 5400/day at 5-min cadence — acceptable; can be rate-limited later if log volume becomes a concern)
