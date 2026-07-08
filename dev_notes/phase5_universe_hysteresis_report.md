# Phase 5 — Universe Hysteresis Report

**Date:** 2026-04-27
**Commit:** `95e6291`
**Status:** Implementation complete (single bundled commit).

## Root cause and fix

`MarketScanner._update_universe` used pure top-N selection with no streak filter, and the re-entry cooldown was hardcoded at 300 s. Composite scores driven by stochastic indicators caused marginal coins to oscillate around the cutoff every 5-min scan — live obs 2026-04-26: **14 rotations / hour**, FILUSDT in/out 3× and AVAXUSDT 2× in 22 minutes. Each rotation triggered KLINE_BACKFILL → cold start → STRAT_SKIP_STALE storm.

Three sub-fixes landed:

1. **Consecutive-scan hysteresis** — `_apply_hysteresis_gate` helper. Per-coin streak counters: `_above_cutoff_streak` advances when `score >= cutoff + entry_threshold_above_min`; `_below_cutoff_streak` advances when `score <= cutoff + exit_threshold_below_min`. Both reset in the dead-band between the two thresholds (transient noise absorbed). New entrants must clear `entry_consecutive_scans` (default 2) above-streak; incumbents leave only after `exit_consecutive_scans` (default 3) below-streak.
2. **Cooldown bump** — `[scanner] reentry_cooldown_seconds` (default 600 s, was hardcoded 300). Force-included BTC/ETH and open-position coins still bypass per existing logic.
3. **`SCANNER_HYSTERESIS` observability** — per-coin entry_pending / entry_confirmed / exit_pending / exit_confirmed events.

## Files modified

- `src/config/settings.py` + `config.toml` — `[scanner.hysteresis]` block + `[scanner] reentry_cooldown_seconds = 600`. New `ScannerHysteresisSettings` nested dataclass.
- `src/strategies/scanner.py` — `_above_cutoff_streak` / `_below_cutoff_streak` instance state; `_apply_hysteresis_gate` helper; `_update_universe` accepts optional `all_scored` to compute cutoff; `scan_market` threads the full scored list through.

## Operator runbook

| Trial | Procedure | Pass criterion |
|---|---|---|
| 5.1 | 24 h rotation count | < 4 rotations / hour (was ~14) |
| 5.2 | Per-coin flap rate | No coin enters AND exits within the same hour |
| 5.3 | Average time in universe | > 15 min |
| 5.4 | Force-include sanity | Open-position coins always present in active_universe; force-include never overridden by hysteresis |

## Rollback

`git revert 95e6291` restores legacy top-N pure-top-N selection + 300 s cooldown. Hysteresis can also be disabled at runtime via `[scanner.hysteresis] enabled = false` without code change.
