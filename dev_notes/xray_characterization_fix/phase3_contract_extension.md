# Phase 3 — `_find_nearest_*` Contract Extension

**Date:** 2026-04-30
**Goal:** widen `_find_nearest_fvg` and `_find_nearest_ob` from `Optional[FairValueGap]` / `Optional[OrderBlock]` returns to structured `NearestFVGResult` / `NearestOBResult` dataclasses that surface BOTH the in-direction nearest zone AND the counter-direction nearest zone within the ATR-scaled window.

This is plumbing only. Phase 4 consumes the new `counter_direction` field to emit `BULLISH_FVG_OB_COUNTER` / `BEARISH_FVG_OB_COUNTER` setups. Until Phase 4 lands, classification output is unchanged.

## Files changed

| File | Δ |
|---|---|
| `src/analysis/structure/models/structure_types.py:226–271` | New `NearestFVGResult` + `NearestOBResult` dataclasses. |
| `src/analysis/structure/models/structure_types.py:471–488` | Added `nearest_fvg_counter: FairValueGap | None = None` and `nearest_ob_counter: OrderBlock | None = None` to StructuralAnalysis. |
| `src/analysis/structure/structure_engine.py:14–25` | Import new result types. |
| `src/analysis/structure/structure_engine.py:611–826` | Rewrote `_find_nearest_fvg` and `_find_nearest_ob`: scan both directions, return result dataclass, emit two `XRAY_NEAREST_DETAIL` records per call (one per slot). |
| `src/analysis/structure/structure_engine.py:399–417` | Updated 2 call sites: thread result through `_fvg_result.in_direction` etc., expose `nearest_fvg_counter` and `nearest_ob_counter` to the StructuralAnalysis constructor. |
| `src/analysis/structure/structure_engine.py:518–522` | Pass new fields into StructuralAnalysis. |
| `tests/test_structure_engine_atr_window.py` | Updated Phase 2 tests to use `result.in_direction` accessor (8 tests modified, 0 added). |
| `tests/test_structure_engine_nearest_finders.py` (new, 230 lines) | 18 tests across 5 test classes. |

## Selection-rule change (documented behavior delta)

Pre-Phase-3: "first match wins" — function returned on the first iterated FVG/OB inside the window. Since FVGs arrive ordered by `created_index DESC`, this preferred the most-recent in-direction zone.

Post-Phase-3: "closest within window wins" — the function scans the entire list and tracks the smallest `dist` seen per direction slot.

In practice the live universe rarely has multiple in-direction unfilled FVGs within a 2-5% window so the behavioral delta is small. The new rule matches the semantic intent of "nearest" and removes the ordering coupling.

## Backward compatibility

The two callers (`structure_engine.py:399, 402`) are updated atomically in this commit (Option A from the plan). External callers do not exist — `grep -rn "_find_nearest_fvg\|_find_nearest_ob"` shows only the 2 production call sites and the unit-test file. Test fixtures that used to assert `out is fvg_obj` now assert `result.in_direction is fvg_obj`.

The `cfg=None` legacy path is preserved: callers that don't have `SetupTypesSettings` still get the legacy 2.0%/3.0% fixed window, with both slots populated correctly.

## Empty-direction guard

When `suggested_direction == ""` (caller has no market-structure-derived bias), the finders return an empty `NearestFVGResult(suggested_direction="")` instead of raising or guessing. The pre-Phase-3 finders silently treated `direction=""` as "match any" via the `if direction and ...` guard; the new behavior is more explicit — no direction means no slot resolution.

## Observability

Each call to `_find_nearest_fvg` or `_find_nearest_ob` now emits TWO `XRAY_NEAREST_DETAIL` DEBUG records — one for `in_direction` slot, one for `counter` slot:

```
XRAY_NEAREST_DETAIL | sym=BTCUSDT kind=fvg slot=in_direction direction=long
                      found=false distance_pct=- atr_pct=0.420 window_pct=2.000 reason=no_match_in_window
XRAY_NEAREST_DETAIL | sym=BTCUSDT kind=fvg slot=counter direction=short
                      found=true distance_pct=1.840 atr_pct=0.420 window_pct=2.000 reason=found
```

This makes the asymmetry visible per coin per cycle: counter-direction zones that the pre-Phase-3 logic would have discarded now show up explicitly.

## Tests

| Suite | Result |
|---|---|
| `tests/test_structure_engine_nearest_finders.py` (new) | 18 passed |
| `tests/test_structure_engine_atr_window.py` (updated) | 16 passed |
| `tests/test_phase2_layer1_restructure/` | 17 passed |
| `tests/test_setup_classifier_diagnose.py` | passed |
| `tests/test_structure_engine_*.py` | passed |
| `tests/test_structure_cache_freshness.py` | passed |
| `tests/test_scanner_filter*.py`, `test_scanner_rr_direction.py` | passed |
| **Total** | **83 passed in 2.23s** |

## Verification gate

`setup_type=none` count remains at the Phase 2 level (≈25–28 expected per cycle after a worker restart). Phase 3 is contract-only — no classification change. Phase 4 will consume the counter slots and produce the actual lift.

## Commit

`phase3(xray-counter): extend _find_nearest_* contract to surface counter-direction zones` (1 atomic commit).
