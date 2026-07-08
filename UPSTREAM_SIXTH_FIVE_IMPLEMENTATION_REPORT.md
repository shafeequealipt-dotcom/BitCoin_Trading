# Upstream Brain-Quality — Sixth Five Implementation Report (Ranker Inputs and Trim Pressure)

**Date:** 2026-05-28
**Branch:** main (direct-to-main, atomic commit per issue/pair)
**Spec:** `IMPLEMENT_UPSTREAM_BRAIN_QUALITY_SIXTH_FIVE.md`
**Scope:** E9 (HIGH) + E8 (complete #2), E23 + E13 (complete #13), E19 (completes #7/E17/E18)

## Honest value framing

This sixth batch completes three earlier threads. E9 is the one genuinely-HIGH item — the primary candidate ranker was scoring partly on fabricated values. Re-verification (3 Explore agents + 1 Plan agent) against the current code (post batches 1-5) confirmed the predecessors and refined the shape: E9, E8, E23, and E19 are genuine fixes; E13 turned out ALREADY SATISFIED by #13's own implementation (like E7/E22 in earlier batches). So: four real fixes shipped, one confirmed-complete.

## Predecessor confirmations (Rule 2)

All present in current code: #2 (`reserve_slots_union`, `src/core/ranking.py`), #8+E7 (OI magnitude + brain wiring), #13+E22 (priority-trim markers + ALREADY HOLD / ## OPEN POSITIONS), #7 (`XRAY_SCORE_GATED` coherence gate, `structure_engine.py:635-644`) + E17/E18 (`apex/gate.py`).

## Commits (all pushed to origin/main)

| Order | Commit | Issue | Live effect |
|------|--------|-------|-------------|
| 1 | `5e531f2` | E9 + E8 | the interestingness ranker now scores on real regime/structure/OI inputs (was fabricated zeros) |
| 2 | `d3880fc` | E23 | strategy-hints block ~39 sections → 2; trims fire far less |
| 3 | `f07c083` | E13 | confirm-only (already satisfied) — no behavior change |
| 4 | `5adde67` | E19 | X-RAY shortlist ordered by score × confidence |

---

## E9 (HIGH) + E8 — plumb the real ranker inputs (`5e531f2`, completes #2)

- **Symptom:** the interestingness ranker (blended by #2 to decide which coins reach the brain) computed its cleanness/confluence/extremity components from a hardcoded `regime_confidence=0.0` and blank ADX/choppiness/volume_ratio/position_in_range/MTF inputs (E9), and the open-interest input was never passed (E8) — so the ranker scored partly on fiction.
- **Root cause:** the "Phase 5" rollout never finished — `compute_interestingness` already accepts every input, but the call site (`scanner_worker._build_package`) never passed them.
- **Wiring/fix (plumb, not recompute — all values already in scope):**
  - Mandatory guard: `state = None` initialised before the regime try-block (it was only bound inside the try, so a bare `state.confidence` could raise, be swallowed by the outer try, and **silently zero the whole interestingness score**).
  - E9: `regime_confidence/adx/choppiness/volume_ratio` from the `RegimeState`; `position_in_range` from the structure analysis; `mtf_h1_bias` from the structure's single exposed MTF `aligned_direction` (per-TF biases are not exposed; `mtf_aligned_count` left at its honest default rather than fabricated from a factor-ratio — a band-aid the Plan agent flagged and we avoided).
  - E8: `oi_change_24h_pct` from `alt.oi_change_24h_pct` (the #8/E7 corrected value).
  - Operator decision (both call sites): also fixed the identical `regime_confidence=0.0` in the state-labeler call (same root cause — no half-rollout).
  - Defaults match the function signature, so a missing regime/structure degrades to neutral (no crash, no fabricated non-zero).
- **Before/after:** before, a coin's interestingness used regime confidence forced to 0 and blank structure/OI, so cleanness/extremity were fiction and it ranked wrongly; after, the score uses the real regime confidence and structure/OI values. Verified: stub-fed score 0.384 → real-fed 0.540; cleanness 0.46 → 0.83; extremity 0.075 → 0.15.
- **Observability:** activated the dormant `BRIEFING_INTERESTINGNESS` debug tag, proving the inputs are now real per coin.
- **Verification:** `verify_issue_e9_e8.py` PASS; 154 scanner/interestingness tests green.

## E23 — collapse the strategy-hints block (`d3880fc`, completes #13 at the source)

- **Symptom / root cause:** the strategy-hints + per-coin-consensus block emitted ~39 separate `sections.append()` calls (each its own trim-unit), the dominant prompt-size pressure that pushed the prompt to the cap and triggered the trimmer (which #13 then had to protect the core data from).
- **Fix:** collapsed to TWO joined sections, preserving every field the brain reads (strategy/symbol/direction/score/consensus; symbol/buy/sell/total_score). The `## STRATEGY HINTS` header is folded into the joined hints string so the whole block classifies IMPORTANT and rides the priority-trim as one protected unit (strictly better than the prior OPTIONAL orphans). Applied to both the live Call-A block and the byte-identical dead legacy copy (avoid divergent copies).
- **Before/after:** before, the hints emitted ~39 sub-sections that pushed the prompt toward the cap and triggered trims that dropped data; after, the hints are two sections, the section count drops ~37, and the trimmer fires far less. Visible in `STRAT_CALL_A_CTX | sections=… chars=…`.
- **Verification:** `verify_issue_e23.py` PASS (2 sections from 20 hints + 15 rows; every field preserved; header IMPORTANT); 169 strategist/prompt tests green.

## E13 — full dropped-labels logging (`f07c083`, ALREADY SATISFIED by #13)

- **Honest finding:** the audit reported the trim log truncated dropped labels to the first 8. The current code does NOT — the priority-trim path already logs `dropped_count={len(_dropped_labels)} dropped_labels={_dropped_labels}` (count + the full list), and `_dropped_labels` is unbounded (only each label is shortened to 60 chars for readability). #13's priority-trim implementation already did it. Like E7/E22, a predecessor closed it.
- **Fix applied:** none (no code change). `verify_issue_e13.py` asserts the full count+list logging and the absence of any `[:8]` truncation, and a behavioral mirror confirms >8 labels are all retained. PASS.

## E19 — rank X-RAY shortlist by score × confidence (`5adde67`, completes #7/E17/E18)

- **Symptom / root cause:** `StructureCache.get_top_setups` ranked the brain's X-RAY shortlist by `setup_score` alone, so a high-score zero-confidence (structureless) coin could top the brain's X-RAY block — the last ranking site where score-without-confidence still won.
- **Fix (operator decision: membership preserved, only re-ranked):** the top-N membership is still selected by `setup_score`, but the returned N is ORDERED by `setup_score × setup_type_confidence`. A zero-confidence coin gets conviction key 0 and sorts to the bottom, so it stays in the shortlist (membership preserved) but can never lead. Both consumers (Call-A `strategist.py:1511`, Call-B `:3706`) inherit the order.
- **Before/after:** before, a structureless high-score coin could lead the X-RAY block; after, a confident coin leads and the structureless one sorts last. Verified on the real `get_top_setups`: `n=8 → [CONF, MID, LOW, STRUCT]`, `n=3 → [CONF, MID, STRUCT]` (membership = top-3 by score; ordered by conviction).
- **Observability:** `E19_XRAY_RERANK` logs only when the re-rank changes the lead coin (i.e. it just demoted a structureless high-score coin).
- **Verification:** `verify_issue_e19.py` PASS; 431 structure/xray tests green.

---

## Verification summary

- All four verifiers PASS: `verify_issue_e9_e8.py`, `verify_issue_e23.py`, `verify_issue_e13.py`, `verify_issue_e19.py`.
- Smoke: all touched modules import cleanly.
- Full A-Z regression (4 chunks): **1801 passed, 1 skipped.** Non-passing items are all pre-existing/environmental, NONE attributable to this batch: 1 fail `test_apex_direction_lock::test_system_prompt_still_has_rsi_caution` (asserts on `STRATEGIST_SYSTEM_PROMPT`, untouched) + the Python-3.10 sandbox `datetime.UTC` collection/`j1_reconciler` items (run/collect on the 3.11 VM).
- Completions build on #2/#13-E22/#7-E17-E18 (predecessors confirmed), not duplicating them.
- Trade frequency / candidate-set size / brain information preserved (E9/E8 only made the ranker honest; E23 lost no fields; E19 kept membership; E13 no behavior change). Flips off; protected tables read-only.

## Observability added

`BRIEFING_INTERESTINGNESS` (E9/E8, per-coin debug — was dormant); `STRAT_CALL_A_CTX` section count already shows E23's reduction; the E13 trim log already carries count + full list; `E19_XRAY_RERANK` (on lead change).

## Honest remaining tail (Part F sign-off)

After this batch, the genuinely-remaining tail of the 41-issue audit is, and is only:

- **E24** — insufficient-klines regime fallback (a regime-detector robustness item).
- **E4** — legacy brain path (dead code).
- **E5** — dead open-interest computation (dead code).
- **E6** — dead regime config (misleading config).

These are a small, optional final cleanup pass — none is an active bleed. Also deferred (flagged in E9): exposing true per-TF MTF biases (a structure-engine change) if the single `aligned_direction` anchor proves insufficient. The real PnL leverage remains the separate, higher-priority work (PnL-accounting truth, over-tightening, entry R:R). With this batch, the brain's inputs are honest, the prompt is reliable, and the X-RAY shortlist is confidence-aware — the upstream brain-quality audit is complete but for that small tail.
