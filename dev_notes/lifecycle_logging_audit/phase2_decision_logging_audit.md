# Phase 2 — Lifecycle Phase 2 (Decision) Logging Audit

**Date:** 2026-05-09
**Lifecycle phase:** Decision (Layer 2) — CALL_A scheduling → prompt construction → Claude CLI invocation → response parsing → directive emission, then symmetric for CALL_B.
**Steps audited:** 10 (Steps 2.1 through 2.10).
**Files investigated:**
- `src/brain/strategist.py` (4,010 lines, 120 log calls — grep-walked + targeted reads at lines 650-900, 1620-1640, 2510-2570, 3080-3140, 3470-3500, 4000-4010)
- `src/brain/claude_code_client.py` (1,500 lines, 58 log calls — grep-walked)
- `src/brain/decision_parser.py` (199 lines, 8 log calls — read end-to-end)

---

## Executive Summary

| Severity | Gap count |
|---|---|
| CRITICAL | 0 |
| HIGH | 1 |
| MEDIUM | 8 |
| LOW | 4 |
| **Total** | **13** |

Lifecycle Phase 2 is the most heavily-instrumented phase in the system. STRAT_CALL_A_*, STRAT_CALL_B_*, CLAUDE_*, STRAT_DIRECTIVE, STRAT_POS_ACT, STRAT_PROMPT_*, PROMPT_BUILD_DONE all fire reliably with `did=` correlation. The CLAUDE_PROC_STALL_60S/120S events provide rare visibility into the Claude CLI process state.

Gap concentration:
1. **30+ DEBUG context-build exception swallows** in strategist.py (`_build_trade_prompt` and `_build_position_prompt` paths). Most are legitimately graceful degradation (coaching text, optional sentiment, fund manager trim). **4 are operationally important and should escalate to WARNING** because the prompt is materially incomplete when they fire: X-RAY context build fail, account balance fetch fail, tiered capital limits fail, daily PnL fetch fail.
2. **3 prose log lines** in decision_parser.py + 2 in strategist.py + 1 in claude_code_client.py duplicate existing structured tags or lack a tag entirely.
3. **One missing structured tag** for `Position review failed` (strategist.py:725) — no STRAT_POS_REVIEW_FAIL tag wraps this prose error.
4. **PARSE_JSON strategy markers at DEBUG** — invisible at default sink. The strategy that succeeded (direct/fence/braces) is forensically useful for prompt-format regressions.
5. **No explicit "decision to fire CALL_A vs CALL_B" log** — operators see only the START tags. A pre-decision `STRAT_CYCLE_GATE` tag would explain why a cycle slot triggered one vs the other.

No CRITICAL gaps. The HIGH gap is the X-RAY context build silent fail (1.7-G2 cross-cuts here): a silent X-RAY failure in `_build_trade_prompt` corrupts every CALL_A prompt with empty X-RAY context, directly affecting trade selection.

---

## Tag-Frequency Verification (brain.log, all rotations)

```
3756 STRAT_CALL_A           2678 STRAT_CALL_B           2394 STRAT_POS_ACT
2344 STRAT_DIRECTIVE        2046 STRAT_PROMPT           1974 CLAUDE_CALL_START
1732 CLAUDE_CALL_OK         1049 CLAUDE_PROC_SPAWNED     783 PROMPT_BUILD_DONE
 778 STRAT_CALL_A_CTX        728 STRAT_CALL_A_START      683 CLAUDE_PROC_STALL_60S
 681 STRAT_PROMPT_BUILD      680 STRAT_CALL_A_END        631 STRAT_CALL_A_PLAN
 598 STRAT_CYCLE_START       587 STRAT_CTX               577 STRAT_PROMPT_SIZE
 522 STRAT_PLAN              522 STRAT_CYCLE_END         479 POSITION_INVALIDATED
 476 CLAUDE_RETRY            417 STRAT_CALL_B_FLIP_NOTICE  411 STRATEGIST_PACKAGES_READ
 369 STRAT_CALL_B_CTX        362 STRAT_CALL_B_START      359 STRAT_CALL_B_END
 353 STRAT_CALL_B_PLAN       353 STRAT_CALL_B_PARSED     235 CLAUDE_PROMPT_TRIMMED
 216 CLAUDE_PROC_STALL_120S  203 STRAT_PROMPT_REFRESH    179 STRAT_TOP_N_APPLIED
 104 CLAUDE_CALL_FAIL         87 STRAT_CALL_A_CTX_SLOW    59 CLAUDE_PROC_KILLED
  45 STRAT_CALL_A_NO_TRADES   24 STRAT_CALL_A_FAIL        19 CLAUDE_REFRESH_ATTEMPT
```

Tags with 0 firings (transition/error tags awaiting trigger): STRAT_CALL_A_SKIPPED (no_packages branch), STRAT_CALL_A_URGENT_ACTS (urgent watchdog injection), STRAT_ZERO_TRADES_INTENTIONAL (zero_two_contract branch), STRAT_CALL_A_PRECHECK_ERR (pre-check exception), STRAT_CALL_B_FAIL, STRAT_PLAN_FAIL, STRAT_AGGRESSIVE_FRAMING (sentinel — should be 1 per CALL_A).

---

## Step-By-Step Findings

### Step 2.1 — CALL_A scheduling (`src/brain/strategist.py:730-734`)

**Code path:** `create_trade_plan()` is the canonical CALL_A entry point. It generates a fresh `did = new_decision_id()` and emits `STRAT_CALL_A_START | did={did} | {ctx()}`. The decision to call this method (vs CALL_B) is made by the brain orchestrator (brain_v2.py / cycle controller, out of scope for this phase per audit prompt).

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `STRAT_CALL_A_START` | INFO | 734 | ✓ — 728 firings |
| `STRAT_CALL_A_SKIPPED` | WARNING | 755-759 | ✓ when no packages available |
| `STRAT_CALL_A_PRECHECK_ERR` | DEBUG | 770-772 | invisible |
| `STRAT_AGGRESSIVE_FRAMING` | INFO | 820-826 | ✓ once per CALL_A — sentinel for framing flags |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 2.1-G1 | The "fire CALL_A vs CALL_B" decision happens upstream (cycle controller) without an explicit log. Operators see only the START tags. Recommend a `STRAT_CYCLE_GATE | slot=N call=A reason=alternation positions=N inflight_a=N` log at the cycle controller. | MEDIUM | Easy — single log at the gate |
| 2.1-G2 | `STRAT_CALL_A_PRECHECK_ERR` at DEBUG (line 770). Pre-check exception is rare but operationally meaningful (it indicates layer_manager.get_coin_packages threw). Promote to WARNING. | MEDIUM | Trivial |

### Step 2.2 — CALL_A prompt construction (`strategist.py:_build_trade_prompt`)

**Code path:** `_build_trade_prompt()` (~ line 2330+) assembles 22+ sections including: regime, fear & greed, top-N coin packages, X-RAY structural context, recent thesis lessons, account balance, tiered capital limits, position list, daily PnL, event buffer, fund manager rules, aggressive-exploitation framing.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `STRAT_CALL_A` | INFO | 776 | ✓ chars=N |
| `STRATEGIST_PACKAGES_READ` | INFO | 2529 | ✓ — 411 firings |
| `STRAT_TOP_N_APPLIED` | INFO | 2564 | ✓ — 179 firings |
| `STRAT_CALL_A_CTX` | INFO | 3275 | ✓ sections=N chars=N el=Nms |
| `STRAT_CALL_A_CTX_SLOW` | WARNING | 3306 | ✓ — 87 firings |
| `STRAT_CTX` | INFO | 1628 | ✓ general context summary |
| `STRAT_CTX_SLOW` | WARNING | 1630 | ✓ |
| `STRAT_PROMPT_BUILD` | INFO | 3091 | ✓ per-section timings |
| `STRAT_PROMPT_BUILD_SLOW` | WARNING | 3104 | ✓ top-3 slowest sections |
| `STRAT_PROMPT_SIZE` | INFO | 3140 | ✓ final counts |
| `STRAT_PROMPT_REFRESH` | INFO | 640 | ✓ — 203 firings |
| `STRAT_PROMPT` | INFO | 653 | ✓ chars=N |
| `PROMPT_BUILD_DONE` | INFO | (in `_build_*_prompt`) | ✓ — 783 firings |
| `CLAUDE_PROMPT_TRIMMED` | INFO | (claude_code_client) | ✓ — 235 firings |

**DEBUG context-build exception swallows (~30 sites):**

Lines 968, 992, 1001, 1011, 1019, 1054, 1100, 1162, 1206, 1209, 1320, 1399, 1534, 1549, 1553, 1595, 1622, 2406, 2415, 2448, 2596, 2648, 2707, 2757, 2760, 2851, 2906, 3006, 3067, 3764.

Each is a `try: build_section() / except Exception as e: log.debug("X failed: {err}", err=str(e))` pattern that returns empty/zero and continues. Most are legitimately graceful (e.g. coaching text fetch, ticker fetch for min trade size, sentiment aggregation), but **4 sites are operationally important** because the prompt becomes materially incomplete:

- Line 1320 / 2851: `X-RAY context build failed: {err}` — **HIGH severity**. X-RAY context drives trade selection.
- Line 1534: `Account balance fetch failed: {err}` — affects equity in prompt → affects sizing reasoning.
- Line 1553: `Tiered capital limits failed: {err}` — affects capital tier reasoning.
- Line 1595: `Daily PnL fetch failed: {err}` — affects performance enforcer reasoning.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 2.2-G1 | X-RAY context build failure at DEBUG (lines 1320, 2851 — both CALL_A and CALL_B paths). Silent failure here corrupts every prompt without operator visibility. Promote to WARNING with structured tag `XRAY_CTX_BUILD_FAIL | call={A or B} err='{err}' | {ctx()}`. | HIGH | Trivial — change `log.debug` to `log.warning` + structured tag |
| 2.2-G2 | Account balance fetch fail (line 1534) at DEBUG — promote to WARNING with `STRAT_CTX_BALANCE_FAIL` tag. | MEDIUM | Trivial |
| 2.2-G3 | Tiered capital limits fail (line 1553) at DEBUG — promote to WARNING with `STRAT_CTX_TIERED_CAPITAL_FAIL` tag. | MEDIUM | Trivial |
| 2.2-G4 | Daily PnL fail (line 1595) at DEBUG — promote to WARNING with `STRAT_CTX_DAILY_PNL_FAIL` tag. | MEDIUM | Trivial |
| 2.2-G5 | The remaining ~26 DEBUG context-build sites are legitimately graceful (coaching, ticker fetches, optional sentiment). Document in Phase 11 as INTENTIONAL silence rather than gaps. Optionally roll into `STRAT_CTX_DEGRADED | failed_sections=[...]` if any non-empty. | LOW | Easy if rolled |

### Step 2.3 — CALL_A Claude CLI invocation (`src/brain/claude_code_client.py:send_message`)

**Code path:** `send_message(prompt, system_prompt)` (line ~280+) handles the Claude CLI subprocess. Spawns process, streams stdout, handles auth refresh, retries on transient failure, kills hung processes.

**Logs (16+ events):**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `CLAUDE_RATE` | DEBUG | 283 | invisible (per-call rate-sleep) |
| `CLAUDE_PROMPT` | DEBUG | 299 | invisible (per-call prompt size) |
| `CLAUDE_CALL_OK` | INFO | 338, 425 | ✓ — 1,732 firings |
| `CLAUDE_NONRETRY` | WARNING | 354 | ✓ |
| `CLAUDE_ALERT_FAIL` | WARNING | 396, 478 | ✓ |
| `CLAUDE_AUTH_RECOVERED` | INFO | 408 | ✓ |
| `CLAUDE_POST_REFRESH_FAIL` | WARNING | 441 | ✓ |
| `CLAUDE_CRED_RELOAD` | INFO | 252, 445 | ✓ |
| `CLAUDE_AUTH` | ERROR | 461 | ✓ |
| `CLAUDE_RETRY` | WARNING | 506 | ✓ — 476 firings |
| `CLAUDE_CALL_FAIL` | ERROR | 535 | ✓ — 104 firings |
| `CLAUDE_REFRESH_SKIP` | DEBUG | 779 | invisible |
| `CLAUDE_REFRESH_ATTEMPT` | INFO | 782 | ✓ — 19 firings |
| `CLAUDE_REFRESH_FAIL` | WARNING | 806, 834, 837 | ✓ |
| `CLAUDE_PROC_KILLED` | WARNING | 1364 | ✓ — 59 firings |
| `CLAUDE_ORPHAN_CLEANUP` | WARNING | 1403 | ✓ |
| `CLAUDE_USAGE_RECOVERED` | INFO | 276 | ✓ |
| `CLAUDE_PROC_STALL_60S` | WARNING | (in subprocess streamer) | ✓ — 683 firings |
| `CLAUDE_PROC_STALL_120S` | WARNING | (in subprocess streamer) | ✓ — 216 firings |
| `CLAUDE_PROC_SPAWNED` | INFO | (in subprocess spawner) | ✓ — 1,049 firings |

**Prose lines:**
- Line 218: `log.info("ClaudeCodeClient: Telegram alert callback registered")` — tag-less.
- Line 462: `log.error("Claude auth failed — backoff {backoff_s}s. Run 'claude login' to fix.")` — duplicate of CLAUDE_AUTH at line 461.
- Line 881: `log.info("Claude Code Client diagnostics:")` — diagnostic header (acceptable in dump path).

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 2.3-G1 | Line 218 prose "Telegram alert callback registered" → `CLAUDE_ALERT_CALLBACK_OK \| {ctx()}` structured. | LOW | Trivial |
| 2.3-G2 | Line 462 prose duplicate of CLAUDE_AUTH at line 461 — delete prose. | LOW | Trivial |
| 2.3-G3 | `CLAUDE_RATE` and `CLAUDE_PROMPT` at DEBUG (lines 283, 299). Per-call DEBUG is fine for these — they fire 1974 times in a rotation. **Document as intentional silence**. No fix needed. | LOW | None |

### Step 2.4 — CALL_A response parsing (`src/brain/decision_parser.py`)

**Code path:** `parse(response_text)` extracts JSON via 3 strategies (direct → fence → braces) and builds a `BrainDecision`. `_build_decision()` (line 79) sets all fields. `parse_watchdog_decision()` (line 116) does the same for watchdog actions.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `PARSE_JSON` (strategy=direct) | DEBUG | 47 | invisible |
| `PARSE_JSON` (strategy=fence) | DEBUG | 57 | invisible |
| `PARSE_JSON` (strategy=braces) | DEBUG | 68 | invisible |
| `PARSE_FAIL` | ERROR | 73 | ✓ |
| `PARSE_OK` | INFO | 109 | ✓ |
| (prose) "Parsed decision..." | INFO | 110-113 | duplicate of PARSE_OK |
| (prose) "Invalid watchdog action..." | WARNING | 135-137 | tag-less |
| (prose) "Parsed watchdog decision..." | INFO | 158-161 | tag-less |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 2.4-G1 | Line 110-113 prose duplicate of PARSE_OK at line 109 — delete. | LOW | Trivial |
| 2.4-G2 | Line 158-161 prose `Parsed watchdog decision` should be `PARSE_OK_WD \| act={act} sym={sym} conf={conf:.2f} new_sl={...} \| {ctx()}` structured. | MEDIUM | Trivial |
| 2.4-G3 | Line 135-137 prose `Invalid watchdog action '{a}'` should be `PARSE_INVALID_WD_ACTION \| received={a} defaulted_to=hold \| {ctx()}` structured (operator may want to grep these to detect schema drift). | MEDIUM | Trivial |
| 2.4-G4 | `PARSE_JSON` strategy markers at DEBUG (lines 47, 57, 68). Forensically useful for detecting prompt-format regressions (e.g. Claude suddenly emits JSON without code fences would shift fence→direct). Roll into PARSE_OK as a `strategy=` field, OR promote to INFO sparsely. | MEDIUM | Trivial — add a `strategy=` field to PARSE_OK; remove the DEBUG ones |

### Step 2.5 — CALL_A directive emission (`strategist.py:858-863`)

**Code path:** After plan parsing, the strategist emits one `STRAT_DIRECTIVE` per `plan.new_trades` entry, with `#i+1`, `sym=`, `dir=`, `lev=`, `rsn=`. Then dispatches each via the layer-manager / APEX gate.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `STRAT_CALL_A_PLAN` | INFO | 854-857 | ✓ — 631 firings |
| `STRAT_DIRECTIVE` | INFO | 863 | ✓ — 2,344 firings |
| `STRAT_CALL_A_NO_TRADES` | WARNING | 866 | ✓ — 45 firings |
| `STRAT_ZERO_TRADES_INTENTIONAL` | INFO | 875-879 | ✓ when zero_two_contract |
| `STRAT_CALL_A_URGENT_ACTS` | INFO | 848-850 | ✓ when urgent watchdog injection |
| `STRAT_CALL_A_END` | INFO | 882 | ✓ — 680 firings |
| `STRAT_CALL_A_FAIL` | ERROR | 886 | ✓ — 24 firings |

**Gaps:** none significant. Step 2.5 is excellently instrumented.

### Step 2.6 — CALL_B scheduling (`strategist.py:891-895`)

**Code path:** `create_position_plan()` is the CALL_B entry point. Same `did = new_decision_id()` + `STRAT_CALL_B_START` pattern. Pre-conditions checked: open positions exist (otherwise CALL_B is skipped at the cycle gate level).

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `STRAT_CALL_B_START` | INFO | 895 | ✓ — 362 firings |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 2.6-G1 | Same as 2.1-G1: no explicit "fire CALL_B" decision log. The cycle gate decides CALL_A/B/none based on alternation, position count, in-flight state. Recommend `STRAT_CYCLE_GATE` at the gate. | MEDIUM | Easy |

### Step 2.7 — CALL_B prompt construction (`strategist.py:_build_position_prompt`)

**Code path:** `_build_position_prompt(positions)` (~line 3340+) builds CALL_B prompt: per-position list with PnL/age/thesis, recent close outcomes, watchdog alerts, management framing. Uses similar STRAT_CTX/PROMPT_BUILD/PROMPT_SIZE infra as CALL_A.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `STRAT_CALL_B` | INFO | 923 | ✓ chars=N — 2,678 firings |
| `STRAT_CALL_B_CTX` | INFO | (in _build_position_prompt) | ✓ — 369 firings |
| `STRAT_CALL_B_CTX_SLOW` | WARNING | 3550 | ✓ |
| `STRAT_CALL_B_FLIP_NOTICE` | INFO | 3478, 3490 | ✓ — 417 firings (per-symbol when XRAY/source flips direction) |

**Gaps:** none significant. CALL_B has parallel coverage to CALL_A.

### Step 2.8 — CALL_B Claude CLI invocation

Same code path as Step 2.3 — `claude.send_message(prompt, system)` is shared. All CLAUDE_* tags apply equally.

**Gaps:** same as Step 2.3.

### Step 2.9 — CALL_B response parsing (`strategist.py` + `decision_parser.py`)

**Code path:** Strategist's `_parse_position_plan()` extracts position_actions from Claude's response. Emits `STRAT_CALL_B_PARSED | total=N planned_actions=... | {ctx()}` (line 4004) with per-action breakdown.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `STRAT_CALL_B_PLAN` | INFO | (in CALL_B) | ✓ — 353 firings |
| `STRAT_CALL_B_PARSED` | INFO | 4004 | ✓ — 353 firings |
| `STRAT_CALL_B_END` | INFO | 945 | ✓ — 359 firings |
| `STRAT_CALL_B_FAIL` | ERROR | 949 | ✓ |

**Gaps:** none specific to Step 2.9. The decision_parser.py gaps (2.4-G2, 2.4-G3, 2.4-G4) apply equally to CALL_B parsing if the same code path is reused (which it is, via `parse_watchdog_decision`).

### Step 2.10 — CALL_B action emission

**Code path:** Per-action emission via `STRAT_POS_ACT | sym={sym} act={act.action} rsn='{...}' | {ctx()}` (line 684). Acts: hold, close, tighten_stop, set_exit, take_profit. Then handed off to coordinator/L4P for execution.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `STRAT_POS_ACT` | INFO | 684 | ✓ — 2,394 firings |
| `STRAT_NO_TRADES` | WARNING | 688 | ✓ |
| `POSITION_INVALIDATED` | (varies) | 585 | ✓ — 479 firings (when position closed externally during CALL_B) |
| `STRAT_PLAN_FAIL` | ERROR | 695 | ✓ |
| (prose) "Strategic plan creation failed" | ERROR | 696 | duplicate STRAT_PLAN_FAIL |
| `STRAT_CYCLE_END` | INFO | 691, 698 | ✓ — 522 firings |
| (prose) "Position review failed" | ERROR | 725 | tag-less — needs STRAT_POS_REVIEW_FAIL |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 2.10-G1 | Line 696 prose duplicates STRAT_PLAN_FAIL (line 695) — delete prose. | LOW | Trivial |
| 2.10-G2 | Line 725 prose `Position review failed: {err}` — `review_positions()` (a 30s-cycle watchdog method, distinct from CALL_B) — needs structured tag `STRAT_POS_REVIEW_FAIL \| err='{err}' \| {ctx()}`. | MEDIUM | Trivial |

---

## Cross-Step Observations (Carry Forward To Phase 11)

### Observation A — DEBUG context-build pattern in strategist

The 30+ DEBUG `except Exception as e: log.debug("X failed: {err}", err=str(e))` sites are the dominant pattern in `_build_trade_prompt` and `_build_position_prompt`. The audit's Rule 3 explicitly forbids "Wrapping a function in try/except just to log the exception, without addressing why the exception happens" — but this code is not band-aid; it's **graceful degradation by design** (the prompt builds with whatever sub-sections are available; missing sections become empty strings).

Per-site decision in Phase 11:
- 4 sites should be promoted to WARNING (X-RAY ×2, balance, tiered capital, daily PnL).
- 26 sites can stay at DEBUG with a per-cycle `STRAT_CTX_DEGRADED | sections=[failed_section_names]` rollup so operators see when the prompt is degraded without grepping 30 lines.

### Observation B — Cycle gate visibility

Steps 2.1 and 2.6 both lack a "decision to fire CALL_A/B/none" log at the gate. The brain orchestrator decides based on cycle slot, alternation pattern, open positions count, and in-flight CALL state. Operators today reverse-engineer this from `STRAT_CYCLE_START` + `STRAT_CALL_A_START` + `STRAT_CALL_B_START` ordering. A single `STRAT_CYCLE_GATE | slot=N decision={A|B|skip} reason=... has_packages=Y open_positions=N inflight_a=N inflight_b=N | {ctx()}` log at the gate would close this.

### Observation C — Forensic strategy markers at DEBUG

`PARSE_JSON | strategy={direct,fence,braces}` markers in decision_parser.py are forensically valuable for detecting prompt-format regressions (e.g. Claude suddenly emitting bare JSON without code fences would shift fence→direct, an early signal of model behavior drift). Promote to INFO via a `strategy=` field on PARSE_OK rather than a separate log line.

### Observation D — `did=` correlation works exceptionally well

Phase 2 is the heaviest user of `did=`. Across brain.log:
- 21,068 lines carry `did=` — virtually all CALL_A/CALL_B-related events.
- The `did` from `new_decision_id()` propagates correctly through every prompt-build, every Claude call, every parse, every directive emission, every position action.

This is the gold-standard reference for how context binding should work in other lifecycle phases.

---

## Verification Gate

| Gate | Status |
|---|---|
| All 10 steps audited | PASS |
| Code paths read or grep-walked end-to-end | PASS (decision_parser.py read whole; strategist.py + claude_code_client.py grep-walked + targeted reads of ~700 lines total) |
| Tag emission verified in real logs | PASS (40+ tags grep'd against `brain.log`) |
| Gap list complete | PASS (13 gaps catalogued) |
| Severity assigned per gap | PASS (1 HIGH, 8 MEDIUM, 4 LOW) |
| Fix difficulty assigned per gap | PASS (all Trivial/Easy) |
| Evidence cited per gap (file:line + log status) | PASS |

**Phase 2 verification gate:** PASS. Proceeding to Phase 3.

---

## Notes carried forward to Phase 3 investigation

- The X-RAY context-build silent-fail (2.2-G1) is HIGH because it materially corrupts the trade decision prompt. **This may interact with Phase 3 (APEX) if APEX also reads X-RAY data** — Phase 3 audit should check whether APEX has its own X-RAY-fetch path with similar silent-fail risk.
- The `did=` propagation in Phase 2 is the gold standard. Phase 3 (APEX) operates on directives that came from CALL_A — verify APEX preserves `did=` end-to-end.
- The CLAUDE_PROC_STALL_60S/120S tags are unique to Phase 2 and represent the operator's known "Claude CLI latency climb" issue (memory `feedback_overhaul29_execution.md` references this). Recommended: Phase 11 NOT propose new logging here — the existing instrumentation already surfaces the issue clearly.
- decision_parser.py gaps (2.4-G2, G3) overlap Phase 6 (Active Management) since `parse_watchdog_decision` is used by the watchdog flow too. Phase 6 audit should reference these.
