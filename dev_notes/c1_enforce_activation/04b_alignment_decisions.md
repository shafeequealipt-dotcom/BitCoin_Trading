# C1 — Phase 1.4b Alignment Decisions

## Goal

Bring the brain CALL_B prompt and the watchdog scoring intercept onto a single canonical SL%-consumption formula, with the diagnostic (Phase 1.4) surfacing any remaining gap caused by trailing.

## Canonical formula choice

The arithmetic is identical between brain and scorer; only the SL value differed. The alignment therefore is not about changing math — it is about deciding which SL each call site uses, and unifying the math under one helper.

The chosen design:

- **Math.** A single function `compute_sl_consumption_pct(side, entry_price, stop_loss, current_price) -> float | None` in `src/risk/wd_brain_scoring.py`. Pure, direction-aware, clamps to `[0, 100]`, returns `None` on malformed inputs.
- **Scorer.** Uses `pos.stop_loss` (current trailed SL). Unchanged from prior behaviour; the watchdog's `_calculate_sl_proximity` is now a thin wrapper over the helper. The composite is unaffected.
- **Brain prompt.** Shows BOTH numbers when trailing has moved the SL: "SL consumed: A% (entry-budget) / B% (current-stop)". When no trailing, single number. Brain has explicit context for both interpretations.
- **Diagnostic.** New `WD_SL_PCT_DIVERGENCE` log event emits both percentages, both buckets, and a bucket-flip flag on every scored close vote.

## Why not pick one SL and discard the other

Three options were considered:

1. **Show only entry SL%** (the brain's pre-C1 behaviour): hides the actual current risk envelope from the brain. Brain might think "SL is far away" when in fact the trailed SL is 5% from mark.
2. **Show only current SL%**: discards the original risk-budget reference, which is the number Claude reasoned over at trade open. Loses context the brain may use to recognise that the position has already converted unrealised loss into realised hedge via trailing.
3. **Show both, with explicit labels**: zero context lost, modest prompt-size cost (one extra line per trailed position). Selected.

## Why the scorer uses current SL and not entry SL

- It is what `_calculate_sl_proximity` always did (line 3218 pre-alignment).
- The scoring system's job is to evaluate "is the brain right to close NOW?". The relevant risk is what the SL is NOW, not what it was at trade open.
- Changing the scorer to use entry SL would shift every composite that has had any trailing. The 28 historical composites are the baseline; re-bucketing them would invalidate the threshold (6.0) the operator validated against the log-only data. The operator would have to re-validate. Not worth it for what is essentially a labelling clarification.

## Why we keep `_calculate_sl_proximity` rather than calling the helper everywhere

Four callers exist outside the scoring intercept (`position_watchdog.py:795, 2525, 2730, 2832`). Three of them (`sl_prox = self._calculate_sl_proximity(...) or 0`) tolerate `None`, and pass the value to either `coordinator.get_maturity`, `_check_maturity`, or `WatchdogConcern`. None depend on values above 100. Keeping the wrapper:

- Preserves the existing call shape (no churn at four call sites).
- Centralises the side-detection (`pos.side.value if hasattr...`) so callers don't repeat it.
- Marks the watchdog's preferred SL choice (current) at one definitional site rather than spread across callers.

## Behaviour-change scope check

Each alignment commit was specifically engineered to **not** change the composite for any pre-existing position:

| commit | what changed | does it shift composites? |
|---|---|---|
| `c1: add shared compute_sl_consumption_pct helper` | new pure function, not used yet | No |
| `c1: watchdog _calculate_sl_proximity delegates to shared helper` | `_calculate_sl_proximity` body replaced with helper call; same inputs, same arithmetic, same return shape; only difference is values >100 are clamped to 100 instead of returned as-is | No (no caller depends on >100; verified) |
| `c1: brain CALL_B prompt renders current+entry SL% via shared helper` | brain prompt text addition only | No (brain prompt is downstream of scorer) |
| `c1: add WD_SL_PCT_DIVERGENCE diagnostic` | new log event only | No (read-only) |

After all four commits, `WATCHDOG_CLOSE_SCORE_COMPUTED` should produce bytewise-identical `sl_pct` and `sl_bucket` values for any pre-existing position state. The composite cannot change because of these commits.

## Prompt-size impact on CALL_B

The brain prompt addition is at most one extra line per position with a trailed SL. The typical CALL_B prompt has < 10 open positions; most do not have trailing engaged in the first 10 minutes. Worst-case prompt-size increase is ~10 lines × ~80 chars = ~800 characters per prompt. Negligible vs the typical CALL_B prompt size of ~50k characters.

## What the operator sees in the brain CALL_B prompt now

Untrailed position (current behaviour preserved):
```
### INJUSDT [Sell]
  Entry: $25.00 | Now: $24.85 | PnL: +0.60%
  SL: $26.00 | TP: $24.20 | Lev: 5x
  Age: 8min | Remaining: 32min | Regime: TRENDING_DOWN 71%
  SL consumed: 0%
```

Trailed position (new dual rendering):
```
### CRVUSDT [Sell]
  Entry: $0.5800 | Now: $0.5750 | PnL: +0.86%
  SL: $0.6100 (entry) / $0.5950 (trailed) | TP: $0.5400 | Lev: 5x
  Age: 22min | Remaining: 18min | Regime: TRENDING_DOWN 65%
  SL consumed: 33% (entry-budget) / 67% (current-stop)
```

The brain can see both interpretations explicitly. The 71% number Claude reported for CRVUSDT in the captured monitoring case is no longer ambiguous — it would have been labelled "(entry-budget)" and the operator and the brain agree.

## Backwards compatibility with the CALL_B decision parser

The decision parser (`src/brain/decision_parser.py`) parses Claude's response, not the prompt. Adding a line to the prompt does not affect the parser. The parser is downstream of the brain's free-form reasoning over the prompt; if Claude writes "SL 71% consumed" in its response, the parser does not consume that text — it consumes the structured JSON in the response. No parser change needed.

## Conclusion of Phase 1.4b

The alignment is in place. The scorer and brain prompt now share a single canonical SL% formula. The brain sees both interpretations when trailing has occurred. The diagnostic surfaces any remaining gap to the operator on every vote. None of the four commits change the composite for any pre-existing position. Activation can proceed.
