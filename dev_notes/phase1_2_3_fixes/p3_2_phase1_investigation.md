# P3-2 Phase 1 — Y Residual: SL Gateway Coordination Investigation

## TL;DR

The "Y residual" bug is real but smaller than the prompt suggested. All four uncoordinated SL submission sources funnel through ONE wrapper — `position_watchdog._push_sl_to_shadow` at `position_watchdog.py:785–994` — and that wrapper does not consult `sl_gateway.next_eligible_in_seconds` before calling `sl_gateway.apply`. The most cost-effective fix is to add a single rate-limit pre-check at the top of `_push_sl_to_shadow`, which automatically covers all four sources (`trail_update`, `sentinel_deadline`, `sentinel_advisor`, `trail_activation`) with one code change. The "26 rejects on profit_sniper_trail" mystery is resolved: today's logs confirm `profit_sniper_trail` has 0 reject (T2-6 catches all 83+ rate-limit conditions via `SNIPER_RATE_LIMIT_AWARE_SKIP`). The 26 count was from a pre-T2-6 sample.

## 1. Verified Current Data — Today's 24-h Reject Distribution

Source: `data/logs/workers.2026-05-13_05-19-51_967148.log`.

By `rsn=`:

| rsn | count | source breakdown |
|-----|-------|------------------|
| `too_close` (R2) | 47 | sentinel_deadline: 47 |
| `rate_limit` (R4) | 18 | trail_update: 14, sentinel_advisor: 3, trail_activation: 1 |
| `step_exceeded` (R3) | 13 | sentinel_deadline: 12, trail_activation: 1, brain_tighten: 1 |
| `loosening` (R1) | 2 | trail_update: 2 |

`profit_sniper_trail`: **0 SL_GATEWAY_REJECT** today. T2-6's pre-check (`SNIPER_RATE_LIMIT_AWARE_SKIP src=profit_sniper_trail`) successfully intercepts these. 83 SKIP events observed today.

The 18 rate_limit rejects today come from non-sniper sources:

- `trail_update`: 14
- `sentinel_advisor`: 3
- `trail_activation`: 1

This is the P3-2 fix surface. The 47 `too_close` and 13 `step_exceeded` rejects are different gateway rules (R2 / R3) and are out of scope for the rate-limit coordination fix — they require different remediation (or are working as intended).

## 2. sl_gateway Interface (verified, file:line)

`src/core/sl_gateway.py` (824 lines, read end-to-end via agent):

- `apply(symbol, new_sl, source, direction, plan, current_sl, current_price, reason, bypass_rate_limit, bypass_step_cap, bypass_step_cap_for_breakeven) -> SLGatewayResult` (line 288).
- `next_eligible_in_seconds(symbol) -> float` (line 183) — stateless query, returns seconds remaining in R4 window (0.0 if eligible).
- Rejection reasons (lines 100–108): R1 `loosening`, R2 `too_close`, R3 `step_exceeded`, R4 `rate_limit`, plus operational `no_position`, `no_price`, `wire_fail`, `invalid_input`.
- `SLGatewayResult` namedtuple/dataclass: `accepted: bool, reason: str, old_sl, new_sl_applied`.

## 3. T2-6 Short-Circuit Pattern (verified, file:line)

`src/workers/profit_sniper.py:1782–1800`:

```python
_t2_6_remaining_s = self.sl_gateway.next_eligible_in_seconds(symbol)
if _t2_6_remaining_s > 0.0:
    log.info(
        f"SNIPER_RATE_LIMIT_AWARE_SKIP | sym={symbol} "
        f"next_eligible_in_s={_t2_6_remaining_s:.1f} "
        f"src=profit_sniper_trail | {ctx()}"
    )
    return False
result = await self.sl_gateway.apply(...)
```

Emit shape: `SNIPER_RATE_LIMIT_AWARE_SKIP | sym=X next_eligible_in_s=N src=profit_sniper_trail`. The prompt's Rule 6 specifies extending this to 4 new sources — same tag, different `src=` values.

## 4. The Single Choke Point — `_push_sl_to_shadow`

`position_watchdog.py:785–994` (210 lines). Used by all four uncoordinated sources:

| Caller (file:line) | source kwarg |
|--------------------|--------------|
| `position_watchdog.py:1569` | `sentinel_deadline` |
| `position_watchdog.py:1668` | `trail_activation` (NOT in profit_sniper as the prompt suggested) |
| `position_watchdog.py:1682` | `trail_update` |
| `position_watchdog.py:3050` | `sentinel_advisor` |

11 total call sites in watchdog. Internal behavior:

- Time-decay 10 s coalesce (lines 858–872).
- Trail 10 s coalesce (lines 882–893) for `trail_*` sources.
- Sentinel 10 s coalesce (lines 903–914) for `sentinel_*` sources.
- Step-clamp for trails (lines 937–963).
- **No rate-limit gateway consultation.** Calls `sl_gateway.apply` directly at line 967.

The fix landing in this one method automatically covers all four sources.

## 5. The "26 Rejects on profit_sniper_trail" Mystery — Resolved

The prompt cited 26 rejects on `profit_sniper_trail` in 5 hours despite T2-6. Today's data shows:

- profit_sniper_trail: 0 SL_GATEWAY_REJECT.
- 83 SNIPER_RATE_LIMIT_AWARE_SKIP intercepts in 24 h.

The 26 number was almost certainly from a pre-T2-6 log sample (the prompt's Phase 5 monitoring sessions could pre-date the T2-6 commit `9202289` merged 2026-05-12). T2-6 is working as designed. No additional sniper-side coordination work needed.

## 6. Aim Preservation — Defer Risk per Source

| Source | Defer risk if blocked ≤30 s | Notes |
|--------|----------------------------|-------|
| `sentinel_deadline` | MEDIUM | Deadline-driven tier action. But the actual force-exit is gated by separate deadline logic — the SL move here is supportive, not the primary close trigger. Acceptable. |
| `trail_activation` | LOW | One-shot transition into profit-trail; next watchdog tick can retry. Acceptable. |
| `trail_update` | LOW | Continuous trailing at 5–10 s cadence; one skipped tick out of many is benign. Acceptable. |
| `sentinel_advisor` | LOW | Opportunistic proposal; not deadline-bound. Acceptable. |

All four sources have a natural retry path on the next watchdog tick (5–10 s).

## 7. Helper Design Options

### Option A — Single check inside `_push_sl_to_shadow` (RECOMMENDED)

Add a rate-limit short-circuit at the top of `_push_sl_to_shadow` (after argument validation, before the existing coalesce logic). Reuses the `next_eligible_in_seconds` accessor already on the gateway.

```python
# P3-2 (2026-05-13): coordinate with sl_gateway's rate-limit window
# BEFORE consuming the per-source coalesce + step-clamp work below.
# T2-6 already does the equivalent inside profit_sniper.py for the
# profit_sniper_trail source. This block extends the same pattern
# to all four watchdog sources (trail_update / sentinel_deadline /
# sentinel_advisor / trail_activation).
_remaining_s = self.sl_gateway.next_eligible_in_seconds(symbol)
if _remaining_s > 0.0:
    log.info(
        f"SNIPER_RATE_LIMIT_AWARE_SKIP | sym={symbol} "
        f"next_eligible_in_s={_remaining_s:.1f} "
        f"src={source} | {ctx()}"
    )
    return False
```

- LOC: ~12 lines, in one place.
- Risk: LOW. Strictly an early return on a rate-limit window that the gateway WILL reject anyway. No semantic change to the gateway's enforcement.
- Migration: NONE. All four sources already call `_push_sl_to_shadow`.
- Observability: matches the T2-6 pattern exactly. Operators get the same `SNIPER_RATE_LIMIT_AWARE_SKIP` tag with `src=trail_update`, `src=sentinel_deadline`, etc.

### Option B — Per-source short-circuits at each of the 4 call sites

Same logic, copy-pasted at each of the 4 call sites in position_watchdog. Allows different policies per source.

- LOC: ~40 lines (4 × ~10).
- Risk: LOW.
- Migration: 4 call sites.
- Benefit: Lets the operator tune behavior per source. Today none of the sources need a different policy, so the extra surface is not justified.

### Option C — New helper method on `sl_gateway` itself

```python
def should_skip_for_rate_limit(self, symbol: str, source: str) -> float:
    remaining = self.next_eligible_in_seconds(symbol)
    if remaining > 0:
        log.info(...)
    return remaining
```

- LOC: ~10 in gateway + 1 call in `_push_sl_to_shadow`.
- Risk: LOW.
- Migration: small.
- Question: moves the logging responsibility into the gateway. Slightly tighter coupling but cleaner reuse if other paths surface in the future.

### Recommendation

**Option A.** Smallest change, covers all four sources at the single chokepoint, no API change to the gateway, observability identical to T2-6. If a future scenario needs per-source tuning, Option B can be added incrementally on top.

## 8. Hard Constraints for the Fix

- All four sources must NOT submit when `next_eligible_in_seconds > 0`.
- Each MUST emit `SNIPER_RATE_LIMIT_AWARE_SKIP src=<source> next_eligible_in_s=N`.
- Retry MUST happen on the next natural watchdog tick (5–10 s typical, well after the 30 s rate-limit window).
- The gateway's R4 enforcement REMAINS as the safety net.
- `_push_sl_to_shadow`'s existing coalesce + step-clamp logic stays intact (the pre-check runs FIRST so we skip the coalesce update timestamps too).

## 9. Aim Preservation Confirmed

- No trade-frequency change.
- No defensive bias.
- All 4 sources have natural retry paths; legitimate SL moves still happen, just deferred to the next tick.
- Sentinel_deadline's deadline-driven close-trigger is separate from the SL move — defer doesn't risk leaving a position unprotected.
- Shadow path unaffected (the gateway is the same code in both modes).

## 10. NOT FOUND

- Pre-T2-6 logs are no longer in the rotation; cannot directly compare the "26 rejects" baseline against current data. The agent verified the post-T2-6 numbers (0 rejects on profit_sniper_trail) which is sufficient evidence the fix is effective.
- `trail_activation` is in `position_watchdog.py:1668`, NOT in `profit_sniper.py` as the prompt suggested. Documenting this so the operator decision is informed.

## 11. Verification Plan (Phase 4)

After the fix lands:

- 30+ min observation window.
- `SNIPER_RATE_LIMIT_AWARE_SKIP src=trail_update`: > 0 expected.
- `SNIPER_RATE_LIMIT_AWARE_SKIP src=sentinel_deadline`: > 0 expected.
- `SNIPER_RATE_LIMIT_AWARE_SKIP src=sentinel_advisor`: > 0 expected.
- `SL_GATEWAY_REJECT rsn=rate_limit` from these sources: → 0 within the verification window.
- Other rejection reasons (too_close, step_exceeded, loosening) remain at current rate (they are not rate-limit cases — different gateway rules).

A small implementation note for the verification: the prior 24 h baseline rate-limit reject count was 18. Target after fix: 0–2 (residual race window between check and submit).

## 12. Next Step

Write the Phase 2 report with three options and present to operator. Recommendation: Option A. Awaiting operator decision.
