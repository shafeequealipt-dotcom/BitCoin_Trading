# INVESTIGATE AND BUILD — A Per-Trade Dynamic Adaptive Exit System That Scales Every Threshold To Each Coin's Live Volatility (Three Phases: Map The Whole Exit System And Every Hardcoded Value, Merge With The Twelve Known Issues Into One Master List, Then Design And Build The Adaptive System), With Exhaustive File-By-File Root-Cause Investigation (Main Branch, No New Branch)

## STOP. READ THIS ENTIRE DOCUMENT BEFORE WRITING ANY CODE.

### The situation we are in

The system is structurally honest — the scoreboard tells the truth, the inputs are fresh and two-sided, the brain's decisions execute as made, the exit-authority collision is resolved and the owner switch is enforcing correctly. None of those are the problem anymore. The problem is now proven, precisely, by a complete forensic investigation of the exit systems (EXIT_SYSTEMS_DEEP_FORENSIC_FINDINGS.md) cross-checked trade by trade against the logs.

### What we are facing — the proven root cause

The exit systems are built for market moves several times larger than the entries actually produce. The trades peak at a median of about plus 0.23 percent (ninetieth percentile about plus 0.71 percent), but the profit ladder's first real rung sits at plus 0.6 percent, the trailing stop's minimum distance is 0.30 percent of entry, and the take-profit target sits near plus 2.25 to 6 percent. The result, proven on real trades: about 97.6 percent of trades that graduate die in the dead band between the 0.2 percent arm and the 0.6 percent first rung, where the only thing the system can lock is a breakeven sliver. The Chandelier trail — the tool whose whole job is to let a winner run — floors at breakeven on any sub-0.3-percent move and therefore writes essentially zero stops (8 wins out of 3,319 attempts across three days). The ladder, the only profit tool that ever acts, can lock only plus 0.05 to plus 0.13 percent, and even that is frequently dropped by the gateway's minimum-distance clamp (99.6 percent of rejected writes are clamp-noops). The take-profit is unreachable by construction.

The deeper truth beneath all of this: every threshold in the exit stack is a FIXED, HARDCODED PERCENTAGE applied identically to every coin, regardless of how much that coin actually moves. A flat 0.6 percent rung is unreachable on a quiet coin and trivial on a volatile one. The system does not adapt its profit-taking, loss-cutting, or trailing geometry to each coin's real volatility — so it fits almost none of them.

### What our current loss looks like

Nearly every trade goes green and the system cannot keep it. Around 100 percent of trades reach profit; roughly half give it all back and close red; even the winners surrender about 88 percent of their peak. On the full day of 254 trades, 103 wins averaged plus 0.13 percent and 154 losses averaged minus 0.23 percent — an average loss about 1.8 times the average win — for a daily net around minus 22.73 percent, with ZERO take-profits hit. The money is lost entirely in the exit: the entries reach profit, and the exit hands it back.

### What we should get

The aim is a system that KEEPS the profit it reaches. Stated concretely as the operator's target: a win rate of 70 percent or higher WITH proper profit per trade — meaning each trade's exit must be sized so the green it reaches is actually locked and grown, not given back, and each trade must be evaluated NET OF ITS OWN FEES (the round-trip taker fee, about 0.11 percent, plus any slippage) so that a "win" is a real net-positive outcome, not a gross figure erased by costs. The system must calculate each trade's fees and required net move and adapt its exit geometry to that trade's coin, volatility, and cost — individually, per open trade, in parallel.

Note honestly: this program makes the system KEEP the move each coin offers and accounts for fees so wins are real — it is the necessary and dominant fix for the proven loss. Whether the entries also need to hunt larger moves is a related question this program informs (by measuring net-of-fee move sizes) but does not by itself settle; the adaptive geometry is the right fix regardless of that answer, because a system trading either small or large moves must size its exits to each coin's volatility.

### What we will do — three phases

Phase 1: investigate the COMPLETE exit system code by code, line by line, and produce a full written inventory of every mechanism and every HARDCODED VALUE that should instead be dynamically adaptive. Phase 2: merge that inventory with the twelve already-proven issues from the forensic report into ONE master list — the twelve, plus every additional hardcoded value, gap, anomaly, or mistuned threshold Phase 1 surfaces. Phase 3: design the best dynamic adaptive exit system for this project's architecture, and implement it — fixing everything on the Phase 2 master list by making the exit geometry scale to each coin's live volatility and each trade's fees, working individually for each open trade in parallel.

**THESE ARE FIXES AND CALIBRATIONS — NOT NEW FEATURES, AND NOT NEW GATES.** This converts fixed, hardcoded exit values into volatility-and-fee-derived dynamic values, and fixes the proven defects. It adds no new trading gate, no coin-selection filter, no trade-suppression rule. The owner hierarchy (Head, green owner, red owner, advisory) stays exactly as built and the adaptive layer sets the VALUES that hierarchy uses — it does not change WHO owns the stop. If any part appears to require a genuinely new gate or mechanism, stop and escalate to the operator rather than building one.

**ONE CRITICAL GUARDRAIL ON "ADAPTIVE."** Adaptive means the thresholds are DERIVED from each coin's MEASURED volatility (ATR, regime) and the trade's fees, through TRANSPARENT, BOUNDED formulas with hard floors and ceilings — predictable and replayable. Adaptive does NOT mean a self-optimizing loop that changes its own values based on its own profit and loss in real time. The system must never free-run or self-tune; it scales off measured inputs through fixed formulas, so any trade's geometry can always be explained ("this coin's ATR is X, the formula gives rung Y"). A self-modifying optimizer would rebuild the unpredictability and the collision risk the project just eliminated, and is forbidden.

The authoritative context is EXIT_SYSTEMS_DEEP_FORENSIC_FINDINGS.md (the proven twelve issues and the move-size mismatch), the captured logs, and the existing volatility profiler the report identified (src/analysis/vol_scale.py, already consulted by the gateway). Read them first. If this prompt and those documents conflict, stop and escalate rather than guessing.

Do not review this prompt. Do not critique it. Do not rewrite it. Execute the phases.

**IMPORTANT — WORK ON THE MAIN BRANCH. DO NOT CREATE A NEW BRANCH. DO NOT CREATE A NEW DIRECTORY.** All work happens in the existing working tree at the project root. Investigation notes and the inventories go in single files, not a tree of files. All code changes are committed directly to main as atomic, clearly-labeled, individually-revertible commits with plain-language messages. If you believe a branch is needed, stop and escalate instead of creating one.

**SHIP ONE THING AT A TIME.** This changes the most safety-critical machinery in the system — the stop-loss geometry. Within Phase 3, ship one coherent change at a time, verify it, and revert anything that destabilises the system, weakens the catastrophic floor, or regresses a working protection. Do not batch.

**EFFICIENCY MANDATE.** Do not waste time. No elaborate dev-notes directory trees. No git command loops. No repeated identical shell commands. A prior session lost roughly three hours stuck in command loops; do not repeat that. Read each file thoroughly once, map it, and move on.

**ACCESSIBILITY.** The operator (Inshad) is a blind developer using a screen reader. All output, all reports, and all inventories must use proper heading structure (h1/h2/h3). No emoji. Clear prose. No ASCII-art tables. No decorative separators. State findings in sentences. Where a value matters, state it in a sentence with its location.

The operator must approve at the decision gate at the end of Phase 1, again at the end of Phase 2, and again on the Phase 3 design BEFORE any implementation. Applying implementation before the design gate is approved is a serious violation. Because this touches the stop-loss, the bar for each gate is the highest in the project.

---

## Part A — The Investigation Depth Mandate (The Most Important Rule — Read Twice)

Across all three phases, before concluding anything and before touching any code, you must:

- Go through EACH AND EVERY file in the exit system, code by code, LINE BY LINE, until you fully understand what each does — not what you assume. The profit sniper (both its profit-fetching and loss-cutting sides), the time-decay loss engine and the time dial, the stop-loss gateway and owner switch, the position watchdog, the sentinel/deadline/advisor/firewall, the trade coordinator close path, the execution and close layer, the risk models, the stop-loss geometry and validation, the volatility profiler, the orchestration and wiring, and the configuration. Do not miss a single file. If unsure whether a file is relevant, read it and decide; do not skip it on assumption.
- Map the COMPLETE dependency picture: every file that reads or writes an exit threshold, every config key and its current value, every consumer of every threshold, every place volatility is or could be measured, and every interaction between systems. The full wiring — every threshold, every formula, every cross-file relationship — captured before any change.
- Confirm every finding from BOTH the code AND the logs (the forensic report and the captured windows). A claim from one without the other is not proven. Where the forensic report already proved something, cite it; where Phase 1 finds something new, prove it the same way.
- For every hardcoded value found, determine: what it controls, why it was set to that value, what depends on it, and whether and how it should become volatility-or-fee-derived — so the adaptive replacement preserves intent and breaks no consumer.
- Only after the complete, dependency-mapped, line-by-line understanding is established do you design, and at implementation go through ALL connected files again to ensure the change is correct everywhere the threshold reaches.

This depth is mandatory. A change proposed without the complete file-by-file investigation behind it is rejected. Because this is the stop-loss geometry, an incomplete map is unacceptable. The goal is the LAST fix for the exit-sizing problem — a system that fits every coin — not another partial patch.

---

## Part B — The Three Phases

### PHASE 1 — Map the complete exit system and every hardcoded value (investigation and written inventory; no code change)

The situation. The exit is a deep, interacting stack, and its failure is that its values are fixed where they should be adaptive. Before anything can be fixed, the complete truth must be on paper.

What Phase 1 produces. A full written inventory, in one report file, of: every mechanism in the exit stack (profit ladder, Chandelier trail, score-action engine, profit guards, graduation latch; the five loss models, the force-close gate stack, the stall valve, the recovery logic; the watchdog lanes, the deadline tiers; the gateway rules and the owner switch; the catastrophic cap) — what each does, read from the code line by line — AND, the central deliverable, EVERY HARDCODED VALUE in the entire exit system that should instead be dynamically adaptive. For each hardcoded value: its name and exact location, its current value, what it controls, what depends on it, why it is mistuned for the real move sizes (with evidence), and the proposed basis for making it adaptive (which volatility or fee input should drive it). This must be exhaustive — every arm, rung, lock, trail distance, minimum distance, dead-band, take-profit, stop multiple, hard stop, age gate, time threshold, ratio gate, and any other fixed number in the exit path. Include even values that seem minor; the inventory must be complete.

How it should work. Read the whole stack line by line, list every mechanism, hunt down every fixed numeric threshold, and for each decide whether it is legitimately constant (a true invariant) or wrongly hardcoded (should scale with volatility or fees). Cross-check against the forensic report so nothing it already found is missed. Produce the inventory and STOP at the gate for the operator to review before Phase 2.

Why this matters. The forensic report found twelve issues, but it was scoped to the failures it could prove in the examined windows; it did not enumerate every hardcoded value in the entire stack. Phase 1 does — so the adaptive system replaces ALL the fixed values that should adapt, not just the dozen the report surfaced. Trial/gate: the inventory is complete (every exit file covered, every fixed threshold listed with location and current value), each entry justified, and the operator approves it before Phase 2.

### PHASE 2 — Merge into one master list (the twelve issues plus everything Phase 1 found)

The situation. There are now two sources of truth: the twelve proven issues from the forensic report, and the complete hardcoded-value inventory from Phase 1. They must become one master list so the fix in Phase 3 addresses everything.

What Phase 2 produces. ONE master list that contains: the twelve proven issues from the forensic report (the trail that never wins, the unreachable first rung, the unreachable take-profit, the dead-drifter gap, the faded-winner lockout, the clamp-noop, the wrong-side wire-fails, the hardcoded hard stop, the unmanaged dead band, the unactivated recovery capture, the fee-suppression side effect, and the mislabeled wire-fail severity), PLUS every additional hardcoded value, gap, anomaly, or mistuned threshold Phase 1 surfaced that was not already in the twelve. Each item carries its code location, its current behavior, the evidence it is wrong or should be adaptive, and the proposed adaptive basis. The list is de-duplicated (where a Phase 1 value underlies a forensic issue, they are linked, not double-counted) and prioritized by how much it costs (proven from the give-back and fee numbers).

How it should work. Take the twelve, append everything new from Phase 1, link the overlaps, prioritize by cost, and present the unified master list at the gate for the operator to review before any design or implementation. Why it matters: this is the single, complete specification of everything the adaptive system must fix — so nothing is missed and nothing is fixed twice. Trial/gate: the master list is complete (the twelve plus all Phase 1 additions), de-duplicated, prioritized, each item evidenced, and the operator approves it before Phase 3.

### PHASE 3 — Design and build the per-trade dynamic adaptive exit system

The situation. With the complete master list approved, design the best dynamic adaptive exit system for this project's architecture and implement it — fixing everything on the master list by making the exit geometry scale to each coin's live volatility and each trade's fees, individually per open trade, in parallel, under the existing owner hierarchy.

What the design must be. Each open position carries its own exit geometry — arm, ladder rungs, lock offsets, trail distance, dead-band, take-profit, stop multiples — DERIVED from that coin's live volatility (ATR/regime, via the existing profiler) and that trade's fees (the round-trip cost), through transparent, bounded formulas with hard floors and ceilings. The geometry is recomputed as the trade lives (a coin quiet now and volatile later gets updated geometry), and every position is managed in parallel and independently (each touches only its own stop — the non-colliding shape, the opposite of the old collision). The owner hierarchy is unchanged: the Head/green-owner/red-owner switch still decides WHO owns each stop; the adaptive layer sets WHAT VALUES they use. Fees are calculated per trade so a win is net-positive: the minimum profit to arm, the lock levels, and the take-profit are all set above the trade's round-trip cost so the system locks REAL profit, not a gross figure fees erase. The behavior gaps that are not pure scaling (the dead-drifter band needing a scratch-exit, the faded-winner lockout, the recovery-bounce capture) are addressed as part of the design, coordinated with the owner hierarchy, not bolted on as new gates.

How it should work after building. A volatile coin gets wide, reachable rungs and a wide trail; a quiet coin gets tight, reachable rungs and a tight trail that can sit inside its small move; each trade's profit targets clear its own fees; the trail can actually win and trail a real peak; the ladder locks reachable profit instead of a sliver; the take-profit is reachable for the move the coin makes; the dead band is managed; and every position runs its own geometry in parallel. The clamp-noop and wrong-side wire-fails resolve because the locks are sized to the coin's real movement and clear the minimum distance. The catastrophic cap stays sacred and only-tightens.

Why it is built this way. Because the proven root cause is fixed values applied to variable movement; the cure is values derived from each coin's movement and cost. Trial/verification: REPLAY the design against the real trades from the captured windows BEFORE enabling live — show, on the actual logged trades, how many winners the adaptive geometry would have locked and grown versus the flat values that clipped them, and confirm the net-of-fee outcomes. Then ship one coherent change at a time, log-only or shadow where possible, verify on live trades, with a forced catastrophic-stop test confirming the Head still fires and only tightens. The target to measure against: materially higher win rate and real net-of-fee profit per trade, with winners no longer surrendering most of their peak.

Honest note on the 70 percent target. The operator's target is a win rate of 70 percent or higher with proper net profit. State plainly at the design gate what the adaptive exit can and cannot do toward that: the adaptive geometry can stop the give-back and make wins net-positive (a large, measurable gain), but the final win rate also depends on the entries and the realized move sizes, which this program measures but does not change. Do not promise 70 percent from the exit alone; show, from the replay, what win rate and net profit the adaptive exit would have produced on the real trades, and let the evidence set expectations honestly.

---

## Part C — Hard Rules (Non-Negotiable)

### Rule 1 — The investigation depth mandate (Part A) governs all three phases

Every relevant file, line by line, complete dependency map, every finding proven from code AND logs, every hardcoded value's intent and consumers understood before it is made adaptive, all connected files re-checked at implementation. A change without this is rejected. This is the stop-loss geometry; an incomplete map is unacceptable.

### Rule 2 — These are fixes and calibrations, NOT features and NOT gates

Fixed values become volatility-and-fee-derived; proven defects are fixed. No new trading gate, no coin filter, no trade-suppression rule. The owner hierarchy is unchanged. If a genuinely new mechanism seems required, stop and escalate.

### Rule 3 — Adaptive means derived-and-bounded, NEVER self-optimizing

Thresholds are derived from measured volatility and fees through transparent, bounded formulas with hard floors and ceilings — predictable and replayable. No self-tuning loop, no value that changes based on its own profit and loss. Any trade's geometry must be explainable from its inputs. A self-modifying optimizer is forbidden.

### Rule 4 — Per-trade, parallel, independent

Each open position carries its own geometry, recomputed as it lives, managed independently of every other position (each touches only its own stop). This is the non-colliding shape; it must not reintroduce cross-trade interference.

### Rule 5 — Fees are calculated per trade; wins must be net-positive

Each trade's round-trip cost is computed, and the arm, locks, and take-profit are set above that cost so the system locks REAL net profit. A gross "win" that fees erase is not a win.

### Rule 6 — The catastrophic cap (the Head) stays sacred and only tightens

The adaptive layer sets values BELOW the Head; it never weakens or overrides the catastrophic per-trade stop, which always can fire and only tightens.

### Rule 7 — The owner hierarchy is unchanged

The Head/green-owner/red-owner switch decides who owns the stop; the adaptive layer sets what values they use. Do not change ownership; do not regress the enforcing owner switch.

### Rule 8 — Do not regress the confirmed-working parts

The honest scoreboard, the two-sided signal, the fresh inputs, the enforcing owner switch, the sacred cap, the working loss-cutters (the cap and stops keep losses small; no catastrophe breached), the graduation latch, and the correct spine selection are confirmed working. No change may regress them. Where a working protection is preserved verbatim, keep it.

### Rule 9 — No assumptions, no guess-fixes

Every claim cites a code location AND a log fact, re-verified against current code. Probably and should-be are not a basis for action. Reuse the existing volatility profiler rather than inventing a parallel one; confirm how it already feeds the gateway before extending it.

### Rule 10 — Replay before enabling

The adaptive design is replayed against the real logged trades to prove it would have locked and grown the winners and produced net-of-fee gains, BEFORE it is enabled live. Ship one coherent change at a time, shadow/log-only where possible, verify live, with a forced catastrophic-stop test.

### Rule 11 — Parameters centralized and tuning-ready

Every formula, floor, ceiling, and volatility/fee coefficient is named, centralized configuration, never hardcoded inline, with boot sentinels confirming load. The whole point is to remove hardcoded values; do not introduce new ones.

### Rule 12 — File edits via sed, with backups

No interactive editors. Timestamped in-place backups before editing any file, kept in the original directory.

### Rule 13 — Commit on main, atomic and labeled

No new branch, no new directory. Atomic, individually-revertible commits with plain-language messages, one coherent change each.

### Rule 14 — Observability for every change

Each trade's computed geometry visible in the logs (its volatility input and the resulting arm/rung/trail/stop/TP), the trail now winning and writing where it should, the locks at reachable levels, the fee-aware net targets, and boot sentinels confirming the adaptive layer loaded. The operator must be able to see each trade's geometry and why it is what it is.

### Rule 15 — Self-verification with concrete values

Each phase verified against its gate; the Phase 3 implementation verified against the replay and on live trades, including the forced catastrophic-stop test. Verification scripts in the project root, clearly named, never deleting or rewriting data to pass.

### Rule 16 — Honest reporting and provisional verdicts

If a hardcoded value turns out to be a legitimate invariant, if a value cannot be made adaptive without breaking a consumer, if the replay shows the adaptive exit underperforms expectations, or if the 70 percent target is not achievable from the exit alone — say so plainly at the gate. All verdicts are provisional until measured live.

### Rule 17 — Protected tables untouched

No retention or cleanup on tias_results, trade_log, trade_history, thesis_store, virtual_positions.

---

## Part D — Trial Behavior And Expected Values (How The System Checks Itself)

Phase 1: the inventory is complete — every exit file read line by line, every hardcoded value listed with its location, current value, what it controls, why it is mistuned, and its proposed adaptive basis — and the operator approves before Phase 2.

Phase 2: the master list is complete (the twelve proven issues plus every Phase 1 addition), de-duplicated, prioritized by cost, each item evidenced — and the operator approves before Phase 3.

Phase 3 design: a per-trade, volatility-and-fee-derived, bounded, parallel adaptive geometry under the unchanged owner hierarchy, with fees making wins net-positive and the behavior gaps addressed — presented with a replay against the real logged trades showing the win-rate and net-profit it would have produced, and approved before implementation.

Phase 3 implementation: shipped one coherent change at a time; each trade's geometry visible and explainable in the logs; the trail winning and writing where it should; locks at reachable, net-positive levels; the take-profit reachable for the coin's move; the dead band managed; the clamp-noop and wire-fails resolved; the catastrophic cap still firing in a forced test and only tightening; the confirmed-working parts intact; measured on live trades against the higher-win-rate, real-net-profit target.

Cross-cutting: investigation-first throughout; everything proven from code and logs; adaptive-but-bounded, never self-optimizing; per-trade and parallel; fees accounted; the Head sacred; the owner hierarchy unchanged; parameters centralized with no new hardcoded values; protected tables untouched; one change, one commit, one verification at a time.

---

## Part E — Anti-Patterns To Avoid

Do not build a self-optimizing or self-tuning loop — derive from measured volatility and fees through bounded formulas only. Do not change who owns the stop — the adaptive layer sets values, the hierarchy is unchanged. Do not weaken the catastrophic cap. Do not introduce new hardcoded values while removing old ones — centralize every coefficient. Do not let the geometry collide across trades — each touches only its own position. Do not count a fee-eroded gross gain as a win — size targets above the round-trip cost. Do not enable live before replaying against the real trades. Do not regress the working parts (owner switch, cap, loss-cutters, spine, latch). Do not promise the 70 percent target from the exit alone — show what the exit can do and be honest about what depends on the entries. Do not assume — prove from code and logs. Do not skip a hardcoded value because it seems minor — the inventory must be complete. Do not create a branch or directory. Do not waste time in command loops. Do not declare any phase done until its gate passes; do not declare Phase 3 done until the replay and the live verification (including the forced catastrophic-stop test) pass.

---

## Part F — What Success Looks Like

The exit finally fits the market it trades. Every open position carries its own geometry — arm, rungs, lock, trail, dead-band, take-profit, stops — derived from that coin's live volatility and that trade's fees, recomputed as the trade lives, managed in parallel and independently under the unchanged owner hierarchy. A quiet coin gets tight, reachable rungs and a trail that sits inside its small move; a volatile coin gets wide, reachable rungs and a trail with room to run. Every trade's profit targets clear its own fees, so a win is real net profit, not a gross figure costs erase. The trail wins and trails real peaks; the ladder locks reachable profit instead of a breakeven sliver; the take-profit is reachable for the move the coin makes; the dead band is managed; the faded winner and the recovered trade are handled; and the clamp-noop and wrong-side wire-fails dissolve because the locks are sized to the coin's real movement. The catastrophic cap stays sacred. Everything on the master list — the twelve proven issues plus every hardcoded value Phase 1 surfaced — is fixed by one coherent adaptive design, proven on a replay of the real trades before going live, shipped one change at a time, observable and revertible, on main. The system stops handing back the green it reaches.

---

## Part G — What Success Does NOT Mean

This program makes the exit fit each coin and makes wins net-positive — the necessary and dominant fix for the proven loss. It does not by itself guarantee the 70 percent win-rate target, and that must not be claimed. The win rate and the net profit also depend on the entries and the realized move sizes, which this program MEASURES (through the fee-and-move-size accounting and the replay) but does not change. The adaptive exit will stop the give-back and make the wins real — a large, measurable improvement provable on the replay — but whether the final figure reaches 70 percent depends on whether the entries produce enough net-of-fee move to clear costs and win consistently, which is a separate, related question this program informs and the operator decides next. Success here is an exit that fits every coin, keeps the green, and counts fees honestly — and an evidence-based, replayed projection of the win rate and net profit it produces. The remaining distance to the target, if any, is the entries' to close, and the data this program produces will show exactly how much that is. The adaptive geometry is the right foundation regardless; the market and the entries decide the rest.

---

## Part H — End Of Prompt

Begin Phase 1.

Confirm the working tree is clean and on main, the active adapter, and the protected-table row counts. Read EXIT_SYSTEMS_DEEP_FORENSIC_FINDINGS.md and the captured logs in full, and locate the existing volatility profiler and how it already feeds the gateway.

Then execute Phase 1: read the COMPLETE exit stack code by code, line by line, file by file, and produce the full inventory of every mechanism and EVERY HARDCODED VALUE that should be dynamically adaptive — each with its location, current value, what it controls, what depends on it, why it is mistuned, and its proposed adaptive basis. Stop at the gate.

After approval, execute Phase 2: merge the twelve proven issues with every Phase 1 finding into one de-duplicated, prioritized master list, each item evidenced. Stop at the gate.

After approval, execute Phase 3: design the per-trade dynamic adaptive exit system — every threshold derived from each coin's live volatility and each trade's fees, bounded and explainable, recomputed as the trade lives, parallel and independent, under the unchanged owner hierarchy, with wins made net-positive and the behavior gaps handled — present it with a replay against the real logged trades showing the win rate and net profit it would have produced, and stop at the design gate. After approval, implement one coherent change at a time, replay-verified and live-verified, with the forced catastrophic-stop test, all connected files re-checked.

Remember throughout: the situation is that the exit is built for moves the entries do not produce, every threshold hardcoded where it should scale to the coin; the aim is to KEEP the green and make wins net-positive, targeting a far higher win rate with real profit. These are FIXES AND CALIBRATIONS, NOT features and NOT gates. Adaptive means DERIVED AND BOUNDED, never self-optimizing. Per-trade, parallel, independent. Fees counted so wins are real. The Head sacred; the owner hierarchy unchanged; no new hardcoded values; the working parts intact. Understand before you touch — code by code, line by line, file by file. Root cause, not band-aid. Prove from code and logs — no assumptions. Replay before live. One phase, one gate; one change, one commit, one verification. Work on main, no new branch, no new directory. Be honest about what the exit can and cannot do toward the 70 percent target. If the code contradicts the documents, escalate. If something does not fit, document it and escalate.

Begin Phase 1.
