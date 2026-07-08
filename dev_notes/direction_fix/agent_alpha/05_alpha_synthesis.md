# 05 — ALPHA Synthesis

Agent ALPHA Phase 1, Step 5. Synthesis of investigation + design intent + fix
options. Operator-facing recommendation grounded in the project aim
(aggressive, balanced-direction exploitation).

## Where the inversion happens (file:line, exact mechanism — corrected)

The spec's R1 mechanism is wrong as stated. The corrected mechanism:

- `src/analysis/structure/structure_engine.py:269-275` writes
  `suggested_direction` from market_structure. This field tracks the regime
  (uptrend → long, downtrend → short). It is set once per `analyze()` call,
  before any setup-classification runs.
- `src/analysis/structure/structure_engine.py:1175-1194` (the
  BULLISH_FVG_OB_COUNTER branch) and `:1196-1216` (the BEARISH_FVG_OB_COUNTER
  branch) are the only places in classify_setup that mutate
  `analysis.trade_direction`. They set it to the OPPOSITE of
  `suggested_direction` because the COUNTER setup represents a contrarian
  trade-payoff direction. Line 1189: `analysis.trade_direction = "long"`. Line
  1211: `analysis.trade_direction = "short"`.
- `analysis.suggested_direction` is NEVER mutated by classify_setup. The 691
  bullish-counter events keep `suggested_direction=short` because the regime
  was downtrend; the counter logic only records the inverted trade-payoff.

What reaches the brain (CALL_A) prompt:

- `src/workers/scanner_worker.py:615-626` populates `pkg.xray.trade_direction`
  via `getattr(structure, "trade_direction", "") OR
  getattr(structure, "suggested_direction", "") OR ""`. So
  `pkg.xray.trade_direction` carries the inverted value for counter setups.
- `src/brain/strategist.py:1948-1952` and `:2543-2547` render the brain
  prompt with `pkg.xray.trade_direction` AND the "(COUNTER-TRADE — trade
  direction is OPPOSITE to market structure bias; lower conviction)"
  annotation when setup_type contains "counter".

What reaches APEX:

- `src/apex/assembler.py:737` writes `StructuralData.suggested_direction =
  analysis.suggested_direction` — the raw regime label.
- `StructuralData` does NOT carry `trade_direction`. APEX's
  `_check_direction_lock` operates on the regime label and is blind to the
  counter inversion.

This is the actual cross-layer information loss: XRAY computes
`trade_direction` correctly, the brain sees it, but APEX does not.

What COMPLETE_FINDINGS counted as "87% short XRAY suggested_direction":

- `suggested_direction=short`: 2,296 / 2,644 = 86.8% (matches "87% short").
- `trade_direction=short`: 1,640 / 2,644 = 62.0%.

The 87% figure represents the regime, NOT what the brain receives. The
brain receives the more balanced 62%/38% via `trade_direction`. The bias the
brain expresses (89% Sell) cannot be entirely blamed on XRAY direction
inversion — XRAY is sending balanced enough information.

The Sell-bias R1 contribution is therefore most fairly characterized as: APEX
loses the counter-inverted trade_direction in the XRAY→APEX handoff and
applies its regime-only lock against the counter signal — that suppresses
counter-Buy opportunities at the APEX layer, not at the XRAY layer.

## Why it works this way (design intent — from 03)

The Phase 4 commit `3a59637` introduced the split deliberately:

> "trade_direction field added to StructuralAnalysis so downstream consumers
>  can distinguish 'trade direction implied by setup' from 'suggested
>  direction implied by market structure.' For in-direction setups they
>  match; for counter setups trade_direction is OPPOSITE."

The Phase 5d commit `a3948c5` deliberately added the brain-prompt annotation
to signal "lower conviction" for counter setups, complementing the
mechanical 0.7x confidence multiplier in TradeScorer/ensemble.

The original motivation was to broaden the opportunity surface — Phase 0
verification showed 66% of "NONE" coins were actually counter-setup
candidates. The aim was "FIND MORE TRADES" (consistent with project aim of
aggressive exploitation).

The split was correct under the project's layer doctrine:
- Layer 1B (XRAY) emits multiple direction views.
- Layer 2 (Brain) chooses among them.
- Layer 3 (APEX) optimizes/locks.

The architectural bug is that Layer 3 doesn't read the same Layer 1B field
the brain reads. That is fixable without breaking the layer doctrine.

## Which option ALPHA recommends, and why

**Primary recommendation: Option E — APEX reads trade_direction.**

Strong rationale:

- It addresses the actual cross-layer information loss (assembler.py:737 drops
  trade_direction). All other options leave that loss in place.
- It preserves the design intent. The split (suggested_direction vs
  trade_direction) stays. APEX just consumes the field XRAY meant for trade
  payoff.
- All five aim-bias questions answer YES (with BETA's cooperation):
  1. Frequency preserved (additive, no entries rejected).
  2. Aggression preserved (un-locks structurally-supported counter trades).
  3. Decision quality improved (APEX agrees with the brain on what the
     structural signal is).
  4. Passive-close preserved (no change to close paths).
  5. Separation of concerns respected (XRAY publishes; APEX consumes; no
     layer boundary crossed).

Cross-agent caveat: Option E requires BETA's optimizer.py to consume the new
field. ALPHA can land the field plumbing (models.py + assembler.py + tests)
on `fix/r1-xray-counter-inversion`; BETA's branch consumes it. DELTA must
sequence them in the same Phase 3 window.

**Secondary recommendation (free to ship independently): Option D —
observability of trade_dir splits.**

Pure observability win. Helps operators see what the brain actually
receives (62%/38%), preventing future investigations from getting
side-tracked by the 87% suggested_direction figure.

**Tertiary (optional, defer): Option C — operator-tunable counter annotation
strength.**

Useful long-term tuning surface but defer until Option E + BETA's R2/R3 land
and we have real data on counter-trade WR. Adding it pre-emptively risks
the operator tuning away from the conservative 0.7x default without
evidence.

**Reject: Option A (null) and Option B alone.** Option A leaves the bias
unaddressed. Option B's field plumbing without BETA's consumption is dead code.

## Trial behavior specification (post-fix, Option E + Option D shipped)

Scenario: XRAY detects `bullish_fvg_ob` setup against a `trending_down` regime
on coin X (the COMPLETE_FINDINGS framing of "Buy-favoring structural setup in
bear regime").

Actual code path post-fix:

1. `analyze()` writes `analysis.suggested_direction = "short"` (regime
   downtrend → short).
2. `classify_setup()` finds bullish in-direction zones MISSING but bullish
   counter zones PRESENT near price. Branches at line 1175.
3. `_counter_alignment("long", "downtrend", cfg)` returns True (downtrend
   allows long counter).
4. Branch fires: emits `SetupType.BULLISH_FVG_OB_COUNTER`, sets
   `analysis.trade_direction = "long"`, confidence = min(mtf, smc) * 0.7
   (typical range 0.28-0.49).
5. `XRAY_CLASSIFY` log emits at INFO with `setup_type=bullish_fvg_ob_counter
   trade_direction=long suggested_direction=short is_counter=true`.
6. `XRAY_CLASSIFY_SUMMARY` (per-tick) emits with new fields (Option D):
   `bullish_fvg_ob_counter=N trade_dir_long=N trade_dir_short=N`.
7. ScannerWorker builds CoinPackage with `pkg.xray.trade_direction = "long"`
   and `pkg.xray.setup_type = "bullish_fvg_ob_counter"`.
8. State labeler reads `trade_direction="long"` → emits a LONG state label.
9. Brain CALL_A prompt renders coin X as: "Setup: bullish_fvg_ob_counter
   (COUNTER-TRADE — trade direction is OPPOSITE to market structure bias;
   lower conviction) (confidence 0.35, trade_direction=long)".
10. Brain decides on direction; assume it picks Buy (long).
11. APEX assembler builds StructuralData with BOTH
    `suggested_direction="short"` AND new field `trade_direction="long"`.
12. APEX `_check_direction_lock` (BETA's change in optimizer.py) reads
    `trade_direction="long"` first; lock either does not fire OR uses
    structural-evidence-aware logic. The "regime=trending_down therefore
    Sell" hard-lock does NOT fire against a counter-long setup.
13. APEX produces an OPT_DIR consistent with the brain's choice (Buy).
14. Layer 4 gate runs; if all other checks pass, order is placed Buy.
15. Result: a Buy entry that previously would have been blocked at APEX is
    now permitted. PnL outcome depends on the counter setup's true edge.

Log events emitted in this chain:

- `XRAY_CLASSIFY | sym=X setup_type=bullish_fvg_ob_counter ...
  trade_direction=long suggested_direction=short is_counter=true`
- `XRAY_CLASSIFY_SUMMARY | ... bullish_fvg_ob_counter=N trade_dir_long=N
  trade_dir_short=N` (new fields from Option D)
- `STRAT_DIRECTIVE | #1 sym=X dir=Buy ...` (brain's choice)
- `APEX_DIR_LOCK | ... locked=False reason='counter_trade_signal_overrides'`
  (new lock-reason path from BETA's consumption)

Field values to verify in the brain prompt:

- `setup_type=bullish_fvg_ob_counter`
- `setup_type_confidence=0.28-0.49` (counter-multiplier applied)
- `trade_direction=long` (the inverted payoff)
- `suggested_direction` field — NOT rendered on the Setup line. (The brain
  prompt's `pkg.xray` block does not have a suggested_direction field; that's
  carried in the regime line and state-label section.)

## Verification queries (operator-runnable post-fix)

Run these against the post-fix workers.log over 24-48 hours of live trading
to verify the change works as designed.

### Query V1 — verify XRAY direction split telemetry

```
grep "XRAY_CLASSIFY_SUMMARY |" workers.log \
  | tail -50 \
  | grep -oE "trade_dir_long=[0-9]+|trade_dir_short=[0-9]+"
```

Expect: post-Option-D, the summary line includes these new fields. Operators
see trade_dir distribution per tick, can compute average over time.

### Query V2 — verify APEX consumes trade_direction (post BETA's change)

```
grep "APEX_DIR_LOCK |" workers.log \
  | grep -oE "trade_direction=[a-z]*"
```

Expect: APEX_DIR_LOCK lines carry the new `trade_direction=` field, sourced
from `StructuralData.trade_direction`.

### Query V3 — verify counter setups no longer blocked at APEX

```
grep "APEX_DIR_LOCK |" workers.log \
  | grep "counter" \
  | grep -E "locked=False"
```

Expect: when setup_type contains "counter" and trade_direction is opposite to
suggested_direction, APEX_DIR_LOCK does NOT fire (locked=False) or fires with
a permissive reason.

### Query V4 — verify counter-LONG entries actually place orders

```
grep -E "STRAT_DIRECTIVE.*dir=Buy" workers.log \
  | wc -l
```

Expect: brain Buy directives count over 24h should be materially higher
than the pre-fix 11 Buy / 73 Sell ratio. Target informal: 30%+ Buy.

### Query V5 — verify trade_direction inversion correctness

```
grep "XRAY_CLASSIFY | sym=.* setup_type=bullish_fvg_ob_counter" workers.log \
  | head -20 \
  | awk '{ for(i=1;i<=NF;i++){
              if($i ~ /^trade_direction=/) td=$i;
              if($i ~ /^suggested_direction=/) sd=$i;
            } print td, sd }' \
  | sort | uniq -c
```

Expect: 100% of bullish_fvg_ob_counter events show `trade_direction=long
suggested_direction=short`. Mirror query for bearish counters.

### Query V6 — verify brain prompt visibility (sample)

```
grep "Setup: bullish_fvg_ob_counter" \
    dev_notes/brain_enrichment/live_monitoring/SYSTEM_LOGS_*.log \
  | head -10
```

Expect: the brain prompt renders the COUNTER-TRADE annotation with
`trade_direction=long`.

## Risk register for the recommended fix

- R-A1: APEX consuming trade_direction may permit aggressive counter-Buy
  entries that lose money. Mitigation: `setup_type_confidence` floor on the
  consumption (require >= 0.40, matches counter_mtf_min). Existing
  `_counter_alignment` gate already excludes the worst-case (long-counter on
  uptrend). Confidence-floor flag is BETA's PR territory.
- R-A2: ScannerWorker fallback (line 615-626) currently uses
  `trade_direction OR suggested_direction`. After Option E, APEX uses the
  same fallback semantics. Need to ensure both consumers agree on the
  fallback contract. Easy to centralize in a helper.
- R-A3: The new `trade_direction` field on StructuralData adds a small
  serialization footprint (~16 bytes). Negligible.
- R-A4: Existing tests in `tests/test_setup_classifier_counter.py` (26 cases)
  cover the XRAY emit-side. Need new test for the assembler.py propagation
  AND for BETA's consumption side. Combined target: ~5 new tests.

## Effort estimate

- Option E ALPHA portion: 1-2 hours (one field, one assignment, one render
  audit, three tests).
- Option D: 30 minutes (one log line extension, one config touch).
- Option C (if shipped): 1 hour (setting + two render variants + test).

Total ALPHA Phase 3 effort: 2-4 hours. BETA's coordinated portion is
separate.

## Final recommendation summary

Ship Option E + Option D together. Defer Option C unless operator
explicitly wants more tuning surface. Reject Option A (null) and Option B
(field plumbing only, dead without BETA).

Cross-agent dependency: Option E requires BETA's optimizer.py to consume
the new field. Flag this to DELTA in synthesis. DELTA's sequencing must put
ALPHA's plumbing change BEFORE BETA's consumption — otherwise BETA's
optimizer reads a field that doesn't exist.
