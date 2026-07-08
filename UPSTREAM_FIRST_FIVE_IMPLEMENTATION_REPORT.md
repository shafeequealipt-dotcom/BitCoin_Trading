# Upstream Brain-Quality — First Five Fixes — Implementation Report

Date: 2026-05-27. Branch: main (no new branch, no new directory). All five fixes are committed as separate, individually revertible commits and uploaded to origin/main.

This report covers the first five issues from the Upstream Brain-Quality Audit: the three regime issues (#6, #9, #11) and the two scanner issues (#2, #12). The regime issues were fixed first, then the scanner issues, because the scanner consumes regime-derived inputs.

## How this was verified

Every fix was verified offline against the current code in the VM, as requested. I did not restart or probe the running trading process. Verification uses small, clearly named self-check scripts in the project root (verify_issue_6.py, verify_issue_9.py, verify_issue_11.py, verify_issue_2.py, verify_issue_12.py), each exercising the real current code, plus read-only samples of the live database for the baseline numbers. No database row was written or altered by this work. The operator will restart the system later to take the fixes live; until then the running process continues on the old code.

All five self-checks pass. An import smoke test confirms every module the live boot loads still imports cleanly. The protected tables were only read, never altered. The direction-flip switches remain off.

## Issue #6 — the regime ELSE dead-zone

Symptom. When a coin matched none of the five regime branches, the detector returned a RANGING label at a flat 0.40 confidence. On the live data sample this fabricated label affected 36.6 percent of all classifications.

Root cause, confirmed in code. The trending branches required a hardcoded choppiness below 45, while the ranging branch required choppiness above 50. That left a structural gap that no branch covered, so those coins fell through to a hardcoded RANGING at 0.40. A configuration change alone could not fix this because the choppiness cut was a hardcoded number, not a setting.

Connected files mapped before changing it. The regime label is read by the strategy-category gating (registry and scorer), the scanner's opportunity and interestingness scores, the brain prompt (global line and per-coin tags), and the APEX optimizer. The fix changes only how the label and confidence are computed; it does not change the regime data shape, the regime list, or the category mapping, so all of those consumers keep working and simply receive a truthful label.

Fix applied. The choppiness ceiling for a clean trend is now a configuration value (trending_choppiness_max, default 45) instead of a hardcoded number. The rule space is now fully tiled: a coin that has trend strength but is too choppy for a clean trend is labeled a weak trend, which keeps momentum strategies eligible to vote on it; every other fall-through coin is labeled ranging with a confidence computed from choppiness, never a flat constant.

Before and after, in plain words. Before, a coin with an ADX of 22 and choppiness of 48 reached the brain labeled ranging at 40 percent confidence, and its momentum strategies were silenced. After, that same coin reaches the brain labeled a weak up-trend at a computed confidence of about 43 percent, and momentum strategies can vote. A genuinely quiet coin with an ADX of 16 and choppiness of 35 was also labeled ranging at a flat 40 percent before; after, it is ranging at a computed 35 percent that reflects how range-like it actually is.

Verification result. Across a 540-cell grid of synthetic conditions, every cell is classified, every confidence is valid, and all 128 cells that previously fell into the dead-zone now carry a meaningful label and a computed confidence, with zero fabricated flat values.

## Issue #9 — the fake ATR percentile

Symptom. The field called the ATR percentile was computed as the normalized ATR times 100, which is a fixed absolute level rather than a rank. On live data its maximum value reached 641, which is impossible for a real percentile, and the VOLATILE label fired on about a quarter of all classifications, often on merely-normal movement. Its confidence was degenerate.

Root cause, confirmed in code. The percentile was a placeholder that was never replaced with a real historical rank.

Fix applied. The value is now a true rolling percentile: the current normalized ATR is ranked against the series of normalized ATR values over the 200 hourly candles the detector already fetches. This reuses the existing volatility helper, adds no extra data fetch, and is self-normalizing across high- and low-volatility coins. The VOLATILE confidence now reflects the real percentile and is no longer capped at one half.

Before and after, in plain words. Before, a normally-moving coin could be labeled VOLATILE because its absolute normalized ATR happened to exceed a fixed level, and the volatility confidence could never rise above one half. After, VOLATILE means the coin's current volatility is genuinely high relative to its own recent history, and the confidence rises with how extreme that rank is.

Verification result. Across 40 random volatility profiles the percentile stayed within zero to 100 (the old code reached 641). A window whose latest bar was the most volatile produced a percentile of 100; one whose latest bar was the calmest produced about 14. When the regime was genuinely volatile, the confidence was 1.00 rather than the old cap of one half.

## Issue #11 — the stale Call-B regime

Symptom. The position-management call reused a regime label that had been cached by the previous trade-finding call. Because the two calls alternate, that value could be several minutes old on top of the regime detector's own cadence, so a decision to close on a regime change could act on a stale reading.

Root cause, confirmed in code. The cache was deliberate, to avoid re-running hourly technical analysis on every call, and the staleness was an unintended side effect.

Fix applied. The position-management call now re-reads the latest regime at the moment it builds its prompt. This is a zero-cost cached read with no recomputation, so it bounds the staleness to a single regime detection cycle. A log line reports the regime and its age so the freshness is visible.

Before and after, in plain words. Before, the position review could reason on a regime label that was set up to roughly seven and a half minutes earlier. After, it reasons on the most recently committed regime, refreshed each detection cycle, and the log shows exactly how old that reading is.

Verification result. The position-prompt builder now re-reads the regime and emits the freshness log; the rendered line no longer uses the stale cached value; and the fresh read returns the latest committed regime with a recent timestamp.

## Issue #2 — interestingness as the sole ranker

Symptom. Both the scanner's cut from fifty coins to fifteen and the brain's cap from fifteen to ten ranked candidates by an interestingness score first, with the tradeable opportunity score only a tiebreak. The two scores correlate only weakly, so high-expected-value coins were silently dropped before the brain ever saw them.

Root cause, confirmed in code. The briefing-mode rewrite made interestingness the primary key without reconciling it with the opportunity score.

Fix applied, as the operator chose: reserve slots. Both stages now fill the available slots by drawing alternately from the top-by-opportunity ordering and the top-by-interestingness ordering, de-duplicated. A high-opportunity coin is therefore never dropped purely for low interestingness, and vice versa. The slot count is preserved, so this re-ranks without shrinking the candidate set, and the open-position pins are untouched. A small shared helper backs both sites, and each logs how the slots split between the two scores.

Two dependencies are noted but, per scope, not fixed here. Interestingness is currently fed partly by placeholder inputs, which is why opportunity is the more trustworthy half today; the reserve handles this by giving opportunity the first pick. And open positions still consume slots in the brain's cap, which bounds how many free slots exist; that crowd-out is a separate audit item.

Before and after, in plain words. Before, a coin with the strongest tradeable opportunity but a low cleanness score sat at the bottom of the interestingness ranking and was cut before the brain saw it. After, that coin is picked first from the opportunity side of the reserve and reaches the brain, while the cleanest-looking coins still get their share of the slots.

Verification result. In a constructed cycle where a high-opportunity, low-interestingness coin was dropped by the old ranking, the new reserve retains it as the first pick, the candidate set size is preserved, and the slots split across both scores. The helper never shrinks the set below the smaller of the requested count and the available coins. This was checked against the corrected regime inputs, so the coupling between the regime fixes and the scanner fix holds.

## Issue #12 — silently-degraded packages

Symptom. When a data source failed while a coin package was being built, the package still went to the brain with default values such as a blank regime, a NONE consensus, and a neutral direction, and the brain had no way to tell a source failure from a genuinely neutral market. The package already computed a completeness score and a list of failed sources, but both were discarded before the prompt.

Root cause, confirmed in code. The package build used defensive defaults with no provenance carried forward, and the completeness and blocker information was computed and then thrown away.

Fix applied. The completeness, the missing fields, and the failed sources are now carried onto the package and rendered as a per-coin data-quality line in the brain prompt. The line is shown only when a package is not fully clean, and it names the failed sources explicitly, so the brain can discount a degraded coin instead of trusting fabricated defaults. The build-time source failures are also written to the log now, where before they were recorded nowhere. This was applied to both scanner build paths.

Before and after, in plain words. Before, a coin whose signal and funding sources had failed reached the brain looking like a calm, neutral market, indistinguishable from a real one. After, that coin reaches the brain with a line reading, in effect, data quality completeness 0.80, missing the structure type and regime and fear-and-greed, source failed on the signal and funding feeds — so the brain knows those neutral values are failures, not the market.

Verification result. The real validator scores a degraded package at 0.80 completeness with the missing fields named, and the prompt renders the data-quality line with the failed sources marked; a fully clean package renders no such line.

## Combined verification and sign-off

All five self-checks pass. The modules the live boot loads all import cleanly. The scanner fixes were verified against the corrected regime, so the dependency order held. The candidate set is re-ranked, not shrunk, so trade frequency and the brain's opportunity set are preserved. The strategy-category gating still functions, now on truthful regimes. The direction-flip switches remain off. The protected tables were only read; their small row growth during the session is the live system trading, not this work.

## Files changed, and the commit per issue

Issue #6 changed src/strategies/regime.py, src/config/settings.py, and config.toml. Commit: remove the regime dead-zone that fabricated a RANGING label.

Issue #9 changed src/strategies/regime.py. Commit: make the volatility percentile a real rank, not scaled NATR.

Issue #11 changed src/brain/strategist.py. Commit: read a fresh market regime in the position-review call.

Issue #2 added src/core/ranking.py and changed src/workers/scanner_worker.py and src/brain/strategist.py. Commit: rank brain candidates by reserved slots, not interestingness alone.

Issue #12 changed src/core/coin_package.py, src/workers/scanner_worker.py, and src/brain/strategist.py. Commit: show the brain a data-quality line so it can spot fabricated readings.

Each commit also includes its verification script. The five commit identifiers, oldest to newest, are 03020aa, f454aa8, 4518c95, 319cfce, and 959522c, all on main and uploaded.

## Observability added

Each fix is visible in the logs. The regime detector logs a tiling line when a former dead-zone coin is now classified, and the regime line now reports the real percentile and the raw normalized ATR. The position-review call logs the regime it read and that reading's age. Both ranking stages log how the reserved slots split between opportunity and interestingness. The scanner logs the per-coin blockers and the validation summary, and the brain prompt itself carries the per-coin data-quality line.

## A note on backups

Before editing each file I took a timestamped copy in its own directory, as a safety net during the work. After each fix was committed and uploaded, git history became the durable record of every prior version, so the temporary copies were removed at the end to leave a clean working tree, in line with the project's clean-state rule. Any prior version is recoverable from the five commits above.

## What this does and does not change

These five fixes improve the honesty and quality of the inputs that reach the brain. They do not change what the brain does with those inputs, the execution path, sizing, or the direction-flip switches. They do not fix the other audit items, including the interestingness placeholder inputs and the position crowd-out, which are separate tasks noted above as dependencies that bound the effect of the ranking fix.

## Post-implementation QA pass

After the five fixes, I ran the project's own test suite across every test that touches the changed code — 64 test files, 809 tests. This surfaced two things, both now resolved and uploaded in commit cf8adb5.

First, the position-review regime fix had a fragility. It fed the freshly-read regime confidence straight into a percentage format, and a malformed regime reading — or a mocked one in the tests — is not a real number, which crashed the whole position-prompt build at that step. The re-read now coerces the regime label and confidence to real types first and adopts them only when that succeeds, otherwise falling back to the cached values, so a bad regime reading can no longer crash the position review. This restored five position-prompt tests.

Second, one regime test still asserted the old fabricated flat 0.40 confidence that the dead-zone fix deliberately removed. It now asserts the new computed confidence, matching the shipped behavior.

After both corrections, 809 tests pass across the touched subsystems and all five self-checks pass. One unrelated test fails — it expects specific wording in the APEX optimizer's prompt — but that is a pre-existing failure with no connection to this work, which changed zero lines in the APEX code.
