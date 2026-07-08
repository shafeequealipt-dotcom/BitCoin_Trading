# Upstream Brain-Quality — Fifth Five Implementation Report (The Loose-Ends Completion Batch)

**Date:** 2026-05-28
**Branch:** main (direct-to-main, atomic commit per issue/pair)
**Spec:** `IMPLEMENT_UPSTREAM_BRAIN_QUALITY_FIFTH_FIVE.md`
**Scope:** E7 + E21 (completes #8), E22 (completes #13), E25 (completes #6/#9/#11), E28 (completes #19)

## Honest value framing

This is the completion pass: four prior batches fixed the core of four subsystems; each left one live loose end this batch closes. Re-verification against the current code (post batches 1-4) materially changed the shape — two of the five were already substantially done by their predecessor:

- E7 and E21 were already satisfied by #8's companion work; this batch is confirm + observability, not a re-fix.
- E28's cap was already fully wired; this batch enables it (a value change), with minimal live effect while regime-weighting is off.
- E22 and E25 are the genuinely-new fixes.

Frame the value as meaningful completion, not a new bleed. None reduces trade frequency or aggression.

## Predecessor confirmations (Rule 2)

All four predecessors confirmed present in the current code: #8 (`altdata_repo._compute_oi_delta_pct`, 24h delta), #13 (`_TRIM_ESSENTIAL_MARKERS` + `_infer_section_priority`), #6/#9/#11 (regime.py dead-zone tiling / ATR percentile / fresh Call-B), #19 (`regime_weighter` per-(strategy,regime) factors; `regime_weighting_enabled` currently False/shadow).

## Commits (all pushed to origin/main)

| Order | Commit | Issue | Live effect today |
|------|--------|-------|-------------------|
| 1 | `fc4025c` | E7 + E21 | none (already wired by #8; adds an observability sentinel) |
| 2 | `038c9ca` | E22 | held constraint now trim-proof (prevents erroneous duplicate entries only) |
| 3 | `df31b9a` | E25 | brain regime label now matches the scores it shows |
| 4 | `af552d2` | E28 | cap enabled at 0.4; rarely binds while weighting is off |

---

## E7 + E21 — open-interest wiring + strategy reactivation (`fc4025c`, completes #8)

- **Symptom (audit):** the brain prompt's open-interest field was always zero, and three OI-gated strategies never fired.
- **Confirmed status (current code):** ALREADY SATISFIED by #8's companion. The brain field is assigned from the corrected source — OI fetch → `_oi_cache` → `altdata_worker.get_oi()` → `scanner_worker.py:832` (`alt.oi_change_24h_pct = ...`) → `CoinPackage.alt_data` → brain render `strategist.py` `OI_24h=`. The three strategies (`D2_oi_divergence` `<-2%`, `F3_liquidation_hunt` `>5%`, `G3_liq_frontrunner` `>8%`) read the same corrected `oi_change_24h_pct`.
- **Live data proof:** OI is fresh (251,847 rows, 50 symbols, current). The real 24h-delta distribution crosses all three thresholds — D2 `<-2%` on 16 symbols, F3 `>5%` on 6, G3 `>8%` on 5 (median 2.52%, max 11.25%). They fire on real moves.
- **Fix applied (operator decision: confirm + observability only, no threshold change):** added the `OI_BRAIN_WIRED` per-cycle sentinel in `scanner_worker` (oi_nonzero/oi_zero across delivered packages). E21 fire/non-fire is already observable via `STRAT_L1_DONE` `top_firing`/`non_firing`.
- **Before/after (plain prose):** Before #8, every prompt showed open-interest at exactly 0.00% and the brain had no derivatives signal, and the OI strategies sat in `non_firing`. After #8 (confirmed here), the brain sees real, varying OI per coin, and the OI strategies appear in `top_firing` when real moves occur. This batch makes that visible per cycle.
- **Verification:** `verify_issue_e7_e21.py` PASS (static wiring + live-DB threshold crossing).

## E22 — protect the held-symbols hard constraint from trim (`038c9ca` + `<correction>`, completes #13)

- **Honest finding (cross-check correction):** like E7/E21, E22 was ALREADY satisfied in the live path by a predecessor — here the E16 fix (2026-05-27). The live brain calls `create_trade_plan` (Call-A → `_build_trade_prompt`) and `create_position_plan` (Call-B → `_build_position_prompt`); it does NOT call `create_strategic_plan` / `_build_context_prompt` (legacy, used only by `scripts/run_30min_test.py`; the code itself comments "the dead `_build_context_prompt`" at strategist.py:3234). The live Call-A already surfaces the held constraint under the ESSENTIAL `## OPEN POSITIONS` header (E16, strategist.py:3838; its comment notes it "also addresses companion E22"), and the live Call-B is a compact prompt with no priority trim. So there was no live trim gap remaining.
- **Symptom (audit / legacy path):** the legacy `_build_context_prompt` appends the held constraint as a plain `"You ALREADY HOLD: ..."` section with no `##` header, so `_infer_section_priority` defaulted it to OPTIONAL and the trimmer could drop it.
- **Fix applied:** added the substring marker `"ALREADY HOLD"` to `_TRIM_ESSENTIAL_MARKERS`, reusing #13's mechanism (no parallel mechanism). This classifies the legacy block ESSENTIAL so it never trims. The marker is a unique substring (only the held block uses it), so it cannot mis-protect another section. **Net: correct and harmless, but it hardens the LEGACY path; the live path was already covered by E16.** The commit-message framing was corrected to say so (follow-up commit); no revert (the marker is a harmless belt-and-suspenders for the legacy single-call path).
- **Before/after:** Live behavior is unchanged (E16 already prevents the brain from being told to re-enter a held coin). On the legacy single-call path, the held list now always survives a trim.
- **Verification:** `verify_issue_e22.py` PASS (real `_infer_section_priority` → ESSENTIAL for the exact held block; unmarked control stays OPTIONAL); 66 trim/priority tests green. (Limitation noted: the verifier exercises `_infer_section_priority` on the block text, not the path's reachability — which is why the dead-path scope was caught only at cross-check.)

## E25 — single fresh per-cycle scoring regime shared scorer → brain (`df31b9a`, completes #6/#9/#11)

- **Symptom:** the strategy worker scores each coin under a fresh per-cycle regime; the brain rendered a regime LABEL re-read from the detector cache (updated independently by the RegimeWorker). So for the same coin in the same cycle the votes/scores the brain reads could have been computed under a different regime than the label shown beside them — an internal inconsistency the brain cannot detect.
- **Root cause:** two independent regime-access patterns (fresh scorer detect vs cached brain read).
- **Fix (per-coin consistency by construction; fresh, not stale; does NOT touch `price_data.regime`/the state-labeler):** added `StrategiesBlock.scoring_regime`; `strategy_worker._build_per_coin_consensus` now tags each coin with the regime it was scored under (the same `coin_regimes`/`global_regime` snapshot the scoring loop used); the scanner carries it onto the package; the brain's Call-A rich block renders it as the label (`_reg_str = _score_reg or _cache_reg_str`), keeping the cache's live metric fields. Falls back to the cache when a coin was not scored this cycle (frequency never reduced).
- **Before/after:** Before, on a coin whose regime drifted mid-cycle the brain could read scores from one regime with a label from another. After, the label is the regime the scores were computed under — they align.
- **Observability:** `E25_SCORING_REGIME_TAGGED` (scorer, per cycle) + `E25_REGIME_SNAPSHOT` (brain, on drift — proof the label realigned to the scores), joinable by symbol.
- **Verification:** `verify_issue_e25.py` PASS (real scorer tags per-coin override + global fallback; carry round-trips); `tests/test_e25_regime_snapshot.py` 5/5.

## E28 — single-strategy dominance cap 1.0 → 0.4, enabled (`af552d2`, completes #19)

- **Symptom:** the dominance cap (`single_strategy_max_share`) was wired but set to 1.0 (disabled), so a STRONG consensus could in principle be driven by one strategy rather than genuine breadth.
- **Root cause:** the cap disabled.
- **Fix (operator decision: 0.4, enforced):** set `config.toml` `single_strategy_max_share = 0.4` — no single strategy may supply >40% of a side's agreeing total, so a STRONG requires breadth (~3 strategies). Added `ENSEMBLE_DOMINANCE_CAP_BOUND` sentinels in BOTH the live and the regime-weighted shadow contribution paths (the shadow becomes live when `regime_weighting_enabled` flips on). The cap FORMULA is unchanged (math-identical restructure to carry the voter for the log).
- **Honest scope:** with `regime_weighting_enabled=false`, a single equal-weight voter contributes ≤~1.0 vs the STRONG floor 4.0, so it cannot drive STRONG alone today — the cap rarely binds. Its real value is robustness for when weighting is enabled. Frequency-safe: a balanced multi-voter STRONG (e.g. 5×0.9=4.5) is preserved; only a dominant-voter masquerade is downgraded.
- **Before/after:** Before, a one-strategy-heavy agreeing total could present as STRONG. After, a single strategy is clamped to ≤40% of the side, so STRONG means several strategies agree; a genuinely broad STRONG is unaffected.
- **Verification:** `verify_issue_e28.py` PASS (live config 0.4; both sentinels; single-dominant `[9,0.2,0.2]`→0.667 downgraded below the floor; balanced `[0.9×5]`→4.5 preserved). The cap formula is covered by `test_ensemble_single_strategy_cap.py`. The layer3/shadow weighting tests were isolated from the cap (they test weighting, not the cap); `test_real_settings_carry_vote_trace_and_cap` updated to assert 0.4.

---

## Verification summary

- All four verifiers PASS: `verify_issue_e7_e21.py`, `verify_issue_e22.py`, `verify_issue_e25.py`, `verify_issue_e28.py`.
- Full A-Z regression (top-level `tests/test_*.py`, 4 chunks): **1802 passed.** Non-passing items are all pre-existing / environmental, NONE attributable to this batch:
  - 1 fail `test_apex_direction_lock::test_system_prompt_still_has_rsi_caution` (asserts on `STRATEGIST_SYSTEM_PROMPT`, untouched).
  - 2 fails `test_j1_position_reconciler` + ~4 collection errors: the Python-3.10 sandbox artifact `from datetime import UTC` (3.11-only) or the removed `src.brain.prompt_builder`. These run/collect on the 3.11 deployment VM.
- Live-object cross-check (real `Settings._load_fresh()`): cap=0.4; `StrategiesBlock.scoring_regime` present; `AltDataBlock.oi_change_24h_pct` present; a real `EnsembleVoter` constructs with the live cap.
- Completions build on #8/#13/#6-9-11/#19 (predecessors confirmed present), not duplicating them.
- Flip switches still off; protected tables untouched (no writes; only reads); frequency/aggression preserved (E28 not culling balanced STRONGs; E22 only preventing erroneous duplicates).

## Observability added

`OI_BRAIN_WIRED` (E7, scanner per cycle); `STRAT_L1_DONE` top_firing/non_firing already covers E21; `CLAUDE_PROMPT_TRIMMED` protected-kept covers E22; `E25_SCORING_REGIME_TAGGED` + `E25_REGIME_SNAPSHOT` (E25); `ENSEMBLE_DOMINANCE_CAP_BOUND` path=live/shadow (E28).

## Honest assessment of the genuinely-remaining tail (Part F sign-off)

The four subsystems are now complete. The remaining audit tail is low-value and out of scope here; a small optional cleanup pass is the operator's call:

- **E19 (X-RAY shortlist ranking by score):** a ranking refinement; low/medium value.
- **E4 (legacy brain path), E5 (dead open-interest computation), E6 (dead regime config):** pure dead-code / misleading-config cleanup; low value, low risk; bundle into a future hygiene pass.

None of these is an active bleed. The genuine PnL leverage remains the separate, higher-priority work (PnL-accounting truth, over-tightening, entry R:R / wd_timeout) — not part of the upstream brain-quality completion. With this batch, the brain's inputs are as complete, consistent, and honest as the audit can make them.
