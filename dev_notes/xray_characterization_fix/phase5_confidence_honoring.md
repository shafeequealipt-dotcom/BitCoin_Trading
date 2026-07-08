# Phase 5 — Downstream Confidence Honoring (4 atomic commits)

**Date:** 2026-04-30
**Goal:** ensure every downstream consumer of structural setup_type weights by `setup_type_confidence`. Without this, counter setups (Phase 4, conf ≈ 0.35) would rank identically to in-direction setups (conf ≈ 0.55–0.85) — defeating the quality discrimination Phase 4 introduced.

User directive (from plan-mode AskUserQuestion): comprehensive scope — also scale ensemble `size_mult` (5c). Not minimum-viable.

## Pre-Phase-5 verdict (from Explore agent)

Confirmed via direct code reading: **NONE of TradeScorer, EnsembleVoter, ScannerWorker._compute_opportunity_score, or ScannerWorker._qualifies read `setup_type_confidence` today.** A counter setup at conf=0.35 with otherwise identical features ranks the same as an in-direction setup at conf=0.85 in opportunity_score and ensemble size_mult. Stage 2 brain prompt renders the confidence as informational text but no rule enforces weighting.

`_qualifies()` stays out of scope per the prompt's "out of scope" list — counter setups still pass criterion 1 (binary `setup_type != NONE`) but get downweighted later in the pipeline.

## Commits

### 5a — `src/strategies/scorer.py` (Quality multiplier)

**Change:** in `_xray_sr_score`, multiply pre-clamp `sr_pts` by `max(0.5, min(1.0, setup_type_confidence))` before clamping to 0–8.

**Behavior:**
- Counter setup (conf 0.35) → factor floors at 0.5 → sr_pts halved.
- In-direction (conf 0.85) → factor 0.85 → sr_pts × 0.85.
- Full conviction (conf 1.0) → no change.
- Floor 0.5 prevents zeroing out legitimate structure when confidence is low.
- Default 0.85 when `setup_type_confidence` is absent (legacy producers).
- DEBUG log `SCORER_QUALITY_DETAIL` only when conf < 0.85 (sparse).

**Tests:** `tests/test_strategies/test_scorer_confidence_weighting.py` — 7 tests across full preservation, counter reduction, 0.5 floor, zero protection, legacy default, ceiling clamp, proportionality.

### 5b — `src/workers/scanner_worker.py` (opportunity_score struct_norm × confidence)

**Change:** `_compute_opportunity_score` now reads `_get_setup_type_confidence(coin)` and multiplies `struct_norm` by `max(0.5, min(1.0, conf))`. Breakdown dict gains `structure_raw` and `structure_conf` keys.

**Plumbing:**
- New `StructureWorker.get_setup_type_confidence(coin)` accessor (delegates to cached `StructuralAnalysis.setup_type_confidence`).
- New `ScannerWorker._get_setup_type_confidence(coin)` accessor — defensive try/except with DEBUG log.
- SCANNER_SELECTED log enriched with `struct_raw:` and `struct_conf:` fields per coin.

**Tests:** `tests/test_scanner_opportunity_score_confidence.py` — 7 tests covering counter reduction, floor, ceiling, legacy default, in-direction-outranks-counter, score-signal preservation, breakdown shape.

### 5c — `src/strategies/ensemble.py` (size_mult × structural confidence)

**Change:** after `CONSENSUS_SIZE` picks the base size_mult (1.0 for STRONG, 0.75 for GOOD, etc.), multiply by `max(0.5, min(1.0, setup_type_confidence))`.

**Plumbing:**
- `scorer.score()` now writes `setup_type_confidence` and `trade_direction` into `scoring_details` so ensemble can read them off the ScoredSetup.
- New `ENSEMBLE_VOTE_WEIGHTED` INFO log emitted only when struct_conf < 0.85 (visible in live data without flooding).
- Explicit `None` check on raw value (instead of `or 0.85`) so a real 0.0 confidence floors at 0.5 rather than coercing back to 0.85.

**Tests:** `tests/test_strategies/test_ensemble_confidence_weighting.py` — 6 tests across full preservation, counter reduction, in-direction outsizing counter, legacy default, 0.5 floor, ceiling clamp.

### 5d — Brain prompt + XrayBlock counter visual

**Changes:**
- `XrayBlock` (in `src/core/coin_package.py`) gains `trade_direction: str = ""` field.
- `ScannerWorker._build_coin_package` populates `XrayBlock.trade_direction` from `structure.trade_direction` (Phase 4) with fallback to `suggested_direction`.
- `ClaudeStrategist._format_packages_for_prompt` renders counter setups with explicit annotation:

```
Setup: bullish_fvg_ob_counter (COUNTER-TRADE — trade direction is OPPOSITE
to market structure bias; lower conviction) (confidence 0.35,
trade_direction=long)
```

The "lower conviction" hint nudges the brain toward smaller positions / tighter SL on counter setups, complementing the mechanical 5a/b/c weighting.

**Tests:** existing `tests/test_phase7_layer1_restructure/test_format_packages.py` (17 tests) all pass with the new format.

## Combined effect

Per the verification plan from Phase 4, after Phase 5 ships:

| Property | Before fix | After Phase 5 |
|---|---|---|
| Counter setup at conf 0.35 vs in-direction at conf 0.85, same setup_score | tie in opportunity_score | in-direction outranks |
| Counter setup at conf 0.35, STRONG consensus | size_mult = 1.0 | size_mult = 0.5 (floor) |
| In-direction setup at conf 0.85, STRONG consensus | size_mult = 1.0 | size_mult = 0.85 |
| Brain prompt counter visibility | "Setup: bullish_fvg_ob_counter (confidence 0.35)" | "Setup: bullish_fvg_ob_counter (COUNTER-TRADE — opposite ...) (confidence 0.35, trade_direction=long)" |
| TradeScorer Quality on counter | full sr_pts | sr_pts × 0.5 (floor) |

## Tests

| Suite | Result |
|---|---|
| `tests/test_strategies/test_scorer_confidence_weighting.py` (5a) | 7 passed |
| `tests/test_scanner_opportunity_score_confidence.py` (5b) | 7 passed |
| `tests/test_strategies/test_ensemble_confidence_weighting.py` (5c) | 6 passed |
| `tests/test_phase7_layer1_restructure/test_format_packages.py` (5d) | 17 passed |
| Existing scorer/ensemble/scanner suites | 38 passed |
| **Phase 1-5 combined** | **164 passed in 1.80s** |

## What did NOT change (per scope)

- `ScannerWorker._qualifies()` — still binary `setup_type != NONE` for criterion 1. Counter setups pass through to scoring. Downweighting is mechanical via 5a/b/c.
- Consensus voter logic — only the size_mult output is scaled; the agreeing/opposing computation, CONSENSUS_SIZE thresholds, and STRONG/GOOD/LEAN/WEAK/CONFLICT determination are untouched.
- Regime alignment matrix — uses consensus.direction (which equals trade_direction for counter setups via Phase 4); the regime check still works correctly because counter trade_direction is the OPPOSITE direction the regime expects.
- RR threshold and blocker logic — unchanged.

## Commit log

```
phase5d(xray-counter): brain prompt counter visual + XrayBlock trade_direction
phase5c(xray-counter): ensemble size_mult × structural confidence
phase5b(xray-counter): opportunity_score struct_norm × structural confidence
phase5a(xray-counter): scorer Quality multiplier on structural confidence
```
