# INVESTIGATE AND TUNE — Why The Already-Live Adaptive Exit Still Clips Winners Now That The Coins Move (Find From The Logs And The Code Why The Movement Unit R Reads Too Small To Capture The Real Moves, And Recalibrate The R Measurement And Geometry To Match), Layer By Layer (Main Branch, No New Branch)

## STOP. READ THIS ENTIRE DOCUMENT BEFORE WRITING ANY CODE.

### The situation we are in — what changed and what is now in front of us

Two major fixes have shipped and are live. First, the daily universe-refresh system now rebuilds the 50-coin universe around coins that actually move, and it works: the coins being traded are fresh movers, and they peak far higher than before. In the captured window (2026-06-17 01:30 to 2026-06-18 05:30 UTC), trades peak as high as plus 3.08 percent, 61 percent of trades peak above plus 0.3 percent, 37 percent above plus 0.5 percent, 14 percent above plus 1.0 percent, and 3 percent above plus 2.0 percent. Compared to the old windows (median peak about plus 0.23 percent, nothing above plus 0.55 percent), this is a transformation: the coins finally move enough to make real money.

Second, the R-based adaptive exit is also already built and live. The logs confirm it: the ladder computes a per-coin movement unit R and derives the arm, the lock, and the trail from it (for example, LADDER_ADAPTIVE shows sym=ZROUSDT with R=0.332% producing arm=0.166% and lock=0.110%, and sym=BCHUSDT with R=0.301% producing arm=0.150%). The geometry is R-derived, fee-floored, staged, and trails behind the peak, and the graduation latch and the profit-lock exemption are running. So the adaptive exit is not something to build; it exists and functions mechanically.

### What we are facing — the problem, stated precisely

Despite both fixes being live, the system still loses, and the reason is now narrow and specific. In this window the win rate is 46 percent, but the average win is only plus 0.40 percent while the average loss is minus 0.39 percent, for a net of about minus 6.51 dollars over 397 trades. The coins peak at plus 1, plus 2, even plus 3 percent — but the average win is only plus 0.40 percent, and the median win is only about plus 0.23 percent (with a maximum win of plus 3.56 percent proving the system CAN occasionally let a winner run). So the exit is still clipping: the coins now make large moves, and the adaptive exit captures less than half of the typical one.

The cause is visible in the R values themselves. ZRO's R reads 0.332 percent and BCH's R reads 0.301 percent — these are SMALL R values — and so the geometry they produce is small: arm around plus 0.15 percent, lock around plus 0.11 percent. But these same coins are peaking far above that. There is a mismatch between the R being measured (around 0.3 percent) and the move the coin actually makes (plus 1 to plus 3 percent). The adaptive exit is sizing the geometry to an R that is too small relative to the real moves, so the geometry comes out too tight and still clips the winner. The median win sitting at plus 0.23 percent while peaks reach plus 3 percent is the signature of geometry that locks far too early for the movement now present.

### Why this is happening — the hypotheses to investigate (not assume)

The adaptive exit derives everything correctly from R; the problem is upstream, in R itself and in how the geometry is scaled from it. The likely causes, to be investigated and proven or corrected from the code and the logs, not assumed: the ATR measurement that produces R may be computed on too short a window or too calm a recent period, so it reads around 0.3 percent on a coin actually capable of plus 2 percent moves; the cached R (the profiler caches on a roughly 60 to 120 second time-to-live) may serve a stale, small value during the fast early part of a move, sizing the geometry before the move develops; and the R-multiples themselves (the arm at a fraction of R, the rungs at small multiples of R) may be too conservative for the new movers, compounding any under-measurement of R. The investigation must determine which of these, in what combination, makes the geometry too tight, and prove it from the data.

### What we wanted, and what result we need

We want the exit to KEEP the moves the coins now make, instead of clipping them. The aim is that when a coin peaks at plus 1, plus 2, or plus 3 percent, the exit captures most of that move rather than locking it at plus 0.11 to plus 0.40 percent. Stated as the operator's target: a win rate of 70 percent or higher with real net-of-fee profit per trade, where the average win is sized to the moves the coins actually make rather than to an R that reads too small. The exit must continue to compute each trade's fees and keep wins net-positive, and it must remain adaptive and bounded — the tuning is to make R and the geometry REFLECT the real movement, not to abandon the adaptive design. The result to measure: the average win rises substantially toward what the peaks offer (the gap between the median peak and the median win narrows sharply), the losses stay controlled, and the net turns positive.

Honest note on the 70 percent target: tuning the exit to capture the real moves is a large and necessary gain — it directly addresses the clip that is currently costing most of every winner — but the final win rate also depends on the entries and the realized move sizes. This program measures and captures the moves the coins make; it does not change how often the entries are right. Do not promise 70 percent from this tuning alone; show, from the replay, what win rate and net profit the retuned exit would produce on the real trades, and let the evidence set expectations.

### What we will do

Investigate the complete exit system and the complete logs, code by code and trade by trade, to find exactly why R reads too small and the geometry comes out too tight to capture the moves the coins now make, and recalibrate the R measurement and the geometry so the exit fits the real movement — phase by phase, each gated by the operator, proven on a replay before going live.

**THESE ARE FIXES AND CALIBRATIONS — NOT NEW FEATURES, AND NOT NEW GATES.** The adaptive exit already exists; this tunes its inputs and coefficients (the R measurement, the cache behavior on fast moves, the R-multiples and their bounds) so the geometry matches the real moves. It adds no new exit mechanism, no new trading gate, no new filter, no trade-suppression rule. The owner hierarchy (Head, green owner, red owner, advisory) and the universe system stay exactly as they are. If any part appears to require a genuinely new mechanism or gate, stop and escalate to the operator rather than building one.

**ADAPTIVE REMAINS DERIVED-AND-BOUNDED, NEVER SELF-OPTIMIZING.** The tuning keeps the exit deriving its geometry from measured volatility and fees through transparent, bounded formulas with hard floors and ceilings — predictable and replayable. It must not introduce any self-tuning loop that changes values based on its own profit and loss. Making R reflect real movement means measuring volatility correctly, not letting the system free-run.

The authoritative context is EXIT_SYSTEMS_DEEP_FORENSIC_FINDINGS.md and ADAPTIVE_EXIT_BLUEPRINT_AND_INTEGRATION_MAP.md (the design and the prior findings), the captured log (log_bundle_2026-06-17T0130_to_2026-06-18T0530_UTC.log, showing the coins now move and the exit still clips), and the live adaptive-exit code (the R geometry in src/analysis/vol_scale.py, the ladder and trail in src/workers/profit_sniper.py, the R source in src/analysis/volatility_profile.py). Read them first. If this prompt and those documents conflict, or the code contradicts them, stop and escalate rather than guessing.

Do not review this prompt. Do not critique it. Do not rewrite it. Execute the phases.

**IMPORTANT — WORK ON THE MAIN BRANCH. DO NOT CREATE A NEW BRANCH. DO NOT CREATE A NEW DIRECTORY.** All work happens in the existing working tree at the project root. Investigation notes, if any, go in a single file, not a tree of files. All code changes are committed directly to main as atomic, clearly-labeled, individually-revertible commits with plain-language messages. If you believe a branch is needed, stop and escalate instead of creating one.

**SHIP ONE PHASE AT A TIME.** This touches the stop-loss geometry, the most safety-critical machinery in the system. Ship one phase, verify it on real trades, and revert anything that destabilises the system, weakens the catastrophic floor, or regresses a working protection. Do not batch.

**EFFICIENCY MANDATE.** Do not waste time. No elaborate dev-notes directory trees. No git command loops. No repeated identical shell commands. A prior session lost roughly three hours stuck in command loops; do not repeat that. The log is very large; grep it deliberately and in a targeted way rather than scanning it repeatedly. Read each file thoroughly once, map it, and move on.

**ACCESSIBILITY.** The operator (Inshad) is a blind developer using a screen reader. All output and all reports must use proper heading structure (h1/h2/h3). No emoji. Clear prose. No ASCII-art tables. No decorative separators. State findings in sentences. Where a value matters, state it in a sentence with its evidence.

The operator must approve at the decision gate at the end of each phase before the next begins. Applying a phase before its gate is approved is a serious violation. Because this is the stop-loss geometry, the bar at each gate is the highest in the project.

---

## Part A — The Investigation Depth Mandate (The Most Important Rule — Read Twice)

Before concluding anything and before touching any code, you must:

- Go through EACH AND EVERY file in the adaptive-exit path, code by code, LINE BY LINE, until you fully understand what each does — not what you assume. At minimum: the R source (src/analysis/volatility_profile.py — how the ATR percent is computed, over what window, on what candle period, how it is cached and for how long, and what its typical values are); the R geometry (src/analysis/vol_scale.py — every formula turning R and the fee into the arm, the rungs, the lock, the trail distance, the take-profit, the hard stop, and every floor and ceiling and coefficient); the ladder and trail (src/workers/profit_sniper.py — _compute_ladder_floor, _compute_trail_stop, the spine selection, the graduation latch, how R is fetched and whether it is smoothed); the gateway and its profit-lock exemption (src/core/sl_gateway.py); and the configuration holding every coefficient. Do not miss a single file. If unsure whether a file is relevant, read it and decide.
- Map the COMPLETE dependency picture: how R flows from the profiler through the cache to the geometry to each threshold to the gateway, every coefficient and its current value, every floor and ceiling, and every consumer of each computed value. The full wiring, captured before any change.
- CROSS-CHECK against the logs, trade by trade. The log is the ground truth for what is actually happening. For a representative and sufficient set of real trades — especially the ones that peaked high (above plus 1 percent) and closed low, and the rare ones that captured big (the plus 3 percent wins) — trace the FULL lifecycle: what R was computed for the coin and when, what arm and lock and trail the geometry produced from that R, what the coin's peak actually was, where the stop was placed relative to the peak, and where and why the trade closed below its peak. The central quantitative question: for the trades that peaked high and closed low, compare the measured R against the coin's actual realized move — and show, with numbers, how much smaller R is than the move, and therefore how much too tight the geometry is. This comparison, across many trades, is the heart of the diagnosis.
- Prove every finding from BOTH the code AND the logs. The hypotheses above (R window too short or too calm, cache serving stale small R on fast moves, R-multiples too conservative) are to be verified or corrected against the data, each labeled proven or hypothesis. A claimed cause without log proof on real trades is not a finding.
- Determine which cause, in what combination, makes the geometry too tight, and quantify each one's contribution to the clip.
- Only after the complete, dependency-mapped, line-by-line understanding is established and the cause is proven from the logs do you design the tuning, and at implementation re-check every connected file so the change is correct everywhere R and the geometry reach.

This depth is mandatory. A change proposed without the complete file-by-file investigation AND the log-proven cause behind it is rejected. This is the stop-loss geometry; an incomplete map is unacceptable. The goal is the tuning that makes the exit capture the real moves — proven, not guessed.

---

## Part B — The Phases

### PHASE 0 — Prove why the geometry is too tight (no code change)

Establish the ground truth from the logs and the code together. Inventory how R is computed and cached and what its typical values are; map every coefficient and bound in the geometry. Then trace real trades that peaked high and closed low, tick by tick, showing the measured R, the geometry it produced, the actual peak, the stop placement, and the close — and quantify, across many trades, how much smaller the measured R is than the realized move, and therefore how much too tight the geometry is. Confirm or correct each hypothesis (R window too short or too calm; cache serving stale small R during fast early moves; R-multiples too conservative), label each proven or hypothesis, and quantify each one's contribution to the clip. Produce the complete diagnosis as the foundation. Report before Phase 1.

### PHASE 1 — Fix the R measurement so it reflects the real movement

The situation: R reads around 0.3 percent on coins that move plus 1 to plus 3 percent, so the geometry built from it is too tight. The need and aim: R must reflect the coin's actual movement capacity, so the geometry sizes to the real moves. How it should work after fixing: the ATR or volatility measurement that produces R should be computed over a window and a candle period that capture the coin's genuine recent range (including its larger moves), so R for a coin capable of plus 2 percent moves reads at a scale consistent with that, not at a fraction of it. The exact correction depends on the Phase 0 finding — it may be the window length, the candle period, the averaging method, or how recent-versus-longer movement is weighted — and must follow the proven cause, not a guess. Why it is not working today: the current R is measuring too small a slice of the coin's movement. Whatever the correction, R must remain a measured, bounded quantity (no self-tuning), and the change must be validated on the replay (below) before going live. Trial: after the fix, the measured R for the high-moving coins reflects their real movement range, and the geometry built from it is correspondingly wider; verified on the replay against the real trades.

### PHASE 2 — Fix the cache behavior on fast-moving trades (if Phase 0 proves it matters)

The situation: R is cached for roughly 60 to 120 seconds, and a fast move can develop faster than the cache refreshes, so the geometry may be sized on a stale, small R during exactly the part of the move that matters. The need and aim: the geometry on a fast-developing trade should not be sized on a stale small R. How it should work: where Phase 0 proves the cache lag costs material profit on fast movers, the R used for the geometry should refresh quickly enough (a shorter fast path, or a recompute trigger when the trade moves fast) that the geometry tracks the developing move, while preserving the smoothing that prevents thrash. Why it may not be working today: the cache TTL is tuned for cost, not for fast moves. This phase is conditional on Phase 0 proving it matters; if Phase 0 shows the cache lag is immaterial, skip it and say so. Trial: on fast-moving trades, the geometry tracks the developing move rather than lagging on stale small R, without introducing stop thrash; verified on the replay.

### PHASE 3 — Recalibrate the R-multiples and bounds for the real movers

The situation: even with R measured correctly, the multiples (the arm at a fraction of R, the rungs at small multiples of R, the trail distance, the take-profit and hard-stop multiples) and their floors and ceilings may be too conservative for the new movers, so winners still lock too early. The need and aim: the multiples and bounds should let winners run to capture most of the move the coins now make, while still cutting losers and keeping wins net of fees. How it should work: with R correct, the staged capture should lock progressively as the trade clears its R-scaled rungs and trail behind the peak at a distance that captures the move rather than the noise — so a coin peaking plus 2 percent is captured near that, not at plus 0.4 percent. The exact multiple and bound values follow the replay evidence (what values would have captured the real moves on the logged trades), not a guess. Why it is not working today: the multiples and bounds were set before the universe fix, when the coins moved far less, so they are calibrated for small moves. Values for the catastrophic cap are never touched; the hard stop stays below it. Trial: after the recalibration, the replay shows winners captured near their real peaks (the median win rises sharply toward the median peak), losses controlled, net positive; verified on the replay and then on live trades.

---

## Part C — Hard Rules (Non-Negotiable)

### Rule 1 — The investigation depth mandate (Part A) governs every phase

Every relevant file, line by line, complete dependency map, the cause PROVEN from both the code AND the logs on real traced trades (the R-versus-realized-move comparison), all connected files re-checked at implementation. A change without this is rejected. This is the stop-loss geometry; an incomplete map is unacceptable.

### Rule 2 — These are fixes and calibrations, NOT features and NOT gates

The adaptive exit already exists; this tunes its inputs and coefficients. No new exit mechanism, no new trading gate, no new filter. The owner hierarchy and the universe system are unchanged. If a genuinely new mechanism seems required, stop and escalate.

### Rule 3 — Adaptive remains derived-and-bounded, NEVER self-optimizing

R and the geometry stay measured-and-bounded through transparent formulas with floors and ceilings. No self-tuning loop, no value that changes based on its own profit and loss. Making R reflect real movement means measuring correctly, not free-running.

### Rule 4 — The catastrophic cap (the Head) stays sacred and only tightens

The tuning sets values below the Head; it never weakens or overrides the catastrophic per-trade stop, which always can fire and only tightens. The hard stop stays below the cap for every R value.

### Rule 5 — Do not regress the confirmed-working parts

The honest scoreboard, the two-sided signal, the universe refresh (now selecting real movers), the enforcing owner switch, the sacred cap, the working loss-cutters, the graduation latch, and the adaptive exit's correct mechanical operation are confirmed working. No change may regress them. The fix is to the R measurement and the coefficients, not to the working machinery.

### Rule 6 — Prove on a replay before going live

Each tuning is validated by replaying it against the real logged trades — showing it would have captured the real moves (the median win rising toward the median peak) net of fees — BEFORE it is enabled live. The replay must drive the real exit pipeline, not a reimplementation. Ship one change at a time, verify live, with a forced catastrophic-stop test confirming the Head still fires.

### Rule 7 — No assumptions, no guess-fixes

Every claim cites a code location AND a log fact (a real traced trade, the R-versus-move numbers), re-verified against current code. Probably and should-be are not a basis for action. The cause of the under-sized geometry is proven from the data before anything is changed.

### Rule 8 — Parameters centralized and tuning-ready

Every coefficient, window length, cache parameter, multiple, floor, and ceiling touched is named, centralized configuration, never hardcoded inline, with boot sentinels confirming load. Do not introduce new scattered hardcoded values.

### Rule 9 — File edits via sed, with backups

No interactive editors. Timestamped in-place backups before editing any file, kept in the original directory.

### Rule 10 — Commit on main, atomic and labeled, one coherent change each

No new branch, no new directory. Atomic, individually-revertible commits with plain-language messages.

### Rule 11 — Observability for every change

Each trade's R and resulting geometry remain visible in the logs (the LADDER_ADAPTIVE line and equivalents), now showing R reflecting the real movement and the geometry sized to it; the median-win-versus-median-peak gap trackable; boot sentinels confirming the tuned coefficients loaded. The operator must be able to see that R now matches the moves and the geometry captures them.

### Rule 12 — Self-verification with concrete values

Each phase verified against its trial before it is done, on the replay and then live, including the forced catastrophic-stop test. Verification scripts in the project root, clearly named, never deleting or rewriting data to pass.

### Rule 13 — Honest reporting and provisional verdicts

If a hypothesis is wrong, if R cannot be made to reflect the moves without a side effect, if the cache lag turns out immaterial, if the replay shows the retuning underperforms, or if the 70 percent target is not reachable from the exit alone — say so plainly at the gate. All verdicts are provisional until measured live.

### Rule 14 — Protected tables untouched

No retention or cleanup on tias_results, trade_log, trade_history, thesis_store, virtual_positions.

---

## Part D — Trial Behavior And Expected Values (How The System Checks Itself)

Phase 0: the diagnosis is complete — how R is computed and cached, its typical values, the geometry coefficients, and the R-versus-realized-move comparison on real traced trades quantifying how much too tight the geometry is, with each cause labeled proven or hypothesis and its contribution quantified.

Phase 1: the measured R for high-moving coins reflects their real movement range; the geometry built from it is correspondingly wider; verified on the replay against the real trades.

Phase 2 (conditional): on fast-moving trades the geometry tracks the developing move rather than lagging on stale small R, without stop thrash; or, if Phase 0 shows the cache lag is immaterial, this phase is skipped with that stated.

Phase 3: after recalibrating the multiples and bounds, the replay shows winners captured near their real peaks (the median win rises sharply toward the median peak), losses controlled, net positive; verified on the replay and then live, with the catastrophic stop still firing in a forced test.

Cross-cutting: investigation-first throughout; the cause proven from code and logs on real trades; adaptive-but-bounded, never self-optimizing; the Head sacred; the owner hierarchy and universe system unchanged; the confirmed-working parts intact; parameters centralized with boot sentinels; protected tables untouched; one phase, one commit, one verification at a time; the replay driving the real pipeline before anything goes live.

---

## Part E — Anti-Patterns To Avoid

Do not rebuild the adaptive exit — it exists and works mechanically; tune its inputs and coefficients. Do not guess why the geometry is too tight — prove it from the R-versus-realized-move comparison on real traced trades. Do not introduce a self-tuning loop — R and the geometry stay measured and bounded. Do not weaken the catastrophic cap. Do not let the hard stop rise above the cap for any R value. Do not introduce new hardcoded values while tuning the coefficients — centralize them. Do not enable any change live before replaying it against the real trades on the real pipeline. Do not regress the universe system, the owner switch, the cap, or the loss-cutters. Do not promise the 70 percent target from this tuning alone — show what the replay produces and be honest about what depends on the entries. Do not scan the large log repeatedly — grep deliberately. Do not create a branch or directory. Do not waste time in command loops. Do not declare any phase done until its trial passes on the replay and the live behavior confirms it.

---

## Part F — What Success Looks Like

The exit finally captures the moves the coins now make. R reflects each coin's real movement range, so a coin capable of plus 2 percent moves no longer has its geometry sized to a fraction of that; the cache no longer starves a fast move of a correct R; and the multiples and bounds let winners run to capture most of the move while still cutting losers net of fees. The staged capture locks progressively as the trade clears its R-scaled rungs and trails behind the peak at a distance that holds the move rather than the noise, so a coin peaking plus 2 percent is captured near that instead of at plus 0.4 percent. The median win rises sharply toward the median peak, the losses stay controlled, and the net turns positive. Every change rests on a complete file-by-file investigation and a log-proven diagnosis on real traced trades, is proven on a replay against the real pipeline before going live, and ships one phase at a time, observable and revertible, on main — with the catastrophic cap sacred, the adaptive design preserved and bounded, the universe system and owner hierarchy untouched, and no new gate. The system stops clipping the moves it now reaches.

---

## Part G — What Success Does NOT Mean

This tuning makes the exit capture the real moves the coins now make — the dominant remaining cost — but it does not by itself guarantee the 70 percent win-rate target, and that must not be claimed. The win rate and the net profit also depend on the entries and how often they are right about direction, which this program measures through the replay but does not change. The retuned exit will keep the moves instead of clipping them, a large and measurable gain provable on the replay, but whether the final figure reaches 70 percent depends on the entries delivering enough correct, large-enough moves, which is a separate, related question this program informs and the operator decides next. Success here is an exit whose R reflects the real movement and whose geometry captures it — and an evidence-based, replayed projection of the win rate and net profit that produces. The remaining distance to the target, if any, is the entries' to close, and the data this program produces will show how much that is. The adaptive design tuned to the real moves is the right foundation; the entries and the market decide the rest.

---

## Part H — End Of Prompt

Begin Phase 0.

Confirm the working tree is clean and on main, the active adapter, and the protected-table row counts. Read EXIT_SYSTEMS_DEEP_FORENSIC_FINDINGS.md, ADAPTIVE_EXIT_BLUEPRINT_AND_INTEGRATION_MAP.md, and the captured log in full, and locate the R source, the R geometry, and the ladder and trail in the code.

Then execute Phase 0 — prove why the geometry is too tight: map how R is computed and cached and its typical values, map the geometry coefficients, and trace real trades that peaked high and closed low to quantify how much smaller the measured R is than the realized move and therefore how much too tight the geometry is, with each cause labeled proven or hypothesis and quantified. Report before designing.

Then execute the phases in order, one at a time, each with the full Part A investigation, the design, the plain-prose explanation, the decision gate (the highest bar in the project — this is the stop-loss geometry), and after approval the implementation with all connected files re-checked and the trial run on the replay (driving the real pipeline) and then live with the forced catastrophic-stop test: fix the R measurement so it reflects the real movement; fix the cache behavior on fast moves if Phase 0 proves it matters; and recalibrate the R-multiples and bounds so winners are captured near their real peaks.

Remember throughout: the situation is that the universe fix made the coins move (peaks to plus 3 percent) but the adaptive exit still clips because R reads too small (around 0.3 percent) and the geometry built from it is too tight, so the average win is only plus 0.40 percent; the aim is to make R reflect the real movement and the geometry capture it, targeting a far higher win rate with real net profit. These are FIXES AND CALIBRATIONS, NOT features and NOT gates — tune the inputs and coefficients of the existing adaptive exit, do not rebuild it. Adaptive remains DERIVED AND BOUNDED, never self-optimizing. The Head stays sacred; the owner hierarchy and universe system are unchanged; no new hardcoded values; the working parts intact. Understand before you touch — code by code, line by line, file by file. Root cause, not band-aid. Prove the cause from the code AND the logs on real traced trades — no assumptions. Replay on the real pipeline before live. One phase, one commit, one verification at a time. Work on main, no new branch, no new directory. Be honest about what the exit tuning can and cannot do toward the 70 percent target. If the code contradicts the documents, escalate. If something does not fit, document it and escalate.

Begin Phase 0.
