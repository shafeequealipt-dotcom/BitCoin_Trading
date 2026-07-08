# Issue A Phase 2 — Operator Report: Prompt Trimming Dropping URGENT Alerts

> Operator decision document. Plain prose, h1/h2/h3 structure. Phase 1 evidence in `issueA_phase1_synthesis.md`.

## Root cause

The CALL_A prompt builder appends the URGENT WATCHDOG ALERTS section using the header `## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED` (`src/core/urgent_queue.py:128`). The priority-aware trimmer is supposed to protect URGENT, but the marker it looks for is the substring `OVERRIDE — URGENT WATCHDOG ALERTS` (`src/brain/strategist.py:352`). Those two strings have no common substring, so the trimmer classifies the live URGENT block as OPTIONAL and drops it first whenever the prompt exceeds the 14 000-character cap.

The line-694 `OVERRIDE — URGENT WATCHDOG ALERTS:` text the marker was originally written for is appended to the **system prompt** (a separate string passed via `--system-prompt`), not to the user-prompt `sections` list. The marker therefore never matches any section. It is effectively a dead marker.

A separate, related bug: three single-line metadata sections — `Equity: $X`, `Available: $X`, `Maximum concurrent positions: N` — are appended at `strategist.py:2861/2862/2904` as their own list entries with no leading header, so they too get classified OPTIONAL and dropped first. Only the sibling line `Per-trade size limit:` was protected (its marker `Per-trade size limit` was added at line 375 in commit `b25148c0`); the same marker treatment was never extended to the other three sibling lines.

## Severity

In the 13:00–16:00 window on 2026-05-08:
- 21 priority-mode trim events.
- 14 events dropped URGENT WATCHDOG ALERTS.
- 17 events dropped at least one IMPORTANT-tagged section (cascading past OPTIONAL).
- All 21 events dropped Equity / Available / Maximum concurrent positions.
- Raw prompt sizes 16,910–19,919 chars vs the 14,000-char cap (3–6 k overshoot per cycle).

This is more severe than the audit reported (audit said "at least 2" URGENT-drop events; reality is 14).

## Why the current approach is wrong

The priority-aware trim makes two assumptions that are violated by the live code:

1. Every section worth keeping starts with a marker substring listed in `_TRIM_ESSENTIAL_MARKERS`. Bare-line metadata (Equity, Available, Max concurrent positions) and certain headers (the urgent_queue block) violate this.
2. The marker tuple stays in sync with every emit site. The urgent_queue refactor and the bare-line additions changed the emit sites without updating the marker tuple.

When marker-vs-header drift happens silently, the trim degrades. The trim emit `CLAUDE_PROMPT_TRIMMED` reports drop counts but does not detect or warn that an intended-essential section was misclassified. The bug is invisible from log review unless an operator actually reads the dropped_labels.

## Hard constraints (per the prompt)

- URGENT WATCHDOG ALERTS must never be dropped.
- Available capital, equity, and position counts must never be dropped.
- Brain decision quality must improve (more context delivered).
- No meaningful CALL_A latency or cost increase.
- No model change (still Claude CLI).

## Solution options

Each option was derived from the investigation, not pre-committed in the plan. Each is a real, implementable path. I have a recommendation, but the operator decides.

### Option 1 — Marker hardening (minimal-blast-radius fix)

Make the marker tuple match what the code actually emits, and explicitly tag the bare-line metadata as ESSENTIAL.

Changes:
- Replace the dead marker `"OVERRIDE — URGENT WATCHDOG ALERTS"` with `"## URGENT WATCHDOG ALERTS"` (substring of the actual urgent_queue header).
- Add markers for the three bare lines: `"Available:"`, `"Equity:"`, `"Maximum concurrent positions"`.
- Add a sanity check: at the end of the trim block, if any `_TRIM_ESSENTIAL_MARKERS` substring would have matched a section that was dropped, emit a loud `STRAT_TRIM_ESSENTIAL_DROPPED` warning. (Defends against future marker-vs-header drift.)
- Keep the 14,000-char cap as-is.

Effect:
- URGENT alerts protected.
- Equity / Available / Max-positions protected.
- Section growth still needs OPTIONAL/IMPORTANT to be trimmed — the cap pressure is not relieved, so X-RAY, Sentiment, lessons, and per-coin score-line clusters still drop most cycles.
- Latency / cost: unchanged.

Tradeoff: Surgical and low-risk, but does not address Root Cause #3 (cap is genuinely too tight). 17 IMPORTANT-drop events would still happen — and IMPORTANT category includes Direction Performance, Trading Mode, Strategy Hints, Setup, all of which the operator may want preserved.

### Option 2 — Marker hardening + raise the cap

Same code changes as Option 1, plus raise `_CHAR_CAP` from 14,000 to a value chosen to fit current prompts (recommended: 30,000 to leave headroom; the model's actual context window is ≈800k chars, so this is still ~4% of capacity).

Effect:
- All of Option 1 plus: typical prompts fit under the cap, no trim fires at steady state.
- Trim becomes a backstop for runaway prompts (e.g., 50+ open positions), not a per-cycle behaviour.
- Latency: ~+50–100 ms per call from larger prompt parsing on the model side; negligible at this scale.
- Cost: ~+$0.003 per CALL_A call (a 5k-char prompt vs 20k-char prompt at Sonnet rates is ≈ $0.005 vs $0.020). Across ~20 CALL_A per hour, ~$0.30/h additional. ~$7/day.

Tradeoff: Fixes both Root Causes #1/#2 (marker bug) and #3 (cap pressure). More content into Claude → better-informed decisions. Modest cost increase.

### Option 3 — Marker hardening + targeted compression

Same code changes as Option 1, plus enable the existing gated compression path (`enable_prompt_compression` toggle introduced by commit `481b91e` on 2026-05-07) to compress bloated sections (X-RAY narrative, lessons block, per-coin verbosity) without changing semantics.

Effect:
- Cap stays at 14,000; prompt fits via compression.
- Compression logic exists but has not been live-tested at scale on Bybit-demo.
- Risks: any compression bug could hide critical detail — and the compression path is itself part of the same `_build_trade_prompt` we are trying to make safer. Adding a second mode-switching code path adds maintenance surface.

Tradeoff: Most architecturally elegant if you trust the compression. Highest implementation risk because the compression code itself needs verification.

### Option 4 — Hybrid: marker hardening + cap raise + compression as latent backstop

Combine Options 1 and 2; leave Option 3 in place but disabled (turn the gate on later if observability shows future cap pressure).

Effect:
- Fixes the bug today.
- Buys ~3× headroom on the cap.
- Keeps compression as a future tool without depending on it.

Tradeoff: Slightly more code touched than Option 2 alone, but each piece is independently verifiable.

## Recommendation

**Option 2 (marker hardening + raise cap to 30,000).** Reasoning:

1. Root Cause #1 (URGENT marker mismatch) and Root Cause #2 (bare-line lack of marker) are both real bugs and both must be fixed. Marker hardening is the only path that fixes them at the root.
2. Root Cause #3 (cap is too tight) is also real — `dropped_important > 0` in 17/21 events shows the OPTIONAL→IMPORTANT cascade is firing routinely. Even after #1 and #2, IMPORTANT content (Strategy Hints, Direction Performance, Trading Mode, Setup) would continue to drop. Raising the cap is the simplest, lowest-risk way to relieve pressure.
3. Cost (~$7/day) and latency (≈100 ms) are negligible against the value of every CALL_A getting full context.
4. The operator's stated philosophy is aggressive opportunity exploitation. Decisions made on truncated context bias toward conservatism (Claude misses signals it can't see). Removing this bias supports the philosophy.
5. Option 3 (compression) could be added later if growth continues, but adding it now means depending on a code path that has never been live-tested at scale on Bybit-demo.

If the operator prefers minimal change, Option 1 alone fixes the URGENT-drop bug and is shippable in one commit. The cap pressure remains, and 17/21 events would still drop IMPORTANT content.

If the operator prefers maximum headroom, Option 4 ships the same code as Option 2 and enables compression later behind the existing toggle.

## Implementation plan (when approved)

For Option 2 (recommended):

1. **Phase 3a — Marker tuple correction.**
   - Edit `_TRIM_ESSENTIAL_MARKERS` at `strategist.py:343–391`:
     - Replace the dead marker `"OVERRIDE — URGENT WATCHDOG ALERTS"` with `"## URGENT WATCHDOG ALERTS"`.
     - Add `"Available:"`, `"Equity:"`, `"Maximum concurrent positions"` to the tuple.
   - Atomic commit: `fix(issueA/3a): correct ESSENTIAL marker tuple to match live emit sites`.
   - Tests added: priority-classifier returns ESSENTIAL for the four corrected/new markers given representative section text.

2. **Phase 3b — Cap raise.**
   - Edit `_CHAR_CAP` at `strategist.py:3018` from 14000 to 30000.
   - Update the comment at lines 3000–3010 to reflect the new value and the rationale.
   - Atomic commit: `fix(issueA/3b): raise prompt char cap to 30000 to reflect current section count`.
   - Tests added: prompt at 25k chars not trimmed; prompt at 31k chars trimmed; cap respected.

3. **Phase 3c — Drift-detection guardrail.**
   - In the priority-aware trim block (lines 3032–3082), after computing `_dropped_labels`, scan each dropped label against the FULL `_TRIM_ESSENTIAL_MARKERS` tuple. If any dropped label contains an essential marker substring (i.e., we dropped something that should have been protected), emit a `STRAT_TRIM_ESSENTIAL_DROPPED` warning naming the marker. This catches future drift loudly.
   - Atomic commit: `fix(issueA/3c): add drift-detection warning for misclassified essential drops`.
   - Tests added: synthetic prompt where an essential marker is misconfigured triggers the warning.

4. **Phase 3d — Trim observability enrichment.**
   - Enrich `CLAUDE_PROMPT_TRIMMED` with `protected_kept_count` and `protected_categories` fields so the operator can read each event and confirm the protections fired.
   - Atomic commit: `fix(issueA/3d): emit protected_kept_count and categories on CLAUDE_PROMPT_TRIMMED`.

Total: 4 atomic commits. Each independently reviewable, revertable, testable.

Tests:
- Unit: prompt below cap not trimmed.
- Unit: prompt above cap trimmed correctly with priority order.
- Unit: URGENT WATCHDOG ALERTS preserved through trim (using actual urgent_queue header text).
- Unit: Equity / Available / Maximum concurrent positions preserved through trim (separate single-line section inputs).
- Unit: classifier returns correct priority for each marker in the tuple given representative section text.
- Unit: drift-detection warning fires when an essential section is dropped.
- Integration: synthesised CALL_A cycle with URGENT alerts present asserts the urgent block appears in the submitted prompt and the trim emit shows it as protected.

## Verification (Phase 4)

- Run 4–6 hours post-deploy on the live Bybit-demo system.
- Compare against the 13:00–16:00 baseline:
  - `CLAUDE_PROMPT_TRIMMED` rate should drop substantially (steady-state trim near zero with 30k cap).
  - `URGENT WATCHDOG` should never appear in `dropped_labels`.
  - `Available:`, `Equity:`, `Maximum concurrent positions` should never appear in `dropped_labels`.
  - `STRAT_TRIM_ESSENTIAL_DROPPED` should never fire.
  - CALL_A latency: track p50 and p95; expect ≤ +100 ms.
  - Brain decision quality (subjective): more URGENT-aware closes should occur because Claude now sees the alerts.
- Edge cases: 30+ open positions; multiple URGENT alerts; regime change cycle.

## Discrepancies surfaced (added to `discrepancies.md`)

| # | Topic | Audit/memory | Reality |
|---|---|---|---|
| 1 | `_build_trade_prompt` line | 3073 | 2235 (line 3073 is the trim emit) |
| 2 | URGENT-drop count in 3h | "at least 2" | 14 |
| 3 | `dropped_important > 0` events | not flagged | 17 |
| 4 | Equity/Available "essential" | (Explore-agent claim during planning) | NOT in marker tuple |
| 5 | Marker `OVERRIDE — URGENT WATCHDOG ALERTS` | implied to match live URGENT | matches no section; only system-prompt text at strategist.py:694 |

## Operator's decision needed

Choose:
- Option 1 — marker hardening only (minimal change, leaves cap pressure).
- Option 2 — marker hardening + cap raise to 30,000 (recommended).
- Option 3 — marker hardening + enable compression (highest implementation risk).
- Option 4 — Option 2 today, compression as latent backstop.
- A modified version of any of the above.
