# Upstream Brain-Quality Fixes — Third Five — Implementation Report

Date: 2026-05-27 (completed across the midnight boundary into 2026-05-28)
Branch: main (direct-to-main, all commits pushed to origin/main)
Scope: the third batch of upstream brain-quality fixes — E12, E10, E11, E18, E17 — completing the first batch's #12 provenance work and the second batch's #7 X-RAY coherence work.

## One-paragraph summary

All five targeted issues are fixed, each in its own revertible commit with plain-language messages and added observability. The package data-quality system now works: the validator counts the failure-defaults so completeness is meaningful (E12), the stale-fields signal now reaches the brain on the existing data-quality line (E10), and a per-cycle source-failure heat-map is logged (E11). And APEX no longer trusts structureless coins: the A-plus size boost is withheld when structural confidence is missing (E18), and a coin with a high score but near-zero confidence is rejected without ever culling a legitimate aggressive entry (E17). During verification a separate, pre-existing production bug surfaced — the open-interest 24-hour delta collapsed to about zero for an hour each day near midnight UTC due to a timestamp-format mismatch — and with the operator's approval its root cause was fixed too. Every per-issue self-check passes, the full test suite is green apart from the same pre-existing failures unrelated to this work, the direction-flip switches remain off, and no protected table was touched.

## Re-verification corrections (the audit, checked against current code)

Per the spec's instruction to re-verify rather than trust the audit, three findings reshaped the work:

- E10 was already mostly done by the first batch's #12. The brain prompt's per-coin "Data quality" line already renders completeness, missing fields, and failed sources. The one provenance value still dropped was stale_fields, so E10 became a one-line addition to that existing line rather than a new render.
- E11 was partly done. A per-package blocker log already exists. The residual was a per-cycle aggregate, so operators can see which sources fail and how often across a cycle, not just one line per package.
- E17 had to be reframed after #7. Because #7 already caps a no-structure setup's score at the producer, the classic high-score/zero-confidence coin now arrives at APEX already demoted. So E17 is a precise belt-and-suspenders net for the residual leak (the structure classifier raised and its cap was skipped, or a caller stamped a score directly), defined so it can never touch a legitimate trade.

## Per-issue outcome

### E12 — the validator now counts the decisive failure-defaults
- Problem: the completeness score checked freshness, price, and structural levels, but never a NONE consensus, a neutral direction, a zero funding, or a zero confidence — so a coin shipping neutral-by-failure still scored a perfect completeness. Live data confirmed it: essentially every package scored 1.00.
- Fix: four new checks for exactly those defaults, each firing ONLY when there is corroborating failure evidence (a build blocker was recorded, or the confidence is exactly 0.0, which a real reading virtually never is). A genuinely-neutral-but-real package is not penalised; a failure-defaulted one is.
- Before and after: before, a package built after its signal and funding sources errored shipped with a NONE consensus and zero confidence yet scored a perfect 1.00 and looked healthy. After, the validator counts those four defaults, the completeness drops to about 0.63 with the four fields named, and the brain sees the data-quality warning — while a real coin that simply has no setup this cycle (no blocker, a real 0.55 confidence) still scores about 0.95 and is untouched.
- Frequency safeguard: failure-defaulted packages land in a warning band (about 0.63), not the quarantine band, so they still reach the brain (discounted, not dropped). Because that honest lower score also feeds a batch-wide cold-start gate, the two cold-start average thresholds were relaxed (0.85 to 0.70, boot-grace 0.95 to 0.80) so the new scores cannot block the whole new-trade batch; the per-package floor and minimum-package count still guarantee a warm cache. This interaction is documented in the assistant memory.
- Observability: a boot sentinel (PACKAGE_VALIDATOR_FABRICATION_CHECKS_ACTIVE), and the existing per-package log now names the new fields.
- Self-check (verify_issue_e12.py): PASS — clean 1.00, fabricated 0.63 warning with the four names, real-neutral 0.95 with none of them.

### E10 — stale-fields now reaches the brain
- Problem: the validator computed stale_fields (data present but past its freshness window) but it was never rendered, even though #12 already rendered completeness, missing fields, and failed sources.
- Fix: stale_fields is appended to the same data-quality line (no second block), so the brain can tell a stale reading from an absent one.
- Before and after: before, a coin whose price block was stale showed nothing about it. After, the line reads, for example, "Data quality: completeness=0.80 stale=['built_at']".
- Self-check (verify_issue_e10.py): PASS — the stale token renders, nothing renders for a clean package, and there is still exactly one data-quality line.

### E11 — a per-cycle source-failure heat-map
- Problem: per-package blockers were logged one line at a time only when a package had them; there was no way to see which sources fail and how often across a cycle.
- Fix: a per-cycle aggregate, PACKAGE_BLOCKER_HEATMAP, counts each blocker label across all coins scanned in the cycle (including quarantined ones in the live path), logged once per cycle, sorted by frequency, only when something failed. Added in both scanner modes.
- Before and after: before, a degraded funding source produced scattered per-coin lines and no overview. After, one line per cycle reads, for example, "by_label=[signal_missing=2, funding_missing=1]".
- Self-check (verify_issue_e11.py): PASS — the aggregation produces the right counts and stays silent when nothing failed.

### E18 — the A-plus size boost has a confidence floor
- Problem: any coin scoring 80 or more got a 20 percent size boost with no check on structural confidence, so a structureless high-score coin was upsized — risk on the weakest setups. The existing structural ladder even left zero-confidence neutral, so there was no offset.
- Fix: the boost fires only when X-RAY structural confidence is at or above a floor (set live to 0.70); otherwise it is withheld and logged. It only withholds the multiplier — it never blocks a trade.
- Before and after: before, a coin with score 100 and confidence 0.0 was sized up 20 percent. After, that boost is withheld (logged A_PLUS_BOOST_WITHHELD) while a genuinely confident A-plus is still boosted; neither is rejected.
- Self-check (verify_issue_e18.py): PASS, confirmed on the real gate — a weak-confidence A-plus has its boost withheld and is not rejected; a confident A-plus keeps its boost.

### E17 — a precise structureless-high-score reject
- Problem: the zero-conviction reject required confidence AND score AND reward-to-risk to all be low, so a high score with zero confidence survived.
- Fix: a second reject, placed after the existing one (which is left exactly as-is), fires only on the contradiction — structural confidence at or below a near-zero floor AND score at or above a high floor (65, just past the score #7 allows for a weak-confidence setup). A real aggressive entry always carries genuine confidence, so it can never match.
- Before and after: before, a high-score/zero-confidence coin passed the gate and could be traded. After, it is rejected with reason structureless_high_score, while a legitimate aggressive entry (confidence 0.55, score 70) is kept, and a coin #7 already capped to 49 is not double-rejected.
- Over-reject safeguard: the live values (confidence floor 0.05, score floor 65) are inert on any real trade, and the dataclass defaults disable the guard entirely (floor 0.0, score 999). Real-gate check confirms the legitimate entry is not culled.
- Self-check (verify_issue_e17.py): PASS — structureless rejected, aggressive kept, capped coin not double-rejected.

## Discovered and fixed: the open-interest midnight-delta bug (operator-approved, outside the five)

While verifying, two open-interest tests failed because it was near midnight UTC. Root cause: stored open-interest timestamps use the space format from SQLite datetime('now'), while the lookback cutoff is built with Python isoformat (with a 'T'). For about an hour each day, when the 23-hour cutoff falls on the current calendar date, a raw string compare made the space-format latest row sort at or below the 'T'-format cutoff, so the query picked the newest row as its own prior and the 24-hour delta collapsed to about zero — silently weakening the open-interest signal for that window. This is a pre-existing bug (the delta path predates this batch; none of the five fixes touch it), surfaced by a second-batch test seed at the date boundary. With the operator's approval the root cause was fixed: both the ordering and the comparison now normalise each timestamp with SQLite datetime(), which parses the space format, the 'T' format, fractional seconds, and timezone to one canonical form — correct at every hour and robust to the mixed formats already in the table. A pinned-to-23:55-UTC regression test locks it (returned 0.0 before, 4.1667 percent after), and the live database delta now computes correctly near midnight (Bitcoin about +12.3 percent rather than 0).

## Verification performed

- Per-issue self-checks: all five Third-Five verify scripts return PASS, plus the second batch's open-interest verifier after the midnight fix.
- Full regression, run in memory-safe chunks: green except the same pre-existing failures unrelated to this work — an APEX prompt expecting an "rsi_caution" phrase, a test pinning schema version 32 while the real schema is 40, and three test_phase7 collection errors that import a long-removed module. The third batch introduced zero new failures.
- A cold-start test alignment was required and committed separately because E12 intentionally relaxed the two batch-wide average thresholds; the tests that pinned the old values were updated, with the block-demonstrating cases recalibrated below the new floors. Test-only.
- Smoke test: all changed modules import cleanly, the config parses, and the live thresholds are confirmed (A-plus floor 0.70, structureless floor 0.05 and score 65, cold-start 0.70 and 0.80).
- Aim confirmation: trade frequency and aggression are preserved — E12 keeps failure-defaulted packages in the candidate set with a warning (not quarantined) and the cold-start gate is relaxed; E17 is inert on legitimate trades by construction; E18 only withholds a size multiplier and never blocks. The direction-flip switches remain off; no protected table was altered.

## Commits (all on main, all pushed to origin/main)

- c9a8902 — Fix E12: make the package validator count failure-defaults so completeness is meaningful
- e4351b5 — Fix E10: render the package stale-fields on the brain's data-quality line
- 6adefed — Fix E11: log a per-cycle source-failure heat-map so operators see which sources fail
- 5c72509 — Fix E18: withhold the A-plus size boost when structural confidence is missing
- 8e15dcf — Fix E17: reject a structureless coin that carries a high score but no confidence
- fb4f386 — Align cold-start tests with E12's relaxed average-completeness thresholds
- 4329745 — Fix the open-interest 24h-delta collapse near midnight UTC (timestamp format mismatch)

Each fix is an independent, revertible commit. The verify scripts ship alongside their fixes. Git history is the durable backup; no per-file backup clutter was left.

## Files changed (production)

- src/core/coin_package_validator.py — E12 four blocker-gated checks plus the completeness math and docstrings.
- src/brain/strategist.py — E10 stale-fields appended to the existing data-quality line.
- src/workers/scanner_worker.py — E11 per-cycle blocker heat-map in both scanner paths; E12 boot sentinel.
- src/apex/gate.py — E18 confidence floor on the A-plus boost; E17 structureless reject after the existing one; the structureless-guard boot sentinel.
- src/config/settings.py — E18 and E17 settings fields; the relaxed cold-start defaults.
- config.toml — E18 floor 0.70 live, E17 floor 0.05 and score 65 live, cold-start relax.
- src/database/repositories/altdata_repo.py — the open-interest midnight-delta fix.

## Rollback

- E18: set gate_a_plus_conf_floor back to 0.0 (boost always applies).
- E17: set gate_structureless_score_min back to 999 (guard never fires).
- E12: revert the commit; the cold-start thresholds restore with it.
- Any other issue: revert its single commit. Each is self-contained.

## What this brings

The brain now sees an honest data-quality picture — completeness that means something, the stale and failed sources called out, and a per-cycle view of which sources are failing — and APEX concentrates risk on validated setups, never upsizing or even accepting a coin that scores high with no structure behind it. None of it changes the brain's decision logic, the execution path, or trade aggression; it makes the inputs more truthful and the risk placement more disciplined. The open-interest signal is also now correct around the clock.
