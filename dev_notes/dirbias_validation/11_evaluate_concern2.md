# Phase 2.2 — Concern 2: Does Issue 2 Option A (regime-concentration adaptive multiplier) violate the operator directive?

## Concern restated

Issue 2 Option A proposes: replace scalar `counter_confidence_multiplier = 0.7` with a function of universe regime concentration. When the universe is 50/50, the multiplier stays at 0.7 (baseline). When the universe → 100% one direction, the multiplier → 1.0 (rare-direction signals get full conviction).

Proposed formula (from prior report Section 3.6 Option 2.A):
```
effective_multiplier = baseline + correction * concentration
where concentration = max(long_pct, short_pct)  # in [0.5, 1.0]
```

The operator's design directive (2026-05-17): *"sell and buy should be both work according to the best scenarios, not hard coded saying if sell this much then buy this much not like that."*

The concern: Option 2.A literally hardcodes "if universe is X% one direction, force more of the other." That contradicts the directive.

## Evaluation

### Is the proposal a hardcoded asymmetric correction?

**YES, by construction.** The formula reads:
- Detect concentration (a measurement).
- When concentration is high, INCREASE counter-direction confidence.
- This is the textbook "if universe is X% one direction, force more of Y" pattern.

### Does the proposal violate the directive?

The directive says asymmetry must emerge from **data and scenario**, not from **direction-specific hardcoded numbers**.

Option 2.A:
- Uses universe concentration (a data point) — pro-directive.
- Applies a correction formula with a hardcoded `correction` parameter — anti-directive.
- The intent is "when one direction is rare, value it more" — this IS scenario-driven IN SPIRIT.
- But the implementation hardcodes the rebalancing function — anti-directive IN LETTER.

### How does this compare to R4 (rejected portfolio direction cap)?

| Aspect | R4 portfolio cap | Option 2.A |
|---|---|---|
| Trigger | Portfolio direction concentration > 70% | Universe regime concentration > X |
| Action | Block trade in dominant direction | Inflate counter-direction confidence |
| Mechanism | Hard reject | Soft multiplier |
| Hardcoded? | YES (70% threshold) | YES (correction coefficient + formula shape) |

Same conceptual structure. R4 was rejected. Option 2.A is a softer version of R4 but still hardcoded asymmetric correction.

### Counter-argument from prior report

The prior report's reasoning (Section 3.6 Option 2.A): "the asymmetry between counter and in-direction confidence shrinks when the universe is heavily biased toward one direction." This is operator-intent-aligned — counter signals SHOULD be valued more when they're rare.

But the implementation puts a number on "more" via the `correction` coefficient. The operator's directive is about HOW the asymmetry is encoded — even if the INTENT is scenario-driven, the CODE is still hardcoded.

### Is there a directive-compliant version?

A genuinely directive-compliant version would let the asymmetry emerge from data WITHOUT a hardcoded correction function. Examples:
- Compute the per-direction historical WR from trade_log (as R3 already does). Use that to scale confidence: high-WR direction gets higher confidence, low-WR gets lower. No hardcoded "correction" parameter.
- This is essentially what R3 does for the XRAY override threshold — and the operator accepted R3.

R3 vs Option 2.A:
- R3: threshold = wr_base × (1 - wr/100). Direction-symmetric formula; wr is measured.
- Option 2.A: multiplier = baseline + correction × concentration. Direction-symmetric formula; concentration is measured.

Hmm — they're structurally similar. Both use a measured scenario variable to derive an asymmetric scaling. Why is one acceptable and one not?

Possible distinction:
- R3's scenario variable (per-direction WR) is a PERFORMANCE measurement (data-driven, ground truth).
- Option 2.A's scenario variable (universe regime concentration) is a STATE measurement (not data-driven, not ground truth — it's the same input the bias is operating on).

Option 2.A is rebalancing AGAINST the data, not WITH it. That's the directive violation.

### Empirical check

In current data:
- Regime concentration = 1567 / (1567 + 176) = 90% trending_down.
- Option 2.A with `correction = 0.3` would give effective multiplier = 0.7 + 0.3 × 0.9 = 0.97 (almost 1.0).
- Effect: counter-direction confidence becomes nearly un-cut.

Net: in highly-concentrated markets, Option 2.A makes counter trades much more attractive. This INCREASES contrarian trades when the universe is biased.

If the universe is biased because the market is genuinely trending, increasing contrarian trades = more counter-trend losses. The 14-day data shows both directions below 50% WR — adding contrarian counter trades doesn't obviously help.

If the universe is biased due to a bias in the regime detector, Option 2.A would mask the detector bug — same band-aid pattern.

## Verdict

**VALID.** Option 2.A violates the operator's design directive. The formula is direction-symmetric in code, but it encodes a hardcoded "if universe X% one direction, force more counter" rebalancing. The right form of "scenario-driven asymmetry" is **performance-data-driven** (like R3 using per-direction WR), not **state-data-driven** (rebalancing against the measurement that the bias is responding to).

## Recommendation

- **Reject Option 2.A**. It does not meet the operator's design directive.
- The genuinely directive-compliant Issue 2 fixes are:
  - Option 2.B (split direction-confidence from size-confidence): preserves suppression but only at sizing layer.
  - Option 2.E (remove ×0.7 entirely — Concern 7): no correction at all; let WR data and downstream sizing decide.
  - Option 2.D (operator-tunable calibrated multiplier based on historical counter WR): performance-data-driven, like R3.
- Most aligned with directive: **Option 2.D** or **Option 2.E**. Option 2.B is a refactor that preserves the hardcoded 0.7 in a different field — better than 2.A but still has a hardcoded number.

## Implications for fix path

- Drop Option 2.A from Path A and B.
- Prefer Option 2.E (config-only test of removal) as the empirically-cheapest first step. If WR doesn't degrade, ratify with code removal.
- Fall back to Option 2.D (data-calibrated multiplier) only if removal degrades WR — then the multiplier is justified by data.
- Option 2.B (field split) is acceptable for code hygiene but only as a structural cleanup, not as the "fix" for the bias.
