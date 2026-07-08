# C1 — Phase 1.3 Enforce-Path Completeness Verification

## Goal

Confirm every code path the enforce mode touches is complete, has no stubs, and behaves safely. The enforce branch has never executed in production. This verification is the precondition for flipping the flag.

## The four runtime branches

The intercept (`position_watchdog.py:3419–3665`) supports four execution branches, decided by the recommendation field of `compute_brain_close_score`:

| `_enforce` | recommendation | Action | Skip existing close? |
|---|---|---|---|
| False | (any) | Log `WD_CLOSE_SCORE_LOG_ONLY`, fall through | No (brain's close fires) |
| True | `execute` | Log `WATCHDOG_CLOSE_EXECUTED`, fall through | No (brain's close fires) |
| True | `reject` | Log `WATCHDOG_CLOSE_REJECTED` | Yes (`_scoring_skip_close = True`) |
| True | `reject_and_tighten` | Log `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN`, call `_tighten_sl_breakeven_30pct`, then | Yes |

Inspected line-by-line, all four branches are wired with concrete behaviour. There is no stub or `TODO` in the path.

## Branch 1 — `reject`

`position_watchdog.py:3635–3641`:

```python
elif _score.recommendation == "reject":
    log.warning(
        f"WATCHDOG_CLOSE_REJECTED | sym={symbol} "
        f"composite={_score.composite:.2f} "
        f"threshold={_threshold:.2f} | {ctx()}"
    )
    _scoring_skip_close = True
```

Behaviour: log line carries symbol, composite, threshold. `_scoring_skip_close` is the local flag inside `_execute_strategic_actions()`. Below at line 3664: `if _scoring_skip_close: continue` — the outer loop moves to the next action, skipping the brain's `close_position(symbol, close_trigger="wd_claude_action")` at line 3669.

No SL change. The position is held. The next watchdog tick (5 s default) re-evaluates with fresh data — if conditions truly deteriorate the deep-loser PnL bucket eventually moves to `moderate_loser`, the time bucket moves toward `imminent`, and the composite climbs. The position is also subject to all other watchdog rails: deadline, trailing SL, time-decay, mode4_stall_valve, hard_stop, etc.

Verdict: complete and correct.

## Branch 2 — `reject_and_tighten`

`position_watchdog.py:3642–3653`:

```python
else:  # reject_and_tighten
    log.warning(
        f"WATCHDOG_CLOSE_OVERRIDE_TIGHTEN | "
        f"sym={symbol} "
        f"composite={_score.composite:.2f} | "
        f"{ctx()}"
    )
    if _pos_for_score is not None:
        await self._tighten_sl_breakeven_30pct(
            _pos_for_score,
        )
    _scoring_skip_close = True
```

Behaviour:
- Log line carries symbol and composite (no threshold — already-failed-threshold is implied).
- `_pos_for_score is not None` guard: if the watchdog couldn't fetch the position (re-fetch failed at lines 3471–3478), tightening is skipped. The close is still blocked via `_scoring_skip_close = True`. This is intentional — when the position-service is transient-unavailable, both the scoring and the brain's close would fail anyway. Consistent failure semantics.
- `_tighten_sl_breakeven_30pct` is awaited; even if it returns False (no-op, rate-limited, push failed, etc.), the close is still blocked. The next tick will retry tightening through the same path.

Verdict: complete and correct. The tightening attempt is best-effort; the close-block is unconditional.

## Branch 3 — `execute`

`position_watchdog.py:3629–3634`:

```python
if _score.recommendation == "execute":
    log.warning(
        f"WATCHDOG_CLOSE_EXECUTED | sym={symbol} "
        f"composite={_score.composite:.2f} | {ctx()}"
    )
    # Fall through to the existing close call.
```

Behaviour: emits the WATCHDOG_CLOSE_EXECUTED log line and falls through. `_scoring_skip_close` stays False. The existing `position_service.close_position(symbol, close_trigger="wd_claude_action")` at line 3669 fires.

The "execute" branch only triggers when composite ≥ threshold (6.0 by default). Historical evidence from Phase 1.1 shows zero such composites in the 2026-05-20 session. The scoring weights make it hard to reach 6.0 for typical losers; reachable mostly by strong winners or aged + structurally-broken + accelerating losers — which are legitimate close cases.

Verdict: complete and correct.

## Branch 4 — log-only fall-through

`position_watchdog.py:3621–3627`:

```python
if not _enforce:
    log.info(
        f"WD_CLOSE_SCORE_LOG_ONLY | sym={symbol} "
        f"composite={_score.composite:.2f} "
        f"would_be={_score.recommendation} | {ctx()}"
    )
    # Fall through to the existing close call.
```

This is the current production path. The would-be recommendation is logged for operator review; the close fires unchanged. Phase 0 confirmed 28 of these in the 2026-05-20 logs.

Verdict: complete and correct. This branch has been running for the full log-only-mode period without incident (0 `WD_BRAIN_SCORE_FAIL`).

## `_tighten_sl_breakeven_30pct` (`position_watchdog.py:1173–1226`)

Geometry verification:

```python
delta = (entry - current_sl) * 0.30
new_sl = current_sl + delta
```

| direction | entry vs current_sl | delta sign | new_sl vs current_sl | Tighter? |
|---|---|---|---|---|
| BUY | `current_sl < entry` (SL below entry) | + | `new_sl > current_sl` (moves up toward entry) | Yes |
| SELL | `current_sl > entry` (SL above entry) | − | `new_sl < current_sl` (moves down toward entry) | Yes |

The formula is symmetric in both sides. The new SL is exactly 30% of the remaining `current_sl → entry` distance closer to entry. It cannot pass entry: if `current_sl == entry` then `delta = 0` and the no-op guard rejects the push. If `current_sl` somehow already passed entry (impossible under tighter-only history, but checked anyway), the delta has the wrong sign and the tighter-only guard at `_push_sl_to_shadow:1134/1140` rejects.

Pre-conditions enforced (line 1195):
```python
if entry <= 0 or current_sl <= 0:
    return False
```

Position without a stop-loss (`current_sl <= 0`) is not tightened — the function returns False, the close is still blocked. The position will be handled by the watchdog's other rails (deadline, time-decay, mode4_stall) on subsequent ticks.

Verdict: safe.

## `_push_sl_to_shadow` safety bounds for `source="wd_brain_scoring"`

Reading lines 883–1171 of `position_watchdog.py`:

1. **No-op guard** (lines 936–947): If `|new_sl - current_sl| / current_sl < 1e-4` (1 basis point), returns False without consulting the gateway. Prevents wasted rate-limit slots.

2. **Rate-limit pre-check** (lines 975–983): If `sl_gateway.next_eligible_in_seconds(symbol) > 0`, returns False (the per-symbol R4 window is active). The push is retried on the next watchdog tick.

3. **Source-specific coalescing**: only `time_decay`, `trail_activation`, `trail_update`, `sentinel_advisor`, `sentinel_deadline` get a 10 s consumer-side coalesce. `wd_brain_scoring` is **not** coalesced — every brain-score rejection-with-tighten attempts a push immediately (subject to gateway rate limits).

4. **Step-clamp** (lines 1071–1097): only applies to `trail_activation` and `trail_update`. `wd_brain_scoring` is not clamped. Acceptable because the 30% delta is by construction a fraction of the existing SL→entry gap, never larger than the original SL distance.

5. **Gateway delegation** (lines 1100–1127): when `sl_gateway` is wired, `apply()` enforces tighter-only, min-distance, max-step, and rate-limit. If any check fails, `accepted=False` and the push returns False.

6. **Legacy path** (lines 1133–1145, `sl_gateway is None`, e.g. unit tests): explicit tighter-only check for both BUY (`new_sl <= current_sl` → reject) and SELL (`new_sl >= current_sl` → reject). Wrong-side placement is impossible.

7. **Plan mirror** (lines 1116–1117): only fires when gateway accepted AND wire succeeded. The local `TradePlan.stop_loss_price` is updated so downstream consumers see the new SL.

8. **Observability**: `SL_PROPAGATED | source=wd_brain_scoring | new={new_sl} prev={cur_sl}` on success; `SL_PROPAGATE_SKIP` or `SL_PROPAGATE_FAIL` on rejection or failure. Every push outcome leaves a log line.

Verdict: the tightening write path is sound. Wrong-side-of-mark placement is impossible by construction.

## Edge cases enumerated

### Position already closing when scoring fires

If `position_service.get_position(symbol)` returns None because the position closed between the brain's vote and the watchdog's processing:
- `_pos_for_score = None`
- Factor collection uses defaults (pnl=0.0, sl=None → midpoint 50, time/age=0)
- Composite likely lands in reject or reject_and_tighten
- Tightening guard `if _pos_for_score is not None` skips the call (line 3649)
- `_scoring_skip_close = True` blocks the existing close (which would also have failed)

No SL push attempted on a closed position. No close attempted on a closed position. Safe.

### SL already at break-even

If `current_sl == entry`:
- `delta = 0`, `new_sl = current_sl`
- No-op guard at `_push_sl_to_shadow:941` rejects (`diff_bps < 1`)
- `_tighten_sl_breakeven_30pct` returns False
- `_scoring_skip_close = True` blocks the brain's close
- Net effect: the position is held; SL stays where it is.

This is the intended behaviour — once SL is at break-even, there's no downside to holding through to the deadline.

### Position with no SL

If `pos.stop_loss is None or pos.stop_loss <= 0`:
- `_calculate_sl_proximity` returns None at line 3224
- Scorer substitutes 50 (midpoint) for the SL factor → `tight` bucket → factor 0.0
- Tightening pre-condition `current_sl <= 0` at line 1195 returns False
- Close still blocked

Same outcome as break-even SL: position held. Acceptable.

### Concurrent gateway race

If two SL pushes hit the gateway simultaneously (e.g., trail and wd_brain_scoring):
- The gateway is single-writer-of-record per symbol via its R4 rate-limit (30 s).
- Whichever push lands first gets accepted (if it satisfies all R1-R4 rules); the second sees rate_limit and returns False.
- `_scoring_skip_close = True` is set regardless of push outcome, so the close is still blocked.
- The next tick retries the push.

No race-condition surprises. The gateway is the single point of truth.

### Brain reasoning is empty

If `PositionAction.reason` is empty/None:
- `_classify_reasoning("")` returns `empty` → factor 0.0
- The composite is unchanged from what it would have been with a vague reasoning (which is +0.5). Loses +0.5 of upward push, makes the close slightly more likely to be rejected. Conservative; acceptable.

### XRAY structure cache unavailable

If `self.structure_cache is None` or the lookup raises:
- `_xray_match = "unavailable"` → factor 0.0
- Composite is unchanged. No `broken` bonus (+2.0), so the close is less likely to clear threshold. Conservative; acceptable.

### Velocity unavailable

If neither time-decay state nor the prev-pnl cache has data:
- `_velocity = None` → scorer substitutes 0.0 → `stationary` bucket → factor 0.0
- Composite unaffected. Acceptable.

## What the enforce-mode flip does at runtime

When `wd_brain_scoring_enforce = True`:

- Every brain `close` or `take_profit` vote enters the scoring intercept (unchanged from log-only).
- The scoring runs (unchanged).
- The `WATCHDOG_CLOSE_SCORE_COMPUTED` log fires (unchanged).
- Instead of `WD_CLOSE_SCORE_LOG_ONLY`, one of `WATCHDOG_CLOSE_EXECUTED` / `WATCHDOG_CLOSE_REJECTED` / `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN` fires.
- For below-threshold composites: the close is **blocked**; for composites < 0 the SL is also tightened.
- For at-or-above-threshold composites: the close **executes**.

No other code path changes. No other layer changes. The brain CALL_B prompt, the gate, the execute step, the Shadow adapter, the regime detector — all untouched.

## Missing pieces (to add in Phase 1.5b / 1.5c)

- No `WD_SCORING_ENFORCE_ACTIVE` boot sentinel exists. Operator currently has no startup confirmation of which mode is active. **Action: add boot sentinel in `__init__` or first-tick.** Phase 1.5b.
- No watchdog-harness integration test exercises the three enforce branches. Tests are pure-function only. **Action: add `tests/test_wd_scoring_enforce_integration.py` with execute / reject / tighten / disabled scenarios.** Phase 1.5c.

## Conclusion of Phase 1.3

The enforce-mode code path is complete. Every branch has concrete behaviour. The SL-tightening fallback is direction-aware and the push path enforces tighter-only by construction. Wrong-side-of-mark placement is impossible. All identified edge cases fail safely.

The only outstanding items before activation are:
1. The boot sentinel (Step 1.5b) so operator can see enforce mode at startup.
2. Integration tests for the three enforce branches (Step 1.5c).
3. The SL% divergence diagnostic and alignment (Step 1.4 and 1.4b).

None of these are blockers for the code itself — they are operability / verification hardening.
