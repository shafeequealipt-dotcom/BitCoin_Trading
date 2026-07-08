# Phase 5 — CoinPackage Validator + Quarantine Report

**Date:** 2026-04-27
**Commits:** 3 atomic — validator module, scanner wiring, unit tests.

## Bug summary

Pre-fix: ScannerWorker writes packages directly to `layer_manager._coin_packages`. Defensive defaults (0.0, "", "neutral", "none") for missing services mean a package with `setup_type="none"`, `total_score=0.0`, `signals.confidence=0.0`, `regime=""` can reach Stage 2 — wasted brain call.

## Fix summary

Pure-function validator with verdict (`ok` / `warn` / `fail`) + completeness score (0..1) + missing/stale field lists. ScannerWorker quarantines `fail` packages (not in `_coin_packages`); `warn` packages flow but are flagged.

| File | Change |
|---|---|
| `src/core/coin_package_validator.py` (NEW) | `validate_package()` pure function + `ValidationResult` frozen dataclass + 3 verdict constants |
| `src/config/settings.py` | `CoinPackageValidatorSettings` (3 tunables, validated); Settings field; parser; wired |
| `src/workers/scanner_worker.py` | Validate each built package before insertion; quarantine FAILs; emit `PACKAGE_VALIDATE` per package + `PACKAGE_VALIDATE_SUMMARY` per cycle |
| `config.toml` | `[coin_package_validator]` block with operator comment |
| `tests/test_coin_package_validator.py` (NEW) | 11 cases |

## New observability

```
PACKAGE_VALIDATE         | cycle_id=X sym=BTCUSDT completeness=0.93
                           verdict=ok missing=[] stale=[]
PACKAGE_QUARANTINED      | cycle_id=X sym=DOGEUSDT completeness=0.42
                           missing=['price_data.current','xray.setup_type']
                           stale=[]
PACKAGE_VALIDATE_SUMMARY | cycle_id=X packages_built=12 ok=10 warn=2
                           fail_quarantined=0
```

## Verification — automated

```
pytest tests/test_coin_package_validator.py — 11 passed
Full regression — 116 passed
```

## Verification — operator-driven (post-deploy, 1 hour)

| # | Trial | Pass criterion |
|---|---|---|
| 5.1 | All packages validated | `PACKAGE_VALIDATE` per package per cycle |
| 5.2 | Most pass | `ok` ≥ 90% of built packages |
| 5.3 | Quarantine works | `PACKAGE_QUARANTINED` fires when synthetic broken package injected; `_coin_packages` excludes it |
| 5.4 | No regression | Stage 2 receives package list (smaller is OK if some quarantined) |
