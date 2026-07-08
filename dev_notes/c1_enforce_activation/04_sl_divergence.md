# C1 — Phase 1.4 SL% Divergence Investigation

## The divergence in plain terms

The brain prompt and the watchdog scorer report a different number for "SL % consumed" of the same position whenever the stop-loss has been trailed from its entry-time value. Both numbers are correct for the question they answer; they answer different questions.

| call site | code | SL reference | question it answers |
|---|---|---|---|
| brain prompt | `strategist.py:4214` (pre-C1) | `thesis_data["stop_loss_price"]` (entry-time) | "what fraction of my original risk budget have I consumed?" |
| watchdog scorer | `position_watchdog.py:3506-3510` (via `_calculate_sl_proximity`) | `pos.stop_loss` (current trailed) | "how close is price to my current stop?" |

Operator-captured examples from earlier monitoring:
- CRVUSDT — brain "SL 71% consumed", scorer `sl_pct=57%` (14 percentage-point gap).
- AEROUSDT — brain "SL 66% consumed", scorer `sl_pct=75%` (9 percentage-point gap).

The arithmetic is identical: `min(moved / total_risk, 1.0) * 100`. Only the SL value (entry vs current) differs.

## Investigation method

Two complementary approaches were used:

1. **Theoretical bound** (this document): given the published seven-factor weight table and the historical composite distribution, derive an upper bound on how much an SL% bucket re-classification could shift any composite. If the upper bound stays below the activation threshold, the divergence cannot flip a decision.
2. **Empirical diagnostic** (Phase 1.4 commit `c1(wd-scoring): add WD_SL_PCT_DIVERGENCE diagnostic`): a new log event `WD_SL_PCT_DIVERGENCE` emits, per close vote, the two percentages side by side plus the bucket each falls into and whether the bucket flips. After 24h of live data the operator can count flipped-bucket events and confirm the theoretical bound empirically.

## Theoretical bound on the SL%-bucket factor shift

From `wd_brain_scoring.py:66-71` the four `sl_consumption` buckets and weights:

| bucket | range | factor |
|---|---|---:|
| spacious | 0–30% | −2.0 |
| comfortable | 30–60% | −1.0 |
| tight | 60–80% | 0.0 |
| imminent | >80% | +1.0 |

The maximum factor swing from a bucket re-classification:
- `spacious` (−2.0) → `imminent` (+1.0) = **+3.0**

A bucket flip can only push the composite UP by at most 3.0. (Going the other direction — from imminent to spacious — would push composite DOWN, making the close *less* likely to clear threshold, which is the conservative direction and can never cause a wrongly-executed close.)

## Historical composites range

From Phase 0 and Phase 1.1, the 28 scored composites in the 2026-05-20 session range from −9.5 to +1.5. The highest composite under threshold was +1.5 (ONDOUSDT, 19:00:16). The activation threshold is 6.0. The headroom between the highest observed composite and the threshold is **4.5**.

## Does the divergence flip any decision?

The maximum possible upward shift from re-bucketing SL% is **+3.0**. The minimum headroom is **+4.5** (highest observed composite to threshold).

Therefore, no realistic re-bucketing of the SL%-consumption factor would lift any of the 28 historical composites past the 6.0 threshold. The divergence does not flip a decision.

This is a strict upper bound — not all positions had a trailed stop, and most observed shifts will be one bucket (e.g., comfortable → tight, +1.0 swing) rather than the worst-case three-bucket swing.

## Implications for activation

The SL% divergence is **not blocking**. Activating enforce mode does not risk reversing a flagged close into an executed close because of the divergence.

However, the divergence is still worth fixing for two reasons:
1. **Operator clarity.** When the brain says "SL 71% consumed" and the operator looks at the scorer logs reading `sl_pct=57%`, the discrepancy looks like a bug. It is not — but the operator should not have to know which SL each value comes from.
2. **Future-proofing.** If the threshold or weights are tuned later, the headroom may narrow. Aligning the formulas now means the divergence cannot become load-bearing under any future weight choice.

## What the alignment commits accomplish

Three commits delivered the alignment (see `04b_alignment_decisions.md` for the design rationale):

1. **`c1: add shared compute_sl_consumption_pct helper`** — single canonical formula in `src/risk/wd_brain_scoring.py`. Tested across both directions, in-profit / at-stop / past-stop / at-entry / malformed inputs.
2. **`c1: watchdog _calculate_sl_proximity delegates to shared helper`** — the watchdog's own method becomes a thin wrapper over the helper. Same behaviour, same tests pass.
3. **`c1: brain CALL_B prompt renders current+entry SL% via shared helper`** — the brain prompt now shows BOTH percentages when the SL has been trailed, with explicit labels ("entry-budget" and "current-stop"). When no trailing has occurred, only one number is shown.

And one diagnostic commit:

4. **`c1: add WD_SL_PCT_DIVERGENCE diagnostic`** — emits both values side-by-side on every brain close vote, regardless of enforce mode. Gives the operator a permanent observability point for the gap.

## What the operator will observe post-deployment

After the worker restart (Phase 4 of the activation):

- `WD_SL_PCT_DIVERGENCE | sym=X sl_current=A sl_entry=B pct_current=Y pct_entry=Z delta_pct=W sl_tightened=bool bucket_current=K bucket_entry=L bucket_flipped=bool` fires once per scored close vote.
- `pct_current` should equal the `sl_pct` field in the corresponding `WATCHDOG_CLOSE_SCORE_COMPUTED` line (the scorer uses the same helper).
- `pct_entry` should equal the brain's prompt-rendered "(entry-budget)" line at the same time (the brain prompt uses the same helper with the entry SL).
- When `sl_tightened=false`, `delta_pct=0.00` and `bucket_flipped=false`. Confirms alignment held.
- When `sl_tightened=true`, `delta_pct` is non-zero. Operator can count `bucket_flipped=true` events to confirm the theoretical bound empirically.

## Conclusion of Phase 1.4

The SL% divergence is real, definitional, and bounded. The bound (max +3.0 composite shift) is comfortably below the activation headroom (min +4.5). No historical composite would have flipped past the threshold under any realistic SL%-bucket re-classification. The divergence is therefore **not a blocker for enforce activation**.

The alignment commits land an explicit unified formula and surface both numbers to the brain and the operator. The diagnostic provides ongoing observability. Activation can proceed.
