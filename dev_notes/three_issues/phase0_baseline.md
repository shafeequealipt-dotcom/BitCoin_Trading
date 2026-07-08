# Phase 0 — Pre-Flight Verification + Baseline Metrics

Window: 2026-05-08 13:00:00 → 16:00:59 UTC
Source log: `data/logs/combined_2026-05-08_13-00_to_16-00.log` (17.9 MiB, all sources concatenated)
Date of analysis: 2026-05-08

## Verification

- Branch: `feature/bybit-demo-adapter`
- Working tree: clean of source-code changes; runtime files modified (`data/layer_state.json`, `data/logs/layer1c_full.jsonl`) and 100+ untracked `data/stage2_dumps/*` (acceptable per operator).
- Most recent commit: `9ac9b54 docs(bybit_demo_logging/pipeline): comprehensive end-to-end pipeline verification`.
- Stage2 dumps in window 13:00–16:59 on 2026-05-08: 56 dump files (each contains full CALL_A or CALL_B prompt + response JSON).

## Baseline — Issue A (Prompt Trimming)

| Metric | Value |
|---|---|
| Total `CLAUDE_PROMPT_TRIMMED` events | 24 |
| Priority-mode trims (`site=size mode=priority`) | 21 |
| Event-buffer trims (separate, smaller cap) | 3 |
| Trim events with `dropped_important > 0` | 17 |
| Trim events with `URGENT WATCHDOG` in `dropped_labels` | 14 |
| CALL_A starts in window | 22 |

**Per-event observations from the 5-event sample:**
- Original prompt size: 16,910 / 17,736 / 18,456 / 18,716 / 19,919 chars (cap = 14,000).
- `dropped_optional` per event: 25 / 30 / 36 / 32 / 35.
- `dropped_important` per event: 0 / 0 / 0 / 0 / 2.
- Recurring dropped labels: `Maximum concurrent positions: 10`, `Available: $X`, `Equity: $X`, per-coin score lines, and at events 4 and 5: `## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED`, `## WATCHDOG EVENTS (since last review)`.

**Key Phase-0 finding (root-cause candidate):** The marker tuple at `strategist.py:343–391` includes the substring `OVERRIDE — URGENT WATCHDOG ALERTS`, but actual prompt sections begin with `## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED`. The substring `OVERRIDE — URGENT` appears 0 times in the entire 17.9 MiB combined log, while `URGENT WATCHDOG` appears in 14 trim events' `dropped_labels`. Substring mismatch → URGENT classified OPTIONAL → dropped first when over cap. **To be re-verified by reading the classifier in Issue A Phase 1.**

## Baseline — Issue B (APEX OpenRouter)

| Metric | Value |
|---|---|
| `APEX_FAIL_UNEXPECTED` events | 4 |
| `APEX_OK` events | 26 |
| `APEX_TIER` emits | 51 |
| Failure rate (fails / OK) | 4 / 30 ≈ 13% |

**Affected coins (audit-vs-reality delta):**
- Audit said: EGLDUSDT, ORCAUSDT, LDOUSDT (×2).
- Reality from log: EGLDUSDT (1), ORCAUSDT (1), LDOUSDT (1), **ONDOUSDT (1)** — not LDOUSDT twice.

**Time pattern:**
- 15:33:35 — EGLDUSDT
- 15:49:23 — ORCAUSDT
- 15:49:25 — LDOUSDT
- 15:49:26 — ONDOUSDT

Three of four events fired within 4 seconds at 15:49 — strong upstream-incident pattern. Not coin-specific.

## Baseline — Issue C (Profit Sniper)

**Audit-vs-reality delta — significant.** Audit said: "32 mode4_p9 (full close) + 17 SNIPER_STALL_ESCAPE + 4 M4_ACT_CLOSE = 53/64 sniper-driven = 83%". Reality:

| Closure type | Count |
|---|---|
| `M4_ACT_CLOSE` (sniper full close — actual events) | **4** |
| `M4_ACT_PARTIAL` (sniper partial close) | 13 |
| `M4_ACT_TIGHTEN` (sniper trail tighten — not a close) | 148 |
| `MODE4_STALL_ESCALATE` (escalation log; precedes close) | 4 |
| `MODE4_PARTIAL_DEGRADED_TO_FULL` | 4 |
| `STRAT_ACTION_CLOSE` (CALL_B strategic close) | 9 |
| `TRAIL HIT` (natural close) | 2 |
| `closed_by=strategic_review` | 8 |
| `closed_by=shadow_sl_tp` (Bybit SL/TP fill) | 6 |

**Actual full closures in window (rough total):** ~21–25 across all paths. Sniper full-close share: **4 / ~21 ≈ 19%**, not 83%.

**`mode4_p9` substring:** appears 32 times in the log, but **not as close events** — appears in tick-action evaluation logs (action source / phase tag during normal score reporting), not as the `M4_ACT_CLOSE` event. The audit conflated phase-tag occurrences with close events.

**The 4 actual sniper closes (all loss-side):**
| Time | Sym | PnL | Peak | Pullback | src | Score |
|---|---|---|---|---|---|---|
| 14:54:19 | SANDUSDT | −1.25% | +0.06% | 0% | stall_escape | 37 |
| 15:24:32 | INJUSDT | −0.30% | +0.30% | 0% | stall_escape | 34 |
| 15:33:34 | ARBUSDT | −0.31% | +0.13% | 0% | stall_escape | 17 |
| 15:47:56 | HYPERUSDT | −0.37% | +0.00% | 0% | stall_escape | 31 |

All four fired via `src=stall_escape` (the mature-stall valve at `profit_sniper.py:2484`). All four closed positions in loss after only briefly touching profit (or never reaching profit). Score at close was 17–37, well below regime full-close thresholds (65–85). **This means the `score>=full` mode4_p9 score path did NOT fire any of the 4 closes — they all came from the stall-escape valve.**

| Guard counts (all firing — guards are working) | Value |
|---|---|
| `SNIPER_AGE_GUARD` (sub-300s blocks) | 2,427 |
| `SNIPER_GRACE_BLOCKED` (60-tick gap) | 241 |
| `SNIPER_PROFIT_GUARD` (in-profit blocks) | 128 |

## Audit-vs-Reality Discrepancies (running list)

| # | Topic | Audit/prompt said | Reality | Material? |
|---|---|---|---|---|
| 1 | APEX model name | `deepseek/deepseek-chat-v3-0324` | `deepseek/deepseek-v3.2` (`settings.py:1782`) | Cosmetic for now |
| 2 | `_build_trade_prompt` line | 3073 | 2235 (3073 is the trim-emit site) | Cosmetic |
| 3 | Sniper "phases" | "M1–M9 phases" | regime-aware score thresholds; `mode4_p9` is a log/phase tag | Material — reframes Issue C |
| 4 | Issue C close count | 32 mode4_p9 + 17 stall_escape + 4 M4_ACT_CLOSE = 53/64 sniper closures (83%) | 4 M4_ACT_CLOSE total; sniper share of full closures ≈ 19% | **Material** — invalidates the 83% premise but not the underlying concern that all 4 sniper closes were losing trades killed via mature-stall valve |
| 5 | Issue B affected coins | EGLDUSDT, ORCAUSDT, LDOUSDT (×2) | EGLDUSDT, ORCAUSDT, LDOUSDT, ONDOUSDT (each ×1) | Cosmetic |
| 6 | Issue A URGENT alerts dropped | "at least 2 events" | 14 events (much higher) | Material — reinforces severity |
| 7 | Issue A `dropped_important > 0` | not enumerated | 17 events | Material — IMPORTANT-tagged sections also being trimmed |
| 8 | Issue C SNIPER_GRACE_BLOCKED | 154 | 241 | Cosmetic |

## Operational Verification

- Logs available: workers.log + 1 rotated, brain.log, mcp.log, shadow.log, layer1c_full.jsonl — all current.
- 56 stage2 dumps in window provide direct CALL_A prompt evidence for Issue A.

## Phase 0 Verdict

System state is known. Baselines captured. **Issue C's 83% premise is invalidated by data**, but the underlying concern — sniper killing positions via stall_escape that briefly touched profit then sat flat — is real (4/4 events show this pattern). Issue A is more severe than the audit reported (14 URGENT-drop events, not 2). Issue B is real and concentrated at 15:49.

Proceeding to Issue A Phase 1.
