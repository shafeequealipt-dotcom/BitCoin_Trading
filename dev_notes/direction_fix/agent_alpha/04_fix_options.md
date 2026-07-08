# 04 — Fix Options (Aim-Bias-Evaluated)

Agent ALPHA Phase 1, Step 4. With the Phase 0 drift resolved (counter logic does
NOT collapse suggested_direction; it sets trade_direction to the opposite of
suggested), the spec's Option A/B/C/D need reframing. Each option is evaluated
on the actual mechanism, with the five aim-bias questions answered explicitly.

The five aim-bias questions per option:

1. Trade frequency preserved?
2. Aggression preserved?
3. Decision quality improved?
4. Passive-close advantage preserved?
5. Structural separation of concerns respected?

A "yes" answer must be evidence-backed. A "no" answer must come with operator
sign-off note.

---

## Option A — Keep current design; do nothing (the null option)

The counter system already does what the design intended. The COMPLETE_FINDINGS
"R1 mechanism" turns out not to be a real bug. The bias problem is real but
its R1-side cause is at the APEX→XRAY handoff (assembler.py:737 drops
trade_direction), which is properly addressed by BETA in optimizer.py.

### Mechanism change

None.

### Observable behavior change

None. The brain prompt continues to render the COUNTER-TRADE annotation with
`trade_direction=long|short`. XRAY_CLASSIFY logs continue to emit both fields.

### Brain prompt visibility

Unchanged. The brain sees `trade_direction` and the annotation.

### Aim-bias evaluation

1. Frequency preserved? YES (no behavior change).
2. Aggression preserved? YES (no caution mechanism added).
3. Decision quality improved? NO — the bias remains and the root chain
   (XRAY→APEX information loss) is unaddressed.
4. Passive-close preserved? YES (no change to watchdog or close paths).
5. Separation of concerns? YES (no change).

### Expected impact on Sell/Buy distribution

None at the XRAY surface. Whatever bias the brain receives via
`trade_direction` (62%/38%) remains.

### Risk of false positives / regression

Zero. But it leaves the bias in place.

### Complexity

1/5. No code changes.

### Recommendation

REJECT unless BETA's fix fully closes the bias chain. Document as the "do
nothing" baseline.

---

## Option B — Add trade_direction to StructuralData and APEX prompt input

Plumb the inverted `trade_direction` through assembler.py:737 into
StructuralData, so APEX_DIR_LOCK has visibility into the counter signal.

### Mechanism change

- `src/apex/models.py:258` — add `trade_direction: str = ""` field to
  `StructuralData`.
- `src/apex/assembler.py:737` — populate `sd.trade_direction =
  analysis.trade_direction` alongside `suggested_direction`.
- APEX optimizer (`src/apex/optimizer.py` — touches BETA's scope) reads
  `trade_direction` and can factor it into `_check_direction_lock`. ALPHA can
  ONLY add the field and the propagation; BETA's PR consumes it.

### Observable behavior change

`APEX_DIR_LOCK` events would receive a new input. If BETA's R2 fix uses it,
they could weaken the lock when the counter signal indicates an opposite trade.
Without BETA's consumption this is purely additive observability — no
behavior change.

### Brain prompt visibility

Unchanged for brain prompt. Adds observability for APEX_DIR_LOCK and APEX
internal logs.

### Aim-bias evaluation

1. Frequency preserved? YES (additive; no entries rejected).
2. Aggression preserved? YES (no caution mechanism).
3. Decision quality improved? YES IF BETA consumes the field; NO if BETA
   doesn't. Without BETA consumption this is plumbing-only.
4. Passive-close preserved? YES.
5. Separation of concerns? YES — the field travels from XRAY (its rightful
   source) through the assembler (its rightful conduit) to APEX (the rightful
   consumer).

### Expected impact on Sell/Buy distribution

When combined with BETA, would let APEX un-lock Sell when XRAY computed
trade_direction=long (the counter signal). Could meaningfully reduce the
Sell-bias on the 691 counter cases per session. Without BETA, no impact.

### Risk of false positives / regression

Low if BETA implements responsibly (counter signals carry 0.7x confidence and
the brain already sees them; APEX would just stop force-locking against them).
Risk of regression: zero — the field is additive.

### Complexity

2/5 for ALPHA's piece (one new field, one assignment, one test). 3/5 for
BETA's piece (consumption logic).

### Cross-agent dependency

YES. This option requires BETA's cooperation to be effective. Should be flagged
to DELTA (synthesis) as an interaction.

### Recommendation

STRONG candidate IF BETA's R2 fix consumes the field. Otherwise reduces to
Option A.

---

## Option C — Make counter-direction operator-tunable (down-weight or up-weight)

Expose two existing config knobs in `[analysis.structure.setup_types]` more
prominently AND make the brain prompt annotation tunable:

- `counter_confidence_multiplier` (default 0.7) — already exists. Operators
  can lower to 0.5 to halve counter signal weight, or raise to 1.0 to treat
  counter setups at full conviction.
- `counter_setup_enabled` (default True) — already exists. Setting to False
  disables counter-emission entirely (counter cases fall back to NONE).
- ADD `counter_brain_annotation_strength` (new config; default "balanced") —
  controls whether the brain prompt annotation says "lower conviction" (current
  default), "balanced" (no leaning), or "high conviction" (treats counter as
  full structural opportunity).

### Mechanism change

- `src/config/settings.py` — add `counter_brain_annotation_strength` setting on
  `SetupTypesSettings`.
- `src/brain/strategist.py:1944-1946` and `:2542` — render different
  annotation strings based on the new setting.
- No change to classify_setup or trade_direction.

### Observable behavior change

Brain prompt annotation text changes. Counter setup_type_confidence value
unchanged unless operator lowers the multiplier; downstream ensemble/score
weighting reacts to the multiplier change automatically.

### Brain prompt visibility

CHANGED. The "(COUNTER-TRADE — lower conviction)" annotation becomes
operator-controlled.

### Aim-bias evaluation

1. Frequency preserved? YES.
2. Aggression preserved? YES — operator can tune toward more aggressive (raise
   multiplier to 1.0, set annotation "balanced") if data supports it.
3. Decision quality improved? YES IF operator tunes correctly. The current
   default "lower conviction" annotation may be too cautious for an aggressive
   exploitation system.
4. Passive-close preserved? YES.
5. Separation of concerns? YES — config-driven knobs respect layer
   boundaries.

### Expected impact on Sell/Buy distribution

If operator sets multiplier=1.0 and annotation="balanced", the brain may
weight 691 counter-LONG signals at full conviction, increasing Buy entries
when counter setups fire on bearish-suggested coins. Could meaningfully shift
Sell/Buy ratio if the brain currently under-weights counter setups.

### Risk of false positives / regression

Medium if operator removes the conviction reduction without evidence that
counter setups achieve in-direction WR. Counter setups fight regime — they
historically had lower WR. Allowing operator-tunability without trial period
risks aggressive bias shift in the wrong direction.

Mitigation: ship with defaults unchanged. The new knob is opt-in.

### Complexity

2/5. New setting, two render-site changes, one test for each annotation
variant.

### Recommendation

GOOD candidate for operator-controlled tuning. Combines well with Option B.
ALPHA should ship the knob; operator decides default.

---

## Option D — Surface XRAY direction telemetry in trade_direction terms

The public XRAY_CLASSIFY log emits both fields, but downstream operator
observability (XRAY_CLASSIFY_SUMMARY, dashboards) tend to count
`suggested_direction` (the regime label). Add summary aggregation by
`trade_direction` so operators don't conclude "87% short" when the brain's
input is 62%/38%.

### Mechanism change

- `src/workers/structure_worker.py:273-277` — extend XRAY_CLASSIFY_SUMMARY
  to also emit `trade_dir_long=N trade_dir_short=N counter_count=N` so
  operators see what the brain actually receives.
- Add a new metric line `XRAY_DIRECTION_SPLIT` per tick: emits trade_direction
  and suggested_direction counts side by side.

### Observable behavior change

New log line. No behavior change in trading.

### Brain prompt visibility

Unchanged.

### Aim-bias evaluation

1. Frequency preserved? YES (purely observational).
2. Aggression preserved? YES.
3. Decision quality improved? Marginally — operators get truer bias signal,
   can make better tuning decisions.
4. Passive-close preserved? YES.
5. Separation of concerns? YES.

### Expected impact on Sell/Buy distribution

Zero (no behavior change). Improves the operator's mental model.

### Risk of false positives / regression

Zero.

### Complexity

1/5. Pure observability. ~10 lines.

### Recommendation

GOOD complementary fix. Independent of other options. Operators can
deploy this BEFORE deciding on B/C/E to get truer baseline numbers.

---

## Option E — Honest mechanism alignment: feed APEX `trade_direction` instead of `suggested_direction`

The strongest fix consistent with the design intent. The split between
suggested_direction (regime label) and trade_direction (setup-payoff) was
intentional. Currently APEX reads the regime label and ignores the
setup-payoff. The honest mechanism alignment is: APEX should read the
setup-payoff direction whenever the setup_type is non-NONE, because that's the
direction the structural intelligence layer recommended.

This is the fix that R1 (correctly stated) deserves.

### Mechanism change

- `src/apex/models.py:258` — add `trade_direction: str = ""` AND keep
  `suggested_direction: str = ""` (preserving regime context). Add
  `setup_type: str = ""` (already done in fact — line 268).
- `src/apex/assembler.py:737` — populate both fields. Already populates
  `setup_type` (line 752). Add `trade_direction`.
- `src/apex/optimizer.py` (BETA scope) — `_check_direction_lock` reads
  `trade_direction` when `setup_type != "none"` and trade_direction is
  non-empty; falls back to `suggested_direction` otherwise. This is a
  one-line conditional change in the lock-reason builder.

### Observable behavior change

`APEX_DIR_LOCK` becomes counter-aware. When a counter setup fires (e.g.
bullish_fvg_ob_counter, trade_direction=long, suggested_direction=short), APEX
no longer locks to Sell on regime alone — it sees the structural counter
signal and either does not lock OR provides a less-restrictive lock.

### Brain prompt visibility

No change to brain prompt (it already reads `trade_direction`).

### Aim-bias evaluation

1. Frequency preserved? YES — the counter setups generate entries that were
   previously blocked by APEX lock.
2. Aggression preserved? YES — this UN-locks structurally-supported
   counter-trades that were being suppressed by regime-only logic.
3. Decision quality improved? YES — APEX sees the same input the brain sees,
   so its lock decision is in agreement with what the brain decided. Removes
   the "brain says Buy, APEX locks to Sell" pathway specifically for
   counter setups (per evidence in COMPLETE_FINDINGS: 10 of 11 Qwen Buy-flip
   attempts blocked).
4. Passive-close preserved? YES — close paths unchanged.
5. Separation of concerns? YES — APEX consumes the structural intelligence
   layer's CHOSEN trade direction (not the raw market_structure label). The
   regime label is still available for ensemble logic that explicitly needs it.

### Expected impact on Sell/Buy distribution

Most impactful of the options. With 691 counter-LONG events per session no
longer blocked by APEX lock, the Sell/Buy ratio could shift toward
balanced. Magnitude depends on how many of those 691 also pass brain decision,
RR threshold, gate checks.

### Risk of false positives / regression

Medium. Counter setups are explicitly lower-conviction (0.7x multiplier).
Allowing them to override APEX_DIR_LOCK could let lower-quality entries
through. Mitigations:

- Require setup_type_confidence >= operator-tunable floor (say 0.40) before
  trade_direction wins over suggested_direction at APEX.
- Existing `_counter_alignment` already excludes long-counter on uptrend,
  preventing double-long. The same gate continues to fire at XRAY before APEX
  even sees the data.
- BETA's R2/R3 fixes (lock thresholds) can further calibrate.

### Complexity

3/5. ALPHA part (one field + one assignment + one test) is 2/5; BETA part
(one branch in `_check_direction_lock`) is 2/5. Combined under DELTA: 3/5.

### Cross-agent dependency

STRONG — requires BETA's optimizer change to land in the same Phase 3.
DELTA must sequence them together.

### Recommendation

ALPHA's strongest single recommendation. Aligns with design intent
(trade_direction is the "trade direction implied by setup" — that's exactly
what APEX should consume). Aim-aligned. Highest expected impact.

---

## Cross-option comparison

| Option | Code change | Complexity | Brain prompt | Sell/Buy impact | Risk | Aim-bias verdict |
|---|---|---|---|---|---|---|
| A — null | none | 1 | unchanged | none | none | 4/5 yes (decision quality NOT improved) |
| B — plumb trade_direction to APEX | additive field | 2 | unchanged | none alone | low | 5/5 (with BETA) |
| C — operator-tunable annotation | new config + render switch | 2 | changed | medium if tuned | medium | 4/5 (decision quality depends on tuning) |
| D — observability of trade_dir splits | new log lines | 1 | unchanged | none | none | 4/5 (decision quality marginal) |
| E — APEX reads trade_direction | field add + APEX logic | 3 | unchanged | strong | medium | 5/5 (with BETA's R2 cooperation) |

## ALPHA's three-of-five recommendation slate

For DELTA synthesis, the operator's primary decision artifact is the choice
between:

1. **Option E (preferred)**, with explicit BETA coordination. Aligns design,
   highest impact, all five aim-bias YES. Requires BETA's optimizer.py change
   to consume the new field.
2. **Option B + Option D combined** as a "soft" fix. Adds the
   trade_direction field to APEX assembler (preparing for BETA's consumption
   later) AND adds the observability so operators see the real split. Safe to
   ship independently of BETA.
3. **Option C** as a complementary tuning surface — gives operators a control
   to raise counter conviction if data supports it after E lands. Independent
   of B/E.

Option A is the null baseline. Option D should ship unconditionally — it's a
free observability win.

The next file (05_alpha_synthesis.md) makes the operator-facing
recommendation.
