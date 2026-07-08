# P3-2 Phase 2 — Operator Decision Report (Y Residual)

## Diagnosis (from Phase 1)

- Today's 24-h `SL_GATEWAY_REJECT` distribution: **18 rate_limit rejects** from four uncoordinated sources (`trail_update` 14, `sentinel_advisor` 3, `trail_activation` 1) — these are the P3-2 fix surface.
- All four sources go through ONE wrapper: `position_watchdog._push_sl_to_shadow` (lines 785–994). The wrapper does not consult `sl_gateway.next_eligible_in_seconds` before calling `sl_gateway.apply`.
- T2-6 (`profit_sniper_trail` short-circuit) is verified working: 0 rejects today, 83 SKIP intercepts.
- The "26 rejects on profit_sniper_trail" number from the prompt was from a pre-T2-6 sample. T2-6 IS effective. No sniper-side change needed.

Other reject reasons in today's logs are out of P3-2 scope:

- 47 `too_close` (R2) — different gateway rule, not rate-limit.
- 13 `step_exceeded` (R3) — different gateway rule.
- 2 `loosening` (R1) — P3-3 territory, not P3-2.

## Three Fix-Shape Options

| Option | Mechanism | LOC | Risk | Migration |
|--------|-----------|-----|------|-----------|
| **A** (Recommended) | Single pre-check at the top of `_push_sl_to_shadow`. Covers all four sources in one place. Reuses existing `next_eligible_in_seconds`. | ~12 LOC | LOW | None — sources already funnel through this wrapper |
| **B** | Per-source short-circuit at each of the 4 call sites in `position_watchdog`. Allows different policy per source. | ~40 LOC | LOW | 4 call sites |
| **C** | New `should_skip_for_rate_limit` method on `sl_gateway` itself; `_push_sl_to_shadow` calls it. Centralizes logging. | ~10 in gateway + ~2 in watchdog | LOW | None |

## Recommended Option A — Why

- The "Y residual" defect is that one wrapper bypasses an existing gateway accessor. Fix the wrapper.
- All four sources behave identically with respect to rate-limit: they all submit through the same `_push_sl_to_shadow` and they all retry naturally on the next watchdog tick.
- No need for per-source tuning today.
- Observability tag matches T2-6 exactly (`SNIPER_RATE_LIMIT_AWARE_SKIP src=<source>`), so existing dashboards / greps continue to work.
- One commit, one file change, one test addition.

## Implementation Sketch

1. Branch `fix/p3-2-y-residual-coordination` from `audit/all-tier2-combined`.
2. Single commit modifying `position_watchdog._push_sl_to_shadow`:
   - Add the rate-limit short-circuit at the top of the method (after arg validation, before existing coalesce logic).
   - Emit `SNIPER_RATE_LIMIT_AWARE_SKIP | sym=X next_eligible_in_s=N src=<source>`.
   - Return `False` to the caller (same return type as today).
3. Test: a small unit test that constructs a fake `sl_gateway` whose `next_eligible_in_seconds` returns 5 s, calls `_push_sl_to_shadow` for each of the 4 sources, and asserts:
   - `apply()` is NOT called.
   - Log contains `SNIPER_RATE_LIMIT_AWARE_SKIP src=<source>` for each.
   - Helper returns `False`.
4. Merge to audit, restart, verify.

## Verification

After deploy:

- Pull 30+ min of `data/logs/workers.log`.
- Count `SNIPER_RATE_LIMIT_AWARE_SKIP src=trail_update / sentinel_deadline / sentinel_advisor / trail_activation`: > 0 expected.
- Count `SL_GATEWAY_REJECT rsn=rate_limit` from those sources: should drop from 18/24h baseline to 0–2/24h (residual race only).
- The other reject reasons (`too_close`, `step_exceeded`, `loosening`) stay at baseline — different rules, out of P3-2 scope.

## Aim Preservation

- No trade-frequency change.
- No defensive bias.
- Each source's natural retry path (next watchdog tick at 5–10 s) is unchanged.
- Sentinel_deadline's deadline-driven close trigger is separate from the SL move it submits here — deferring the SL by one tick does NOT delay any actual close-decision.
- Shadow unaffected.

Awaiting operator decision on Option A/B/C.
