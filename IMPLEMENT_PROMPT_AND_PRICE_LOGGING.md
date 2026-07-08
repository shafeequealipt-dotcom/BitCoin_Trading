# BUILD — Two Observability Logging Systems (Complete Call-A And Call-B Prompt-And-Response Capture, And Per-Second Open-Trade Price Logging), Both Fire-And-Forget, Both Rotated, Neither Touching The Trade Path, With Exhaustive File-By-File Investigation (Main Branch, No New Branch)

## STOP. READ THIS ENTIRE DOCUMENT BEFORE WRITING ANY CODE.

### Why these two systems exist

The system is mid-way through fixing its exit (the exit-authority consolidation is in flight), and the next step after that is calibrating the profit engine's arm, lock, and trail values. Both of those depend on EVIDENCE the system does not currently capture cleanly:

First, repeatedly this month the analysis of trading windows was crippled because the full Call-A prompt the brain actually saw was not captured for that window — the per-coin candidate data had to be reconstructed from scattered log fragments. The brain's decisions can only be audited against the exact prompt it received. There is no reliable, complete, per-call capture of the assembled prompt and the brain's response for both Call-A (find-new-trades) and Call-B (manage-positions).

Second, the exit calibration that is coming needs the COMPLETE price path of each trade — where the price went, second by second, from entry to close — so that new exit settings can be replayed against real trades to prove where they would have exited before they are enabled live. Today the price is only sampled at the cycle cadence in scattered log lines; there is no clean, dedicated, per-second record of each open trade's price.

So this builds two observability systems: one that captures every Call-A and Call-B prompt and response in full, and one that logs every open trade's price every second to a dedicated file. Both are OBSERVABILITY ONLY — they record what the system already does; they change no trading behavior, add no API calls, and never block or slow the trade path.

### The single most important principle

A system built to OBSERVE the live trading box must never DEGRADE the live trading box. This box is CPU- and IO-constrained and has been observed to strain during brain spawns. Therefore both loggers must: add ZERO new exchange API calls (they record data the system already fetches), never block or slow the trading or exit path (every write is fire-and-forget — if it fails, the trade cycle proceeds anyway), and never contend with the trading database (their writes go to dedicated files, not the trading tables). Any design that polls the exchange, writes on the hot path synchronously, or risks filling the disk is wrong and must be rejected.

**THESE ARE OBSERVABILITY ADDITIONS — NOT TRADING FEATURES, NOT GATES.** Neither system makes, blocks, modifies, or influences a single trading decision. They are passive recorders. If at any point a logger would need to alter, delay, or gate a trade to do its job, that is wrong — stop and escalate. Nothing here touches the decision path, the exit path's logic, the stop-loss, or any trading rule.

Do not review this prompt. Do not critique it. Do not rewrite it. Execute the phases.

**IMPORTANT — WORK ON THE MAIN BRANCH. DO NOT CREATE A NEW BRANCH. DO NOT CREATE A NEW DIRECTORY.** All work happens in the existing working tree at the project root. Investigation notes, if any, go in a single file, not a tree of files. All code changes are committed directly to main as atomic, clearly-labeled, individually-revertible commits with plain-language messages — one per system so each is independently revertible. If you believe a branch is needed, stop and escalate instead of creating one.

**SHIP ONE SYSTEM AT A TIME.** Build and verify the prompt logger fully, then the price logger. Do not batch.

**EFFICIENCY MANDATE.** Do not waste time. No elaborate dev-notes directory trees. No git command loops. No repeated identical shell commands. A prior session lost roughly three hours stuck in command loops; do not repeat that. Read each file thoroughly once, map it, and move on.

**ACCESSIBILITY.** The operator (Inshad) is a blind developer using a screen reader. All output and all reports must use proper heading structure (h1/h2/h3). No emoji. Clear prose. No ASCII-art tables. No decorative separators. State findings in sentences.

The operator must approve at the decision gate for each system before that system is applied. Applying a change before its gate is approved is a serious violation.

---

## Part A — The Investigation Depth Mandate (Read Twice)

For each system, before writing any code, you must:

- Find EVERY relevant file in the chain. For the prompt logger: where Call-A and Call-B prompts are assembled, where the API call is made, and where the response is received and parsed. For the price logger: where the exit/monitoring loop reads each open position's current price every tick. Read these files LINE BY LINE until you understand exactly where the prompt/response and the price already exist in memory, so the logger can TAP them rather than recompute or refetch.
- Map the COMPLETE dependency picture: every place a Call-A or Call-B prompt is built and sent, every place the price is read per tick, every existing logging mechanism (so the new logger is consistent with how the project already logs and rotates), and every consumer that must not be disturbed.
- CONFIRM the data already exists at the tap point before designing. For the price logger specifically: FIRST verify whether the existing per-tick logs already contain per-second price for open trades (the exit forensics traced trades second-by-second from existing logs — the data may already be present). If it already exists at sufficient resolution, surface that at the gate — the right build may be a PARSER over existing logs, not a new logger. Do not build a new subsystem the data does not require.
- Only after the complete understanding is established do you design the tap, and at implementation re-check every call site so the capture is complete (every Call-A AND every Call-B; every open trade's every tick) and correct.

A logger proposed without knowing exactly where the data already lives is rejected. The goal is a complete, faithful, zero-impact recorder — not an approximate one and not a redundant one.

---

## Part B — The Two Systems

### SYSTEM 1 — Complete Call-A and Call-B prompt-and-response capture

The current situation. The brain is called twice on its cycle: Call-A (find new trades — the candidate blocks, market context, the instruction) and Call-B (manage open positions — the position data, PnL, thesis, lessons). The full assembled prompt the brain actually receives, and the full response it returns, are not captured completely and reliably per call. When a window needs auditing, the exact prompt is often unavailable and must be reconstructed.

What we need and the aim. A dedicated logger that captures, for EVERY Call-A and EVERY Call-B: the complete assembled prompt text exactly as sent, the complete response exactly as received (the full JSON — picks, sizes, leverage, reasoning, or the manage-position decisions), the call type (A or B), the call id, and the timestamp. Written to a dedicated, rotated log so any window can later be audited against the exact prompt the brain saw.

How it should work after building. At the point where each call's prompt is assembled and sent, and where its response returns, the logger writes one complete record (prompt + response + metadata) to its own rotating log file. It taps the prompt and response objects that ALREADY EXIST in memory at that point — it does not rebuild the prompt or re-call anything. The write is fire-and-forget: if it fails, the trading cycle proceeds unaffected. It covers both call types — neither Call-A nor Call-B may be missed.

Why it is not working today. There is no dedicated complete-capture logger at the call boundary; existing logging records fragments, not the whole prompt-and-response. The investigation must find the exact assemble-and-send point and the response-receive point for BOTH calls, confirm the full prompt and response objects are in hand there, and add the fire-and-forget complete write. Trial: after building, every Call-A and every Call-B in a live session produces one complete record with the full prompt, the full response, the type, the id, and the timestamp; a reconstructed coin's data from the new log matches what the brain was shown; no call type is missing; and a forced logging failure does not disturb the trading cycle.

### SYSTEM 2 — Per-second open-trade price logging

The current situation. The exit engines read each open position's current price on every tick to evaluate stops, but there is no dedicated per-second record of each trade's price path from entry to close. The upcoming exit calibration needs that full path to replay new arm/lock/trail settings against real trades.

What we need and the aim. A dedicated logger that records, for every open trade, its price approximately every second from entry to close — each point carrying the timestamp, the symbol, the trade id, the price, and the unrealized PnL percent — written to its own rotating log file, so each trade's complete price path can be read back for the exit calibration.

How it should work after building. The logger TAPS the price the exit/monitoring loop ALREADY reads each tick for each open position — it does NOT open its own polling loop and makes NO new exchange API calls (this is the absolute rule: zero added API load). It records at most one point per second per open trade (the resolution centralized in config; deduplicate within the second so a faster tick does not bloat the file). To avoid per-second disk contention with the trading database, the preferred design accumulates each trade's path in memory and flushes to the dedicated file in batches (with a periodic safety flush so a crash does not lose a long trade's path, and a final flush at close); writing append-only to a dedicated file outside the trading DB is acceptable if it is fire-and-forget and rotated. The write never blocks the exit tick: if it fails, the tick proceeds and the stop logic is untouched.

Why it is not working today. The price is read but not recorded to a dedicated per-second path store. The investigation must FIRST confirm whether the existing tick logs already provide this (if so, a parser may suffice — surface at the gate), and if a logger is needed, find the exact per-tick price-read point for open positions and add the fire-and-forget per-second capture there. Trial: after building (or after confirming the parser suffices), every open trade has a complete per-second price path from entry to close readable from the dedicated file (or the parser output); the resolution is about one point per second; NO new API calls are added (verified); the exit tick is never blocked (a forced logging failure leaves stop evaluation untouched); and the file rotates so it cannot fill the disk.

---

## Part C — Hard Rules (Non-Negotiable)

### Rule 1 — The investigation depth mandate (Part A) governs both systems

Every relevant file, line by line, the exact tap points found, the data confirmed already present at the tap, the existing-logs-already-suffice check done for the price path. A logger proposed without this is rejected.

### Rule 2 — Observability only, never the trade path

Neither system makes, blocks, modifies, delays, or influences any trading decision, the exit logic, the stop-loss, or any rule. They are passive recorders. If a logger would need to touch the trade path to work, stop and escalate.

### Rule 3 — Zero new API calls

Both systems record data the system ALREADY fetches or holds in memory. Neither opens a polling loop or makes a new exchange call. The price logger taps the existing per-tick read; the prompt logger taps the existing prompt and response objects. Verify zero added API load.

### Rule 4 — Fire-and-forget, never blocking

Every write is fire-and-forget: if logging fails or stalls, the trading and exit cycle proceeds unaffected. The loggers sit beside the paths they observe, never in them. A forced logging failure must leave trading and stop evaluation completely undisturbed — verify this explicitly.

### Rule 5 — Rotation from day one (disk safety)

Both logs grow fast (full prompts are large; per-second prices accumulate). Each writes to its own dedicated, rotated store (by size or by day) with old files auto-pruned, so neither can fill the disk and take the system down. The box's disk is finite; unrotated logs are a time-bomb and are not acceptable.

### Rule 6 — Dedicated stores, never the protected or trading tables

Both loggers write to their own dedicated files, separate from the trading database. They never write to, and the rotation/retention never touches, the protected tables (tias_results, trade_log, trade_history, thesis_store, virtual_positions) or any trading table. The retention logic is written carefully — a prior cleanup bug deleted learning data once; that must not recur.

### Rule 7 — Complete capture, nothing missed

The prompt logger captures EVERY Call-A AND EVERY Call-B (neither type missed). The price logger captures EVERY open trade's path from entry to close (no trade missed, no gap). Verify completeness on a live session.

### Rule 8 — No assumptions; confirm the data is at the tap

Every tap point is confirmed to already hold the full prompt/response or the current price before the logger is added. For the price path, the existing-logs-already-suffice check is done first so a redundant subsystem is not built. Probably and should-be are not a basis for action.

### Rule 9 — Parameters centralized

The price-path resolution (about one per second), the flush cadence, the rotation size/age, and the log file locations are named, centralized configuration, with boot sentinels confirming the loggers are active.

### Rule 10 — File edits via sed, with backups

No interactive editors. Timestamped in-place backups before editing any file, kept in the original directory.

### Rule 11 — Commit on main, atomic and labeled, one per system

No new branch, no new directory. One atomic, individually-revertible commit per system, plain-language messages.

### Rule 12 — Self-verification with concrete values

Each system verified against its trial in Part B before it is done: completeness (every call / every trade), zero added API calls, fire-and-forget non-blocking (forced-failure test), and rotation working. Verification scripts in the project root, clearly named, never deleting or rewriting data to pass.

### Rule 13 — Honest reporting

If the price-path data already exists in the logs (parser suffices), if a tap point does not hold the complete object expected, or if anything cannot be done without touching the trade path — say so plainly at the gate rather than proceeding.

---

## Part D — Trial Behavior And Expected Values (How The System Checks Itself)

System 1 (prompt capture): in a live session, every Call-A and every Call-B produces one complete record — full prompt as sent, full response as received, call type, id, timestamp — in the dedicated rotated log; a coin's data read back from the log matches what the brain saw; no call type missing; a forced logging failure leaves the trading cycle undisturbed; the file rotates.

System 2 (price path): every open trade has a complete per-second price path from entry to close (from the dedicated file, or from the parser if existing logs sufficed); resolution about one point per second; zero new API calls verified; a forced logging failure leaves the exit tick and stop evaluation undisturbed; the file rotates so it cannot fill the disk.

Cross-cutting: both systems observability-only (no trading behavior changed); both fire-and-forget; both on dedicated rotated stores away from the trading and protected tables; both shipped one at a time, observable, independently revertible; parameters centralized with boot sentinels.

---

## Part E — Anti-Patterns To Avoid

Do not open a new polling loop or make any new exchange API call — tap the existing reads. Do not write synchronously on the hot path — fire-and-forget, and prefer memory-buffer-plus-batch-flush for the price path. Do not write to the trading database or the protected tables — dedicated files only. Do not skip rotation — an unrotated log fills the disk and downs the system. Do not miss a call type — both Call-A and Call-B. Do not miss a trade — every open trade's full path. Do not build the price logger before checking whether existing logs already provide the data. Do not let any logger touch, delay, or gate a trading decision. Do not assume the tap holds the full object — confirm it. Do not create a branch or directory. Do not waste time in command loops. Do not declare either system done until its trial passes, including the forced-failure non-blocking test and the rotation check.

---

## Part F — What Success Looks Like

The system can finally be audited against exactly what it saw and did. Every Call-A and every Call-B is captured in full — the complete prompt the brain received and the complete response it gave — in a dedicated rotated log, so any trading window can be analyzed against the brain's true input without reconstruction. And every open trade's price is recorded second by second from entry to close, in a dedicated rotated file, so the coming exit calibration can replay new arm, lock, and trail settings against the real price paths and prove where each trade would have exited before anything is enabled live. Both systems record only what the system already fetches and holds — zero new API calls, zero blocking of the trade or exit path, zero contention with the trading database, and rotation that keeps the disk safe. They are passive instruments: they change no decision, gate nothing, and sit entirely beside the trading path. Every tap rests on a complete file-by-file investigation that confirmed the data already lived there, shipped one system at a time, observable and independently revertible, on main.

---

## Part G — What Success Does NOT Mean

These two systems make the trading auditable and the exit calibration possible — but they do not themselves improve a single trade. They are instruments, not fixes. Capturing the prompts does not make the prompts better; logging the price paths does not make the exits better. Their value is entirely downstream: the prompt capture makes every future analysis complete instead of reconstructed, and the price-path capture is the prerequisite the exit calibration needs to be done on evidence rather than guesswork. Success here means the instruments exist and are faithful and zero-impact — not that the system trades better yet. The trading improvements come from the exit-authority consolidation already in flight and the profit-engine calibration that follows, both of which these instruments will make measurable.

---

## Part H — End Of Prompt

Begin System 1.

Confirm the working tree is clean and on main. Then investigate the Call-A and Call-B assemble-send and response-receive points, confirm the full prompt and response objects are in hand there, and design the fire-and-forget complete-capture logger to its own rotated file — covering both call types. Present the design at the gate. After approval, implement, then verify: every call captured in full, a forced failure non-blocking, rotation working.

Then build System 2. FIRST check whether the existing tick logs already provide per-second open-trade price (if so, a parser suffices — surface it at the gate). If a logger is needed, find the exact per-tick price-read point for open positions and add the fire-and-forget per-second capture to its own rotated file, tapping the price already read — zero new API calls, memory-buffered and batch-flushed, never blocking the exit tick. Present at the gate. After approval, implement, then verify: every open trade's full per-second path captured, zero added API calls, a forced failure non-blocking, rotation working.

Remember throughout: these are OBSERVABILITY INSTRUMENTS, not trading features and not gates — they record what the system already does and change nothing. Zero new API calls — tap existing reads. Fire-and-forget — never block the trade or exit path. Dedicated rotated stores — never the trading or protected tables, never fill the disk. Complete capture — every Call-A and Call-B, every open trade. Check whether the price data already exists before building. Understand before you touch. No assumptions. One system, one commit, one verification at a time. Work on main, no new branch, no new directory. If something cannot be done without touching the trade path, stop and escalate.

Begin System 1.
