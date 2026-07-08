# Phase 2.5 — Concern 5: Ship Issue 4 alone first, run 48h, measure

## Concern restated

The senior reviewer suggested an experimental approach instead of the 17-23 day all-up commitment:
- Ship Issue 4 alone first (~80 LOC, 1-2 days, LOW risk).
- Run 48 hours in production.
- Measure direction distribution at brain output.
- Decide: if brain shifts from 92% Sell to 60-70%, Issue 4 was the dominant cause and Issues 1, 2, 3 may be much smaller than feared. If brain stays at 85-90% Sell, the upstream issues are real and bigger fixes are justified.

## Evaluation

### Theoretical basis for expecting Issue 4 alone to shift the brain

The Issue 4 fix replaces the asymmetric MARKET REGIME block at `strategist.py:3371-3390`:
- Asymmetric `direction_hint` dict (trending_down → "DEFAULT SELL BIAS", trending_up → "BUY preferred") → symmetric paired directives.
- Trending_down-only conditional NOTE → mirrored NOTE on both trending_down and trending_up at confidence > 0.60.
- Header "## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)" → softer "## MARKET REGIME (CONTEXT)".

Mechanism of expected effect:
- Claude currently reads "DEFAULT SELL BIAS — check per-coin regime before deciding" and the trending_down NOTE on every CALL_A when global regime is trending_down + conf > 0.60 (which happened on every audited cycle).
- Removing the directive language reduces the prompt-level amplification.
- Per Phase 1.7 finding, the brain is ~2-3 pp above the regime-proportional expectation. Removing the amplification should drop brain Sell from 92.3% to ~89-90%.
- After the override layer applies its 25 Sell→Buy flips, final orders would likely shift from 89.3% Sell to perhaps 82-86% Sell.

### Empirical prediction with current data

Pre-fix (current state):
- Brain: 92.3% Sell (84 Sell / 7 Buy in audit window).
- Final orders: 89.3% Sell (75 Sell / 9 Buy).

Post-Issue-4-only prediction:
- Brain: 85-90% Sell (drop of 2-7 pp).
- Final orders: ~82-87% Sell (drop of 2-7 pp).

Buy WR prediction: roughly unchanged (45-50%) — the brain isn't selecting *worse* Buy setups, it's just selecting MORE Buy setups when allowed.

Decision thresholds for the experiment:

| Outcome at 48h | Interpretation | Next step |
|---|---|---|
| Brain Sell drops to ≤80% AND Buy WR ≥ 40% | Issue 4 was dominant; system is healthy | Hold position; observe 7 days; decide if 2, 3 are needed |
| Brain Sell drops to 80-90% AND Buy WR ≥ 40% | Issue 4 helped modestly; upstream effects remain | Proceed to Issue 3 (labeller soft haircut) |
| Brain Sell stays ≥90% AND Buy WR ≥ 40% | Issue 4 was inert; upstream is dominant | Skip to Issue 2 (Concern 7 config test) |
| Brain Sell stays ≥90% AND Buy WR < 40% | Issue 4 was insufficient AND Buys are degrading | Revert Issue 4; reassess fundamentals |
| Buy share exceeds 70% within 24h | Issue 4 over-corrected; symmetric prompt is too aggressive | Revert Issue 4; try softer wording |

### Counter-argument — all four issues are coupled

The senior reviewer's suggestion treats issues as independent. But Phase 1.9 synthesis notes the cross-issue dependencies:
- Issue 4 (prompt) tells Claude what to weigh.
- Issue 3 (labels) determines what Claude SEES (716 SHORT labels vs 148 LONG).
- Issue 2 (counter ×0.7) determines whether counter LONG setups even reach Claude with meaningful confidence.
- Issue 1 (XRAY rr_long) determines whether Claude's Buy decisions get flipped to Sells by the override layer.

If we ship Issue 4 alone, Claude reads a symmetric prompt but is still presented with 4.84× more SHORT labels than LONG labels. The brain may correctly trade WITH the label slate (Sell-biased) and the prompt change has minimal effect.

This is a real risk. But it's also the experiment's value — the data answers the question.

### Why "Issue 4 alone" is still the right first move

1. **Lowest risk surface**. 10 LOC text edit + 2 sentinel updates + 11 test-marker updates. Reversible by git revert.
2. **Highest information yield per LOC shipped**. 48 hours of production data tells us how much of the bias is prompt-level vs upstream-level.
3. **Honors operator directive most cleanly**. Removing the asymmetric directive language is directive-aligned with no hardcoded numbers.
4. **Stops false-advertising**. The `STRAT_AGGRESSIVE_FRAMING regime_instr=minimal` boot sentinel is currently lying about state. Issue 4 fixes this regardless of whether the prompt change shifts behavior.
5. **Doesn't preclude other fixes**. If the 48h measurement shows upstream issues dominate, Issues 1/2/3 ship next.

### The "all-up commitment" alternative

Path A (ship all 4 fixes over 17-23 days): commits the operator to a specific implementation theory before observing data. If Issue 4 alone is sufficient, the other 88% of effort is wasted. If Issue 4 is insufficient, the other fixes are needed anyway — but the order can be data-driven, not theory-driven.

The empirical evidence (Phase 1.1: Issue 1 has 12% ceiling; Phase 1.7: orders are regime-proportional; Phase 1.8: 14d WR is break-even for both directions) favors a data-driven sequencing.

## Verdict

**STRONGLY VALID.** Path C (Issue 4 alone first, measure 48h, then decide) is the empirically-supported and directive-aligned first move. Lowest risk, highest information yield, reversible.

## Recommendation

Adopt Concern 5's approach as the recommended fix path (Path C variant):
1. Ship Issue 4 (symmetric prompt) + the `STRAT_AGGRESSIVE_FRAMING` sentinel correction as Phase A. ~1 day, 1 atomic commit per concern, ~80 LOC.
2. Optionally ship Concern 7's config-only Issue 2 test (`counter_confidence_multiplier = 1.0`) in parallel — zero LOC, just TOML.
3. Run 48 hours.
4. Decision matrix above determines next step.

## Implications for fix path

- Path A (all-up) — REJECTED in favor of Path C.
- Path B (modified) — partially aligned with Path C if it starts with Issue 4.
- Path C (smallest viable first) — RECOMMENDED.
