# Stage 2 Aggressive Framing — Runbook

## Context

The aggressive-framing rewrite (2026-05-05, nine commits ending at the
HEAD of the branch) replaces avoidance-bias content in Stage 2 Call A
with operator-aligned exploitation framing. The rewrite is text-only
and contained to `src/brain/strategist.py`, the prompt-side renderer
of `FundLimits.to_prompt_text` (deferred), and three `tests/` files.

This runbook documents what changed, how to verify the new state is
live, the four monitoring axes for the 24-hour observation trial, and
the rollback recipe.

## What changed

Six framing surfaces were removed or simplified in `_build_trade_prompt`:

1. Mode line (`MODE: SHADOW/TESTNET/MAINNET`) — gone (commit 1)
2. Performance Enforcer coaching block (`PERFORMANCE COACH`,
   `CAPITAL PRESERVATION MODE`, `RISK MANAGEMENT MODE`) — gone (commit 2)
3. `## TODAY'S PERFORMANCE` block (Daily PnL %, Trades today) — gone
   (commit 3)
4. `FUND RULES (non-negotiable)` 13-line block (tier label, growth %,
   capital allocation, etc.) — replaced by two clean lines
   `Per-trade size limit: $X` / `Maximum concurrent positions: N`
   (commit 4). Commit 4 also fixed a latent UnboundLocalError trap at
   `strategist.py:2719` and promoted two `log.debug` calls to
   `log.warning` so wallet-fetch failures surface to the operator.
5. `## DIRECTION PERFORMANCE` block (per-direction win/loss split) —
   gone (commit 5)
6. `## REGIME-SPECIFIC TRADING INSTRUCTIONS` prescriptive block —
   replaced by a single factual line (commit 6)

Two system-prompt constants were rewritten with FIX Change 7
verbatim aggressive-exploitation framing:

7. `TRADE_SYSTEM_PROMPT_ZERO_TWO` (live in production) — rewrote the
   philosophical preamble + `STRICT 0-2 CONTRACT` + JUDGMENT block +
   `WHEN TO RETURN ZERO TRADES` enumeration. Retained operational
   machinery (DIRECTION BY REGIME, FEAR & GREED, FOR EACH NEW TRADE,
   POSITION GATE, JSON schema, RULES). Two terminology fixes: size
   guidance now refers to `the per-trade size limit shown above`
   instead of `FUND RULES max-single-trade`. Commit 7.
8. `TRADE_SYSTEM_PROMPT` (legacy, dormant when
   `enable_zero_two_contract=true`) — rewrote in lockstep with
   ZERO_TWO so a flag-flip back doesn't re-introduce the
   `ALWAYS find at least 2 trades` mandate or the
   `prefer waiting for a pullback to RSI 40-60` avoidance language.
   Commit 8.

Observability:

9. `STRAT_AGGRESSIVE_FRAMING` info log emitted once per Call A
   immediately before `claude.send_message`. Single-line breadcrumb
   covers all six framing-removal switches and the system-prompt
   flag state. Commit 9.

## Verification — first 5 minutes after restart

After `sudo systemctl restart trading-workers`:

```
tail -f logs/brain.log | grep STRAT_AGGRESSIVE_FRAMING
```

Expected output once per Call A (every ~5 minutes):

```
STRAT_AGGRESSIVE_FRAMING | mode_line=skipped coaching=skipped \
  fund_rules=minimal today_perf=skipped dir_perf=skipped \
  regime_instr=minimal contract=aggressive_exploit zero_two_flag=True | did=...
```

If `zero_two_flag=False` appears unexpectedly, check
`config.toml [stage2] enable_zero_two_contract` — should be `true`.

To inspect actual prompt content per cycle:

```
touch data/stage2_dumps/.enabled
.venv/bin/python scripts/monitor_stage2_live.py
```

Expected in the system prompt (each cycle):
- Opens with `Your aim is to exploit the current market situation...`
- Contains `Aggressive exploitation. Maximum profit. Find the play.`
- Does NOT contain `STRICT 0-2 CONTRACT`, `Three or more is a HARD
  violation`, `JUDGMENT — USE THE FULL PER-COIN DATA`,
  `DO NOT require unanimous agreement`, `trust the structure`,
  `Missing a genuine opportunity is as costly`

Expected in the user prompt (each cycle):
- Does NOT contain `MODE: SHADOW`, `MODE: TESTNET`,
  `MODE: MAINNET`, `Maximum caution required`
- Does NOT contain `PERFORMANCE COACH`, `Win rate:`,
  `CAPITAL PRESERVATION MODE`, `RISK MANAGEMENT MODE`
- Does NOT contain `FUND RULES (non-negotiable):`, `Tier: 1`,
  `Tier: 2`, `Tier: 3`, `CONSERVATIVE (unproven)`, `Growth:`
- Does NOT contain `## TODAY'S PERFORMANCE`, `Daily PnL:`,
  `Trades today:`
- Does NOT contain `## DIRECTION PERFORMANCE`,
  `## REGIME-SPECIFIC TRADING INSTRUCTIONS`
- Does NOT contain `wait for a pullback to RSI 40-60`,
  `DEFAULT BIAS: SHORT — 70% shorts`
- Contains `Per-trade size limit: $...` and
  `Maximum concurrent positions: N`
- Contains `Global regime: ranging|trending_up|trending_down|...
  (confidence=N%, Fear & Greed=M)`

To turn dumping off without restart:

```
rm data/stage2_dumps/.enabled
```

Test verification (no live pipeline needed):

```
.venv/bin/python -m pytest tests/test_stage2_phase3 \
  tests/test_stage2_phase4 tests/test_trading_mode -q
```

All tests pass.

## Monitoring axes — 24-hour observation trial

### Axis 1 — Trade frequency

```
grep STRAT_CALL_A_PLAN logs/brain.log | \
  grep -oP "trades=\d+" | sort | uniq -c
```

Baseline (pre-fix, 30-min window observed 2026-05-05): roughly 0×3,
1×1, 2×1 per 30-min window. Pass criteria: `trades=1` and `trades=2`
dominate the distribution; `trades=0` becomes the exception. Total
daily trades should rise from 1-2/day baseline to 5-15/day.

### Axis 2 — Direction balance

```
grep STRAT_DIRECTIVE logs/brain.log | grep -oP "dir=\w+" | \
  sort | uniq -c
```

Baseline: long-biased (Buy >> Sell). Pass criteria: shorts appear on
overbought conditions. If `dir=Sell` count stays at zero, the next
bottleneck is the short-side scoring issue (Top-5 audit Issue 3 —
out of scope for this fix).

### Axis 3 — Reasoning language

Pull 10-15 dump files from `data/stage2_dumps/` and grep the
response field for exploitation-pattern citations:

```
grep -E "fade|exhaustion|exploitation|reclaim|reversal|breakout" \
  data/stage2_dumps/*.json | head -30
```

Pass criteria: reasoning text cites specific patterns from the new
catalog. Failure mode: responses still say `wait for pullback`,
`overbought, skip`, `not enough conviction` — the framing didn't
land and the next pass is needed.

### Axis 4 — Execution rate

```
grep -c STRAT_DIRECTIVE logs/brain.log
grep -c ORDER_PLACED logs/brain.log
```

Compare counts. If many directives but few orders place, the
bottleneck is downstream gates (TradeGate, OrderService, FUND RULES
trim, TradeScorer instability — Top-5 audit issues, out of scope).
This isolates Stage 2 effects from Layer 3 effects.

### Axis 5 — System stability

```
grep -E "ERROR|CLAUDE_CALL_TIMEOUT|STRAT_FUND_LIMITS_FAIL|STRAT_ACCOUNT_FETCH_FAIL" \
  logs/brain.log | tail -30
```

Pass criteria: no new error categories. Note that
`STRAT_FUND_LIMITS_FAIL` and `STRAT_ACCOUNT_FETCH_FAIL` were
formerly silent `log.debug` and now surface as `log.warning` (commit
4). Their appearance is correct visibility, not a regression.

## Pass thresholds

After 24 hours of observation:

- Trade frequency rises to 5-15 trades/day (5x-7x baseline)
- Some `dir=Sell` trades appear on overbought conditions
- Reasoning cites exploitation patterns explicitly in 60%+ of
  sampled responses
- No new ERROR categories
- No `CLAUDE_CALL_TIMEOUT` introductions
- Test suite green

## Failure modes

- Frequency unchanged → downstream gates are the bottleneck
  (Top-5 audit Issues 1, 3, 4, 5). Do not blame the framing fix.
- Frequency rises, win rate collapses → strategy edge is the
  underlying issue (separate concern). The framing fix improves
  opportunity-capture, not strategy quality.
- Parse failures → JSON schema or RULES section was clipped
  accidentally. Revert commit 7 or commit 8 in isolation.
- `STRAT_ACCOUNT_FETCH_FAIL` warnings appear in volume → wallet-
  fetch issue surfacing because of the promoted `log.warning`.
  Investigate `account_service` health; this is correct visibility,
  not a regression introduced by the framing fix.

## Rollback

Each commit is individually revertable. Rollback in reverse order:

```
git revert HEAD          # commit 9 — observability log
git revert HEAD~1        # commit 8 — legacy TRADE_SYSTEM_PROMPT
git revert HEAD~2        # commit 7 — TRADE_SYSTEM_PROMPT_ZERO_TWO
...
```

Or, for a quick partial rollback to the prior framing on the
flag-off path only, edit `config.toml`:

```
[stage2]
enable_zero_two_contract = false
```

After commits 7 and 8 the legacy path is also rewritten with the
aggressive framing, so this flip alone changes nothing. To return to
the pre-rewrite legacy framing specifically, revert commit 8 only.

## Out of scope (observed anomalies)

These were documented during plan-mode but explicitly NOT addressed
by this fix. Track them for follow-up after the trial:

- OBS-1: `_build_context_prompt` (line 759) and
  `create_strategic_plan` (line 508) are dead code with no external
  callers. They still reference `STRATEGIST_SYSTEM_PROMPT` (alias
  to TRADE_SYSTEM_PROMPT), `get_coaching_text`, `to_prompt_text`,
  `get_claude_mode_instruction`, daily PnL. A separate cleanup
  commit should retire them.
- OBS-2: Call B (`_build_position_prompt`) emits `## TODAY: PnL=`
  at line 2963. Out of scope per FIX brief; flag for next pass if
  Call B refusal patterns appear.
- OBS-3: `_build_regime_instructions()` method at line 3198 is now
  unreferenced from live code. Garbage-collect with OBS-1.
- OBS-4: `_build_direction_performance()` method is now unreferenced.
  Garbage-collect with OBS-1.
- OBS-5: `FundLimits.to_prompt_text()` is now unreferenced from live
  code. Garbage-collect with OBS-1.
- OBS-6: `PerformanceEnforcer.get_coaching_text()` is now
  unreferenced from live code. Garbage-collect with OBS-1.
- OBS-9: Orphan markers in `_TRIM_ESSENTIAL_MARKERS` and
  `_TRIM_IMPORTANT_MARKERS` (`## TODAY'S PERFORMANCE`,
  `## DIRECTION PERFORMANCE`, `## REGIME-SPECIFIC TRADING INSTRUCTIONS`)
  reference content the production no longer emits. Kept as
  defense-in-depth.
- OBS-10: `_SECTION_CAP=80`, `_CHAR_CAP=14000` are likely
  overprovisioned post-fix (prompt should sit ~10-11K). Consider
  lowering after observation; not in this commit set.
- OBS-11: `project_top5_fix_status.md` notes Top-5 trade-blocking
  fix paused awaiting restart; verify operator restarted top-5
  trial before this fix lands, or sequence the two trials.

## Plan reference

Full plan: `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-moonlit-hickey.md`

Original brief: `/home/inshadaliqbal786/FIX_PROMPT_FRAMING_AGGRESSIVE_EXPLOITATION.md`
