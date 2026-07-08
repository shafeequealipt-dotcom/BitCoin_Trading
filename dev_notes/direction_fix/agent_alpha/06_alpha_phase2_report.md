# Agent ALPHA — Phase 2 Operator Report (R1: XRAY Counter-Trade Inversion)

This report is the operator-facing decision artifact for Agent ALPHA's
investigation. It summarises what the spec assumed versus what the code does,
states the production evidence, presents the top three options ranked, and
asks for the operator's decision.

## What the spec assumed vs what the code does

The spec described R1 as: "These are bullish FVG/OB structures detected
against a trending_down regime. The counter-trade logic collapses them to
`suggested_direction=short`. So bullish structural setups get re-labeled as
Sell candidates."

The actual code, verified against the 2026-05-16 production log, does NOT
work that way. The counter-trade logic does NOT mutate `suggested_direction`.
That field is set once per `analyze()` call from market_structure (uptrend →
long, downtrend → short) and is not touched again. The counter logic sets a
SEPARATE field, `trade_direction`, to the OPPOSITE of `suggested_direction`,
explicitly to record the contrarian trade payoff while leaving the regime
label intact. The brain prompt reads `trade_direction` (the inverted value);
the APEX optimizer reads `suggested_direction` (the regime value). The split
was intentional design, introduced in commit `3a59637` (Phase 4 of the
xray-counter feature) on 2026-04-30 with the explicit goal of broadening the
opportunity surface (66% of pre-Phase-4 NONE coins had counter-direction
zones near price and were being discarded).

The bias evidence remains valid, but the mechanism is different. The bias is
real because APEX_DIR_LOCK reads the regime-derived suggested_direction (87%
short on this session) and applies a hard lock; the inverted trade_direction
from counter setups never reaches APEX. So the brain may pick Buy on a
counter signal, and APEX still locks the trade to Sell. This is a cross-layer
information loss between XRAY (Layer 1B) and APEX (Layer 3) at
`src/apex/assembler.py:737`.

## Production evidence (2026-05-16 13:40-18:30 session)

XRAY_CLASSIFY events total: 2,644 (exact match to COMPLETE_FINDINGS).

Setup type breakdown:

- bearish_fvg_ob: 1,531 (in-direction Sell, both fields = short)
- bullish_fvg_ob_counter: 691 (Phase 4 counter, suggested=short, trade=long)
- bullish_fvg_ob: 313 (in-direction Buy, both fields = long)
- bearish_structural_break: 74 (in-direction Sell)
- bearish_fvg_ob_counter: 35 (Phase 4 counter, suggested=long, trade=short)

All 691 bullish-counter events emit `trade_direction=long
suggested_direction=short`. All 35 bearish-counter events emit the mirror.
Zero variance.

Aggregate field distributions:

- `suggested_direction` (regime label): 2,296 short / 348 long = 86.8% short.
  This matches the COMPLETE_FINDINGS "87% short" headline.
- `trade_direction` (setup payoff, what the brain prompt reads): 1,640 short
  / 1,004 long = 62.0% short / 38.0% long.

The brain receives a notably less-biased input than the 87% figure suggests.
Whatever bias the brain expresses (89% Sell directives) is NOT entirely
attributable to XRAY direction inversion. The 24-percentage-point gap
between trade_direction and suggested_direction is what counter logic
contributes — and the brain does see it.

## Three options ranked

### Option E — APEX reads trade_direction (recommended)

Pipe the inverted `trade_direction` field from `StructuralAnalysis` through
`src/apex/assembler.py:737` into `StructuralData`, then have APEX's
`_check_direction_lock` consume it. APEX would then see the same direction
the brain sees on counter setups, and the regime-only lock would not
suppress structurally-supported counter trades.

- Code change: adds one field to `src/apex/models.py:StructuralData`, one
  assignment in `assembler.py:737`, and BETA-side consumption in
  `optimizer.py`'s lock.
- Brain prompt: unchanged.
- Sell/Buy impact: strongest expected impact. With 691 counter-LONG signals
  per session no longer blocked by regime lock, many would survive to
  becoming Buy orders.
- Risk: medium. Counter setups carry 0.7x confidence; allowing them to
  override APEX_DIR_LOCK lets lower-quality entries through. Mitigation:
  require `setup_type_confidence >= 0.40` floor on the consumption side
  (BETA's territory). Existing `_counter_alignment` already excludes the
  worst case (long-counter on uptrend).
- Aim-bias evaluation: all five YES.
  1. Frequency preserved (additive, no rejections).
  2. Aggression preserved (un-locks structurally supported counters).
  3. Decision quality improved (APEX and brain agree on the structural
     input).
  4. Passive-close advantage preserved (no change to close paths).
  5. Separation of concerns respected (the trade_direction field is XRAY's
     output for trade-payoff; APEX is its rightful consumer).
- Cross-agent dependency: requires BETA to consume the field in
  optimizer.py during the same Phase 3 window. DELTA must sequence so
  ALPHA's plumbing lands before BETA's consumption.

### Option D — observability of trade_dir splits (free win, ship alongside E)

Add `trade_dir_long=N trade_dir_short=N counter_count=N` to the
XRAY_CLASSIFY_SUMMARY log line, plus a new XRAY_DIRECTION_SPLIT metric line
per tick. No behavior change. Just shows the operator what the brain
actually receives (62%/38%) versus the regime label (87%/13%).

- Aim-bias: all five YES (purely observational).
- Impact on Sell/Buy: zero. Improves the operator's mental model.
- Risk: zero.
- Complexity: 1/5 (~10 lines).

### Option C — operator-tunable counter annotation strength (defer)

New config `counter_brain_annotation_strength` controls whether the brain
prompt annotation says "lower conviction" (current default), "balanced", or
"high conviction". Default unchanged; operator can tune up if data supports
it.

- Aim-bias: 4/5 (decision quality contingent on correct tuning).
- Impact on Sell/Buy: depends on operator tuning.
- Risk: medium if operator removes the conviction reduction without trial
  data on counter-trade WR.
- Recommendation: defer to Phase 5 (post Option E live trial) when we have
  real counter-trade WR data.

## ALPHA's recommendation

Ship Option E and Option D together on `fix/r1-xray-counter-inversion`.
Defer Option C to a future tuning sweep. Reject Option A (null —
unaddressed) and Option B (plumbing only without consumption — dead code).

The recommendation is grounded in the project aim:

- The aim is aggressive opportunity exploitation with balanced direction
  trading. Currently APEX blocks structurally supported Buy entries because
  it cannot see the counter-trade signal. Option E un-blocks them.
- The fix removes a bias mechanism without adding a caution mechanism. No
  trades are rejected; some that were rejected become permitted.
- The fix respects the layer doctrine. XRAY publishes; APEX consumes; the
  brain reads the brain's input. No layer boundary is crossed.

Cross-agent dependency: Option E requires BETA's optimizer.py change in the
same Phase 3 window. DELTA's sequencing recommendation should put ALPHA's
plumbing change first (so BETA's consumer code has something to consume).

## Decision request

The operator needs to choose between:

1. Option E + Option D — strong fix, cross-agent with BETA.
2. Option D only — observability only, full fix deferred until BETA's
   optimizer.py is ready.
3. Option A — null (do nothing on the ALPHA side).
4. Some combination requested by the operator.

Once the operator chooses, ALPHA proceeds to Phase 3 implementation on
`fix/r1-xray-counter-inversion`. DELTA receives the choice as input to
synthesis and integration sequencing.

## Notes for the operator

The Phase 0 drift resolution is the most important finding here. The R1
mechanism the spec described does not exist. The actual cross-layer
information loss between XRAY and APEX is what the fix should target. This
also has implications for BETA (R2/R3): BETA's
`_check_direction_lock` currently operates on `suggested_direction` (regime).
If Option E lands, BETA can write a much smarter lock that respects both
regime AND counter-evidence.

The bias chain in COMPLETE_FINDINGS — L4 81% Buy → Brain 89% Sell — is NOT
explained by XRAY alone. XRAY's trade_direction is 62%/38%, much closer to
balanced. The brain's 89% Sell must be explained by other inputs the brain
sees (regime line, state labels, ensemble consensus, sentiment, etc.) AND
by APEX overriding the brain's choices (R2/R3 territory, BETA's scope).

Operator can verify any of the above with the verification queries in
`05_alpha_synthesis.md`, run on the same production log.
