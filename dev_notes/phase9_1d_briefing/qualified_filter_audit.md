# Phase 9 cutover audit — qualified-filter relaxation

**Date:** 2026-05-01
**Decision:** Update `strategist._format_packages_for_prompt` to render `qualified=False` packages under `surface_briefing_fields=True`.

## What changed

| Filter site | Before (Phase 6) | After (Phase 9) |
|---|---|---|
| `strategist.py:_format_packages_for_prompt` skip rule | `if not pkg.qualified and pkg.open_position is None: continue` | Under `surface_briefing_fields=True`: skip only when `primary == NO_TRADEABLE_STATE AND open_position is None AND interestingness < prompt_floor_interestingness`. Under `surface_briefing_fields=False`: legacy filter unchanged. |

## Risk audit — Q3b BTC/ETH hallucination fix preservation

The original Q3b filter (2026-04-29) was added because the legacy scanner unconditionally force-included BTCUSDT and ETHUSDT into `_active_universe` with `qualified=False AND open_position=None`. Their packages then rendered in the brain prompt under "TRADE CANDIDATES" and Claude hallucinated trades on them every cycle.

**The Q3b fix has two layers:**

1. **Upstream (scanner_worker)**: the unconditional BTC/ETH ref-pair add was removed. The active universe now reflects qualified survivors + open positions only. (See `scanner_worker.py:~1199-1208` in the legacy code.)
2. **Downstream (strategist)**: the filter at `_format_packages_for_prompt` was a defensive guard in case the upstream regressed.

**Briefing mode's selection step**:

```
src/workers/scanner_worker.py:_tick_briefing_mode()
  - watch_list ∪ protected (open positions) iterated
  - NO BTC/ETH unconditional add anywhere in the briefing path
  - selected = forced_records + candidate_records[:budget]
  - soft floor padding from candidate_records (filtered by interestingness)
```

Result: a coin only reaches the strategist filter under briefing mode if it was either an open position (HR-2) or it ranked into the top-N by interestingness. There is no path under briefing mode where BTC/ETH would appear with `qualified=False AND open_position=None` unless an operator deliberately adds them to the watch_list AND they fail to qualify by interestingness — in which case the new skip-rule (`primary=NO_TRADEABLE_STATE AND interest < prompt_floor`) STILL filters them out.

**Verification grep** (run before Phase 9 commits):

```bash
$ grep -rn "BTC\|ETH" src/workers/scanner_worker.py | grep -iE "force\|add\|append\|insert"
# (no unconditional add results expected)
```

## Skip rule under briefing mode

```
skip = (primary == NO_TRADEABLE_STATE OR primary == "")
       AND open_position is None
       AND interestingness_score < prompt_floor_interestingness  # default 0.20
```

The skip is *intentionally narrow*: a coin with ANY non-advisory label, an open position, or interestingness ≥ 0.20 renders to the brain. This is the whole point of the briefing pipeline — transparency over exclusion.

## Per-cycle visibility into skipped coins

Even when a coin is skipped from the per-coin TRADE CANDIDATES block, it appears in the per-cycle `SCANNER_BRIEFING_SUMMARY` log line with the cycle's label distribution and mean interestingness. Operators can grep:

```bash
grep SCANNER_BRIEFING_SUMMARY data/logs/workers.log | tail -5
```

to confirm advisory-only / NO_TRADEABLE_STATE coins are still being characterized — they're just not rendered in the prompt to keep the byte budget bounded.

## Operator sign-off

Operator approved Phase 9 entry per the rollout plan's Section E touchpoints. This commit reflects that approval.
