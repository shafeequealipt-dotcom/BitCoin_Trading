# INVESTIGATE AND FIX — Make The Exit Capture The Green It Reaches By Tightening Protection As Profit Grows (Fix The Fixed-Distance Trail, Engage The Secure Rungs Earlier, Cover The Separate Legacy Chandelier Path, And Resolve The Inert Third Rung), With Exhaustive File-By-File Root-Cause Investigation Proven On Real Trades (Main Branch, No New Branch)

## STOP. READ THIS ENTIRE DOCUMENT BEFORE WRITING ANY CODE.

### The situation we are facing

The system is structurally honest, the universe now selects coins that genuinely move, and the adaptive exit is live and mechanically correct. A complete code-by-code map of the exit system (EXIT_SYSTEM_CODE_MAP.md) has now proven, from the real formulas against live log values, exactly why the exit still gives back profit on trades that go green and stay green. The give-back is no longer a mystery; it is pinned to specific mechanisms and exact values.

The proven example is HOMEUSDT: a trade that was in profit for its entire life, never once went red, peaked at plus 0.6953 percent, and closed at plus 0.1485 percent — a give-back of 0.547 percent. The map reproduced this from the live formula to the digit: the profit lock is held a fixed distance of one half of R behind the running peak (the formula is the running peak minus trail_r times R, with trail_r equal to 0.5), and that distance never tightens as profit grows. HOME's R was 0.573 percent, so the lock sat 0.287 percent behind the peak for the trade's whole life, and the best lock the geometry could ever place was about plus 0.41 percent on a plus 0.695 percent peak. The staged rungs that would secure more profit sit at 1.5 R and 3 R — about 0.86 percent and 1.72 percent for this coin — and the trade never reached even the first rung, so the trail was the only active protection.

### What we are facing, stated as the root

The structural root, proven by the map, is that no adaptive-exit threshold tightens as a trade's profit grows. Every threshold is fixed for the trade's entire life. A trade that climbs to a real peak locks the same fixed distance behind that peak as it did when barely green, so it surrenders that fixed distance on every pullback. For the sub-one-percent movers the system mostly trades, the secure rungs sit too far out to ever engage, so the loose fixed trail is the only thing holding the profit, and it gives most of it back. On top of this, a separate and older mechanism — the legacy Chandelier trail — governs some trades entirely (it governed BELUSDT, which peaked plus 0.515 percent and closed minus 0.392 percent, with no adaptive-ladder line at all), so a fix to the adaptive trail alone would not cover those trades. And the fee floor governs the smallest movers (it governed SPCXUSDT), which is correct behavior and must not be disturbed.

### What we need

We need the exit to KEEP the profit it reaches on green trades — to tighten its protection as a trade banks more profit, so a trade that climbs to plus 0.7 percent locks close to that instead of giving back a fixed half of R, and so the secure levels engage on the sub-one-percent movers the system actually trades rather than sitting permanently out of reach. We need this applied wherever the give-back lives — the adaptive profit lock and the separate legacy Chandelier path — while the fee floor on the smallest movers and the catastrophic cap and the safety mechanisms are left exactly as they are.

### Our aim and our hope

The project's aim is maximum profit and the full exploitation of every situation — to catch good trades and let them pay, keeping the profit the system reaches instead of clipping it. The map proved the entries now find coins that move and the exit reaches profit on them; the hope of this fix is to convert that reached profit into kept profit, so a green trade closes near its peak instead of near breakeven. Concretely: the average win rises sharply toward the peaks the trades actually reach, the green-but-clipped trades (the HOMEUSDT pattern) close near their peaks, and the system stops handing back the green it works to reach.

Honest framing, to be stated and not oversold: this fix captures the green the system reaches — a large and necessary gain, proven on the real trades by the replay — but it does not change how often the entries are right about direction. The losses driven by wrong-direction entries are a separate, upstream problem this fix does not address. This fix makes the winners pay; the entry quality is why there are not more winners. Do not promise a profitable system from this fix alone; show, from the replay, what the retuned exit would have captured on the real trades, and let the evidence set expectations.

### What we will do

Investigate the complete give-back path code by code, file by file, line by line, confirm the map's findings against the current code, and fix the give-back at its root — making protection tighten as profit grows across every governor that causes it — phase by phase, each gated by the operator, proven on the real trades by the faithful replay before going live.

**THESE ARE FIXES AND CALIBRATIONS — NOT NEW FEATURES, AND NOT NEW GATES.** This changes how the existing exit thresholds behave (making them tighten as profit grows) and corrects the values and the coverage; it adds no new exit mechanism, no new trading gate, no new filter, no trade-suppression rule. The owner hierarchy, the universe system, the gateway safety, R, and the catastrophic cap stay exactly as they are. If any part appears to require a genuinely new mechanism or gate, stop and escalate to the operator rather than building one.

**ADAPTIVE REMAINS DERIVED-AND-BOUNDED, NEVER SELF-OPTIMIZING.** The fix keeps the exit deriving its geometry from measured volatility and fees through transparent, bounded formulas with hard floors and ceilings — predictable and replayable. Making protection tighten as profit grows means adding a profit-magnitude term to the bounded formula, not a self-tuning loop that changes its own values from its own profit and loss. The system must never free-run.

The authoritative context is EXIT_SYSTEM_CODE_MAP.md (the complete code-grounded map and the proven give-back mechanism with exact locations and values), the captured logs (showing the give-back on real trades), and the live exit code (the geometry in src/analysis/vol_scale.py, the ladder and the legacy Chandelier trail in src/workers/profit_sniper.py, the gateway in src/core/sl_gateway.py, the config in config.toml). Read them first. If this prompt and the map conflict, or the current code contradicts either, stop and escalate rather than guessing.

Do not review this prompt. Do not critique it. Do not rewrite it. Execute the phases.

**IMPORTANT — WORK ON THE MAIN BRANCH. DO NOT CREATE A NEW BRANCH. DO NOT CREATE A NEW DIRECTORY.** All work happens in the existing working tree at the project root. Investigation notes, if any, go in a single file, not a tree of files. All code changes are committed directly to main as atomic, clearly-labeled, individually-revertible commits with plain-language messages. If you believe a branch is needed, stop and escalate instead of creating one.

**SHIP ONE PHASE AT A TIME.** This touches the stop-loss geometry, the most safety-critical machinery in the system. Ship one phase, verify it on real trades, and revert anything that destabilises the system, weakens the catastrophic floor, regresses a working protection, or over-tightens and chokes winners. Do not batch.

**EFFICIENCY MANDATE.** Do not waste time. No elaborate dev-notes directory trees. No git command loops. No repeated identical shell commands. A prior session lost roughly three hours stuck in command loops; do not repeat that. Read each file thoroughly once, map it, and move on. The map already provides exact locations; use them rather than re-deriving them, but confirm them against the current code before editing.

**ACCESSIBILITY.** The operator (Inshad) is a blind developer using a screen reader. All output and all reports must use proper heading structure (h1/h2/h3). No emoji. Clear prose. No ASCII-art tables. No decorative separators. State findings in sentences. Where a value matters, state it in a sentence with its location. Explain issues in prose, not as pasted code blocks.

The operator must approve at the decision gate at the end of each phase before the next begins. Applying a phase before its gate is approved is a serious violation. Because this is the stop-loss geometry, the bar at each gate is the highest in the project.

---

## Part A — The Investigation Depth Mandate (The Most Important Rule — Read Twice)

The map is the starting point, not a substitute for reading the code. Before concluding anything and before touching any code, you must:

- Go through EACH AND EVERY file in the give-back path, code by code, LINE BY LINE, until you fully understand what each does — not what you assume. At minimum: the R geometry (src/analysis/vol_scale.py — the profit_lock_pct function and the trail term, the arm, the rungs, the staged secure levels, the fee floor, and every bound); the ladder and the legacy Chandelier trail (src/workers/profit_sniper.py — _compute_ladder_floor, _compute_trail_stop, the highest-stop-wins spine, the graduation latch, how R is fetched and smoothed, and crucially which trades are governed by the adaptive lock versus the Chandelier trail); the gateway (src/core/sl_gateway.py — confirm the tighten-only, min-distance, max-step, rate-limit rules and the profit-lock exemption so the fix's tighter locks still place); and the configuration (config.toml and src/config/settings.py — every value the map cites). Do not miss a single file. If unsure whether a file is relevant, read it and decide.
- Map the COMPLETE dependency picture: how the profit lock and the Chandelier trail each flow from R through the geometry to the placed stop, every coefficient and its current value, every floor and ceiling, which trades each governor controls, and every consumer of each value. Confirm the map's exact locations and values against the current code before relying on them.
- CONFIRM the map's findings against the code AND the logs. The map proved the give-back from real trades; re-verify the trail formula and the trail_r value, the rung spacing, the fact that no threshold tightens as profit grows, and the separation of the three governors (adaptive trail, legacy Chandelier, fee floor). Trace the proven trades (HOMEUSDT governed by the adaptive lock; BELUSDT governed by the Chandelier trail; SPCXUSDT governed by the fee floor) and confirm which governor controls each, so the fix is applied to the right mechanism for each. A fix applied to the wrong governor would miss the trades it controls.
- For every value or formula you will change, understand WHY it is set the way it is, what depends on it, and what a profit-magnitude-dependent tightening would do to BOTH the large movers (which should lock closer to their peaks) AND the small movers (which must not be over-tightened and choked). The fix must capture more of the big green trades without strangling the small ones.
- Only after the complete, dependency-mapped, line-by-line understanding is established and the governors are confirmed do you design the fix, and at implementation re-check every connected file so the change is correct across every governor and every consumer.

This depth is mandatory. A change proposed without the complete file-by-file investigation behind it is rejected. This is the stop-loss geometry; an incomplete map is unacceptable. The goal is the fix that makes the exit capture the green across every give-back path — proven, not guessed.

---

## Part B — The Phases

### PHASE 0 — Confirm the give-back governors and quantify each, on real trades (no code change)

Confirm the map's findings against the current code and the logs. Re-verify the adaptive trail formula and the trail_r value, the rung spacing and which rungs actually gate the lock, and the structural fact that no threshold tightens as profit grows. Trace the proven trades and confirm which governor controls each: HOMEUSDT by the adaptive profit lock, BELUSDT by the legacy Chandelier trail, SPCXUSDT by the fee floor. Across the captured window, quantify how much give-back each governor causes and on how many trades, so the fix is sized and aimed correctly: how much is the adaptive trail's fixed half-of-R distance, how much is the Chandelier trail's distance on the trades it governs, and how much is correct fee-floor behavior that must not be touched. Produce the confirmed governor map and the quantified give-back as the foundation. Report before Phase 1.

### PHASE 1 — Make the adaptive profit lock tighten as profit grows (the dominant fix)

The situation: the adaptive profit lock holds the stop a fixed half of R behind the peak for the trade's entire life, so a trade reaching plus 0.7 percent locks at plus 0.41 percent and gives back the fixed distance on any pullback. The need and aim: the lock must tighten as the trade banks more profit, so a trade that climbs to a real peak locks close to it. How it should work after fixing: the trail distance behind the peak should narrow as the trade's profit (in R units, or as a fraction of the peak) grows — wide early so a young trade can breathe, progressively tighter as the peak extends, so a larger green trade surrenders far less of its peak than it does today. The exact shape (a profit-scaled trail coefficient, a peak-fraction lock, or an additional progressive term in the bounded formula) follows the Phase 0 quantification and the replay evidence, not a guess, and it remains a bounded, derived formula with hard floors and ceilings — never self-tuning, never tighter than safe, always at or above the fee floor. Why it is not working today: the trail term is a fixed multiple of R with no profit-magnitude dependence. Trial: on the replay against the real trades, the green-but-clipped winners (the HOMEUSDT pattern) close much nearer their peaks while the small movers are not over-tightened or choked; the lock still respects the fee floor and the gateway places it; verified on the replay then live.

### PHASE 2 — Engage the secure rungs earlier for the sub-one-percent movers

The situation: the secure rungs sit at 1.5 R and 3 R, which for the coins the system trades are often near or above one percent, so most trades never reach even the first rung and the trail is their only protection. The need and aim: the secure levels should engage on the sub-one-percent movers the system actually trades, so a trade that climbs to a real peak locks in a secured level rather than relying only on the trail. How it should work after fixing: the rung multiples (and the secured levels they set) should be calibrated so that a typical green trade crosses a secure rung and locks in real profit, working together with the Phase 1 tighter trail rather than against it. The exact multiples follow the replay evidence of what would have secured the real green trades without over-securing and cutting winners short. Why it is not working today: the rungs are spaced for moves larger than the coins make. Also resolve the inert third rung (the 5.0 multiple loaded but never referenced in the formula) — either wire it in as a genuine third secure level or remove it, stated explicitly, so the configuration matches the behavior. Trial: on the replay, typical green trades cross a secure rung and lock real profit, the big movers are captured nearer their peaks, and no winner is cut short by over-securing; verified on the replay then live.

### PHASE 3 — Apply the fix to the legacy Chandelier trail path (the separate governor)

The situation: a separate, older mechanism, the Chandelier trail, governs some trades entirely (it governed BELUSDT, which gave back from a green peak to a loss with no adaptive-ladder line), and the Phase 1 and Phase 2 fixes to the adaptive lock do not touch it. The need and aim: the trades governed by the Chandelier trail must also keep more of their green, so the give-back is fixed everywhere it lives, not just on the adaptive-ladder trades. How it should work after fixing: either the Chandelier trail is given the same profit-magnitude tightening so it too locks closer to the peak as profit grows, or the trades it currently governs are routed through the now-fixed adaptive lock — decided from the Phase 0 finding of how many trades it governs and how much they give back, and presented at the gate. The catastrophic cap and the breakeven floor on this path are not weakened. Why it is not working today: the Chandelier trail's distance is ATR-based and, like the adaptive trail, does not tighten with profit. Trial: on the replay, the Chandelier-governed trades (the BELUSDT pattern) also close nearer their peaks; verified on the replay then live.

### PHASE 4 — Confirm the fee-floor and small-mover behavior is preserved, and the whole fix coheres

The situation: the smallest movers are correctly governed by the fee floor (it governed SPCXUSDT), which must not be disturbed, and the three governors must work together coherently after the fix. The need and aim: confirm the fee floor still protects the smallest movers, confirm the Phase 1 to Phase 3 changes do not over-tighten or choke any cohort, and confirm the whole exit coheres — the tighter trail, the earlier rungs, the fixed Chandelier path, and the fee floor all working together. How it should work: across the full replay, every cohort (the big movers, the typical sub-one-percent movers, the smallest fee-floor movers) keeps more of its green than today without any cohort being cut short, and the catastrophic cap, the gateway safety, and the owner hierarchy are intact. Why this phase exists: a fix to the give-back must not create a new problem by over-tightening, and the three governors must be verified to cohere rather than conflict. Trial: the full replay shows the median win rising sharply toward the median peak across cohorts, no cohort cut short, the fee floor preserved, the cap and safety intact; a forced catastrophic-stop test confirms the Head still fires and only tightens; verified live.

---

## Part C — Hard Rules (Non-Negotiable)

### Rule 1 — The investigation depth mandate (Part A) governs every phase

Every relevant file, line by line, complete dependency map, the map's findings confirmed against the current code AND the logs on real traced trades, the three governors confirmed, all connected files re-checked at implementation. A change without this is rejected. This is the stop-loss geometry; an incomplete map is unacceptable.

### Rule 2 — These are fixes and calibrations, NOT features and NOT gates

This changes how existing thresholds behave (tightening with profit) and corrects values and coverage. No new exit mechanism, no new trading gate, no new filter. The owner hierarchy, the universe system, the gateway safety, R, and the cap are unchanged. If a genuinely new mechanism seems required, stop and escalate.

### Rule 3 — Adaptive remains derived-and-bounded, NEVER self-optimizing

The profit-magnitude tightening is a bounded, derived term in the formula with hard floors and ceilings, always at or above the fee floor — never a self-tuning loop, never a value that changes from its own profit and loss. Any trade's geometry must remain explainable from its inputs.

### Rule 4 — Capture more of the big green trades WITHOUT choking the small ones

The fix must lock the large movers closer to their peaks AND not over-tighten the small movers into being cut short. Both cohorts are checked on the replay; a fix that helps one and harms the other is not shipped.

### Rule 5 — Fix the give-back everywhere it lives, not just one governor

The give-back has three governors: the adaptive profit lock, the legacy Chandelier trail, and the fee floor. The fix addresses the adaptive lock (Phases 1 and 2) and the Chandelier path (Phase 3), and preserves the fee floor (Phase 4). A fix to only one governor that leaves the others bleeding is incomplete.

### Rule 6 — The catastrophic cap (the Head) stays sacred and only tightens; safety intact

The fix sets profit-lock values below the Head; it never weakens or overrides the catastrophic per-trade stop, the gateway tighten-only rule, the min-distance safety, or the wire-fail guards. The fee floor is preserved.

### Rule 7 — Prove on the faithful replay before going live

Each phase is validated by replaying it against the real logged trades, driving the REAL gateway pipeline, BEFORE it is enabled live. Use the faithful harness (simulate_trail_recalibration_replay.py, which drives the real gateway per tick on the captured window), not the unfaithful one (simulate_adaptive_exit_replay.py, which hardcodes a trail multiple of 1.0 unequal to the live 0.5 and whose numbers are not the live geometry). Ship one change at a time, verify live, with a forced catastrophic-stop test.

### Rule 8 — Do not regress the confirmed-working parts

The honest scoreboard, the universe refresh, the enforcing owner switch, the sacred cap, the working loss-cutters, the graduation latch, the gateway placeability (confirmed working), and the fee floor are confirmed working. No change may regress them. R is measured correctly and is NOT touched.

### Rule 9 — No assumptions, no guess-fixes

Every claim cites a code location AND a log fact, re-verified against current code. The map's values are confirmed against the current code before they are relied on. Probably and should-be are not a basis for action. The fix shape follows the replay evidence, not a guess.

### Rule 10 — Parameters centralized and tuning-ready

Every coefficient touched (the trail term and any new profit-scaling coefficient, the rung multiples, the secure levels, the bounds) is named, centralized configuration, never hardcoded inline, with boot sentinels confirming load. Do not introduce new scattered hardcoded values.

### Rule 11 — File edits via sed, with backups

No interactive editors. Timestamped in-place backups before editing any file, kept in the original directory.

### Rule 12 — Commit on main, atomic and labeled, one coherent change each

No new branch, no new directory. Atomic, individually-revertible commits with plain-language messages.

### Rule 13 — Observability for every change

Each trade's geometry remains visible in the logs (the LADDER_ADAPTIVE line and equivalents), now showing the trail tightening as profit grows and the rungs engaging; the median-win-versus-median-peak gap trackable across cohorts; boot sentinels confirming the tuned coefficients loaded. The operator must be able to see the exit now capturing the green.

### Rule 14 — Self-verification with concrete values

Each phase verified against its trial before it is done, on the replay and then live, including the forced catastrophic-stop test. Verification scripts in the project root, clearly named, never deleting or rewriting data to pass.

### Rule 15 — Honest reporting and provisional verdicts

If a governor cannot be fixed without a side effect, if the replay shows over-tightening on a cohort, if the Chandelier path is better handled by rerouting than by tuning, or if the captured green is smaller than hoped — say so plainly at the gate. State plainly that this fix captures the green but does not address the wrong-direction-entry losses. All verdicts are provisional until measured live.

### Rule 16 — Protected tables untouched

No retention or cleanup on tias_results, trade_log, trade_history, thesis_store, virtual_positions.

---

## Part D — Trial Behavior And Expected Values (How The System Checks Itself)

Phase 0: the governor map is confirmed against the current code and the give-back quantified per governor on the real trades — how much is the adaptive trail, how much the Chandelier trail, how much correct fee-floor behavior — with HOMEUSDT, BELUSDT, and SPCXUSDT each attributed to its governor.

Phase 1: on the replay, the green-but-clipped winners close much nearer their peaks because the trail tightens as profit grows; the small movers are not over-tightened; the lock respects the fee floor and the gateway places it; verified live.

Phase 2: on the replay, typical sub-one-percent green trades cross a secure rung and lock real profit; the big movers are captured nearer their peaks; no winner is cut short; the inert third rung is resolved explicitly; verified live.

Phase 3: on the replay, the Chandelier-governed trades (the BELUSDT pattern) also close nearer their peaks, by the same profit tightening or by rerouting to the fixed adaptive lock; verified live.

Phase 4: the full replay shows the median win rising sharply toward the median peak across all cohorts, no cohort cut short, the fee floor preserved, the cap and safety intact; the forced catastrophic-stop test confirms the Head still fires and only tightens; verified live.

Cross-cutting: investigation-first throughout; the map's findings confirmed against the code and logs on real trades; adaptive-but-bounded, never self-optimizing; big movers captured without choking small movers; the give-back fixed across all three governors; the cap sacred and safety intact; the confirmed-working parts and R untouched; parameters centralized with boot sentinels; the faithful replay driving the real pipeline before live; one phase, one commit, one verification at a time.

---

## Part E — Anti-Patterns To Avoid

Do not fix only the adaptive trail and leave the Chandelier path bleeding — fix the give-back everywhere it lives. Do not introduce a self-tuning loop — the profit tightening is a bounded, derived term. Do not over-tighten and choke the small movers chasing the big-mover capture — check both cohorts on the replay. Do not disturb the fee floor on the smallest movers — it is correct. Do not weaken the catastrophic cap, the tighten-only rule, the min-distance safety, or the wire-fail guards. Do not touch R — it is measured correctly. Do not use the unfaithful replay — use the one that drives the real gateway. Do not enable any change live before the replay proves it. Do not introduce new hardcoded values — centralize every coefficient. Do not promise a profitable system from this fix alone — it captures the green but does not fix wrong-direction entries; be honest. Do not assume — confirm the map's values against the current code. Do not create a branch or directory. Do not waste time in command loops. Do not declare any phase done until its trial passes on the replay and the live behavior confirms it.

---

## Part F — What Success Looks Like

The exit finally keeps the green it reaches. Protection tightens as a trade banks more profit, so a trade that climbs to plus 0.7 percent locks close to that instead of giving back a fixed half of R; the secure rungs engage on the sub-one-percent movers the system actually trades, so a typical green trade locks in a real secured level instead of relying only on a loose trail; the separate legacy Chandelier path is fixed too, so the trades it governs also close near their peaks; and the fee floor on the smallest movers, the catastrophic cap, and the gateway safety are left exactly as they are. The HOMEUSDT pattern — green its whole life, peaked plus 0.7 percent, closed plus 0.15 percent — becomes a trade that closes near its peak; the median win rises sharply toward the median peak across every cohort; and the system stops handing back the green it works to reach. Every change rests on a complete file-by-file investigation and the code-grounded map confirmed against the current code on real traced trades, is proven on the faithful replay against the real pipeline before going live, and ships one phase at a time, observable and revertible, on main — with the cap sacred, the safety intact, the adaptive design preserved and bounded, R untouched, and no new gate. The exit captures the green.

---

## Part G — What Success Does NOT Mean

This fix makes the exit capture the green it reaches — the dominant, controllable give-back — but it does not by itself make the system profitable, and that must not be claimed. The biggest losses in the captured windows are wrong-direction entries that no exit fix touches; this fix keeps the profit on the trades that go green, but it does not change how often the entries are right about direction. The win rate and the overall profit also depend on the entries, which this fix does not address. The retuned exit will turn the green-but-clipped winners into trades that close near their peaks — a large, measurable gain provable on the replay — but whether the system is profitable depends on the entries delivering enough correct, large-enough moves, which is a separate, upstream program the operator decides next. Success here is an exit that keeps the green across every give-back path, proven on the real trades — not a profitable system on its own. The remaining distance is the entries' to close, and it is a separate effort. The exit fix is the right next step because the green the system already reaches should not be handed back; what it unlocks, the entries must then build on.

---

## Part H — End Of Prompt

Begin Phase 0.

Confirm the working tree is clean and on main, the active adapter, and the protected-table row counts. Read EXIT_SYSTEM_CODE_MAP.md and the captured logs in full, and locate in the current code the adaptive profit lock, the legacy Chandelier trail, the gateway, and the configuration.

Then execute Phase 0 — confirm the give-back governors and quantify each on real trades: re-verify the trail formula and trail_r, the rung spacing, and the no-tightening fact against the current code; trace HOMEUSDT (adaptive lock), BELUSDT (Chandelier trail), and SPCXUSDT (fee floor) to confirm which governor controls each; and quantify the give-back per governor across the window. Report before designing.

Then execute the phases in order, one at a time, each with the full Part A investigation, the design, the plain-prose explanation, the decision gate (the highest bar in the project — this is the stop-loss geometry), and after approval the implementation with all connected files re-checked and the trial run on the faithful replay (driving the real pipeline) and then live with the forced catastrophic-stop test: make the adaptive profit lock tighten as profit grows; engage the secure rungs earlier and resolve the inert third rung; apply the fix to the legacy Chandelier path; and confirm the fee floor and small-mover behavior is preserved and the whole fix coheres.

Remember throughout: the situation is that the exit reaches profit on the coins that now move but gives it back because no threshold tightens as profit grows, the trail sits a fixed half of R behind the peak, the secure rungs sit too far out for the sub-one-percent movers, and a separate Chandelier path governs some trades entirely; the aim is maximum profit by keeping the green the system reaches, across every give-back path. These are FIXES AND CALIBRATIONS, NOT features and NOT gates. Adaptive remains DERIVED AND BOUNDED, never self-optimizing. Capture the big green trades without choking the small ones. Fix the give-back everywhere it lives. The cap stays sacred, the safety intact, R untouched. Understand before you touch — code by code, line by line, file by file — confirming the map against the current code. Root cause, not band-aid. Prove from the code AND the logs on real traced trades — no assumptions. Use the faithful replay on the real pipeline before live. One phase, one commit, one verification at a time. Work on main, no new branch, no new directory. Be honest that this captures the green but does not fix the wrong-direction entries. If the code contradicts the map, escalate. If something does not fit, document it and escalate.

Begin Phase 0.
