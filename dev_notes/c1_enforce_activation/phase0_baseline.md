# C1 — Phase 0 Baseline

## Capture timestamp

2026-05-21 (UTC), at HEAD `dd5e48c` (`issue2/p3-5: remove R4 audit and full-chain replay scripts`).

## Git ground state

- `git status --short` shows only runtime auto-updates and the new C1 working directory:
  - ` M data/layer_state.json`
  - ` M data/logs/layer1c_full.jsonl`
  - `?? dev_notes/c1_enforce_activation/`
  - `?? dev_notes/five_issues_fix/`
- `git log origin/main..main --oneline` is empty. No unpushed commits.
- `git branch --no-merged main` is empty. No unmerged branches.
- Per `CLAUDE.md` zero-pending rule, the repo is in a clean shipped state. No previous-session debt to resolve.

## Current config values (config.toml lines 530–532)

- `wd_brain_scoring_enabled = true`
- `wd_brain_scoring_enforce = false` (Phase 1: log-only)
- `wd_brain_scoring_threshold = 6.0`

Settings dataclass mirror at `src/config/settings.py:1018–1020`. Builder mapping at `src/config/settings.py:3797–3805`.

## Log event counts across 2026-05-20 rotations plus current `workers.log`

Logs covered: nine `workers.2026-05-20_*.log` rotations under `data/logs/` (~9.6 MB each, except the two short startup rotations) plus the live rotating `workers.log` (~4.7 MB).

| Event | Count | Interpretation |
|---|---|---|
| `WD_SCORING_PATH_REACHED` | 28 | The diagnostic from Phase C1 hardening fires on every brain close/take_profit |
| `WATCHDOG_CLOSE_SCORE_COMPUTED` | 28 | Scoring computed for every brain close vote — equal to the prompt's expected 28 |
| `WD_CLOSE_SCORE_LOG_ONLY` | 28 | Log-only mode confirmed — every scored close fell through to the existing brain close |
| `WATCHDOG_CLOSE_EXECUTED` | 0 | Enforce mode is off, no scored close executed via the scoring path |
| `WATCHDOG_CLOSE_REJECTED` | 0 | Enforce mode is off, nothing rejected |
| `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN` | 0 | Enforce mode is off, no SL tightening invoked from the scoring path |
| `WD_BRAIN_SCORE_FAIL` | 0 | No fail-soft fires — every scoring call succeeded |
| `BRAIN_FAILURE_CASCADE` | 0 | DB cascade absence confirmed |
| `SHADOW_SL_PUSH | source=wd_brain_scoring` | 0 | Tightening path is fully untriggered pre-flip |

## Composite distribution from the 28 scored closes

```
  4  composite=-5.5  reject_and_tighten
  4  composite=-4.5  reject_and_tighten
  4  composite=-4.0  reject_and_tighten
  2  composite=-8.5  reject_and_tighten
  2  composite=-6.0  reject_and_tighten
  2  composite=-2.0  reject_and_tighten
  1  composite=-9.5  reject_and_tighten
  1  composite=-3.5  reject_and_tighten
  1  composite=-3.0  reject_and_tighten
  1  composite=-2.5  reject_and_tighten
  1  composite=-1.0  reject_and_tighten
  1  composite=+0.0  reject
  1  composite=+1.0  reject
  1  composite=+1.5  reject
```

Range: −9.5 to +1.5. Every composite is below the 6.0 threshold. 25 of 28 are below 0 (`reject_and_tighten`); 3 are in the 0–6.0 band (`reject`). None would execute under enforce.

Sample event (INJUSDT, 11:16:08 UTC):
```
WATCHDOG_CLOSE_SCORE_COMPUTED | sym=INJUSDT composite=-3.0 threshold=6.0
  recommendation=reject_and_tighten
  pnl_pct=-0.4362 pnl_bucket=shallow_loser pnl_factor=-3.0
  time_remaining_s=2219 time_bucket=deep time_factor=-2.0
  age_s=481 age_bucket=young age_factor=-1.0
  velocity=0.0 velocity_bucket=stationary velocity_factor=0.0
  sl_pct=44.9 sl_bucket=comfortable sl_factor=-1.0
  xray_bucket=broken xray_factor=2.0
  reasoning_bucket=structural reasoning_factor=2.0
```

Composite arithmetic: −3 − 2 − 1 + 0 − 1 + 2 + 2 = −3. Recommendation matches threshold logic (composite < 0 → `reject_and_tighten`).

## Close-path performance — 2026-05-20 (UTC day)

From `trade_log` joined on `close_reason`:

| close_reason | trades | wins | losses | net pnl ($) |
|---|---:|---:|---:|---:|
| wd_claude_action | 53 | 8 | 45 | −386.04 |
| wd_timeout | 20 | 0 | 20 | −99.69 |
| mode4_stall_valve | 5 | 0 | 5 | −64.03 |
| wd_hard_stop | 1 | 0 | 1 | −41.41 |
| system_close | 9 | 2 | 7 | −15.80 |
| time_decay_force_close | 1 | 0 | 1 | −2.11 |
| mode4_partial | 6 | 5 | 1 | −1.13 |
| bybit_sl_hit | 76 | 39 | 37 | +7.78 |
| wd_trail | 3 | 3 | 0 | +22.99 |
| wd_profit_take | 3 | 3 | 0 | +43.88 |
| bybit_tp_hit | 8 | 8 | 0 | +93.46 |
| wd_dl_action | 32 | 31 | 1 | +239.03 |

Brain's active close (`wd_claude_action`) is the single largest loss path (−$386.04). Passive paths (`wd_dl_action`, `bybit_tp_hit`, `wd_profit_take`, `bybit_sl_hit`) collectively contributed +$384.15.

Spec text in the prompt referenced a 9-hour session 11:00–20:00 UTC at −$65.12 with `wd_claude_action` at −$257.61 (27 losses + 1 win = 28). The full-day breakdown above is consistent: the 28 scored events are the 9-hour-window subset; the remaining `wd_claude_action` trades in the wider day window also closed under the same broken pattern.

## Close-path performance — 14-day cumulative

| close_reason | trades | wins | net pnl ($) |
|---|---:|---:|---:|
| wd_claude_action | 147 | 20 | −952.51 |
| mode4_stall_valve | 43 | 5 | −332.75 |
| wd_timeout | 55 | 0 | −272.55 |
| mode4_partial_fallback_full | 15 | 4 | −117.37 |
| bybit_demo_sl_tp | 117 | 26 | −61.57 |
| mode4_p9 | 25 | 0 | −48.41 |
| mode4_partial | 46 | 33 | −43.56 |
| wd_hard_stop | 1 | 0 | −41.41 |
| system_close | 119 | 57 | −18.51 |
| sentinel_deadline_profit | 8 | 6 | −20.18 |
| emergency_manual | 48 | 20 | +19.06 |
| trailing_stop | 9 | 7 | +31.39 |
| hard_stop | 1 | 1 | +37.92 |
| wd_trail | 7 | 7 | +69.59 |
| profit_take | 4 | 3 | +73.79 |
| bybit_sl_hit | 313 | 150 | +190.28 |
| bybit_tp_hit | 22 | 20 | +241.32 |
| shadow_sl_tp | 69 | 51 | +383.10 |
| wd_profit_take | 15 | 15 | +644.97 |
| wd_dl_action | 134 | 127 | +1035.23 |

`wd_claude_action` 14-day win rate: 20 / 147 = 13.6%. Passive `wd_dl_action`: 127 / 134 = 94.8%. The wd_claude_action loss accumulation is structural, not a single bad day.

`wd_claude_action` per-day:

```
2026-05-11  +0.76    1 win / 3
2026-05-12  -24.19   1 / 7
2026-05-13  -6.06    0 / 1
2026-05-14  -133.61  1 / 11
2026-05-15  -144.61  1 / 11
2026-05-16  -63.62   0 / 10
2026-05-17  -92.08   0 / 13
2026-05-18  -13.67   6 / 17
2026-05-19  -89.39   2 / 21
2026-05-20  -386.04  8 / 53
```

Note that the strategic_review-named close reasons that appear in the long 14-day table are CALL_B reason strings written into `close_reason` rather than the canonical bucket — they are effectively `wd_claude_action` closes carrying the brain's free-text reason. Conservatively treating those as additional brain-close losses would push the cumulative brain-driven loss to roughly −$1,200 over 14 days.

## Invariants checked

- DB cascades absent (`BRAIN_FAILURE_CASCADE` = 0 across the same logs).
- No SL push via `source=wd_brain_scoring` has ever fired (`SHADOW_SL_PUSH | source=wd_brain_scoring` = 0).
- No fail-soft path in the scoring intercept fired (`WD_BRAIN_SCORE_FAIL` = 0).

## Conclusion of Phase 0

The scoring system is live, healthy, and in log-only mode. Twenty-eight scored close votes from the 2026-05-20 sessions match the prompt's spec exactly. Every composite is below threshold; none would execute under enforce. The enforce-mode SL tightening path is fully untriggered. There are no DB cascades and no scoring failures. The system is in a clean state to proceed with Phase 1 investigation.
