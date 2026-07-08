# T1-2 Phase 1 — F8 Trail SL never advances investigation

## 1. Defect statement

The position watchdog's `trail_activation` and `trail_update` callers submit SL changes whose step_pct exceeds the SL Gateway's `max_step_pct` cap of 0.25%. Today's evidence shows raw_step_pct values of 1.032 to 1.248 — 4.1x to 5.0x the cap. Every such submission is rejected with `SL_GATEWAY_REJECT rsn=step_exceeded`. Result: trailing stop never advances on legitimate price movement; the trail SL only catches the price when profit_sniper's smaller-step adjustments (which DO clamp before submitting) happen to slip through.

Tier 0 baseline (workers.log 11:55-14:45): 10 step_exceeded rejects, all from `trail_activation` or `trail_update`. Zero from profit_sniper-* sources. Cap-exceeding ratios today (4.1x-5.0x) are LARGER than the report's 2.04x-2.27x — issue is chronic and worsening.

## 2. Why this matters

The trailing stop is the system's primary peak-profit catcher. With trail SL not advancing:

- Static TP (set once, never adjusts) is the only profit ceiling.
- Sniper's smaller-step adjustments do advance the SL but are also rate-limited (F5).
- Brain CALL_B `tighten_stop` is the slow path (150s cadence).

None of these three can replace the trailing stop. The aggressive-exploitation philosophy depends on letting winners run with a trail that catches near-peak; today the trail is effectively non-functional.

## 3. The two parallel SL paths in the codebase

The SL Gateway (`src/core/sl_gateway.py`) is the single validator. Two upstream callers compute trail steps and submit to it:

### Path A — profit_sniper.py (lines 1469-1525)

profit_sniper ALREADY clamps before submitting:

```python
# profit_sniper.py:1481-1482
gw_cfg = getattr(self.settings, "sl_gateway", None)
max_step_pct = float(getattr(gw_cfg, "max_step_pct", 0.5)) if gw_cfg else 0.5

# profit_sniper.py:1509-1524
if cur_sl is not None and cur_sl > 0:
    requested_step_pct = round(abs(new_sl_candidate - cur_sl) / cur_sl * 100.0, 6)
    if requested_step_pct > max_step_pct:
        if trail.direction in ("Buy", "Long"):
            capped = cur_sl * (1.0 + max_step_pct / 100.0)
        else:
            capped = cur_sl * (1.0 - max_step_pct / 100.0)
        log.info(
            f"SNIPER_CAP | sym={symbol} requested={requested_step_pct:.3f}% "
            f"capped={max_step_pct:.3f}% new_sl={capped:.8f} "
            f"raw_new_sl={new_sl_candidate:.8f} cur_sl={cur_sl:.8f} "
            f"dir={trail.direction} | {ctx()}"
        )
        new_sl_candidate = capped
```

Result: profit_sniper's submissions never trigger gateway R3 step_exceeded. They DO trigger R4 rate_limit (separate F5/T5-3 issue), but step is clean.

### Path B — position_watchdog.py `_push_sl_to_shadow` (lines 772-889)

This helper is the single point of truth for SL propagation from the watchdog. It is called by 7 callsites in position_watchdog.py:

- `trail_activation` (line 1514)
- `trail_update` (line 1528)
- `sentinel_deadline` (line 1415)
- `sentinel_advisor` (line 2880)
- `STRAT_ACTION_SL` strategic-action paths (lines 1348, 1890, 1919, 2377)

`_push_sl_to_shadow` has guards for:
- No-op (already at the value): line 820-836
- Time-decay consumer-side coalescing (10s window): line 845-859

But it does NOT clamp step_pct before submitting to the gateway. The full computed `new_sl` from `plan.trailing_stop_price` (or wherever) flows straight through.

The trail-update flow (watchdog.py:1517-1529):

```python
if plan.trailing_active:
    old_trail = plan.trailing_stop_price
    plan.update_trailing(current_price)  # <-- computes new trailing SL from current_price
    if plan.trailing_stop_price != old_trail:
        await self._push_sl_to_shadow(
            symbol=pos.symbol,
            new_sl=plan.trailing_stop_price,  # <-- UNCLAMPED, may be 1-2% jump
            ...
            source="trail_update",
        )
```

`plan.update_trailing(current_price)` updates `trailing_stop_price` based on a trail distance from the current market price. If price has moved 1.5% since the last update, the new trailing_stop_price is ~1.5% off the previous. The watchdog submits that to the gateway, which rejects on R3 (step > 0.25%).

## 4. Gateway R3 cap semantics

Gateway code at `src/core/sl_gateway.py:437-458`:

```python
# R3 Max-step relative to previous SL.
step_pct = 0.0
if current_sl is not None and current_sl > 0:
    step_pct = round(abs(new_sl - current_sl) / current_sl * 100.0, 6)
    if not bypass_step_cap and step_pct > cfg.max_step_pct:
        ...emit SL_GATEWAY_REJECT rsn=step_exceeded...
        return SLGatewayResult(accepted=False, reason=REASON_STEP_EXCEEDED, ...)
```

step_pct is computed as percent of previous SL. With `max_step_pct=0.25` (config.toml:533) and previous SL at 0.029009 and new at 0.028651:

```
step_pct = round(abs(0.028651 - 0.029009) / 0.029009 * 100.0, 6)
         = 1.234%
1.234 > 0.25 -> REJECT
```

The gateway HAS a `bypass_step_cap: bool = False` parameter (line 244) docstring says "For urgent Time-Decay force-exits" — but no caller in the codebase currently passes True. The gateway's R3 cap was intentionally set tight to prevent rogue large jumps (gateway docstring lines 5-11 cites the RIVERUSDT 2.5% one-shot strangulation incident as the cause for adding R3 in the first place).

## 5. Root cause

Asymmetric clamping: profit_sniper clamps before submission; watchdog does not. Both should clamp at the source so the gateway's R3 cap only catches BUGS in the caller (rogue computations), not legitimate trail catch-up.

The fix is a pure port-forward of the SNIPER_CAP pattern into `_push_sl_to_shadow`. Both paths then share the same clamping discipline; gateway R3 becomes the safety net it was designed to be rather than the production blocker it is today.

## 6. Architectural Theme 1 cross-link (T5-3 scope)

The prompt notes Architectural Theme 1: "Multiple components write to the same SL slot. profit_sniper_trail, trail_activation, trail_update, sentinel_advisor all submit to sl_gateway. One should be writer-of-record; others advise." The 8 sources today (5 from profit_sniper, 3 from position_watchdog) all submit independently and compete for the per-symbol R4 rate-limit slot — causing F5 thrash.

The T1-2 fix as scoped here addresses F8 (step_exceeded) only — clamping at the source so trail steps don't exceed the cap. It does NOT restructure the multi-writer architecture; that work belongs in T5-3 (F5 rate-limit thrash) per the plan. T5-3 will benefit from T1-2's clean step-clamping foundation but is a separate, larger change.

## 7. F5 / T5-3 partial mitigation

After T1-2 lands, trail steps will be smaller and submitted more often (10 incremental 0.25% steps instead of one rejected 2.5%). This INCREASES the rate of accepted submissions, which may exacerbate F5 rate-limit thrash for trail sources. Mitigation options for T1-2:

- Trail-update self-coalescing similar to time-decay's 10s window at `_push_sl_to_shadow:845-859`. The R4 rate-limit is 30s; a 10s consumer-side coalesce on trail sources would prevent thrash while letting trail catch up over time.
- Defer to T5-3's full coalescing design. T1-2 ships the step-clamp; T5-3 adds the coalescing.

## 8. Investigation conclusions

1. profit_sniper has SNIPER_CAP; position_watchdog does not. The asymmetry IS the root cause of F8 step_exceeded.
2. Porting SNIPER_CAP into `_push_sl_to_shadow` fixes F8 for ALL watchdog callers (trail_activation, trail_update, sentinel, strategic actions) in one place.
3. F5 rate-limit thrash is a separate architectural issue (Architectural Theme 1); fix in T5-3.
4. After T1-2, gateway R3 becomes a safety net for genuine bugs in step computation, not a production blocker.
5. Hard constraint: trail SL MUST advance on profitable runs after the fix. Smoke test confirms by simulating a price move that previously caused step_exceeded; after fix, two clamped 0.25% steps land where the rejected 1.2% step would have.

Phase 2 proposal follows.
