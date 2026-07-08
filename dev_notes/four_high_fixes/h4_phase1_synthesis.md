# H4 Phase 1 — Synthesis (Root Cause + Recommendation + Aim-Bias Verdict)

## Root cause finding

Of the 31 `reentry_learning_gate_same_conditions` blocks in the 5h07m baseline window:

- **31 / 31 (100 %)** show the gate's categorical equivalence `(regime, setup_type, direction)` matching the prior losing thesis exactly — bucket (b), gate too coarse.
- **8 / 31 (26 %)** cite a prior loss closed **more than 16 hours ago** (SKR×4, LDO×2, DYDX×1, ARB×1) — bucket (c), no recency clamp.
- **8 / 31 (26 %)** cite a prior loss of **less than $2 magnitude** (ARB $-0.04, AVAX $-1.03 ×3, SKR $-1.57 ×4) — bucket (b'), no magnitude floor.
- **22 / 31 (71 %)** trace to a single 2-second exit cohort at 2026-05-16 07:41 (XRP/LINK/SEI/AVAX all bumped together) — bucket (b), no market-drift escape.

**The dominant root cause is R1: the J6 reentry learning gate's `same_conditions` definition is over-coarse and time-unbounded.** Three orthogonal failure modes inside R1:
1. No recency clamp — a single losing thesis blocks indefinitely.
2. No magnitude floor — fee-noise losses block as effectively as material losses.
3. No market-drift escape — the same prior loss is cited 5+ hours later when ATR / structure / price level have all moved.

R5 (brain has no rejection feedback) is a secondary contributor — brain re-selects XRP/LINK/SEI rather than rotating. But R5 alone cannot fix the 8 stale-prior-loss blocks (those would persist with or without brain visibility, because brain can legitimately want to trade DYDX even if it doesn't know about a 60-hour-old loss).

R2 (candidate pipeline narrowness) and R3 (brain selection bias) are present but minor — Layer 1B/1C is not in scope per spec, and addressing them does not move the gate's coarse equivalence problem.

## Recommended option

**Option R1 — Gate calibration (primary).** Three additive refinements to `check_reentry_learning_gate`:

1. **Recency clamp (settings.apex.reentry_learning_gate_lookback_hours, default ~4-6 h).** Bound the SQL `LIMIT 1` query by `closed_at > now() - lookback_hours`. Losses older than the lookback do not trigger blocks.

2. **Magnitude floor (settings.apex.reentry_learning_gate_min_loss_usd, default ~$3-5).** Filter the query with `actual_pnl_usd < -min_loss_usd`. Losses smaller than the floor (fee noise) do not trigger blocks.

3. **Market-drift escape (per-symbol ATR shift).** Allow re-entry if current 1H or 5M ATR has shifted ≥ X % since the prior loss. Threshold via `settings.apex.reentry_learning_gate_atr_drift_pct` (default ~20-30 %). Cheap to consume: ATR is in the existing TACache. Requires reading the prior thesis's `entry_atr` (already persisted as `entry_volatility` per schema v26+ — verify Phase 2).

These three escapes are additive (any one passes ⇒ allow). Combined, they preserve the gate's original intent (block genuinely-fresh, genuinely-meaningful, genuinely-same-context re-entries) while eliminating the over-blocking pattern.

**Option R5 — Brain visibility (optional secondary).** If Phase 4 verification of R1 alone shows rejection rate still above ~15-20 %, layer R5 on top: surface a `## RECENT REJECTIONS (last 30 min)` block in CALL_A using the E6 pattern. Persist rejections to a small table or in-memory ring buffer. Brain reads the list and rotates picks accordingly. R5 by itself was the original H4 framing; the operator's redirect explicitly rules R5 out as the primary fix.

**Reject R2 / R3** for this engagement: out-of-scope per spec and not the dominant root.

## Aim-bias evaluation (R1 primary)

| Question | Answer | Reasoning |
|---|---|---|
| 1. Preserves trade frequency? | **YES (rises)** | Forecast: 31 → ~5 rejections in 5 h (bucket b'+c contributions eliminated, plus most of the 07:41 cohort blocks after market drifts) → 5-10 more trades per hour open. |
| 2. Preserves aggression? | **YES** | Removes the artificial brake on aggressive exploitation. Brain proposing the same symbol is no longer blocked unless the conditions are *genuinely* fresh + meaningful + drifted. |
| 3. Improves decision speed or quality? | **YES (quality)** | Decisions are not made faster (this is gate calibration, not pipeline speed), but they are made on a non-stale state — re-entries on legitimately-shifted markets execute. |
| 4. Preserves passive-close advantage? | **YES (no impact)** | Gate is open-side. Passive close path (watchdog, profit sniper, time-decay SL) is unaffected. |

All four answer YES. Aim is fully preserved; in fact, aim is *better served* than before (frequency rises, aggression unleashed).

## Aim-bias evaluation (R5 optional secondary)

| Question | Answer | Reasoning |
|---|---|---|
| 1. Preserves trade frequency? | YES (neutral or slight rise) | Brain rotates away from blocked names; freed slots get used on fresh names. |
| 2. Preserves aggression? | YES (neutral) | Brain still proposes 2-3 trades; just different ones. |
| 3. Improves decision speed or quality? | NEUTRAL (slight prompt growth, ≤ 500 chars) | Quality improves marginally; speed not affected unless prompt size pushes CALL_A latency. |
| 4. Preserves passive-close advantage? | YES (no impact) | Open-side only. |

Aim preserved; secondary because R1 must land first.

## Trial behaviour (Rule 16)

After R1 lands:

1. Gate continues to fire on `(regime, setup, direction)` triple-match BUT only when:
   - The prior loss closed within `lookback_hours` (default 6 h).
   - The prior loss exceeded `min_loss_usd` (default $5).
   - ATR has not shifted by `atr_drift_pct` (default 25 %) since the prior loss.
2. New observability events fire:
   - `GATE_RECALIBRATION_ALLOW | sym=... reason=lookback_expired prior_loss_age_h=... prior_pnl=... | ...`
   - `GATE_RECALIBRATION_ALLOW | sym=... reason=loss_below_floor prior_pnl=-1.03 floor=-5.00 | ...`
   - `GATE_RECALIBRATION_ALLOW | sym=... reason=atr_drift_passed atr_then=... atr_now=... pct=...% threshold=... | ...`
   - `GATE_RECALIBRATION_BLOCK | sym=... ... (current "block" log retained for compat) | ...`
3. Brain proposes XRP/LINK/SEI — these now mostly pass the gate (if conditions truly drifted). Trade frequency rises. Win-rate must not regress beyond 1-2 pp (R1 is calibration, not gate-removal).

## Verification metrics (Rule 11)

24h soak after R1 deploy:

| Metric | Baseline (today, 5h) | Target after R1 |
|---|---|---|
| `same_conditions` block count | 31 in 5h (~6.2/hr) | < 10/hr; ideally < 5/hr |
| Rejection rate | 57.4 % | < 30 % (spec target); ideally < 20 % |
| Per-symbol repeat-rejection top-5 | XRP 8, LINK 6, SEI 5 | Each ≤ 2 |
| `GATE_RECALIBRATION_ALLOW reason=lookback_expired` | 0 | ≥ 8 over 24h (the 8 stale ones in the baseline) |
| `GATE_RECALIBRATION_ALLOW reason=loss_below_floor` | 0 | ≥ 8 over 24h |
| `GATE_RECALIBRATION_ALLOW reason=atr_drift_passed` | 0 | ≥ 10 over 24h |
| Trade frequency (BRAIN_DO_TRADE rsn=ok per hour) | ~4.5/hr | HOLD or RISE (target ≥ 5/hr) |
| Win rate (24h closures) | 78.6 % session, ~baseline 50 %+ | HOLD (≥ 50 %) |
| CALL_A median latency | 102 s | Unchanged (R1 does not touch brain pipeline) |
| DB cascade events | 0 | 0 |
| Shadow path | working | working |

If after 24h the rejection rate sits at 20-30 %, that residual is brain-side (R5). Operator can ratify R5 as a follow-on.

## Recommend option

**Recommend R1 (gate calibration with recency / magnitude / ATR escapes) because the evidence shows 100 % of blocks are gate-coarseness driven, 26 % are stale-loss driven, 26 % are noise-magnitude driven, and 71 % are single-cohort-tail driven. Brain visibility (R5) cannot fix any of those — it can only help brain rotate within blocks that R1 should have allowed in the first place. R1 is aim-positive on all four questions. Awaiting operator approval.**
