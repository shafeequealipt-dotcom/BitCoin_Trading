# Gap 2 Phase 1 Synthesis — Brain visibility of `is_structurally_invalid`

Date: 2026-05-19  
Scope: surface the clamp activation flag in the brain prompt so Claude can distinguish a real 5x RR asymmetry from a clamp-floor synthetic asymmetry. Information-supply only — no restrictive guidance per Rule 4 anti-pattern.

## Step 2.1 — Prompt construction (verified)

`src/brain/strategist.py:1357-1429` builds the X-RAY STRUCTURAL SETUPS section. Per-coin line at `:1359-1417` includes (in order): support, resistance, structure, position-in-range, `RR=1:X(quality)`, `RR_DIR(L=...,S=...,best=DIR,Nx)` at :1387-1390, FVG, OB, sweep, SMC, POC, FIB, MTF, CONFL, setup quality.

The `RR_DIR(...)` line is the natural attach point for the new annotation — it already exposes both directions' RR. The invalid flag complements that comparison.

## Step 2.2 — Surfacing options

| Option | Form | Touches |
|---|---|---|
| A — bidirectional fields + extension on RR_DIR | `RR_DIR(L=0.2,S=5.4,best=SHORT,21.6x) INVALID_LONG=Y INVALID_SHORT=N` | structure_types + structure_engine + strategist |
| B — `(clamped)` suffix on raw rr | `RR_DIR(L=0.2(clamped),S=5.4,best=SHORT,21.6x)` | strategist only |
| C — separate INVALID line per coin | New line `INVALID_FLAGS: LONG=Y SHORT=N` below the main per-coin row | strategist + structure_types |

**Recommendation: Option A** — most explicit, structurally clean, follows existing `KEY=VAL` precedent (FVG=, OB=, MTF=, CONFL=, etc.), and the bidirectional fields are reusable by other consumers (Gap 1 Path D would need them).

## Step 2.3 — Framing (Rule 4 anti-pattern compliance)

GOOD framing (informational): `INVALID_LONG=Y INVALID_SHORT=N` next to RR_DIR. The annotation reports a fact (the long-side structural_tp used the math-safety floor). It does NOT tell the brain what to do.

BAD framing (rejected): adding "avoid INVALID setups" to the system prompt. That would be hardcoded direction-restriction, violating the operator directive.

A brief system-prompt explainer is needed so the brain knows what the new field means. Single sentence:
> `INVALID_LONG=Y` / `INVALID_SHORT=Y` indicates the long-side or short-side structural placement was computed using a math-safety floor (price was at/past the relevant level). Lower rr_long or rr_short values when the flag is Y reflect this floor, not a real measure of edge.

Brain decides whether to factor that into its choice. No directional bias added.

## Step 2.4 — Bidirectional compute (verified)

`structure_engine.py:298-313` calls BOTH `_calc_long` and `_calc_short` PER CYCLE. The `long_pl` and `short_pl` placements both exist in scope (lines 297-313). Currently only the chosen one is returned forward.

**Zero new compute cost.** Bidirectional flag exposure is a data-marshalling change.

## Step 2.5 — Trim survival

The X-RAY section is NOT in `_TRIM_ESSENTIAL_MARKERS` (strategist.py:413-419 confirmed). The annotation goes IN-LINE on the existing RR_DIR row, so it shares trim destiny with the section. If priority-trim drops the X-RAY block under chars-cap pressure, the annotation goes with it. Adding the annotation does not change trim risk; it inherits.

No additional trim protection is needed because:
- Option A extends an existing line, not adds a new section
- If the X-RAY block survives, so does the annotation
- If the X-RAY block is trimmed, all per-coin RR_DIR info is gone anyway — the annotation's absence is irrelevant

## Aim-bias 5/5 evaluation

1. **Preserves trade frequency?** YES — no new gates; brain decides.
2. **Preserves aggression?** YES — no new blocking.
3. **Improves decision quality?** YES — brain sees richer info, can distinguish real edge from synthetic clamp-floor.
4. **Preserves passive-close advantage?** YES — close path untouched.
5. **Respects structural separation?** YES — Layer 1B computes the flag; Layer 2 surfaces it. Each layer does its own job.

## Implementation surface

Files modified:
1. `src/analysis/structure/models/structure_types.py` — add `is_long_invalid` / `is_short_invalid` fields + expose in `to_dict()`
2. `src/analysis/structure/structure_engine.py` — marshal both flags onto chosen placement (lines 341-356 area)
3. `src/brain/strategist.py` — render annotation on RR_DIR line (line 1390 area) + add brief system prompt explainer

New test file: `tests/test_gap2_brain_invalid_visibility.py`

## Trial behavior specification (Rule 14)

1. Force a clamp-activated long placement (resistance at current price). Run through structure_engine. Verify:
   - `placement.is_long_invalid == True`
   - `placement.is_short_invalid == False`
   - `placement.to_dict()["is_long_invalid"] == True`
2. Feed that placement into a candidate analysis. Build the prompt. Verify:
   - The X-RAY line for that coin contains `INVALID_LONG=Y INVALID_SHORT=N`
3. Force a healthy placement. Verify both flags are False AND the annotation reads `INVALID_LONG=N INVALID_SHORT=N` (always emitted for symmetry).
4. Verify the system prompt contains the brief informational explainer.
5. Verify NO directive bias text was added to the prompt (Rule 4 anti-pattern check).

## Recommendation

**Option A** with framing as described. Proceed to implementation.
