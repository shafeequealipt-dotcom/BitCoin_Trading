# INVESTIGATE AND FIX — Consolidate The Colliding Exit Systems Into One Coherent Authority Hierarchy (A Single Head Above, Profit-Fetching Owns Green, Loss-Cutting Owns Red, Everything Else Advisory), With Exhaustive File-By-File Root-Cause Investigation Proven Against The Logs (Main Branch, No New Branch)

## STOP. READ THIS ENTIRE DOCUMENT BEFORE WRITING ANY CODE.

### Where we are: the complete situation

The system is structurally honest now — the scoreboard tells the truth, the signal is two-sided, the inputs are fresh, and the brain's decisions execute as made. On that honest foundation, a deep forensic analysis of two consecutive full-day windows (2026-06-13 22:45 to 2026-06-14 09:45, and 2026-06-14 10:45 to 21:45 — 254 trades total) proved exactly where the money is being lost, and it is NOT the entries. It is the EXIT.

The proof, from the trades themselves: every single trade reached profit — 100 percent of them ticked green at some point. Yet about 46 percent of all trades gave that profit back entirely and closed at a loss, and even the trades that won gave back roughly two-thirds of their peak (winners peaked around +0.33 to +0.39 percent and closed at only +0.10 to +0.16 percent). Across all 254 trades over two days, planned targets averaged 3.8 percent and ZERO take-profits were hit — not one. The win rate was actually fine (37 to 42 percent); the system still lost, because the realized reward-to-risk was 0.45 to 0.63 when the planned reward-to-risk was about 2.5. The exit is throwing away three-quarters of every win.

The deeper investigation found WHY, and it is the reason this fix exists: the exit is not one system, it is a COLLISION of many. On a single trade, at least TEN distinct sources write to the same stop-loss — the profit-sniper ladder, the structure guard, the profit gate, loss-recovery, loss-cap, loss-atr-initial, loss-structure, the breakeven override, the brain reaching in directly, and the exchange-authoritative path. Across the two days, the stop-update gateway received over 4,700 calls from the profit ladder alone, plus hundreds each from four separate loss-writers, plus the brain's direct tightening, plus the watchdog, plus sentinel deadlines. The gateway arbitrates this chaos by clamping (over 2,200 times) and rejecting (nearly 1,700 times) — because the writers constantly contradict each other.

And the clip emerges from that collision. The profit ladder arms its trailing floor at only +0.20 percent and locks at +0.13 percent — but worse, during a trade's first 300 seconds the smart stall-escape logic is deliberately muzzled by an age guard while the dumb mechanical clamp and the breakeven override fire anyway. So exactly when a trade peaks young (the peaks cluster at +0.15 to +0.35 percent in the first minutes), the only exit machinery awake is the tight mechanical clamp, with no smart logic to balance it and loss-writers ALSO touching the stop of a green trade. The winner is caged: it cannot run (the stop is pinned tight just above breakeven), it cannot escape cleanly (the profit guard blocks small-profit exits), and it gets closed at or below breakeven on the first normal pullback. No single system decided to clip the winner — the collision of ten did.

So the situation is: two exit engines that should be clean and coordinated are instead buried inside a committee of roughly fourteen systems with overlapping, uncoordinated authority over one number — the stop-loss — and the emergent behavior of that committee is to strangle every winner the system catches.

### What our aim is on this project

The project's aim is aggressive exploitation and maximum profit with no directional bias — to catch good trades and let them PAY. The two-day data proves the entries already catch enough profit (every trade goes green; the win rate is 40 percent); the failure is that the exit refuses to KEEP the profit. So the aim of THIS fix is precise: make the exit a single coherent system that lets winners run while still cutting losers and never breaching the catastrophic risk bound — so the profit the system already reaches is actually realized instead of given back.

### What we have planned (the four-bucket authority hierarchy) — in depth

The fix is to dissolve the ten-way collision into one clear authority hierarchy with a single owner of the stop at any moment, decided by the trade's state, with one Head above all for catastrophe. Nothing is deleted; every existing system is REASSIGNED into one of four buckets:

Bucket one — THE HEAD (always active, overrides everything): the catastrophic per-trade hard stop, the absolute loss cap. It is on top of both engines, it can only ever tighten and never loosen, and it fires regardless of which engine owns the trade or whether the trade is green or red. This is the sacred risk bound. By the operator's explicit decision, the Head is ONLY the catastrophic per-trade stop — nothing else overrides a running green trade (this is the pure profit-priority choice).

Bucket two — THE GREEN OWNER (active only when the trade is in profit): the profit-fetching engine owns the stop when the trade is green, with near-total authority — only the Head can override it. The profit ladder, the trailing floor, the profit guard, the breakeven handling, and the trail computation become the TOOLS of this owner, acting only when green. When a trade is green, the loss-cutting writers do NOT touch its stop. By the operator's decision this is PROFIT-PRIORITY: when green, let the winner run; the profit engine decides lock-versus-run; only catastrophe (the Head) can seize it.

Bucket three — THE RED OWNER (active only when the trade is underwater): the loss-cutting engine owns the stop when the trade is red. The time-decay logic (the MAE guard, the monotonic hold, the age guard), loss-recovery, loss-cap, loss-atr-initial, and loss-structure consolidate into the TOOLS of this single owner, acting only when red. When a trade is red, the profit ladder does NOT touch its stop. The hand-off between the green owner and the red owner happens at the breakeven line: cross up from red to green, authority passes to the profit engine; cross back down, authority passes to the loss engine. One owner at a time, decided by state.

Bucket four — ADVISORY (they suggest, they do not write the stop): the brain's direct tightening, the structure reads, the sentinel deadline, the strategy confluence inputs, and the watchdog's scoring. These currently write the stop independently — part of the collision. Under the hierarchy they ADVISE the engine that owns the stop; they do not write it directly. The position watchdog keeps its genuine job (detecting real exchange closes and reconciliation) but stops independently writing stops.

The stop-update gateway stops being a chaotic arbitrator that clamps and rejects contradictions, and becomes the ENFORCER of this hierarchy: it checks which engine owns the stop right now (by trade state), accepts writes from the rightful owner and the Head, and routes everything else as advice rather than a competing write. The clamps and rejects should largely vanish because the writers stop contradicting each other.

The result: the ten-plus independent writers collapse into one-owner-at-a-time plus the Head plus advisors. The clip dissolves at its root because, when green, only the profit engine touches the stop, the loss-writers are off it, and the early-life collision between the age-guarded smart logic and the always-on mechanical clamp is resolved into one coordinated green owner.

### What we are going to do now

Resolve the collision FIRST — establish the authority hierarchy — and only AFTER that is clean and proven, calibrate the profit engine's arm/lock/trail values. The calibration cannot hold while ten systems fight over the stop, so the authority consolidation is the foundational fix and must come first. This prompt is the authority consolidation. The arm/lock/trail calibration is the SEPARATE next step, deliberately not bundled here.

**THESE ARE FIXES AND CALIBRATIONS — NOT NEW FEATURES, AND NOT NEW GATES.** Nothing here adds a new trading gate, a new exit mechanism, a new filter, or a new lever. Every system already exists; this fix REASSIGNS existing systems into a coherent authority order and makes the existing gateway enforce it. Guards are to be COORDINATED and re-leveled, never lobotomized — each guard exists for a real reason and its protection must be preserved at the correct level. If the fix appears to require a genuinely new mechanism, stop and escalate to the operator rather than building one.

The authoritative context is the two captured log windows (log_bundle_2026-06-13T2245_to_2026-06-14T0945_UTC.log and log_bundle_2026-06-14T1045_to_2026-06-14T2145_UTC.log) and the forensic findings summarized above. Read them first. If this prompt and the evidence appear to conflict, stop and escalate rather than guessing.

Do not review this prompt. Do not critique it. Do not rewrite it. Execute the phases.

**IMPORTANT — WORK ON THE MAIN BRANCH. DO NOT CREATE A NEW BRANCH. DO NOT CREATE A NEW DIRECTORY.** All work happens in the existing working tree at the project root. Investigation notes, if any, go in a single file, not a tree of files. All code changes are committed directly to main as atomic, clearly-labeled, individually-revertible commits with plain-language messages. If you believe a branch is needed, stop and escalate instead of creating one.

**SHIP ONE PHASE AT A TIME.** This fix changes the most safety-critical machinery in the system — the stop-loss. Ship one phase, verify it on real trades, and revert anything that destabilises the system or weakens the catastrophic floor. Do not batch.

**EFFICIENCY MANDATE.** Do not waste time. No elaborate dev-notes directory trees. No git command loops. No repeated identical shell commands. A prior session lost roughly three hours stuck in command loops; do not repeat that. Read each file thoroughly once, map it, and move on.

**ACCESSIBILITY.** The operator (Inshad) is a blind developer using a screen reader. All output and all reports must use proper heading structure (h1/h2/h3). No emoji. Clear prose. No ASCII-art tables. No decorative separators. State findings in sentences. Where you visualize behavior, use plain screen-reader-friendly prose, not a chart.

The operator must approve at the decision gate for each phase before that phase is applied. Applying a change before its gate is approved is a serious violation. Because this touches the stop-loss, the bar for each gate is the highest in the project.

---

## Part A — The Investigation Depth Mandate (The Most Important Rule — Read Twice)

For this fix, before concluding anything and before touching any code, you must:

- Go through EACH AND EVERY file that touches an exit decision or writes a stop-loss — every file in the chain, not the obvious two or three. The profit-sniper, the time-decay loss logic, every loss-writer (recovery, cap, atr-initial, structure), the stop-update gateway, the position watchdog, the brain's tightening path, the sentinel, the strategy confluence inputs, the trade coordinator's close path, and the catastrophic hard-stop. Do not miss a single file. If unsure whether a file is relevant, read it and decide; do not skip it on assumption.
- Read the code LINE BY LINE in each of those files until you fully understand what it does, not what you assume it does.
- Map the COMPLETE dependency picture: every system that writes the stop, through what path, in what order, how the gateway arbitrates them today, every guard and why it exists, every place trade state (green/red) is known or could be known, and every consumer of the stop value. The full wiring — every writer, every guard, every precedence rule, every hand-off, the cross-file relationships — captured before any change.
- PROVE the situation against the logs. The forensic findings above (ten writers on one stop, the gateway clamping and rejecting, the age-guard-muzzles-smart-logic-while-clamp-fires mechanism, the clip emerging from the collision) are stated from the log evidence — re-verify each against BOTH the code AND the logs. Trace specific real trades from the two windows tick by tick: show the actual sequence of which system wrote the stop when, where the collision clipped the winner, and confirm the mechanism is exactly as described (or correct it if the code and logs show otherwise). A root cause asserted from one without the other is not proven.
- Confirm, for each existing guard and writer, WHY it was added and what protection it provides, so the reassignment preserves that protection at the correct level rather than removing it.
- Only after the complete dependency-mapped, line-by-line understanding is established and the situation is proven against the logs do you design the hierarchy, and — at implementation — go through ALL connected files again to ensure the change is correct across every place a stop is written.

This depth is mandatory. A change proposed without the complete file-by-file investigation AND the log-proven situation behind it is rejected. The goal is the LAST fix for the exit collision — not another partial one. Given this is the stop-loss, an incomplete map is not acceptable.

---

## Part B — The Phases

### PHASE 0 — Prove the situation against the logs (no code change)

Before designing anything, establish the ground truth. Inventory every system that writes a stop-loss or blocks/defers an exit, from the code and confirmed in the logs: name each one, its module, what it does, when it fires, and how often it fired across the two windows. Trace at least a handful of specific real trades from the two windows tick by tick — including clipped winners (trades that peaked green and closed red) and genuine losers — showing the exact ordered sequence of stop writes and guard actions, and pinpoint where the collision clipped the winner. Confirm or correct the stated mechanism (ten writers, the gateway clamping/rejecting, the early-life age-guard-versus-clamp collision, the loss-writers touching green trades). Produce the complete writer-and-guard inventory and the proven mechanism as the foundation. Report before Phase 1.

### PHASE 1 — Establish the trade-state owner switch (the core of the hierarchy)

The situation: today, profit-side and loss-side writers are BOTH active regardless of whether the trade is green or red, which is the root of the collision. The need: one owner at a time, decided by trade state. The aim: when green, only the profit engine writes the stop; when red, only the loss engine writes the stop; the Head (catastrophic stop) always able to fire. How it should work: a single, authoritative notion of trade state (green vs red relative to entry, with the breakeven line as the hand-off) gates which engine may write the stop; the gateway enforces it. Why it is not working today: there is no single owner-by-state rule — every writer calls the gateway whenever it wants, and the gateway clamps the contradictions. The investigation must find where trade state is computed and where each writer calls the gateway, then implement the owner switch so the gateway accepts stop writes only from the engine that owns the current state (plus the Head), routing all others as advisory. Trial: on live trades, a green trade shows only profit-engine stop writes (no loss-writer touching it); a red trade shows only loss-engine writes; the hand-off occurs cleanly at breakeven; the catastrophic stop still fires in a forced test; and the gateway's clamp-and-reject counts drop sharply because the writers no longer contradict each other.

### PHASE 2 — Install the Head as the sole override (catastrophic stop on top)

The situation: the catastrophic hard stop currently competes in the same gateway arbitration as everything else rather than sitting clearly above. The need and aim (operator's Option A): the Head is ONLY the catastrophic per-trade stop, it overrides both engines, it can only tighten and never loosen, and nothing else overrides a running green trade. How it should work: the gateway recognizes the catastrophic stop as the top authority — it is always evaluated, always able to tighten the stop regardless of owner, and never blocked by an engine; and crucially, no other system (not the loss-writers, not the brain, not the sentinel) may override a green trade — only the Head may. Why it is not working today: the catastrophic stop is one writer among many rather than a privileged top authority, so its precedence is not guaranteed. The investigation must find the catastrophic-stop path and confirm it can always tighten under the new hierarchy, and confirm that under profit-priority nothing but the Head touches a green trade. Trial: a green trade runs under profit-engine control with no non-Head interference; a forced catastrophic move still triggers the Head and cuts the trade; the Head never loosens a stop.

### PHASE 3 — Consolidate the profit-side tools under the green owner

The situation: the profit ladder, trailing floor, profit guard, breakeven handling, and trail computation currently act as semi-independent writers (the ladder alone called the gateway over 4,700 times) and collide with the age guard's muzzling of the smart logic. The need and aim: these become the coordinated TOOLS of the single green owner, acting only when green, with no internal collision between the mechanical clamp and the smart logic. How it should work: when the profit engine owns the trade (green), its ladder/trail/guard/breakeven act as one coordinated unit on a single timing rule — the early-life age-guard-muzzles-smart-logic-while-clamp-fires contradiction is resolved so the mechanical clamp does not strangle a young peak while the smart logic is held off. Why it is not working today: the profit-side tools run on contradictory timing (smart logic age-gated 300s, mechanical clamp always on) so only the clamp acts at the early peak. The investigation must map the profit-side tools and their timing and consolidate them under one coherent green-owner logic. NOTE: this phase RESOLVES the collision and coordination; it does NOT re-tune the arm/lock/trail VALUES — that calibration is the separate next program. Keep the existing values; fix only the authority and coordination. Trial: when green, the profit-side tools act as one coordinated owner with no internal contradiction; the loss-writers are off the green trade; the clip mechanism (mechanical clamp strangling a young peak while smart logic is muzzled) no longer occurs.

### PHASE 4 — Consolidate the loss-side tools under the red owner

The situation: at least four separate loss-writers (recovery, cap, atr-initial, structure) plus the time-decay guards act independently, some even writing stops on green trades. The need and aim: these consolidate into the single red owner, acting only when red, preserving each guard's genuine protection (the MAE guard protecting recoverable trades, the monotonic hold, the age guard) at the correct level. How it should work: when the loss engine owns the trade (red), its tools act as one coordinated unit; when the trade is green, none of them touch the stop. The protections that work (the loss-cutters held losses below the stop in the data) are preserved. Why it is not working today: the loss-writers are independent and state-blind. The investigation must map every loss-writer, confirm why each exists and what it protects, and consolidate them under the red owner without losing any genuine protection. Trial: when red, the loss tools act as one coordinated owner; when green, no loss-writer touches the stop; the recoverable-trade protection (MAE guard) still holds; the working loss-cutters still cut grinds and stalls.

### PHASE 5 — Demote the advisory systems

The situation: the brain's direct tightening, the structure reads, the sentinel deadline, the strategy confluence inputs, and the watchdog scoring currently write or override the stop independently. The need and aim: they ADVISE the owning engine; they do not write the stop directly. How it should work: these systems pass their input to the engine that owns the stop, which decides; they no longer call the gateway as independent writers. The watchdog keeps its genuine close-detection and reconciliation job. Why it is not working today: they are independent writers, adding to the collision. The investigation must find each advisory writer's path and reroute it as advice to the owner, preserving the watchdog's real close-detection role. Trial: on live trades, the brain/structure/sentinel/confluence no longer appear as independent stop writers; their input reaches the owning engine as advice; the watchdog still correctly detects real exchange closes.

---

## Part C — Hard Rules (Non-Negotiable)

### Rule 1 — The investigation depth mandate (Part A) governs everything

Every relevant file, line by line, complete dependency map, the situation PROVEN against both the code and the logs (with real traced trades), every guard's purpose understood before reassignment, all connected files re-checked at implementation. A change without this is rejected. This is the stop-loss; an incomplete map is unacceptable.

### Rule 2 — These are fixes and calibrations, NOT features and NOT gates

Every system already exists; this reassigns them into a coherent authority order and makes the existing gateway enforce it. No new trading gate, no new exit mechanism, no new lever. If a genuine new mechanism seems required, stop and escalate.

### Rule 3 — Guards are coordinated and re-leveled, never lobotomized

Each guard exists for a real reason (the MAE guard protects recoverable losers; the age guard prevents premature exits; the profit guard prevents sub-fee bailouts). Reassigning a guard to a bucket must PRESERVE its protection at the correct level, not remove it. Confirm each guard's purpose before moving it.

### Rule 4 — The catastrophic stop (the Head) is sacred and only tightens

The Head is the catastrophic per-trade stop; it always can fire, overrides both engines, and can only ever tighten, never loosen. No change may weaken it or make it overridable. Under profit-priority (Option A) it is the ONLY thing that may seize a green trade.

### Rule 5 — One owner at a time, by trade state

When green, only the profit engine writes the stop (plus the Head). When red, only the loss engine writes the stop (plus the Head). The hand-off is at breakeven. No state-blind independent writers remain.

### Rule 6 — This phase does NOT re-tune arm/lock/trail values

This program resolves the AUTHORITY and COORDINATION collision only. The arm/lock/trail value calibration is the separate next program. Keep the existing values here; change only who owns the stop and when. Do not bundle the calibration.

### Rule 7 — Do not regress the confirmed-working fixes

The honest scoreboard, the two-sided signal, the fresh inputs, the working loss-cutters (monotonic-grind and stall cuts held losses below the stop), and the exit engines' genuine protections are confirmed working. No change may regress them.

### Rule 8 — No assumptions, no guess-fixes

Every claim cites a specific code location AND a specific log fact (a real traced trade, a real writer count), re-verified against current code. Probably, likely, and should-be are not a basis for action. For the collision mechanism in particular, PROVE it from the traced trades before changing anything.

### Rule 9 — Parameters centralized and tuning-ready

Any precedence rule, state threshold, or hand-off boundary introduced is named, centralized configuration, never hardcoded inline, with boot sentinels confirming load.

### Rule 10 — File edits via sed, with backups

No interactive editors. Timestamped in-place backups before editing any file, kept in the original directory.

### Rule 11 — Commit on main, atomic and labeled, one per phase

No new branch, no new directory. One atomic, individually-revertible commit per phase, plain-language messages.

### Rule 12 — Observability for every change

Each phase observable: the owner-by-state switch visible in the logs (which engine owns each trade); the gateway's clamp-and-reject counts dropping; the Head always able to fire; the loss-writers off green trades; the advisory systems no longer writing stops directly. Add boot sentinels confirming the hierarchy is loaded.

### Rule 13 — Self-verification with concrete values

Each phase verified against its trial in Part B before it is done, including a forced catastrophic-stop test confirming the Head still fires. Verification scripts in the project root, clearly named, never deleting or rewriting data to pass.

### Rule 14 — Honest reporting and provisional verdicts

If the traced trades show the mechanism differs from this document, if a guard cannot be reassigned without losing protection, or if the hierarchy is entangled with a confirmed-working fix — say so plainly at the gate. All verdicts are provisional until verified live.

### Rule 15 — Protected tables untouched

No retention or cleanup on tias_results, trade_log, trade_history, thesis_store, virtual_positions.

---

## Part D — Trial Behavior And Expected Values (How The System Checks Itself)

Phase 0: the complete writer-and-guard inventory produced; the collision mechanism proven on real traced trades from the two windows (the ordered stop writes, the clip point).

Phase 1: live green trades show only profit-engine stop writes; live red trades show only loss-engine writes; the hand-off occurs at breakeven; the gateway clamp-and-reject counts drop sharply; the catastrophic stop still fires in a forced test.

Phase 2: a green trade runs under profit control with no non-Head interference; a forced catastrophic move triggers the Head; the Head never loosens a stop.

Phase 3: when green, the profit-side tools act as one coordinated owner; the early-life clamp-versus-muzzled-smart-logic collision is gone; no loss-writer touches a green trade. (Arm/lock/trail VALUES unchanged — coordination only.)

Phase 4: when red, the loss tools act as one coordinated owner; the recoverable-trade protection still holds; the working loss-cutters still cut; no loss-writer touches a green trade.

Phase 5: the brain/structure/sentinel/confluence no longer appear as independent stop writers; their input reaches the owning engine as advice; the watchdog still detects real closes.

Cross-cutting: every phase shipped one at a time, observable, independently revertible; the Head sacred and only-tightening; one owner at a time by state; arm/lock/trail values untouched (separate program); the confirmed-working protections preserved; no new gate; protected tables untouched; the situation proven against the logs before any change.

---

## Part E — Anti-Patterns To Avoid

Do not delete or lobotomize a guard — reassign and re-level it, preserving its protection. Do not weaken or make overridable the catastrophic Head. Do not let any non-Head system seize a green trade (profit-priority). Do not leave any state-blind independent writer touching the stop. Do not re-tune the arm/lock/trail values in this program — that is the separate next step. Do not assume the collision mechanism — prove it on real traced trades. Do not regress the working loss-cutters or any genuine protection. Do not hardcode the precedence rules. Do not create a branch or directory. Do not waste time in command loops. Do not declare any phase done until its trial passes — including the forced catastrophic-stop test — and the live behavior confirms it.

---

## Part F — What Success Looks Like

The exit stops being a committee and becomes one coherent system. At any moment a single engine owns the stop: when the trade is green, the profit-fetching engine owns it with near-total authority and lets it run; when the trade is red, the loss-cutting engine owns it and protects or cuts as its now-coordinated tools decide; and above both sits one Head — the catastrophic stop — always able to tighten, the only thing that can seize a green runner. The four separate loss-writers, the profit ladder and its tools, and the advisory systems all take their correct place: one owner at a time by state, the Head on top, the rest advising the owner instead of fighting it. The stop-update gateway enforces the hierarchy instead of refereeing a brawl, and its clamp-and-reject storm subsides because the writers no longer contradict each other. The clip dissolves at its root: when a trade peaks young and green, only the profit engine touches its stop, the loss-writers are off it, and the early-life collision that strangled the peak is gone — the winner is finally allowed to be owned by the system that wants it to run. Every change rests on a complete file-by-file, line-by-line investigation proven against the two days of logs, shipped one phase at a time, observable and independently revertible, on main — with the catastrophic floor sacred, every genuine protection preserved, no new gate, and the arm/lock/trail calibration left for the next program.

---

## Part G — What Success Does NOT Mean

Completing this program resolves the exit-system collision — it gives the stop one owner at a time and a single Head above — but it is the FOUNDATION, not the whole exit fix. It does not by itself re-tune how far winners run; the arm/lock/trail calibration (raising the arm above the noise band, trailing at a distance, staging toward the targets) is the deliberate next program, and it can only be done correctly ONCE the collision is resolved and the profit engine cleanly owns green trades. So success here means the exit is finally a coherent system whose behavior can be measured and tuned — not that the winners already run to target. Nor does it guarantee profitability: it removes the structural reason winners were strangled, which is the precondition for the calibration that follows to actually let profit be kept. The honest sequence is: consolidate the authority now (this program), then calibrate the profit engine on the clean system (next program), then measure whether the realized reward-to-risk finally matches the planned. This program is step one of that sequence, and the most important one, because nothing downstream can hold until the collision is gone.

---

## Part H — End Of Prompt

Begin Phase 0.

Read the two log windows and the forensic findings in full. Confirm the working tree is clean and on main, the active adapter, and the protected-table row counts.

Then execute Phase 0 — prove the situation against the logs: inventory every system that writes a stop or blocks an exit, and trace real clipped-winner and loser trades tick by tick to prove the collision mechanism. Report before designing.

Then execute the phases in order, one at a time, each with the full Part A investigation, the design, the plain-prose before-and-after, the decision gate (the highest bar in the project — this is the stop-loss), and after approval the implementation with all connected files re-checked and the trial run including the forced catastrophic-stop test: establish the trade-state owner switch; install the Head as the sole override; consolidate the profit-side tools under the green owner (coordination only, values unchanged); consolidate the loss-side tools under the red owner (protections preserved); and demote the advisory systems to advice.

Remember throughout: the aim is to LET WINNERS RUN by giving the exit one coherent authority — profit-fetching owns green, loss-cutting owns red, one catastrophic Head above both, everything else advisory. These are FIXES AND CALIBRATIONS, NOT features and NOT gates — existing systems reassigned, not new mechanisms. Guards are coordinated and re-leveled, never lobotomized. The catastrophic Head is sacred and only tightens. One owner at a time by state. Do NOT re-tune the arm/lock/trail values here — that is the next program. Understand before you touch. Root cause, not band-aid. Prove the situation from the code AND the logs on real traced trades — no assumptions. One phase, one commit, one verification at a time. Work on main, no new branch, no new directory. If the code contradicts the evidence, escalate. If something does not fit, document it and escalate.

Begin Phase 0.
