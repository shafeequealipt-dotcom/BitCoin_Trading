# Phase 7 of the 1D Briefing Rewrite — Decision Record

**Date:** 2026-05-01
**Decision:** Lower `[brain.cold_start_protection].min_qualified_packages` from `3` to `1`.
**Status:** Shipped at commit `phase7-1d-briefing-shipped`.

## What changed

| Setting | Before | After |
|---|---|---|
| `min_qualified_packages` | 3 | 1 |
| `min_avg_completeness` | 0.85 | 0.85 (unchanged) |
| `min_per_package_completeness` | 0.75 | 0.75 (unchanged) |
| `boot_grace_period_sec` | 600 | 600 (unchanged) |
| `boot_grace_completeness` | 0.95 | 0.95 (unchanged) |

Both `config.toml` and the `BrainColdStartProtection` dataclass default in `src/config/settings.py` flip in lockstep so a config file missing the key still gets the new default.

## Rationale

### The gate's actual purpose

`_cold_start_block_or_none` in `src/core/layer_manager.py:1010-1072` is a **cache-warmup safety net**, not a minimum-cohort enforcer. It exists to block trading during the first ~10 minutes after worker boot when caches (XRAY, regime, signals, altdata, ticker) are still filling. The completeness floors (`min_avg_completeness=0.85`, `boot_grace_completeness=0.95`) are how it detects cache degradation — every well-populated package shows it as 1.0; a missing-data package shows it as 0.5-0.8.

The `min_qualified_packages=3` threshold was added at the same time as a "feels safer" defensive layer. Live evidence today (Phase 0 baseline, `dev_notes/phase0_1d_briefing/A_current_pipeline_baseline.md`) shows it dropping otherwise-tradeable packages:

- Cycle `c-2026-05-01-00:35` produced 1 valid package (AEROUSDT, completeness 1.00) → `BRAIN_INSUFFICIENT_QUALITY` → trade dropped.
- AEROUSDT's package was structurally complete; it was dropped on cohort size, not quality.

### Why 1 is the right threshold

- A single package with completeness ≥ 0.75 (the `min_per_package_completeness` floor) is **proof that the upstream caches are warm**. That's what the gate is supposed to verify.
- Briefing-mode (Phase 5) emits ≥12 packages per cycle — the legacy `min_qualified_packages=3` is irrelevant in briefing mode.
- Exclusion-mode (the production default through Phase 8) historically produces 1-3 qualified packages. With `=3`, single-survivor cycles drop trades; with `=1`, every cycle that has ANY qualified survivor proceeds — matching the operator's stated aim.

### Risks NOT taken

- **Removing the gate entirely** would be wrong: degraded caches (e.g., XRAY worker stuck) would silently produce zero-completeness packages and brain would still trade. We keep the completeness floors.
- **Lowering completeness thresholds** would be wrong: those are the actual cache-warmup signals.
- **Removing boot_grace_period_sec** would be wrong: 10 minutes after restart is when caches fill from the M5 cycle boundaries; the stricter completeness during this window is correct.

## Verification

Post-deploy verification:

1. After a cycle where briefing-mode produces ≥1 qualified package, `grep BRIEFING_INSUFFICIENT_QUALITY data/logs/workers.log` returns no new occurrences.
2. `BRAIN_COLD_START_BLOCK` still fires correctly when a synthetic test reduces `avg_completeness` below 0.85 (regression check on the completeness gate).
3. Existing test `tests/test_layer_manager_cold_start.py` continues to pass after fixture update for the new default.
4. New test `tests/test_phase7_1d_briefing/test_gate_passes_with_one_qualified.py` proves single-package cycles pass the gate.

## Rollback

Single config flip if regression detected:

```toml
[brain.cold_start_protection]
min_qualified_packages = 3
```

Plus `src/config/settings.py:401` reset to `3` to keep dataclass and config in lockstep. Restart workers; cycle returns to legacy behavior immediately.

## Operator sign-off

Operator approved Phase 7 entry per the rollout plan's Section E touchpoints. This commit reflects that approval.
