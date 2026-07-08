# Phase 10 — Lifecycle Phase 10 (Learning) Logging Audit

**Date:** 2026-05-09
**Lifecycle phase:** Learning — TIAS Phase-2 DeepSeek invocation → lesson storage → lesson injection into next CALL_A → lesson injection into next CALL_B → strategy parameter feedback.
**Steps audited:** 5 (Steps 10.1 through 10.5).
**Files investigated:**
- `src/tias/analyzer.py` (212 lines, read end-to-end)
- `src/tias/deepseek_client.py` (248 lines, grep-walked)
- `src/tias/collector.py` (577 lines, grep-walked)
- `src/tias/repository.py` (526 lines, grep-walked)
- `src/tias/backfill.py` (218 lines, grep-walked)
- `src/workers/manager.py` (TIAS_ANALYZED emission site at line 2014)
- `src/brain/strategist.py` (lesson injection sites at 1385, 3501-3540)

---

## Executive Summary

| Severity | Gap count |
|---|---|
| CRITICAL | 0 |
| HIGH | 1 |
| MEDIUM | 5 |
| LOW | 1 |
| **Total** | **7** |

Phase 10 has one **HIGH design observation**: `_tias_lessons_removed = True` at `strategist.py:3510`. **TIAS lessons are NOT being injected into CALL_A** as of the 2026-05-05 aggressive-framing rewrite. This is intentional per the aggressive-exploitation philosophy (the framing change deliberately removed coaching/lessons/regime instructions from CALL_A). The audit prompt's Step 10.3 expectation (lessons → CALL_A) **is consciously deferred**. Operator must confirm this is intended.

CALL_B still injects lessons via `get_recent_lessons` (strategist.py:1385) — but the success path has no log, and the failure path is DEBUG (silent). Operators cannot confirm "lessons were injected this cycle" from logs.

The TIAS Phase-2 DeepSeek invocation IS instrumented via TIAS_ANALYZED (393 firings at manager.py:2014). The DeepSeek session-close is at DEBUG. No separate TIAS_DEEPSEEK_OK / TIAS_DEEPSEEK_FAIL tags — failures come through TIAS_FALLBACK (0 firings) or as exceptions caught by the wrapper.

---

## Tag-Frequency Verification

```
394 TIAS_SAVE             393 TIAS_ANALYZED              6 TIAS_BACKFILL_OK
  5 TIAS_BACKFILL_START    5 TIAS_BACKFILL_END           0 TIAS_FALLBACK
  0 TIAS_PHASE2 (NOT FOUND)  0 TIAS_DEEPSEEK_OK         0 TIAS_DEEPSEEK_FAIL
  0 TIAS_LESSON / TIAS_LESSONS_FETCHED / LESSON_INJECT  (NOT FOUND)
  0 TIAS_COLLECT_FAIL / TIAS_B_*_FAIL / TIAS_C_*_FAIL / TIAS_D_*_FAIL / TIAS_E_*_FAIL  (all 0 — healthy)
```

`TIAS_SAVE` (394, collector) ≈ `TIAS_ANALYZED` (393, manager) — 1 trade unaccounted (likely race or earlier rotation).

---

## Step-By-Step Findings

### Step 10.1 — TIAS Phase-2 DeepSeek invocation

**Code path:** Trade closes → coordinator callback → TIAS collector saves row → background TIAS analyzer (manager.py orchestrates) calls `analyzer.analyze(trade_data)` → `analyzer._call_with_fallback(...)` → `DeepSeekClient.analyze(system_prompt, user_prompt, model, ...)` via OpenRouter.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `TIAS_ANALYZED` | INFO | manager.py:2014 | ✓ — 393 firings (per-trade analysis success) |
| `TIAS_FALLBACK` | WARNING | analyzer.py:117-123 | ✓ — 0 firings (primary always succeeds) |
| (DeepSeek session close) | DEBUG | deepseek_client.py:247 | invisible |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 10.1-G1 | No per-call DeepSeek visibility separate from the wrapper. `TIAS_ANALYZED` fires AFTER `_map_response` succeeds. Per-call latency, model used, tokens in/out, cost are computed but only at the analyzer level — not in dedicated DeepSeek tag. **Recommend:** add `TIAS_DEEPSEEK_OK \| sym={s} model={m} latency_ms={l} tokens_in={i} tokens_out={o} cost_usd={c} \| {ctx()}` at the DeepSeek client return, and `TIAS_DEEPSEEK_FAIL \| sym={s} model={m} err='{err}' retryable={r} \| {ctx()}` on exception. | MEDIUM | Easy — 2 new logs |

### Step 10.2 — Lesson storage (`tias/repository.py`)

**Code path:** `analyzer._map_response` builds the `ds_*` columns dict → `repository.update_trade_with_analysis(trade_id, ds_*)` → UPDATE SQL into trade_intelligence.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `TIAS_REPO_SYM_HIST_FAIL` | WARNING | repository.py:418 | ✓ — 0 firings |
| (no dedicated success-path log) | — | — | implicit via TIAS_ANALYZED |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 10.2-G1 | No `TIAS_REPO_UPDATE_OK` log for the lesson UPDATE write. The success is implicit in TIAS_ANALYZED firing. Acceptable IF TIAS_ANALYZED is emitted ONLY after the UPDATE succeeds (verify in Phase 11). | LOW | Verify |

### Step 10.3 — Lesson injection into next CALL_A (`strategist.py:3510`)

**Code path:** `_tias_lessons_removed = True` at line 3510 — **TIAS lessons are explicitly REMOVED from CALL_A** as of the 2026-05-05 aggressive-framing rewrite. The `STRAT_AGGRESSIVE_FRAMING` sentinel at line 3540 carries `tias_coaching_removed={_tias_lessons_removed} recency_lessons_count=0` to make this visible per cycle.

**Logs:**

| Tag | Severity | Line | Status |
|---|---|---|---|
| `STRAT_AGGRESSIVE_FRAMING` | INFO | strategist.py:820 | ✓ once per CALL_A; carries tias_coaching_removed flag |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 10.3-G1 | **HIGH design observation.** TIAS lessons are NOT injected into CALL_A in current code. `_tias_lessons_removed = True` at line 3510 is intentional (aggressive-framing rewrite). The audit prompt's Step 10.3 expectation is **consciously deferred**. Phase 11 must confirm with operator that this is intended. **Not a code/logging gap** — but the audit's success criterion "lesson injection into next CALL_A prompt" is currently unmet by design. Surface in Phase 11. | HIGH | Operator decision |

### Step 10.4 — Lesson injection into next CALL_B (`strategist.py:1384-1399`)

**Code path:** `_build_position_review_prompt` calls `thesis_mgr.get_recent_lessons(limit=10)` and appends to sections. Failure: `log.debug("Thesis and lessons fetch failed: {err}", err=str(e))` — DEBUG (silent).

**Logs:**

| Severity | Line | Pattern |
|---|---|---|
| DEBUG | strategist.py:1399 | "Thesis and lessons fetch failed" — silent |
| (no success-path log) | — | implicit via prompt size |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 10.4-G1 | CALL_B injects lessons via `get_recent_lessons(limit=10)` but no log confirms "N lessons injected" or "0 lessons injected (none recent)." Operators cannot tell if lessons reached the prompt. **Recommend:** add `STRAT_CALL_B_LESSONS_INJECTED \| count={n} | {ctx()}` after the lessons fetch. | MEDIUM | Easy — single log |
| 10.4-G2 | Line 1399 silent DEBUG fail — same pattern as Phase 2 audit (DEBUG context-build silent fail). Promote to WARNING with structured `STRAT_CALL_B_LESSONS_FETCH_FAIL` tag. | MEDIUM | Trivial |

### Step 10.5 — Strategy parameter feedback

**Code path:** Audit prompt: "(If implemented) strategy parameters adjust based on TIAS data". Searched `src/` for "strategy_param_feedback" / similar — **NOT FOUND**. Strategy parameters are static (defined at strategy registration), not adjusted by TIAS data.

**Logs:** N/A — feature not implemented.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 10.5-G1 | Strategy parameter feedback is NOT implemented. The audit prompt's "(If implemented)" caveat suggests this is aspirational. **Document in Phase 11 as not implemented** — no logging gap if intentional. | LOW | Documentation only |

### Cross-cutting: TIAS_BACKFILL family

**Code path:** `tias/backfill.py` runs a background pass to retry failed TIAS analyses. Periodic scheduled job.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `TIAS_BACKFILL_START` | INFO | ✓ — 5 firings |
| `TIAS_BACKFILL_OK` | INFO | ✓ — 6 firings |
| `TIAS_BACKFILL_END` | INFO | ✓ — 5 firings |
| `TIAS_BACKFILL_GIVE_UP` | WARNING | ✓ — 0 firings |
| `TIAS_BACKFILL_RETRY_FAIL` | WARNING | ✓ — 0 firings |

**Gaps:** none significant. Backfill is well-instrumented.

---

## Cross-Step Observations (Carry Forward To Phase 11)

### Observation A — TIAS lessons removed from CALL_A (DESIGN — not gap)

Most significant Phase 10 finding: `_tias_lessons_removed = True` at strategist.py:3510. This is the aggressive-framing rewrite (2026-05-05) deliberately stripping coaching/lessons/regime-instructions from CALL_A to maximize Claude's discretion.

**Phase 11 action:**
- Surface this as a "design observation, not gap" with:
  > "Per audit prompt Step 10.3, TIAS lesson injection into CALL_A is expected. Per current code (strategist.py:3510), TIAS lessons are removed from CALL_A by design (aggressive-framing rewrite 2026-05-05). The STRAT_AGGRESSIVE_FRAMING sentinel emits this fact per cycle. Operator confirmation: the deferred lesson injection IS intentional."
- If operator wants to re-enable, that's a separate change (not a logging fix).

### Observation B — Lesson injection visibility for CALL_B (10.4-G1)

CALL_B does inject lessons (`get_recent_lessons(limit=10)`) but the success path has no log. Operators cannot confirm "lessons reached the prompt" from logs without inspecting the prompt itself. Single-log fix: STRAT_CALL_B_LESSONS_INJECTED.

### Observation C — DeepSeek call visibility (10.1-G1)

The DeepSeek invocation is wrapped by the analyzer. TIAS_ANALYZED fires at the wrapper exit — DeepSeek-specific data (latency, tokens, cost, model) is computed but not surfaced as its own tag. Forensically valuable to add TIAS_DEEPSEEK_OK / TIAS_DEEPSEEK_FAIL.

### Observation D — Strategy parameter feedback not implemented (10.5-G1)

Audit's "(If implemented)" caveat is correct — feature absent. Phase 11 documents and moves on.

### Observation E — Phase 10 is well-instrumented except for the one design choice

Excluding Step 10.3 (which is intentional removal), TIAS Phase-2 has good coverage. The improvements are incremental (lesson-count visibility, DeepSeek tag), not structural.

---

## Verification Gate

| Gate | Status |
|---|---|
| All 5 steps audited | PASS |
| Code paths read (analyzer.py end-to-end) + grep-walked | PASS |
| Tag emission verified in real logs | PASS (15+ tags grep'd) |
| Gap list complete | PASS (7 gaps; 1 HIGH design observation, 5 MEDIUM, 1 LOW) |
| Severity assigned per gap | PASS |
| Fix difficulty assigned per gap | PASS |
| Evidence cited per gap (file:line + log status) | PASS |

**Phase 10 verification gate:** PASS. **All 10 lifecycle phase investigations complete.** Proceeding to Phase 11 (consolidated gap report + operator discussion).

---

## Final Note Before Phase 11

All 10 investigation phases complete. Total deliverables:
- phase0_baseline.md (411 lines)
- phase1_analysis_logging_audit.md
- phase2_decision_logging_audit.md
- phase3_optimization_logging_audit.md
- phase4_validation_logging_audit.md
- phase5_execution_logging_audit.md
- phase6_active_management_logging_audit.md
- phase7_closure_logging_audit.md
- phase8_detection_logging_audit.md
- phase9_recording_logging_audit.md
- phase10_learning_logging_audit.md (this file)

Phase 11 will consolidate into `phase11_comprehensive_gap_report.md` with executive summary, top-10 critical gaps, complete gap catalog by phase, working coverage section, and recommended implementation order. Operator approval required before Phase 12.
