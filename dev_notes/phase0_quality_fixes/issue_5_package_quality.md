# Phase 0 — Quality Issue 5: CoinPackage Content Quality

## A — Current observed behaviour

`CoinPackage` (defined in `src/core/coin_package.py:86-126`) has 7 nested blocks:
- **StructuralLevels** (current_price, sl, tp, rr_ratio)
- **XrayBlock** (setup_type, setup_score, setup_type_confidence, mtf_confluence, session, session_phase, key_features)
- **StrategiesBlock** (fired_count, ensemble_consensus, consensus_score, total_score, fired_strategies)
- **SignalsBlock** (confidence, direction, sentiment_score, sentiment_articles_count)
- **AltDataBlock** (funding_rate, funding_signal, oi_change_4h_pct, fear_greed)
- **PriceDataBlock** (current, change_24h_pct, volume_24h_usd, regime)
- Top-level: `symbol`, `qualified`, `opportunity_score`, `qualification_reasons`, `open_position`, `blockers_observed`, `built_at`

**Builder** (`src/workers/scanner_worker.py:300-483`):
- Defensively populates from 7+ services (market, structure_cache, strategy_worker, signal_worker, altdata, position_service, regime_worker, layer_manager)
- On any service failure: appends a "blocker label" (e.g., `xray_missing`, `signal_missing`, `funding_missing`) and proceeds with field defaults (0.0, "", "neutral", "none")
- ~30-35 packages per cycle in observed cycles

**Consumer** (`src/brain/strategist.py:1157-1221`):
- Sorts by `opportunity_score` desc
- Renders 13 fields per package in markdown for Claude's prompt
- No quality gate on input — packages with all-default fields still go to Claude

**Critical gap: No validation exists.** A package with `setup_type="none"`, `total_score=0.0`, `signals.confidence=0.0`, `regime=""` could legitimately reach Claude — wasting a brain call.

## B — Expected behaviour

- Each package validated by a pure-function validator
- Verdict: `ok` (≥0.85 completeness), `warn` (0.5-0.85), `fail` (<0.5)
- Failed packages quarantined (NOT included in `_coin_packages` written for Stage 2)
- `PACKAGE_VALIDATE` per package + `PACKAGE_VALIDATE_SUMMARY` per cycle
- Target: 90%+ packages `ok`, 5-10% `warn`, rare `fail`

## C — Root cause

This is **NEW WORK**, not a fix to existing buggy code. Today there is no validator. Adding one closes the contract gap between Layer 1D (scanner) and Stage 2 (brain).

The investigation already mapped:
- All required vs optional fields (from dataclass definitions)
- Defensive default values (0.0, "", "neutral", "none") that should count as "missing"
- Staleness threshold (`built_at` age > 5 min config)

## D — Verification approach (post-fix)

| Metric | Measure | Target |
|---|---|---|
| Every package validated | `PACKAGE_VALIDATE` per package | 1 emit per package per cycle |
| Most pass | summary log over 1 hour | 90%+ ok (completeness ≥ 0.85) |
| Quarantine works | inject synthetic broken package via test | log + dropped from `_coin_packages` |
| No regression | `PROMPT_BUILD_DONE` packages count | within ±10% of pre-fix baseline (some may now be quarantined) |

## E — Rollback path

Phase 5 adds a new file (`coin_package_validator.py`) and one call site in `scanner_worker.py`. Reverting either commit removes the validation entirely. Threshold values exposed in `config.toml [coin_package_validator]` so operator can soften without redeploy.

## Files end-to-end mapped

| File | Lines | Role |
|---|---|---|
| `src/core/coin_package.py` | 1-134 (dataclass) | Schema; reference for what should be populated |
| `src/workers/scanner_worker.py` | 300-483 (`_build_package`) | Builder; **fix target — call validator + quarantine** |
| `src/brain/strategist.py` | 1157-1221 (`_format_packages_for_prompt`) | Consumer; unchanged in this phase |
| `src/core/coin_package_validator.py` | NEW | Pure-function validator with verdict + completeness scoring |
| `tests/test_coin_package_validator.py` | NEW | Unit tests for validator |
| `config.toml [coin_package_validator]` | NEW | thresholds (`fail_below`, `warn_below`, `staleness_fail_seconds`) |

## Phase 5 fix outline (preview)

3 atomic commits:
1. Add `coin_package_validator.py` — pure-function validator. Returns `(verdict, completeness_score, missing_fields, stale_fields)`. Field-level rules:
   - Required: `symbol` non-empty, `qualified` bool, `opportunity_score` finite in [0,1], `price_data.current > 0`, `built_at` recent
   - Partial: SL/TP/RR > 0 if setup_type ≠ "none"; signal confidence finite; etc.
   - Score = (populated_required + 0.5×populated_optional) / (total_required + 0.5×total_optional)
2. Wire `_build_package()` to call validator. On `fail`, log `PACKAGE_QUARANTINED` and DON'T include in `_coin_packages`. On `warn`, log + include.
3. Unit tests covering full/missing-required/missing-optional/stale-by-5min/stale-by-10min cases.
