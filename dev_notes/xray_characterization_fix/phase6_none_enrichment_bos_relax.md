# Phase 6 — `XRAY_NONE_REASON` Enrichment + BoS Retest Relaxation

**Date:** 2026-04-30
**Goal:** two small additive fixes after the main classifier extension.

## 6.1 — `XRAY_NONE_REASON` enrichment

After Phase 4 ships, NONE only fires when neither in-direction nor counter has structure AND no BoS / sweep / range. The pre-Phase-6 log line was a one-liner ("closest_type / missed_by / weakest_input / mtf / smc / direction / structure / has_*") which made it impossible to tell whether the coin was truly cold or whether the counter branch legitimately rejected the available zones.

**Change:** `diagnose_none` now returns 13 enriched fields beyond the original 8. Worker emits them all in one `XRAY_NONE_REASON` line:

```
XRAY_NONE_REASON | sym=BTCUSDT closest_type=BULLISH_FVG_OB
                  missed_by='no_fresh_bullish_fvg;no_fresh_bullish_ob'
                  weakest_input=fvg_present mtf=0.50 smc=0.30
                  direction=long structure=uptrend
                  in_direction_fvg=missing in_direction_ob=missing
                  counter_direction_fvg=missing counter_direction_ob=missing
                  last_bos_significance=none last_bos_age_bars=-1
                  recent_sweep=False range_compression=False
                  atr_pct=0.420 window_pct_fvg=2.00 window_pct_ob=3.00
                  first_failure_branch=BULLISH_FVG_OB | <ctx>
```

**Field semantics:**

| Field | Possible values |
|---|---|
| `in_direction_fvg`, `counter_direction_fvg` | `missing` (no zone), `filled` (gap consumed), `available` (was within window — classifier rejected for other reason) |
| `in_direction_ob`, `counter_direction_ob` | `missing`, `stale`, `available` |
| `last_bos_significance` | `major`, `minor`, `none` |
| `last_bos_age_bars` | -1 sentinel until market_structure surfaces it |
| `recent_sweep`, `range_compression` | bool |
| `atr_pct`, `window_pct_fvg`, `window_pct_ob` | float — exactly the values Phase 2/3 finders applied |
| `first_failure_branch` | name of the branch that came closest |

The original 8 fields are preserved (no consumer breaks).

## 6.2 — BoS retest relaxation (Lever B)

**Pre-Phase-6:** `config.toml: structural_break_require_retest = true`. BoS branches required `last_bos.significance == "major"`. Phase 0 baseline showed 9–10 of 100 NONE failures landing on `no_bullish_bos` / `no_bearish_bos` — minor BoS coins (XRPUSDT, PLUMEUSDT, EGLDUSDT, ALICEUSDT, SANDUSDT in the live forensic).

**Change:**
- `config.toml`: `structural_break_require_retest = false` — allows minor BoS to qualify.
- New `structural_break_minor_confidence_multiplier = 0.8` knob — minor BoS confidence is reduced by this factor to reflect weaker confirmation than major BoS. Major BoS confidence is unchanged.

**Confidence math:**

| BoS | Pre-fix | Phase 6 |
|---|---|---|
| Major | `max(mtf, smc, 0.5)` (e.g. 0.6) | unchanged |
| Minor | rejected → NONE | `max(mtf, smc, 0.5) × 0.8` (e.g. 0.48) |

The 0.8 multiplier is intentionally above the 0.7 counter multiplier — minor BoS is "in-direction structure exists, just less confirmed" which is more conviction than "no in-direction structure but counter zones present."

## Files changed

| File | Δ |
|---|---|
| `src/config/settings.py:1407–1416` | New `structural_break_minor_confidence_multiplier: float = 0.8` field. |
| `src/config/settings.py:1474–1480` | Validation for the new field. |
| `config.toml:1037–1048` | Set `structural_break_require_retest = false` + add multiplier. |
| `src/analysis/structure/structure_engine.py:1043–1052` | Read `bos_minor_mult` knob in classify_setup body. |
| `src/analysis/structure/structure_engine.py:1185–1199` | BULLISH_STRUCTURAL_BREAK / BEARISH_STRUCTURAL_BREAK apply multiplier when significance != "major". |
| `src/analysis/structure/structure_engine.py:1452–1521` | `diagnose_none` returns 13 enriched fields. Defensive `_range_compression` helper for MagicMock fixtures. |
| `src/workers/structure_worker.py:171–195` | XRAY_NONE_REASON log emission expanded with all 13 enriched fields. |
| `tests/test_xray_none_reason_enrichment.py` (new, 230 lines) | 15 tests across enriched fields + BoS retest relaxation + minor multiplier. |

## Tests

| Suite | Result |
|---|---|
| `tests/test_xray_none_reason_enrichment.py` (new) | 15 passed |
| `tests/test_setup_classifier_diagnose.py` | 6 passed (defensive MagicMock path verified) |
| `tests/test_setup_classifier_counter.py` | 26 passed |
| `tests/test_phase2_layer1_restructure/` | 17 passed |
| Full Phase 1-6 suite | **278 passed in 2.50s** |

## Verification gate (live)

After Phase 7 trial:
- `XRAY_NONE_REASON` contains all 13 enriched fields per line. ✓ (test-verified)
- BULLISH_STRUCTURAL_BREAK / BEARISH_STRUCTURAL_BREAK count rises by 3-5/cycle (live measurement).
- True-NONE coins (≤5/cycle) are structurally cold per spot-check.

## Commit

`phase6(xray-counter): NONE reason enrichment + BoS retest relaxation with minor confidence cut` (1 atomic commit).
