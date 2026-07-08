# Phase 2.1 — Concern 1: Is Issue 1 Phase A2 (RR floor guard) a band-aid?

## Concern restated

The prior report's Issue 1 Phase A2 proposes adding RR floor guards at `strategy_worker.py:1727-1739` (decision boundary actually at line 1860 per Phase 1.1 correction): block the flip when `_chosen_rr < xray_dir_flip_min_chosen_rr` (default 0.5) OR `_flipped_rr < xray_dir_flip_min_flipped_rr` (default 2.0). New log event `XRAY_FLIP_BLOCKED_LOW_EDGE`.

The operator's senior reviewer's concern: this is the same pattern as previously-rejected band-aids — R4 portfolio direction cap, R2 regime-only direction lock, R3 static 10× override threshold. All were "guard at the symptom site that blocks based on threshold." All were correctly rejected. Is the new RR floor different, or the same pattern in new clothes?

## Evaluation criteria (spec lines 525-529)

| Criterion | Phase A2 verdict | Reasoning |
|---|---|---|
| Does it block based on threshold? | YES | `block if chosen_rr < 0.5 OR flipped_rr < 2.0` — both are hardcoded thresholds |
| Does it address root cause? | NO — it suppresses symptoms | The root cause is the RR formula degeneracy (no min-edge floor) and the asymmetric `min_touches` filter. Phase A2 doesn't touch either. |
| Is it operator-tunable with kill switch? | YES | Default 0.0 (no-op). Operator must opt-in via TOML edit to activate. |
| Is its existence justified by other mechanisms NOT existing? | YES | Without Phase A core (the structural fix at `structural_levels.py`), the RR formula keeps producing collapse outputs. The guard exists to mask the upstream defect. |

## Pattern comparison with rejected band-aids

| Mechanism | When applied | What it does | Operator's prior view |
|---|---|---|---|
| R4 portfolio direction cap | Per-trade gate | Block trades that exceed 70% one-direction portfolio | REJECTED — hardcodes "max X% one direction" |
| R2 regime-only direction lock (pre-fix) | At APEX | Lock direction based on regime | REJECTED — replaced with composite scoring (R2 fix shipped 2026-05-17) |
| R3 static 10× override threshold (pre-fix) | XRAY override layer | Block flip if ratio < 10× | REJECTED — replaced with WR-derived threshold |
| **Phase A2 RR floor guard (proposed)** | At flip decision | Block flip if chosen_rr < 0.5 OR flipped_rr < 2.0 | **TBD — operator decides** |

Phase A2 sits in the same architectural slot as the rejected band-aids: late-stage symptom suppression with threshold-based blocking. The semantics ("don't flip if RR is too small to be meaningful") IS more honest than R4's "don't trade if portfolio is too one-direction", but the pattern is similar.

## Counter-argument — when guards are legitimate

Not every threshold guard is a band-aid. Examples of legitimate guards in the project:
- SL-buffer floor: prevents zero-distance SL (would zero-divide risk calc).
- TP-validate-skip: prevents TP placement below current price on a long.

These guards prevent mathematically-meaningless trades, not direction-asymmetric outcomes. They exist because the upstream pipeline can produce invalid numeric configurations and the system needs to handle the edge case.

Phase A2's `chosen_rr ≥ 0.5` floor falls in this category: a 0.2-RR trade is mathematically valid but operationally useless. Blocking it is more like the SL-buffer floor than the R4 portfolio cap.

However, Phase A2's `flipped_rr ≥ 2.0` IS more aggressive — it's saying "only flip when the alternative is genuinely strong, not just better than terrible." That edges toward "block on threshold" territory.

## Evidence from Phase 1.1 (data-grounded)

- 8 of 11 XRAY_DIR_FLIP events in audit window have `chosen_rr ≤ 0.3` (collapse-driven flips).
- The proposed `chosen_rr ≥ 0.5` floor would suppress those 8 flips.
- Of the remaining 3 flips, 2 have `chosen_rr ≥ 0.7` (genuinely structural) and would proceed under Phase A2.
- One was at the boundary (0.4 chosen, 2.5 flipped) — borderline.

So Phase A2 would catch ~73% of collapse-driven flips without harming structural flips.

## Verdict

**PARTIALLY VALID.** The concern is correct that Phase A2 has a band-aid texture (threshold-based blocking at the symptom site). But it's not 100% band-aid because:
- The thresholds reject mathematically-meaningless flips (`chosen_rr = 0.2` is a 5% reward on 20% risk — nobody would take that trade manually).
- Default 0.0 is reversible.
- The structural root fix (Phase A core: min-edge floor + symmetric min_touches) is a larger surface and HIGHER risk; Phase A2 is a stop-bleeding intermediate.

## Recommendation

Option 1 — **Skip Phase A2, go directly to Phase A core (Phase 1.A)** if the operator wants a clean root-cause-only fix path. This is the cleanest reading of "no band-aids."

Option 2 — **Ship Phase A2 as a temporary guard with explicit deprecation date** if the operator wants to stop the worst flips while the structural fix lands. State up front: Phase A2 ships in commit N, deprecated and removed in commit N+5 when Phase A core ships.

Option 3 — **Ship Phase A2 with one threshold only** (`chosen_rr ≥ 0.5` — the math-floor, not the `flipped_rr ≥ 2.0` — the asymmetric guard). The first threshold is defensible as math-floor; the second is more band-aid-like.

The operator's design directive favors Option 1 (no band-aids). The empirical case for Option 2 is the 73% catch rate on collapse-driven flips. Operator decision at Phase 4.

## Implication for fix path

- Path A (ship report as-is) keeps Phase A2 — operator should evaluate whether this is acceptable.
- Path B (modified) — drop Phase A2 entirely; rely on Phase A core (1.A) for the structural fix.
- Path C (Issue 4 first, measure) — Phase A2 is moot until/unless operator decides Issue 1 is needed.
