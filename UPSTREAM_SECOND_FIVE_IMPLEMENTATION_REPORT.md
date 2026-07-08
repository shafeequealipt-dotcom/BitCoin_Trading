# Upstream Brain-Quality Fixes — Second Five — Implementation Report

Date: 2026-05-27
Branch: main (direct-to-main, all commits pushed to origin/main)
Scope: the second batch of upstream brain-quality fixes — E16, #13, #8, #7, #19 — plus a verify-and-close of #5 and #10.

This batch follows the first five (issues #6, #9, #11, #2, #12), whose report sits beside this one. Everything below was implemented against the current code in the VM, verified offline, and shipped; the operator restarts the live system separately.

## One-paragraph summary

All five targeted issues are fixed, each in its own revertible commit with plain-language messages and added observability. Open positions no longer steal the brain's new-trade candidate slots (E16); the brain's core price and indicator block can no longer be trimmed away (#13); open interest now carries one honest, correct-magnitude value everywhere instead of a mislabeled one-hour number (#8); a structureless coin can no longer present to the brain as a top-grade setup (#7); and the strategy ensemble now weights its members by real track record, gradually and reversibly (#19). Two low-value issues, #5 and #10, were re-checked against live data and formally closed as no-fix-needed. Every per-issue self-check passes, the full test suite is green apart from five failures that pre-date this work and are unrelated to it, the direction-flip safety switches stay off, and the protected trading tables were never touched.

## Per-issue outcome

### E16 — open positions no longer crowd out new-trade candidates
- Problem: the find-new-trades call (Call A) capped its candidate list at a fixed number, but open positions were counted inside that cap. With seven positions and a cap of ten, only three slots were left for fresh ideas, so good new setups were silently squeezed out — and this also blunted the first-batch ranker fix (#2), which had no room to work.
- Fix: positions are removed from the candidate pool entirely and given their own dedicated, clearly-labeled "OPEN POSITIONS" block. New candidates get the full budget from the non-position pool, selected through the existing reserve-slots ranker.
- Observability: the STRAT_TOP_N_APPLIED log line now reports how many candidates were chosen and how many positions sit in the separate manage-block, so the independence is visible at runtime.
- Self-check (verify_issue_e16.py): PASS. With seven positions and twelve candidates at a cap of ten, the old path left three new-entry slots; the new path gives the full ten, with all seven positions preserved in the manage-block and none consuming a candidate slot.

### #13 — the brain's core price and indicator data is protected from trimming
- Problem: when a prompt grew too large, the trimmer dropped the lowest-priority sections first. The per-coin market-data lines (price, indicators) were appended as separate, unmarked sections, so they classified as "optional" and could be trimmed away — leaving the brain to decide with the core data missing.
- Fix: the per-coin market-data lines are now joined into the single, already-essential "MARKET DATA" section, so the existing essential-marker protects them. The dropped-section log was also made complete (it previously truncated to the first eight labels), so any future drop is fully visible.
- Observability: the bound section classifies as essential; the full drop list is logged.
- Self-check (verify_issue_13.py): PASS. Under simulated character-cap pressure the market data survives while genuine optional filler is dropped, and the final prompt stays under the cap.

### #8 — open interest now carries one honest, correct-magnitude value
- Problem: the open-interest tracker computed a one-hour-over-one-hour change but stored and labeled it as a 24-hour change. The five open-interest-gated strategies and the brain therefore saw a number roughly an order of magnitude too small, so their thresholds almost never tripped. The brain's own open-interest field was never populated at all.
- Fix: the tracker now sources its delta from the database — the exact same true ~24-hour delta the signal generator already uses (AltDataRepository, 23-hour lookback) — instead of the one-hour API delta. The field is renamed honestly (oi_change_24h_pct), the scanner now populates the brain's field, the brain renders it as OI_24h, and the five strategy thresholds were realigned to the real magnitude. The signal-generator path was deliberately left byte-for-byte unchanged.
- Observability: a value that is now correct-magnitude and a brain field that is now non-zero.
- Self-check (verify_issue_8.py): PASS. On live data the gap is stark — for example Bitcoin's old one-hour reading was about minus 0.65 percent while the new ~24-hour reading is about plus 11.9 percent (a sign flip and roughly eighteen times the magnitude); the new value is larger on five of six sampled coins. The signal path is confirmed untouched.

### #7 — structureless coins can no longer present as top-grade setups
- Problem: the X-RAY structure engine computed a setup score and grade independently from the setup classification. A coin classified as "no setup" (NONE) could still carry an A-plus grade and a score near 100, which then fed the scanner, the scorer, the brain prompt, and the top-setups ranker as if it were a high-conviction structure.
- Fix: right after classification, a coherence gate caps the grade and score when the setup is NONE (down to C, score at most 49) or when the classification confidence is below a small floor (A-plus or A down to B, score at most 64). Strong, high-confidence setups are untouched.
- Observability: an XRAY_SCORE_GATED log line fires whenever a score is capped, naming the symbol, setup type, confidence, and the before-and-after score and grade.
- Self-check (verify_issue_7.py): PASS. Across fifty live coins analyzed through the real structure engine, two were classified NONE and both were correctly capped; there were zero contradictory cases (no NONE-as-top-grade, no top-grade-with-zero-confidence).

### #19 — the strategy ensemble now weights members by real track record, gradually
- Problem: the ensemble combined its member strategies with equal weight, ignoring the fact that some strategies perform far better than others in a given market regime. A data-derived weighter existed but was switched off (shadow only).
- Fix: the data-derived regime weighter is now live. It is gradual by construction — a strategy needs at least twenty trades in a regime before its weight moves off equal, weights are smoothed (exponential moving average, alpha 0.3) and hard-bounded between 0.3 and 3.0, and everything is derived from persisted database tables, so it survives restarts and grows with track record rather than jolting the system. This is the one change that alters the consensus signal itself, so it is the most clearly labeled and the most cleanly reversible: set regime_weighting_enabled to false and restart.
- Observability: a boot sentinel states whether weighting is live or shadow, and the refresh log reports the live factor range so the effect is measurable.
- Self-check (verify_issue_19.py): PASS. Against the live database the deriver produced 88 cells, of which 43 have enough history to carry a real weight and 45 remain at equal weight (cold-start safe); all factors are bounded, and the overall factor range is a gentle 0.94 to 1.02 — confirming the immediate effect is small and will sharpen as track record accumulates.

## #5 and #10 — re-checked and closed (no fix needed)

- #5 (X-RAY section trimming): re-verified against the live brain logs — X-RAY content is essentially never the thing being trimmed, so there is no quality loss to recover here. The #13 fix above independently hardens the trimmer's protection of core data. Closed as no-fix-needed.
- #10 (alt-data staleness ordering): re-verified — the ordering is unchanged and benign; open interest refreshes on its normal cadence and no consumer reads a stale value in a way that affects a decision. Closed as no-fix-needed.

Both are documented closures, not silent drops; if either regresses later it would resurface in the same logs we checked.

## Verification performed

- Per-issue self-checks: all ten verify_issue scripts (the five from the first batch and the five from this batch) return PASS. Each script runs static wiring checks plus a behavioral or real-live-database read-only check; none mutate data.
- Full regression, run in memory-safe chunks (about 3,600 tests): green except for five failures that pre-date this work and are unrelated to any file changed here —
  1. test_apex_direction_lock.py — an APEX prompt expecting an "rsi_caution" phrase,
  2. test_positions_exchange_mode.py — a test pinning schema version 32 while the real schema is 40,
  3-5. three test_phase7 collection errors — they import src.brain.scheduler, which was removed long ago.
  These same five are recorded as pre-existing from the first-batch test battery.
- Two test alignments were required because two fixes intentionally changed behavior, and they are committed separately and transparently:
  - The layer-3 end-to-end test asserted equal-weight behavior; #19 turns weighting on by default, so the assertions were updated to the enabled reality (commit 8bede61).
  - Two open-interest tracker tests assumed the old one-hour delta (two points an hour apart, expecting a ~7 percent change); #8 makes that a true 24-hour delta, which needs about a day of history, so the tests now seed a prior reading about 24 hours old — the same way the dedicated repository-delta test does — and assert the real change (commit 3af364a). No production code was touched for either alignment.
- Cross-cutting confirmations: the three direction-flip safety switches remain off (xray_dir_flip_enabled, xray_trade_suppression_enabled, apex_dir_flip_enabled all false); the protected trading tables (trade intelligence, trade log, trade history, thesis store, virtual positions) were only grown by live trading, never altered by this work; the signal-generator open-interest path is unchanged; trade frequency and aggression are preserved (E16 widens, never narrows, the candidate funnel).

## Commits (all on main, all pushed to origin/main)

- e4e9fd6 — Fix E16: stop open positions eating the brain's new-trade candidate slots
- 446d5d2 — Fix #13: protect the brain's core price and indicator data from trimming
- 51a9e04 — Fix #8: give the strategies and brain a correct-magnitude open-interest value
- d1e3391 — Fix #7: stop structureless coins presenting to the brain as top-grade setups
- 81064c1 — Fix #19: turn on real, track-record-weighted ensemble consensus (gradual)
- 8bede61 — Align layer3 end-to-end tests with #19's enabled-by-default weighting
- 3af364a — Align OI tracker tests with #8's correct 24h-delta semantics

Each fix is an independent, revertible commit. The verify_issue scripts are committed alongside their fixes. Per-file timestamped backups were used during editing and removed after each commit; git history is the durable backup.

## Files changed (production)

- src/brain/strategist.py — E16 candidate budget and OPEN POSITIONS manage-block; #13 market-data binding and complete drop logging; #8 OI_24h rendering; #19 boot sentinel.
- src/analysis/structure/structure_engine.py — #7 producer-side coherence gate.
- src/intelligence/altdata/open_interest.py — #8 DB-sourced delta.
- src/core/coin_package.py — #8 honest field name (oi_change_24h_pct).
- src/workers/scanner_worker.py — #8 populates the brain's OI field.
- src/strategies/ensemble.py and src/strategies/regime_weighter.py — #19 live boot sentinel and live factor-range observability.
- config.toml — #19 regime_weighting_enabled = true (one-flag rollback).
- The five open-interest-gated strategy threshold sites — #8 realignment.

## Rollback

- #19 (the only consensus-signal change): set regime_weighting_enabled to false in config.toml and restart. The deriver returns to shadow; no data migration needed.
- Any other issue: revert its single commit. Each is self-contained.

## What this brings

The brain now sees a fuller candidate field, a protected core-data block, an honest and correctly-scaled open-interest signal, X-RAY grades that mean what they say, and a consensus that leans — gently and reversibly — on the strategies with the best real track record in the current regime. None of it changes the brain's decision logic, the execution path, or trade aggression; it makes the inputs the brain reasons over more truthful and more complete.
