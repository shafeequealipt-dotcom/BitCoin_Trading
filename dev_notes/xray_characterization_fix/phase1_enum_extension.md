# Phase 1 — SetupType Enum Extension

**Date:** 2026-04-30
**Goal:** add `BULLISH_FVG_OB_COUNTER` and `BEARISH_FVG_OB_COUNTER` to the `SetupType` enum. Pure data-model change — no behavior, no logic, no consumer impact.

## Changes

### 1. `src/analysis/structure/models/structure_types.py:13–37`

Added two variants to the `SetupType` enum. Both follow the existing `(str, Enum)` mixin pattern with `lower_snake = "lower_snake"` value form. Updated docstring to describe the counter-variant intent — they fire when the suggested direction's in-direction zones are missing but the OPPOSITE direction has tradeable FVG+OB structure near price.

Before: 9 variants (NONE + 4 bullish + 4 bearish).
After: 11 variants (NONE + 5 bullish + 5 bearish).

### 2. `tests/test_phase2_layer1_restructure/test_setup_classification.py`

Added `TestSetupTypeCounterVariants` class with 5 enum-presence + serialization assertions:
- `BULLISH_FVG_OB_COUNTER` exists with value `"bullish_fvg_ob_counter"`.
- `BEARISH_FVG_OB_COUNTER` exists with value `"bearish_fvg_ob_counter"`.
- str-mixin equality holds (variant compares directly to its string value).
- Counter variants distinct from in-direction variants.
- Total variant count = 11.

## Verification

**Import-time sanity (every consumer of SetupType):**

```
OK   src.analysis.structure.models.structure_types
OK   src.analysis.structure.structure_engine
OK   src.workers.structure_worker
OK   src.workers.scanner_worker
OK   src.brain.strategist
OK   src.core.coin_package
```

**Test suite:** `pytest tests/test_phase2_layer1_restructure/test_setup_classification.py -q` → 17 passed (12 pre-existing + 5 new). 0.31s.

**Behavior impact (none):** No code path emits the new variants yet. `XRAY_CLASSIFY_SUMMARY` will continue to show only the existing 9 variants until Phase 4 ships the classifier branches.

## Files changed

| File | Δ |
|---|---|
| `src/analysis/structure/models/structure_types.py` | +12 lines (2 variants + docstring expansion) |
| `tests/test_phase2_layer1_restructure/test_setup_classification.py` | +33 lines (new test class) |

## Commit

`phase1(xray-counter): add COUNTER setup variants to SetupType enum` (1 commit, atomic).
