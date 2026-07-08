# Phase 2 — ATR-Scaled Distance Windows (Lever A)

**Date:** 2026-04-30
**Goal:** replace fixed 2.0%/3.0% proximity windows in `_find_nearest_fvg`/`_find_nearest_ob` with ATR-scaled windows and floors so low-vol coins keep a sensible minimum and high-vol coins find zones the old fixed window missed.

## Why

Probe baseline (Phase 0) measured per-coin H1 ATR vs the fixed 2% FVG window:

| Coin | ATR% | 2.0% FVG window in ATR multiples |
|---|---|---|
| BNBUSDT | 0.29% | 6.9 ATR (very loose) |
| BTCUSDT | 0.42% | 4.8 ATR (too loose) |
| LINKUSDT | 0.49% | 4.1 ATR (loose) |
| AAVEUSDT | 0.77% | 2.6 ATR (about right) |
| DOGEUSDT | 0.98% | 2.0 ATR (borderline tight) |
| DYDXUSDT | 1.30% | 1.5 ATR (too tight) |

The fixed window was wrong at both ends of the volatility spectrum. New formula:

```
window_pct = max(min_distance_pct, atr_multiplier * atr_pct_h1)
```

Defaults `fvg_atr_multiplier=3.0`, `ob_atr_multiplier=4.0`, `fvg_min_distance_pct=2.0`, `ob_min_distance_pct=3.0`. Effect:

| Coin | New FVG window | New OB window |
|---|---|---|
| BNBUSDT (0.29%) | floor 2.00% (3 ATR floor still high) | floor 3.00% |
| BTCUSDT (0.42%) | floor 2.00% (was 4.8 ATR) | floor 3.00% |
| LINKUSDT (0.49%) | floor 2.00% | floor 3.00% |
| AAVEUSDT (0.77%) | 3 × 0.77 = **2.31%** (expanded) | 4 × 0.77 = **3.08%** (expanded) |
| DOGEUSDT (0.98%) | 3 × 0.98 = **2.94%** (expanded ~50%) | 4 × 0.98 = **3.92%** |
| DYDXUSDT (1.30%) | 3 × 1.30 = **3.90%** (expanded ~95%) | 4 × 1.30 = **5.20%** |

DOGEUSDT's bear OB sits at 3.1% in the live probe — exactly what the new 3.92% OB window now captures. Previously rejected as `no_fresh_bearish_ob`.

## Files changed

| File | Change |
|---|---|
| `src/analysis/structure/models/structure_types.py:506–514` | Added `atr_pct_h1: float = 0.0` field on StructuralAnalysis. |
| `src/config/settings.py:1380–1400` (SetupTypesSettings) | Added 4 ATR knobs + validation. |
| `src/config/settings.py:1432–1457` (`__post_init__`) | Validation rejects ≤0 multipliers / floors. |
| `config.toml:1048–1063` | Added 4 keys with rationale comment. |
| `src/analysis/structure/structure_engine.py:215` | `atr_pct_h1 = self._compute_h1_natr_pct(...)` near numpy extraction. |
| `src/analysis/structure/structure_engine.py:399–407` | Updated 2 call sites to thread atr_pct + cfg + symbol. |
| `src/analysis/structure/structure_engine.py:518` | Pass `atr_pct_h1` into StructuralAnalysis constructor. |
| `src/analysis/structure/structure_engine.py:560–731` | New `_compute_h1_natr_pct` method + rewritten `_find_nearest_fvg`/`_find_nearest_ob` with ATR-scaled window + `XRAY_NEAREST_DETAIL` log. |
| `src/workers/structure_worker.py:117–132` | Accumulate per-coin atr_pct in tick. |
| `src/workers/structure_worker.py:217–249` | Augment `XRAY_CLASSIFY_SUMMARY` with `atr_p50`, `window_p50_fvg`, `window_p50_ob`. |
| `tests/test_structure_engine_atr_window.py` (new, 175 lines) | 16 tests across `_compute_h1_natr_pct`, FVG window, OB window, settings validation. |

## Behavior preserved

- `_find_nearest_fvg` still returns `Optional[FairValueGap]` (Phase 3 will widen the contract).
- Iteration order unchanged — first match within the window wins, same as the pre-fix behavior. FVGs are still ordered by `created_index DESC` so the most-recent in-direction zone is preferred.
- Direction filter, `filled` skip, `fresh` skip — all unchanged.
- `_find_nearest_ob` mirror.
- `setup_type=none` count expected to drop by 4–8/cycle; in-direction setups unchanged.

## Backward compat

The new signature requires `atr_pct` and `cfg` parameters. Both have legacy fallback:
- `cfg=None` falls back to the legacy 2.0%/3.0% fixed window.
- `atr_pct=0.0` falls back to the floor (`min_distance_pct`).

This protects test fixtures that construct an engine without a populated SetupTypesSettings and any future internal callers that don't have atr handy.

## Observability

**New tag:** `XRAY_NEAREST_DETAIL` (DEBUG, per call):

```
XRAY_NEAREST_DETAIL | sym=BTCUSDT kind=fvg direction=long
                      found=true distance_pct=1.450 atr_pct=0.420
                      window_pct=2.00 reason=found
```

**Augmented `XRAY_CLASSIFY_SUMMARY`** (INFO, per cycle):

```
XRAY_CLASSIFY_SUMMARY | total=50 bearish_fvg_ob=15 bullish_fvg_ob=5 ...
                        conf_p50=0.55 conf_p95=0.55
                        atr_p50=0.620 window_p50_fvg=2.00 window_p50_ob=3.00
```

## Tests

| Suite | Result |
|---|---|
| `tests/test_structure_engine_atr_window.py` (new, 16 tests) | 16 passed in 0.29s |
| `tests/test_phase2_layer1_restructure/` (existing, 17) | 17 passed |
| `tests/test_setup_classifier_diagnose.py` (existing) | passed |
| `tests/test_structure_engine_alignment_broaden.py`, `test_structure_engine_mtf_threshold.py` | passed |
| `tests/test_scanner_filter*.py`, `test_scanner_rr_direction.py` | passed |
| **Total** | **65 passed in 1.12s** |

The pre-existing `tests/test_end_to_end_pipeline/test_layer1_pipeline.py::TestPhase2_RealClassifySetup::test_bullish_fvg_ob_through_real_engine` failure is **not introduced by Phase 2** — confirmed via `git stash` test run. It expects `fvg_ob_min_confluence == 0.7` (dataclass default) but config.toml has `0.5` since 2026-04-28's Definitive-fix Phase 2. Out of scope for this task.

## Verification gate

Phase 2 verification gate is passed at the unit-test level. Live verification (4–8/cycle drop in `setup_type=none`) requires a worker restart, deferred to the Phase 7 trial after Phase 4 lands the larger lift.

## Commit

`phase2(xray-counter): ATR-scaled distance windows for nearest FVG/OB finders` (1 atomic commit).
