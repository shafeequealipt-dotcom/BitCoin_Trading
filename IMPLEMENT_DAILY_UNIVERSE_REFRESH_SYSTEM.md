# INVESTIGATE, IMPLEMENT, AND INTEGRATE — The Daily Dynamic Universe-Refresh System That Rebuilds The 50-Coin Universe Around Coins That Actually Move (Twice-Daily Scheduled Plus Manual, Multi-Day Activity Selection, Data-Gated Warm-Up, Open Positions Always Managed), Woven Into The Live System With Exhaustive File-By-File Investigation (Main Branch, No New Branch)

## STOP. READ THIS ENTIRE DOCUMENT BEFORE WRITING ANY CODE.

### The situation we are in

The trading system is structurally honest now: the scoreboard tells the truth, the inputs are fresh and two-sided, the brain's decisions execute as made, and the exit-authority owner switch is enforcing correctly. A great deal of work has gone into the exit systems, and that work is correct. But the system still loses money, and a long, evidence-backed investigation has now found the dominant root cause, and it is not the exit. It is the universe of coins the system is allowed to trade.

### What we are facing — proven from the data

Across every window examined, the defining fact is that the coins the system trades do not move enough to make money. In the one-hour window of 2026-06-15 between 01:44 and 02:44 UTC, seventeen positions were opened and the largest peak any coin reached was plus 0.55 percent; most peaked under plus 0.21 percent; not one trade reached even plus 0.6 percent of movement in a full hour. The system set profit targets of 2.5 to 5 percent on these trades while the coins moved a tenth of that at most. The resulting wins were plus 0.07 percent, plus 0.01 percent, plus 0.006 percent — not profit, fee-scratches that landed barely positive. The single real loss in the window, OPUSDT at minus 1.35 percent, was larger than all seven wins combined. The system had a 58 percent win rate and still lost money, because the wins were rounding errors and one loss erased a dozen of them.

### Why the coins do not move — the universe is the cause

The universe is a fixed list of 50 coins in config.toml [universe].watch_list, organised as 12 majors, 23 mid-caps, and 15 aggressive hunters, updated by manual operator review roughly weekly. The exchange exposes about 582 tradeable USDT coins; the system discards 532 of them at the first step and only ever looks at the 50. Two structural flaws cause the no-movement problem. First, the list is anchored to the calmest coins in the market: the 12 majors that lead it (BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, LINK, ARB, NEAR, ATOM) are the largest, lowest-volatility coins there are, the ones that move 1 to 2 percent on a normal day, so a universe led by them is structurally a low-movement universe. Second, the list is refreshed far too slowly: crypto's big movers rotate daily, the coin up 30 percent today is almost never the coin that moved last week, and a weekly hand-review cannot keep up, so by the time a coin reaches the list its move is usually over. The logs prove the consequence: the trades in that window were LINK, SOL, BNB (majors) and AAVE, ICP, DYDX, IMX, SAND (mid-caps) — the calmest tiers — and they went nowhere.

### Why this feature — what it is for

No exit calibration can make a coin that moves plus 0.3 percent into a profitable trade; the binding constraint is upstream, in which coins are available to trade. This feature puts coins that actually move into the 50, every day, so the brain has something worth trading and the exit work (the adaptive R-based geometry, the owner hierarchy) has moves worth keeping. It does not change the runtime architecture — the system still operates on a fixed 50 during operation — it changes the contents of the 50, on a daily cadence, toward coins in an active, volatile, trending phase.

### What should happen after this feature, one by one

After this feature is built and integrated: twice a day (at 23:00 and 11:00 UTC) and on a confirmed manual Telegram press, the system pauses finding new trades while keeping every open position fully managed; it makes one bulk exchange call that returns every coin's recent activity; it scores all coins on sustained multi-day activity (recent realized volatility as the backbone, plus a volume surge and open interest expansion, all gated by a directionality filter that demotes choppy whipsaw coins, with a hard liquidity floor removing untradeable coins first); it ranks them and rebuilds the 50-coin watch list around the genuine movers while keeping a small stable liquid core and all open-position coins; the newly added coins then warm up until their analysis data passes the existing freshness gates; and the brain resumes on the fresh universe. The operator can see, in Telegram, exactly which 50 coins were selected, and can trigger a refresh on demand. The coins the brain trades thereafter actually move, so the wins stop being rounding errors and the exit work finally has real moves to capture.

### What we should get

The operator's target is a win rate of 70 percent or higher with real net-of-fee profit per trade. This feature is a necessary precondition for that target, not the whole of it: it ensures the coins move enough that a win can be real profit rather than a fee-scratch, and that each trade can be evaluated net of its own fees and still come out ahead. Stated honestly (and this must be stated, not oversold): this feature cannot pick the best 50 coins of the day, because that is knowable only after the day is over; it picks 50 coins with a much-better-than-random chance of moving, which is a large improvement over the static calm list but is not a guarantee. False positives (coins that were active and go quiet) and missed movers (quiet coins that explode on news) are unavoidable and expected. The measure of success is whether the traded coins move materially more than they do today, which is verifiable by comparing the selected coins' realized movement against the old list's, and the build must produce that comparison so the improvement is proven on evidence. The 70 percent target also depends on the entries and the exit work; this feature removes the universe as the binding constraint, and the data this feature produces will show how much of the remaining distance is the entries' to close.

### What we will do

Build and wire the daily universe-refresh system into the live system: the scheduled refreshes, the manual Telegram trigger, the multi-day activity selection, the data-gated warm-up, and the open-position safety — investigation-first, phase by phase, each gated by the operator.

**THESE ARE FIXES AND CALIBRATIONS AND A WOVEN-IN ADDITION — NOT A NEW GATE.** This changes the CONTENTS of the universe on a schedule and on demand; it does not add a per-trade filter that refuses trades at decision time, it does not add a coin-selection gate in the trading path, and it does not change the runtime architecture. The selection is a universe-construction process that runs between trading, not a gate inside trading. The exit systems, the owner hierarchy, the brain logic, and the catastrophic cap are untouched. If any part appears to require a new per-trade trading gate, stop and escalate to the operator rather than building one.

The authoritative context is COIN_SELECTION_PIPELINE_AUDIT.md (how the universe is currently selected, every stage and value), DAILY_UNIVERSE_REFRESH_BLUEPRINT.md (the complete design of this feature, every decision and its reasoning), the captured logs proving the no-movement problem, and config.toml [universe].watch_list (the current 50). Read them first. If this prompt and those documents conflict, or if the code contradicts them, stop and escalate rather than guessing.

Do not review this prompt. Do not critique it. Do not rewrite it. Execute the phases.

**IMPORTANT — WORK ON THE MAIN BRANCH. DO NOT CREATE A NEW BRANCH. DO NOT CREATE A NEW DIRECTORY.** All work happens in the existing working tree at the project root. Investigation notes, if any, go in a single file, not a tree of files. All code changes are committed directly to main as atomic, clearly-labeled, individually-revertible commits with plain-language messages — one coherent change each. If you believe a branch is needed, stop and escalate instead of creating one.

**SHIP ONE PHASE AT A TIME.** This integrates a new control over what the whole system trades. Ship one phase, verify it, and revert anything that destabilises the system, neglects an open position, or degrades the live box. Do not batch.

**EFFICIENCY MANDATE.** Do not waste time. No elaborate dev-notes directory trees. No git command loops. No repeated identical shell commands. A prior session lost roughly three hours stuck in command loops; do not repeat that. Read each file thoroughly once, map it, and move on.

**ACCESSIBILITY.** The operator (Inshad) is a blind developer using a screen reader. All output, all reports, and all Telegram messages must use proper heading structure or clear prose. No emoji. No ASCII-art tables. No decorative separators. State things in sentences.

The operator must approve at the decision gate at the end of each phase before the next begins. Applying a phase before its gate is approved is a serious violation. Because this changes what the whole system trades, the bar at each gate is high.

---

## Part A — The Investigation Depth Mandate (The Most Important Rule — Read Twice)

This is a NEW feature integrated into a LIVE system. Do not rely on this prompt or the blueprint alone — the documents say what to build and why; the code is the truth of what is there to build it into. Before concluding anything and before touching any code, you must:

- Go through EACH AND EVERY file relevant to this feature, code by code, LINE BY LINE, file by file, until you fully understand what each does — not what you assume. At minimum: where the watch list is read and loaded and how the running system would react to its contents changing (src/strategies/scanner.py and config.toml [universe]); the market scanner and the bulk ticker call (market_service.get_all_linear_tickers) and EXACTLY which fields its response carries (price change, high, low, volume, turnover — confirm first-hand); how the analysis workers populate a coin's data and, CRITICALLY, whether they backfill a newly added coin's candle history immediately on add or start from zero (this single fact decides the warm-up length); how open positions are force-kept in the universe and how to GUARANTEE they stay fully managed across a universe swap; how the brain is paused and resumed (the Call-A path); how the scheduler or timed jobs run in this system; and how the Telegram control surface issues commands, confirmations, and status messages. Do not miss a single relevant file. If unsure whether a file is relevant, read it and decide; do not skip it on assumption.
- Map the COMPLETE dependency picture: every reader of the watch list, every consumer of the scanner output, every analysis worker that depends on universe membership, every place open-position coins are protected, the brain pause and resume points, the scheduler, and the Telegram command path. The full wiring, captured before any change.
- CONFIRM every assumption in the blueprint against the code, and where they disagree, SURFACE the disagreement at the gate rather than silently resolving it. In particular, confirm the bulk ticker call's available fields, the backfill-versus-cold-start fact for new coins, and the open-position force-keep mechanism — these three shape the whole design.
- For every file you will touch, understand WHY it is built the way it is and what depends on it, so the integration preserves existing behavior and breaks no consumer.
- Only after the complete, dependency-mapped, line-by-line understanding is established do you design each phase, and at implementation re-check every connected file so the integration is correct everywhere it reaches.

A change proposed without the complete file-by-file investigation behind it is rejected. The goal is a correctly-integrated feature that does not destabilise the live system — not a bolt-on that assumes how the system works.

---

## Part B — The Phases

### PHASE 0 — Investigate and prove the integration points (no code change)

Read the whole relevant stack line by line and produce one written report establishing, from the code: how the watch list is loaded and how the running system would react to it changing between refreshes (does it re-read on a schedule, on restart, or continuously — this determines how a refresh takes effect); the exact fields the bulk ticker call returns (so the selection factors can be computed from one call); whether new coins are backfilled on add or start cold (the warm-up-deciding fact); how open positions are force-kept and how to guarantee they stay managed across a swap; the brain pause and resume mechanism; how timed jobs run; and how the Telegram command and confirmation path works. State plainly where the blueprint's assumptions hold and where they do not. Report before Phase 1. This is the foundation; the design of every later phase depends on it.

### PHASE 1 — The selection engine (the movement score), built and proven offline first

The situation: the system has an opportunity scanner that already scores momentum, volatility, volume, and trend, but it scores only the 50-coin watch list and is not used to CONSTRUCT the universe. The need: a selection engine that scores ALL coins from one bulk call and ranks them by sustained multi-day activity. The aim: identify the coins genuinely in play. How it should work: from the single bulk ticker call, compute for every coin a movement score from recent realized volatility (the backbone, highest weight, the multi-day average range), a volume surge (recent versus baseline), open interest expansion (on the top survivors or omitted if too costly), all gated by a directionality filter (net move over total range, demoting choppy whipsaw coins), with a hard liquidity floor (volume and spread) removing untradeable coins BEFORE scoring. Critically, every factor is measured over a MULTI-DAY window, NOT the last-24-hour price change, because the latter catches exhausted pumps and the former catches coins still in play — this is the single most important property of the selection and must be honored. Why built offline first: before this engine is allowed to change the live universe, it must be PROVEN to select genuine movers. Build it as a pure, testable selection function, run it against recent market data, and show the operator the 50 it would select and their recent realized movement versus the current static 50's. Trial/gate: the engine selects coins with materially higher recent activity than the static list; the multi-day (not 24-hour) basis is confirmed; the whipsaw filter demotes choppy coins; the liquidity floor removes untradeable coins; and the selected-versus-current comparison is presented for the operator's approval before the engine is wired to anything live.

### PHASE 2 — The refresh orchestration (pause, refresh, warm-up, resume), with open positions always managed

The situation: a refresh must pause finding new trades, swap the universe, warm up the new coins, and resume — without ever neglecting an open position. The need and aim: a safe orchestration that does exactly this. How it should work: on a refresh, pause the brain's Call-A (find-new-trades) only; keep every open position fully managed by its exit engines the entire time and force-keep open-position coins in the universe regardless of selection; run the Phase 1 selection; write the new 50 (keeping the stable liquid core and the open-position coins); start the warm-up for the newly added coins; and resume the brain when the new coins' data passes the existing freshness gates. The warm-up is DATA-GATED: the brain resumes when the added coins' analysis actually passes the freshness gates (reusing the existing gates), not on a fixed clock, with a sensible maximum; if Phase 0 found new coins are backfilled on add, this resolves quickly (a short warm-up suffices); if they start cold, the warm-up must wait for real data and this is surfaced as a finding. Why this is the delicate phase: the non-negotiable property is that open positions are never abandoned, neglected, or force-closed by a refresh — they keep full exit management throughout. Trial/gate: a refresh pauses only new-trade-finding; every open position demonstrably keeps full exit management across the swap (verified, not assumed); the new universe takes effect as Phase 0 established it must; the warm-up resumes only on gate-passing data; and the catastrophic cap and stops still fire on open positions during the refresh.

### PHASE 3 — The scheduled refreshes at 23:00 and 11:00 UTC

The situation and aim: the orchestration must run automatically at the two chosen times. How it should work: a timed trigger fires the Phase 2 orchestration at 23:00 UTC (tuning the universe for the Asian session ahead) and at 11:00 UTC (tuning for the Europe and US session ahead, timed so the warm-up finishes before US prime time around 13:30 to 14:00 UTC, and so the pause lands in the late-European-morning lull rather than during the US open). Both times sit clear of the funding settlements at 00:00, 08:00, and 16:00 UTC so the activity data is not distorted. The build must make the divergence between the two refreshes' selections visible, so the operator can later judge whether two refreshes are needed or whether one suffices (if the multi-day-activity selections overlap heavily, the second pause adds little). Why these times, precisely: stated in the blueprint and to be honored — session tuning, the US-open cost avoided by 11:00 rather than 12:00, the dead-hour and funding-settlement distortions avoided. Trial/gate: the refreshes fire at the correct times; each produces a fresh, session-appropriate universe; the timing avoids the funding settlements; and the two selections' overlap is reported so the necessity of two can be judged.

### PHASE 4 — The manual Telegram refresh button

The situation and aim: the operator needs an on-demand refresh for the cases the schedule does not cover (a coin goes parabolic mid-session, a regime shift, a visibly stale universe), and a way to see and judge the selected coins. How it should work: a Telegram button that, on press, asks for confirmation (it pauses new-trade-finding for the warm-up duration, so it must not be triggered accidentally); on confirmation, runs the exact same Phase 2 orchestration as a scheduled refresh; keeps all open positions fully managed throughout; posts clear status updates (refresh started and trading paused, the new 50 coins selected, warm-up in progress with the resume time, trading resumed); and is guarded against overlapping with a scheduled or already-running refresh (if one is in progress or imminent, the press is ignored with a message or reuses the running one, never stacking two). Why a manual button and not an automatic rule: a manual refresh during active hours costs an hour of paused trading, which is acceptable only as a deliberate operator choice carrying judgment an automatic rule could not; and showing the selected 50 makes the button a way to validate the selection. Trial/gate: the button triggers the same orchestration; the confirmation and overlap guards work; the status updates including the selected 50 post correctly; and open positions stay managed throughout a manual refresh.

### PHASE 5 — The stable-core decision and final wiring

The situation: a decision is required on how much of the 50 refreshes each time. The aim and how it should work: present, at this gate, the choice between a full rebuild every refresh (freshest set, more churn, longer warm-up) and a stable liquid core plus rotating slots (less churn, shorter warm-up, the earlier inclination being roughly eight to ten liquid majors stable with the rest refreshed toward movers), with any open-position coin always kept regardless. Implement the chosen approach and complete the wiring so the whole system runs end to end: scheduled and manual refreshes, selection, warm-up, resume, open-position safety, and observability. Why gated here: the core size is a genuine operator choice affecting churn and warm-up, decided on the evidence of how the selections behave. Trial/gate: the chosen core approach is implemented; open positions are always kept; and the full system runs end to end with every safety and observability property in place.

---

## Part C — Hard Rules (Non-Negotiable)

### Rule 1 — The investigation depth mandate (Part A) governs every phase

Every relevant file, line by line, complete dependency map, every blueprint assumption confirmed against the code (especially the bulk-call fields, the backfill-versus-cold-start fact, and the open-position force-keep), all connected files re-checked at implementation. A change without this is rejected. Do not rely on the blueprint alone.

### Rule 2 — This is a woven-in addition and a calibration, NOT a new trading gate

It changes the contents of the universe on a schedule and on demand; it does not add a per-trade filter in the trading path and does not change the runtime architecture. The selection runs between trading, not inside it. If a new per-trade gate seems required, stop and escalate.

### Rule 3 — Open positions are NEVER neglected by a refresh

Across every refresh (scheduled or manual), every open position keeps full exit management running every second, is force-kept in the universe, and is never abandoned or force-closed by the refresh. The refresh pauses ONLY the finding of new trades. This is the single most important safety property and must be verified, not assumed.

### Rule 4 — The selection is on MULTI-DAY activity, never the last-24-hour move

Every selection factor is measured over a multi-day window as a measure of ongoing activity, not as the last day's price change, because the latter catches exhausted pumps and the former catches coins still in play. This property must be honored and verified.

### Rule 5 — The whipsaw filter and the liquidity floor are mandatory

The directionality filter demotes choppy coins that move by thrashing (which stop trades out twice), and the liquidity floor removes untradeable coins before scoring (a mover you cannot enter and exit is worthless). Both must be present and verified.

### Rule 6 — Prove the selection picks real movers before it touches the live universe

The selection engine is built and proven offline first (Phase 1): it must demonstrably select coins with materially higher recent movement than the static list before it is wired to change anything live. The comparison is presented for approval.

### Rule 7 — The warm-up is data-gated, never a blind clock

The brain resumes after a refresh only when the newly added coins' data passes the existing freshness gates, with a sensible maximum, so the brain never trades a blank-data coin. The true minimum warm-up depends on the backfill-versus-cold-start fact from Phase 0.

### Rule 8 — Add NO new API load to the trading path and do not degrade the live box

The selection uses ONE bulk call that the system already makes, plus light computation, run only at the refresh times — not a per-cycle scan and not a per-coin loop over hundreds of coins. The refresh must not contend with or slow the live trading and exit path.

### Rule 9 — The runtime architecture, the exit systems, and the brain are untouched

This feature changes only the contents of the 50. It does not touch the exit systems, the owner hierarchy, the stops, the catastrophic cap, the brain's decision logic, or the analysis workers' computations (it only triggers their warm-up on new coins). No change may regress the confirmed-working parts.

### Rule 10 — No assumptions, no guess-fixes

Every claim cites a code location, re-verified against current code. Probably and should-be are not a basis for action. The bulk-call fields, the backfill fact, and the open-position mechanism are confirmed first-hand, not assumed.

### Rule 11 — Parameters centralized and tuning-ready

Every configurable value (the two refresh times, the warm-up maximum, the factor weights, the volatility lookback, the liquidity floor, the whipsaw threshold, the stable-core size) is named, centralized configuration, read once at boot with a boot sentinel. No new scattered hardcoded values.

### Rule 12 — File edits via sed, with backups

No interactive editors. Timestamped in-place backups before editing any file, kept in the original directory.

### Rule 13 — Commit on main, atomic and labeled, one coherent change each

No new branch, no new directory. Atomic, individually-revertible commits with plain-language messages.

### Rule 14 — Observability for every change

Each refresh logs the bulk call, the scoring, the selected 50, the coins added and removed, the warm-up duration, and the resume. The manual button posts its status to Telegram including the selected 50. Boot sentinels confirm the refresh system and its schedule are loaded. The operator must be able to see what each refresh did and why.

### Rule 15 — Self-verification with concrete values

Each phase is verified against its trial before it is done, including the open-position-managed-across-a-swap test and the selected-versus-current-movement comparison. Verification scripts in the project root, clearly named, never deleting or rewriting data to pass.

### Rule 16 — Honest reporting and provisional verdicts

If a blueprint assumption is wrong, if new coins start cold (making the warm-up a bigger issue), if the two scheduled selections overlap so heavily that one refresh suffices, if the selection cannot be proven to pick real movers, or if anything cannot be done without touching the trading path — say so plainly at the gate. State plainly that this feature improves the universe but does not alone guarantee the 70 percent target. All verdicts are provisional until measured live.

### Rule 17 — Protected tables untouched

No retention or cleanup on tias_results, trade_log, trade_history, thesis_store, virtual_positions.

---

## Part D — Trial Behavior And Expected Values (How The System Checks Itself)

Phase 0: the integration report establishes, from the code, how the watch list takes effect when changed, the bulk-call fields, the backfill-versus-cold-start fact, the open-position force-keep, the brain pause and resume, the scheduler, and the Telegram path — with the blueprint's assumptions confirmed or corrected.

Phase 1: the selection engine selects coins with materially higher recent realized movement than the static 50; the multi-day (not 24-hour) basis, the whipsaw filter, and the liquidity floor are confirmed; the selected-versus-current comparison is presented and approved before the engine touches anything live.

Phase 2: a refresh pauses only new-trade-finding; every open position keeps full exit management across the swap (verified); the new universe takes effect as Phase 0 established; the warm-up resumes only on gate-passing data; the catastrophic cap and stops still fire on open positions during a refresh.

Phase 3: the refreshes fire at 23:00 and 11:00 UTC; each produces a fresh session-appropriate universe; the timing avoids the funding settlements; the two selections' overlap is reported so the need for two is judgeable.

Phase 4: the manual button triggers the same orchestration; the confirmation and overlap guards work; the status updates including the selected 50 post correctly; open positions stay managed throughout.

Phase 5: the chosen stable-core approach is implemented with open-position coins always kept; the full system runs end to end with every safety and observability property in place.

Cross-cutting: investigation-first throughout; no new per-trade gate; open positions never neglected; selection on multi-day activity with the whipsaw and liquidity filters; selection proven to pick real movers before going live; warm-up data-gated; one bulk call only, no new trading-path load; runtime, exit systems, and brain untouched; parameters centralized with boot sentinels; protected tables untouched; one phase, one commit, one verification at a time.

---

## Part E — Anti-Patterns To Avoid

Do not add a per-trade coin-selection gate in the trading path — this constructs the universe between trading, it does not filter trades at decision time. Do not ever let a refresh neglect, abandon, or force-close an open position — open positions keep full exit management throughout. Do not select on the last-24-hour price change — select on multi-day sustained activity, or you will pick exhausted pumps. Do not skip the whipsaw filter — a most-volatile selection without it picks coins that thrash and stop you out twice. Do not skip the liquidity floor — a mover you cannot trade is worthless. Do not wire the selection to the live universe before proving offline that it picks real movers. Do not resume the brain on a fixed clock that could brief it on blank-data coins — data-gate the warm-up. Do not scan hundreds of coins per cycle or loop per-coin over the market — one bulk call, twice a day. Do not touch the exit systems, the owner hierarchy, the cap, or the brain logic. Do not assume the bulk-call fields, the backfill fact, or the open-position mechanism — confirm them in the code. Do not introduce new hardcoded values. Do not create a branch or directory. Do not waste time in command loops. Do not promise the 70 percent target from this feature alone. Do not declare any phase done until its trial passes and the live behavior confirms it.

---

## Part F — What Success Looks Like

The system finally trades coins that move. Twice a day and on demand, the universe is rebuilt around coins in a genuine active phase — selected on multi-day volatility, rising volume, and expanding open interest, with choppy whipsaw coins demoted and untradeable coins removed — from a single cheap bulk call, while every open position keeps its full exit management and the newly added coins warm up until their data is real. The operator sees each selected 50 in Telegram and can refresh on demand. The coins the brain trades thereafter travel far enough that a win can be real net-of-fee profit instead of a rounding error, and the exit work already built finally has real moves to capture. The feature is woven into the live system without touching the runtime architecture, the exit systems, the brain, or the catastrophic cap; it changes only the contents of the 50, on a daily cadence, toward movers. Every part rests on a complete file-by-file investigation that confirmed the integration points against the real code, was proven offline to pick real movers before going live, and shipped one phase at a time, observable and revertible, on main — with open positions never neglected and no new per-trade gate.

---

## Part G — What Success Does NOT Mean

This feature removes the universe as the binding constraint on the system's profitability — the dominant root cause of the losses — but it does not by itself guarantee the 70 percent win-rate target, and that must not be claimed. It cannot pick the best 50 coins of the day, because that is knowable only in hindsight; it picks 50 coins with a much-better-than-random chance of moving, and false positives and missed movers are expected. The win rate and the net profit also depend on the entries and on the exit work; this feature ensures the coins move enough that those can succeed, but it does not make the entries pick the right direction or the exit capture the move. Success here is a universe of coins that actually move, proven to be materially more active than today's static list — not a profitable system on its own. The remaining distance to the target is the entries' and the exit's to close, and the movement data this feature produces will show how much that is, coin by coin. The feature is the necessary first fix because no downstream work can make money on coins that do not move; what it unlocks, the rest of the system must then deliver.

---

## Part H — End Of Prompt

Begin Phase 0.

Confirm the working tree is clean and on main, the active adapter, and the protected-table row counts. Read COIN_SELECTION_PIPELINE_AUDIT.md, DAILY_UNIVERSE_REFRESH_BLUEPRINT.md, the captured logs, and config.toml [universe].watch_list in full.

Then execute Phase 0: read the whole relevant stack line by line and establish, from the code, every integration point — how the watch list takes effect when changed, the bulk-call fields, whether new coins are backfilled on add or start cold, how open positions are force-kept and stay managed across a swap, the brain pause and resume, the scheduler, and the Telegram path — confirming or correcting the blueprint's assumptions. Report before designing.

Then execute the phases in order, one at a time, each with the full Part A investigation, the design, the plain-prose explanation, the decision gate, and after approval the implementation with all connected files re-checked and the trial run: the selection engine proven offline to pick real movers; the refresh orchestration with open positions always managed and the warm-up data-gated; the scheduled refreshes at 23:00 and 11:00 UTC; the manual Telegram button with its confirmation and overlap guards; and the stable-core decision and final wiring.

Remember throughout: the situation is that the system loses because the coins it trades do not move, and the cause is a static universe led by the calmest coins reviewed weekly; the aim is to rebuild the 50 around genuine movers, twice a day and on demand, so the coins move enough for wins to be real net-of-fee profit, targeting a far higher win rate. This is a WOVEN-IN ADDITION AND A CALIBRATION, NOT a new per-trade gate. Open positions are NEVER neglected by a refresh. Selection is on MULTI-DAY activity, with the whipsaw and liquidity filters, proven to pick real movers before going live. The warm-up is data-gated. One bulk call only, no new trading-path load. The runtime, the exit systems, the brain, and the cap are untouched. Understand before you touch — code by code, line by line, file by file — and do not rely on the blueprint alone. Root cause, not band-aid. Confirm from the code — no assumptions. One phase, one commit, one verification at a time. Work on main, no new branch, no new directory. Be honest that this unlocks profitability but does not alone guarantee the 70 percent target. If the code contradicts the documents, escalate. If something does not fit, document it and escalate.

Begin Phase 0.
