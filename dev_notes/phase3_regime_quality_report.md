# Phase 3 — RegimeWorker per-coin Verification Report

**Date:** 2026-04-27
**Commits:**
- `5dfd187` — regime hysteresis config + REGIME_PERCOIN_SUMMARY

## Bug summary (revised vs Phase 0)

Phase 0 surfaced two suspected issues:
1. ❌ Hardcoded ADX/choppiness/ATR thresholds — **REVISED:** these were ALREADY config-driven via `[regime]`, only `hysteresis_count` (the confirm-N-readings literal at `regime.py:185`) was hardcoded.
2. ✅ Per-coin regime distribution invisible — **CONFIRMED:** the `REGIME_DIVERGE` log lists divergent coin names but no aggregate distribution.
3. ❌ ScannerWorker criterion 3 might read global only — **REVISED:** verified at `scanner_worker.py:122`, `_get_regime_alignment(coin)` calls `rw.get_regime(coin)` which returns per-coin (NOT global).

Phase 3's actual scope thus reduced to two minimal changes.

## Fix summary

| File | Change |
|---|---|
| `src/config/settings.py` | `RegimeSettings.hysteresis_count: int = 2` validated >=1; `_build_regime` parses it. |
| `src/strategies/regime.py` | Hysteresis check reads `cfg.hysteresis_count` (defaults to 2 if missing — back-compat); `REGIME_PENDING` log shows `count/N` not `count/2`. |
| `src/workers/regime_worker.py` | NEW per-cycle `REGIME_PERCOIN_SUMMARY` emit with full per-coin regime distribution from the merged cache. |
| `config.toml` | `[regime]` block adds `hysteresis_count = 2` with explanatory comment. |

## New observability

```
REGIME_PERCOIN_SUMMARY | total=49 trending_down=20 ranging=15 trending_up=10
                         volatile=3 dead=1 global=trending_down divergent=29 | did=...
```

(Pre-fix: divergent count alone, no per-category breakdown.)

## Verification — automated

```
pytest 105 passed
  signal_generator_multi_source + setup_classifier_diagnose + state_sync
  + persistence + worker_liveness x2 + corrected_layer1 + universe
  + logging_routing
```

Settings round-trip + validation:
```
regime.hysteresis_count: 2
RegimeSettings(hysteresis_count=0) rejected: must be >= 1
```

## Verification — operator-driven (post-deploy, 1 hour)

| # | Trial | Pass criterion |
|---|---|---|
| 3.1 | `REGIME_PERCOIN_SUMMARY` per cycle | once per RegimeWorker tick (~6/hour at 600s interval) |
| 3.2 | Distribution shows ≥3 categories | `trending_up + trending_down + ranging + volatile + dead` not all in one bucket |
| 3.3 | hysteresis_count config-driven | set to 1 in config.toml + restart → `REGIME_PENDING count/1` ; set to 3 → `count/3` |
| 3.4 | ScannerWorker criterion 3 | `scanner_worker.py:122` already reads per-coin (confirmed in code review — no change needed) |

## Out of scope

- Threshold tuning (already config-driven; Phase 0 evidence didn't show mis-calibration)
- Per-coin REGIME_FLIP individual tracking (existing `REGIME_CHG` already covers this at line 191)
