# Phase 4 — `classify_setup` Counter Branches + `trade_direction`

**Date:** 2026-04-30
**Goal:** the philosophical fix. Extend the `classify_setup` decision tree with two counter-direction branches that fire when the suggested direction's in-direction zones are missing but the OPPOSITE direction has tradeable FVG+OB structure near price. Add a `trade_direction` field to StructuralAnalysis so downstream consumers can distinguish "trade direction" (what the setup says) from "suggested direction" (what market structure says).

This is the lift phase: target ~14 of the 30 NONE coins flip to counter setups (47% of NONE failures are missing-bullish-FVG on uptrend coins → BEARISH_FVG_OB_COUNTER candidates; 19% are missing-bearish-FVG → BULLISH_FVG_OB_COUNTER candidates).

## Decision tree change

Before:

```
1. BULLISH_FVG_OB
2. BEARISH_FVG_OB
3-8. BoS / sweep / range
9. NONE
```

After:

```
1. BULLISH_FVG_OB                    (in-direction, full confidence)
2. BEARISH_FVG_OB                    (in-direction, full confidence)
2.5. BULLISH_FVG_OB_COUNTER         ← NEW (counter, ×0.7 confidence)
2.6. BEARISH_FVG_OB_COUNTER         ← NEW (counter, ×0.7 confidence)
3-8. BoS / sweep / range
9. NONE
```

In-direction setups always take priority. Counter setups only fire when:
- `counter_setup_enabled = true` (config rollback knob)
- `direction == "short"` for BULLISH counter (or `"long"` for BEARISH)
- `nearest_fvg_counter` is the matching opposite-direction unfilled FVG (Phase 3 plumbing)
- `nearest_ob_counter` is the matching opposite-direction fresh OB
- `_counter_alignment(trade_direction, struct, cfg)` returns True
- `mtf_score_01 >= counter_mtf_threshold` (default 0.40, looser than in-direction's 0.50)

## `_counter_alignment` matrix

| trade_direction | struct=uptrend | downtrend | ranging | volatile (strict=false) | volatile (strict=true) |
|---|---|---|---|---|---|
| long | reject | accept | accept | accept | reject |
| short | accept | reject | accept | accept | reject |

A long counter trade in an already-uptrending market doesn't add information — reject. Counter against the trend is the canonical use case (fading exhaustion). Ranging always accepts because there's no trend bias to fight. Volatile is the chop-mode tier — the operator's strict flag controls whether to characterize it.

## Confidence multiplier

Counter setups use the same `min(mtf_score_01, max(smc_01, 0.5))` base as in-direction, then multiply by `counter_confidence_multiplier` (default 0.7). For a typical counter setup with mtf=0.6, smc=0.5: base = 0.5, final = 0.35. Below the typical in-direction setup confidence (0.55–0.85) — preserves quality discrimination.

## `trade_direction` field

Added to StructuralAnalysis at line 510. Set as a side-effect inside `classify_setup`:

| Branch | trade_direction |
|---|---|
| BULLISH_FVG_OB | "long" (= suggested) |
| BEARISH_FVG_OB | "short" (= suggested) |
| BULLISH_FVG_OB_COUNTER | "long" (OPPOSITE of suggested=short) |
| BEARISH_FVG_OB_COUNTER | "short" (OPPOSITE of suggested=long) |
| BoS / sweep / range | suggested |
| NONE | "" |

The 2-tuple return signature `(SetupType, confidence)` is **preserved** for backward-compat with the 12+ existing test call sites that do `stype, _ = eng.classify_setup(a)`. The new information flows through `analysis.trade_direction` (matching the existing pattern at structure_engine.py:556 where the call site already mutates `analysis`).

## Files changed

| File | Δ |
|---|---|
| `src/analysis/structure/models/structure_types.py:512–528` | Added `trade_direction: str = ""` field with extensive docstring. |
| `src/config/settings.py:1397–1415` | Added 4 counter knobs to SetupTypesSettings. |
| `src/config/settings.py:1456–1467` | Validation for counter_confidence_multiplier (in (0,1]) and counter_mtf_threshold (in [0,1]). |
| `config.toml:1063–1078` | 4 keys with rationale comment. |
| `src/analysis/structure/structure_engine.py:925–984` | New `_counter_alignment` static method. |
| `src/analysis/structure/structure_engine.py:1027–1044` | Counter knob extraction in classify_setup body. |
| `src/analysis/structure/structure_engine.py:1057–1063` | trade_direction default = direction at top. |
| `src/analysis/structure/structure_engine.py:1098–1146` | Two new counter branches between bear in-direction and bull BoS. |
| `src/analysis/structure/structure_engine.py:1175–1178` | NONE branch resets trade_direction = "". |
| `src/workers/structure_worker.py:184–199` | XRAY_CLASSIFY log shows trade_direction + suggested_direction + is_counter flag. |
| `tests/test_setup_classifier_counter.py` (new, 290 lines) | 26 tests across 5 test classes covering counter firing, failure modes, trade_direction, confidence, and the alignment helper. |

## Tests

| Suite | Result |
|---|---|
| `tests/test_setup_classifier_counter.py` (new) | 26 passed |
| `tests/test_phase2_layer1_restructure/` | 17 passed |
| `tests/test_setup_classifier_diagnose.py` | passed |
| `tests/test_structure_engine_atr_window.py` | 16 passed |
| `tests/test_structure_engine_nearest_finders.py` | 18 passed |
| `tests/test_structure_engine_alignment_broaden.py`, `tests/test_structure_engine_mtf_threshold.py` | passed |
| `tests/test_scanner_filter*.py`, `tests/test_scanner_rr_direction.py`, `tests/test_force_include_filter.py` | passed |
| **Total** | **126 passed in 2.62s** |

## Verification trials (mandatory before declaring Phase 4 success)

These trials run against the **live system** in Phase 7 (3-hour trial). Phase 4 unit tests verify the logic; Phase 7 verifies the live impact.

| Trial | Target |
|---|---|
| `XRAY_CLASSIFY_SUMMARY` shows non-zero `bull_counter` + `bear_counter` | ≥8/cycle |
| `setup_type=none` total | ≤7/cycle (baseline ~30) |
| In-direction setups unchanged (spot-check 5) | confidence equal pre-fix |
| For 3 BULLISH_FVG_OB_COUNTER coins: trade_direction == "long" while suggested_direction == "short" | yes |
| For 3 BEARISH_FVG_OB_COUNTER coins: mirror | yes |
| Counter setup confidence ≈ 0.35–0.55 (≈0.7 × in-direction range) | yes |
| `pass_xray` (Scanner) | rises from ~20 to ~40 |

## Observability

**XRAY_CLASSIFY (per coin, INFO when not NONE):**

```
XRAY_CLASSIFY | sym=BTCUSDT setup_type=bearish_fvg_ob_counter
                confidence=0.35 score=42
                trade_direction=short suggested_direction=long
                is_counter=true | <ctx>
```

**XRAY_CLASSIFY_SUMMARY (per cycle, INFO):** Phase 2 already augmented this with `atr_p50` + `window_p50_*`; the per-variant counts naturally include the new counter variants since `setup_counts[type_name]` reads the enum value:

```
XRAY_CLASSIFY_SUMMARY | total=50 bearish_fvg_ob=15 bullish_fvg_ob=5
                        bearish_fvg_ob_counter=8 bullish_fvg_ob_counter=4
                        bearish_structural_break=1 none=17
                        conf_p50=0.55 conf_p95=0.55
                        atr_p50=0.620 window_p50_fvg=2.00 window_p50_ob=3.00
```

## What this does NOT change

- `_qualifies()` in scanner_worker.py — out of scope per the prompt; treats counter setups as equally-passing through criterion 1 (binary `setup_type != NONE`). Phase 5 adds confidence weighting downstream so counter setups don't unfairly out-rank in-direction.
- Consensus voter, regime detector, RR check, blockers — out of scope.
- Stage 2 brain prompt — Phase 5 will add a "COUNTER-TRADE" visual indicator.

## Commit

`phase4(xray-counter): characterize-and-rank classifier with counter-direction branches + trade_direction` (1 atomic commit).
