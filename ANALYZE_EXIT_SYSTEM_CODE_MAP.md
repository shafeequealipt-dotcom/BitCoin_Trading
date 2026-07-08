# ANALYZE AND DOCUMENT — A Complete Code-By-Code, File-By-File Map Of The Entire Exit System, Produced As One Comprehensive Reference Document (Analysis Only — No Code Changes)

## STOP. READ THIS ENTIRE DOCUMENT BEFORE DOING ANYTHING.

### This is an ANALYSIS AND DOCUMENTATION task — NOT a fix task

This task changes NO code. It writes no fix, flips no flag, edits no config. Its single deliverable is one comprehensive markdown reference document that maps the complete exit system — every file, every mechanism, every value, every formula, and exactly how each piece works and connects — read directly from the actual code, line by line. The purpose is to have a precise, code-grounded map of the exit system in one readable document, so the next step (designing a fix for the profit give-back) can be specified against the real code rather than reasoned from logs. If at any point the temptation arises to change or fix something, do not — note it in the document and move on.

### Why this document is needed

The exit system has been investigated through logs and live forensics, and a specific problem is now proven: trades that go green and stay green still give back most of their profit before closing. The clearest example from the captured data is a trade (HOMEUSDT) that was in profit for its entire life, peaked at plus 0.70 percent, and closed at only plus 0.15 percent — keeping just over a fifth of a winning trade that never once went red. Other trades show the same give-back (BELUSDT green most of its life, peaked plus 0.52 percent, closed minus 0.39 percent; SPCXUSDT green most of its life, peaked plus 0.28 percent, closed minus 0.08 percent). The give-back is real and concentrated in the trades that genuinely go green.

To fix this precisely, the actual code of the exit geometry must be mapped: exactly how the trail distance, the staged locks, the arm, the rungs, and the stops are computed, what every current value is, how the movement unit R flows through the calculation, how the gateway places or clamps the resulting stop, and where in this machinery a green trade's profit is given back. This document produces that map. Without it, a fix can only be described by intent; with it, a fix can be specified against the exact function and the exact formula.

Do not review this prompt. Do not critique it. Do not rewrite it. Produce the document.

**IMPORTANT — NO CODE CHANGES. WORK ON THE MAIN BRANCH, TOUCH NOTHING.** This is read-only analysis. Do not edit code, config, or flags. Do not create a new branch or directory. The only file written is the reference document, placed at the repository root as a single markdown file (not a tree of files). If you believe a fix is urgent, note it in the document and surface it — do not apply it.

**EFFICIENCY MANDATE.** Do not waste time. No command loops. No repeated identical shell commands. A prior session lost roughly three hours stuck in command loops; do not repeat that. Read each file thoroughly once, map it, and write. Do not re-scan files repeatedly.

**ACCESSIBILITY.** The operator (Inshad) is a blind developer using a screen reader. The document and all output must use proper heading structure (h1/h2/h3). No emoji. Clear prose. No ASCII-art tables. No decorative separators. State everything in sentences. Where a value or formula matters, state it in a sentence with its exact location (file and line). Do not paste large raw code blocks; instead, explain in prose what each piece of code does, citing its location, so the document is readable by a screen reader rather than a wall of code.

### What we will do

Read the complete exit system code by code, file by file, line by line, and produce one comprehensive markdown reference document that fully maps it — every mechanism, every value, every formula, every connection, and specifically how a green trade's profit is given back — so the fix can be designed against the real code.

---

## Part A — The Analysis Depth Mandate (Read Twice)

This analysis must be exhaustive and code-grounded. Specifically:

- Read EVERY file in the exit system, code by code, LINE BY LINE, until you fully understand what each does — not what you assume. Do not skip a file. If unsure whether a file is relevant to the exit, read it and decide.
- For every mechanism, state exactly what it computes, how, with what current values, and where it lives (file and line). Every threshold, every multiple, every coefficient, every formula — named, with its current value and location.
- Map the COMPLETE flow: how the movement unit R is measured, cached, smoothed, and passed; how the geometry (arm, rungs, locks, trail, take-profit, stops) is computed from R and fees; how the resulting stop reaches the gateway; how the gateway accepts, clamps, degrades, or rejects it; and how the owner hierarchy decides who writes the stop. The full path from a price tick to a placed stop, captured precisely.
- Trace, in the document, EXACTLY how a green trade gives back its profit — using the real formulas. Take the proven example (a trade peaking at plus 0.70 percent and closing at plus 0.15 percent): show, from the actual code, where the trail or lock was placed relative to the peak, why the pullback closed it where it did, and which value or formula produced the give-back. This is the central question the document must answer from the code.
- State every finding as code-grounded fact with its location. Where something is unclear or could not be determined from the code, say so plainly rather than guessing.

This depth is mandatory. A document that summarizes the exit system without the exact mechanisms, values, formulas, and locations is insufficient. The goal is a map precise enough to specify a fix against — the exact function, the exact line, the exact value to change.

---

## Part B — What The Document Must Cover

The document must map the complete exit system. At minimum, it must cover the following, each read from the actual code with exact locations and current values.

### The movement unit R — how it is measured and flows

How R (the per-coin volatility unit) is computed: the source file and function, the underlying measure (the ATR or volatility calculation), the window and candle period, the caching (the time-to-live and any jitter), and any smoothing applied before it reaches the geometry. State R's typical and observed range. State exactly where R enters the geometry calculation.

### The R geometry — every formula and value

The pure geometry functions that turn R and the fee into the exit thresholds: the arm, the ladder rungs, the staged locks, the trail distance, the take-profit, and the hard stop. For each: the exact formula (in prose, citing the code location), every coefficient and its current value, and every floor and ceiling. Critically, the trail distance formula — how far behind the running peak the trail sits, and how that distance relates to R — must be stated exactly, because it is the prime suspect for the give-back. State whether any threshold tightens or changes as a trade's profit grows, or whether all are fixed for the trade's life.

### The ladder and trail in the sniper — how the geometry is used per tick

How the ladder and the trail consume the geometry on each tick: the functions that compute the ladder floor and the trail stop, how R is fetched and smoothed there, how the staged locks progress as the trade clears rungs, how the highest-stop-wins selection picks the active stop, and how the graduation latch hands a trade to profit ownership. State exactly what happens to the stop as a green trade climbs to a peak and then pulls back.

### The gateway — how the computed stop is placed, clamped, or rejected

The gateway's rules: the tighten-only rule, the minimum-distance clamp, the maximum-step rule, the rate limit, and the profit-lock and breakeven exemptions. How each rule can modify or reject a computed stop. Specifically, the minimum-distance clamp and the fresh-mark-degrade mechanism — how a computed lock can fail to be placed on a fast move — and the profit-lock floor exemption that holds an armed lock through the clamp. State where the catastrophic cap sits and how it is always admitted and only tightens.

### The owner hierarchy — who writes the stop when

The four buckets (the Head, the green owner, the red owner, the advisory systems) and the owner switch: how trade state (green or red) decides which engine writes the stop, where the hand-off at breakeven occurs, and how this interacts with the geometry. State that the hierarchy decides who writes the stop while the geometry decides what value.

### The give-back mechanism — the central question, answered from the code

Using the real formulas mapped above, explain exactly how a green trade gives back its profit. For the proven example (peaked plus 0.70 percent, closed plus 0.15 percent): show, from the code, where the trail or lock sat relative to the peak at that peak, why the pullback triggered the close where it did, and which specific value or formula (the trail distance, the lock level, the gateway clamp, or the staged-rung spacing) is responsible for the give-back, and how much each contributes. This is the finding the fix will be built on.

### The configuration — every exit value and where it lives

The configuration section holding the exit values, the dataclass, the builder, and the validator. Every current value (the R-multiples, the trail coefficient, the rung spacing, the secure levels, the fee, the floors and ceilings, the gateway distances), stated with its location, so the fix knows exactly what is already tunable and what would need a new key.

### The verification harnesses that exist

The existing replay and verification scripts relevant to the exit (the adaptive-exit replay, the gateway tests, the price-path tools), what each does, and which would be reused to prove a fix on real trades. This tells the fix what proving tools already exist.

---

## Part C — Hard Rules (Non-Negotiable)

### Rule 1 — Analysis and documentation ONLY; no code changes

No edits to code, config, or flags. No branch, no directory. The only output is the document. A change applied during this task is a serious violation.

### Rule 2 — Every finding is code-grounded with its exact location

Every mechanism, value, and formula is stated from the actual code, with its file and line. No assumption stated as fact. Where the code is unclear, say so.

### Rule 3 — The give-back mechanism must be answered from the code

The document must explain, from the real formulas, exactly how a green trade gives back its profit, using the proven example, identifying the responsible value or formula and quantifying each contribution. This is the central deliverable, not an afterthought.

### Rule 4 — Exact values, not approximations

Every current value (the trail coefficient, the rung multiples, the gateway distances, the fee, the floors and ceilings) stated exactly with its location, so the fix can name the precise value to change.

### Rule 5 — Readable for a screen reader

Heading structure, prose, no emoji, no tables, no decorative separators. Explain code in prose with citations rather than pasting large raw code blocks. The operator reads this with a screen reader; a wall of code is not usable.

### Rule 6 — Complete coverage of the exit path

The full path from a price tick to a placed stop: R measurement, the geometry, the sniper's use of it, the gateway's placement, and the owner hierarchy. Every relevant file read line by line.

### Rule 7 — Efficiency

No command loops, no repeated scans. Read each file once, map it, write. Do not waste time.

### Rule 8 — Honest about gaps

If a mechanism cannot be fully determined from the code, if a value is computed dynamically in a way that is hard to pin down, or if something is surprising, say so plainly rather than forcing an explanation.

### Rule 9 — Protected tables untouched

This is read-only anyway, but for completeness: no retention or cleanup on tias_results, trade_log, trade_history, thesis_store, virtual_positions.

---

## Part D — The Document's Required Structure

The single markdown document, at the repository root, must contain in this order:

A short plain-language summary: what the exit system is, the path from a price tick to a placed stop, and the one-paragraph answer to how a green trade gives back its profit.

The movement unit R: how it is measured, cached, smoothed, and flows, with exact locations and observed range.

The R geometry: every formula and value (arm, rungs, locks, trail, take-profit, hard stop), with exact locations, current values, floors, and ceilings — with the trail distance formula stated precisely.

The ladder and trail in the sniper: how the geometry is used per tick, how staged locks progress, how the active stop is selected, with exact locations.

The gateway: every rule and exemption, how a stop is placed, clamped, degraded, or rejected, with exact locations.

The owner hierarchy: the four buckets and the owner switch, who writes the stop when, with exact locations.

The give-back mechanism: the central finding — from the real formulas, exactly how a green trade gives back its profit, using the proven example, with the responsible value or formula identified and each contribution quantified.

The configuration: every exit value, its current setting, and its exact location.

The verification harnesses: the existing replay and test scripts relevant to the exit and what each does.

An honest gaps note: anything that could not be fully determined from the code, and anything surprising.

---

## Part E — What Success Looks Like

The operator and the next step have a complete, precise, code-grounded map of the exit system in one readable document: every mechanism, every value, every formula, and every connection, read from the actual code with exact locations; the full path from a price tick to a placed stop; and, centrally, a clear explanation from the real formulas of exactly how a green trade gives back its profit — with the responsible value or formula identified and quantified. With this document, the fix for the give-back can be specified against the exact function and the exact value, rather than described by intent. Because it changed no code, it carries zero risk while producing total clarity about how the exit actually works.

---

## Part F — End Of Task

Begin the analysis.

Confirm the working tree is on main and that this is a read-only task — nothing will be changed.

Then read the complete exit system code by code, file by file, line by line: the R source, the R geometry, the ladder and trail in the sniper, the gateway and its rules and exemptions, the owner hierarchy, and the configuration. Map every mechanism, every value, every formula, and every connection, with exact locations.

Then write the one comprehensive markdown reference document at the repository root, following the structure in Part D: the summary, R, the geometry, the sniper's use of it, the gateway, the owner hierarchy, the give-back mechanism answered from the code, the configuration, the verification harnesses, and the gaps note.

Remember throughout: this is ANALYSIS AND DOCUMENTATION ONLY — change nothing, fix nothing, flip nothing. Every finding is code-grounded with its exact location; every value stated exactly. The central deliverable is the answer, from the real formulas, to how a green trade gives back its profit — with the responsible value identified and quantified. Read each file once, work deliberately, write the document. The document must be readable by a screen reader: prose with citations, not walls of code. Do not change a single line of production code.

Begin.
