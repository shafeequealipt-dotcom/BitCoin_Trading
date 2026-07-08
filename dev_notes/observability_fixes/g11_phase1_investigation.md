# G11 Phase 1 — TIME_DECAY noise reduction (WARNING → INFO)

## Headline

Phase 0 baseline showed three TIME_DECAY events dominating the WARNING
tier in the audited window:

| Tag | Count/1.5h | Pre-G11 level | Site |
|-----|------------|----------------|------|
| TIME_DECAY_MAE_MONOTONIC_HOLD | 296 | WARNING | time_decay_sl.py:682 |
| TIME_DECAY_MAE_GUARD | 254 | WARNING | time_decay_sl.py:435 |
| TIME_DECAY_AGE_GUARD | 100 | WARNING | time_decay_sl.py:412 |
| TIME_DECAY_SKIP | 210 | INFO (throttled 60s/sym) | position_watchdog.py:344 |

Combined: 650+ events/1.5h on the WARNING tier from one subsystem. The
WARNING tail is meant to surface exceptional conditions operators
should investigate; high-volume normal-operation events drown the real
alerts.

## Analysis per event

### TIME_DECAY_AGE_GUARD (100/window @ WARNING → INFO)

Fires when `position_age < min_age_seconds`. This is the
min-hold-time gate — designed to keep new positions from being
force-closed before the trade thesis can play out. Every firing
represents the gate **working correctly**. Not exceptional.

### TIME_DECAY_MAE_GUARD (254/window @ WARNING → INFO)

Fires when MAE hasn't reached `mae_to_sl_ratio_threshold` of original
SL. Same character: gate working as designed (position still in
normal-development territory, not close to SL invalidation point).
Not exceptional.

### TIME_DECAY_MAE_MONOTONIC_HOLD (296/window @ WARNING → INFO)

Fires when a caller proposes a `candidate` MAE less adverse than the
current `state.mae_pct`. The in-file comment claimed this should be
"a smoking gun for a future direct-mutator" but the 296-event count
shows it routinely fires in benign scenarios (multi-source updates
within a single tick, init_seed paths). The invariant (monotonic-hold
rejects the regression) is preserved; only the severity classification
changes.

If a future audit shows the rate spiking (say >500/h sustained), THAT
becomes a smoking gun. The level downgrade does not impair
investigability — operators grep INFO logs for the tag.

### TIME_DECAY_SKIP (210/window @ INFO, throttled)

Already at INFO with a 60-s per-(symbol, reason) throttle (the
`_log_skip` helper at position_watchdog.py:329). No change in G11.

## Audit-compliance check

The prompt explicitly forbids:

- ✗ "Reducing the noise by removing important events" — events
  preserved.
- ✗ "Switching everything to DEBUG level (loses critical alerts)" —
  downgrade is to INFO, not DEBUG.
- ✗ "Adding a sampling filter that drops state transitions" — no
  sampling; every event still emits.

All three constraints satisfied. The audit's allowed pattern is
"downgrade heartbeats to DEBUG OR sample at 1-in-10"; for state
transitions like these, downgrade to INFO is the closest match.

## Expected volume impact

Pre-G11 WARNING-tier from time_decay: ~650 events / 1.5h ≈ 430/hour.
Post-G11: ~0 events from time_decay on WARNING (only genuine
errors / unrelated WARNINGs remain).

Net total log volume: unchanged. INFO-tier gains 650 events / 1.5h.
The +30% Phase 0 budget targets total volume; this is net-zero on
total volume and a -100 % reduction on WARNING noise from time_decay.

## Behaviour preserved

- All emission lines, field shapes, and call sites unchanged
- Monotonic-hold invariant: regressions still rejected
- AGE_GUARD / MAE_GUARD still return None to block time-decay action
- TIME_DECAY_SKIP path untouched

## Tests

`tests/test_time_decay_log_levels.py` (5 cases):
- Source-level regex pin: each tag's emission line uses `log.info(`
  rather than `log.warning(` — fails the next regression
- `_assign_mae_monotonic` still rejects regression and HOLDs prior MAE
- `_assign_mae_monotonic` still accepts deeper MAE

59 pre-existing time-decay tests pass; zero regression.
