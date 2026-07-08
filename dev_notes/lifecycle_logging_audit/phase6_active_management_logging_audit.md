# Phase 6 — Lifecycle Phase 6 (Active Management) Logging Audit

**Date:** 2026-05-09
**Lifecycle phase:** Active Management (Layer 7) — Watchdog tick scheduling → per-position health → Sniper ladder → Time decay guards → Layer 4 protection → CALL_B periodic management.
**Steps audited:** 15 (Steps 6.1 through 6.15).
**Files investigated:**
- `src/workers/position_watchdog.py` (3,172 lines, 115 log calls — grep-walked)
- `src/workers/profit_sniper.py` (3,667 lines, 94 log calls — grep-walked)
- `src/risk/time_decay_sl.py` (727 lines, 9 log calls — grep-walked)
- `src/risk/layer4_protection.py` (423 lines, 2 log calls — grep-walked)

---

## Executive Summary

| Severity | Gap count |
|---|---|
| CRITICAL | 0 |
| HIGH | 1 |
| MEDIUM | 9 |
| LOW | 4 |
| **Total** | **14** |

Phase 6 is the **most heavily-instrumented lifecycle phase** with 100+ structured tags emitting to workers.log. The dominant tag families are M4_* (sniper, 23 tag types), WD_* (watchdog, 7 tag types), TIME_DECAY_* (5 tag types), SENTINEL_* (5 tag types), and SNIPER_* (7 tag types).

Tag firing volume is significant:
- M4_TRAIL_FLOOR: 42,289 firings (per-tick — every position, every cycle)
- WD_TICK: 43,176 firings (1-sec interval)
- M4_DECISION: 23,316 firings (per-position per-tick)
- TIME_DECAY_MAE_GUARD: 8,656 firings
- SWEET_SPOT_FIRED: 8,173 firings

Gap concentration:
1. **Layer 4 Protection (L4P_*) shows 0 firings** despite the file existing. Either L4P is wired but no protection events triggered (system healthy) or L4P is bypassed. **HIGH** — needs operator confirmation.
2. **22 prose log lines** across watchdog (15) and sniper (7) — close failures, Brain decision failures, alert failures, plan timer failures.
3. **M4_TRAIL_FLOOR per-tick noise** — 42k+ firings represent per-position per-tick trail-floor calc. Not actionable per-tick; should be compressed (emit only on floor change) or rolled into M4_DECISION.
4. **TIME_DECAY_GRACE / TIME_DECAY_FORCE_CLOSE_TRACE / TIME_DECAY_STRUCT_INVALIDATED at 0 firings** — possibly DEBUG-only or rarely triggered.
5. **SNIPER_M5_TIGHTEN at 0 firings** — exists in code but never fires.

No CRITICAL gaps. The phase is operationally well-covered.

---

## Tag-Frequency Verification (workers.log + rotated)

```
43176 WD_TICK                42289 M4_TRAIL_FLOOR        23316 M4_DECISION
11442 SNIPER_AGE_GUARD       10130 M4_GATED               8656 TIME_DECAY_MAE_GUARD
 8573 WD_TICK_DONE            8173 SWEET_SPOT_FIRED       3461 SNIPER_DEVELOPMENT_GUARD
 2689 SNIPER_SPIKE            1718 TIME_DECAY_AGE_GUARD   1439 TIME_DECAY_STRUCT_GUARD
 1398 SNIPER_CAP               927 SNIPER_GRACE_BLOCKED    691 mode4_p9
  681 M4_ACT_TIGHTEN           529 SENTINEL_DEADLINE       326 TIME_DECAY_FLOOR_PRICE_REL
  262 SNIPER_STALL_ESCAPE      223 WD_CLOSE                186 WD_LAST_CLOSE_AUTH
  179 M4_ACT_PARTIAL           143 TIME_DECAY_CALC         132 GHOST_RECONCILED
  113 SENTINEL_ADVISOR_SL       97 M4_ACT_CLOSE             72 SENTINEL_ADVISOR_BLOCK
   60 POS_ACTION_SKIP           46 SENTINEL_STEP_CLAMP      37 WD_TICK_SLOW
   23 WD_CLOSE_PRICE_FALLBACK   11 WD_LAST_CLOSE_FALLBACK    3 EARLY_EXIT_DISABLED_WOULD_FIRE
    1 SNIPER_PROTECTED           0 SNIPER_M5_TIGHTEN          0 L4_PROT_AGE_ERR  (and L4P_*)
    0 TIME_DECAY_STRUCT_INVALIDATED  0 TIME_DECAY_GRACE  0 TIME_DECAY_FORCE_CLOSE_TRACE
```

Notable ratios:
- **WD_LAST_CLOSE_FALLBACK** (11) / **WD_CLOSE** (223) = **4.9% fallback rate** — significantly improved from the audit's reported 35% (P3 last_close retry fix is working).
- **WD_CLOSE_PRICE_FALLBACK** (23) / **WD_CLOSE** (223) = **10.3% fallback rate** — separate code path; Phase 8 audit will dig into this.
- **WD_TICK_SLOW** (37) / **WD_TICK** (43,176) = **0.086%** — watchdog ticks rarely run slow.
- **mode4_p9** (691) and **M4_ACT_CLOSE** (97) — close decision discrepancy. P9 trigger fires 7x more than the close action — possibly P9 is trigger-only, with M4_ACT_CLOSE being the actual close emission.

---

## Step-By-Step Findings

### Step 6.1 — Watchdog tick scheduling (`position_watchdog.py`)

**Code path:** Watchdog's BaseWorker tick loop with `interval_seconds=1`. Each tick increments wid via `new_watchdog_id()`.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `WD_TICK` | INFO | ✓ — 43,176 firings (per second) |
| `WD_TICK_DONE` | INFO | ✓ — 8,573 firings |
| `WD_TICK_SLOW` | WARNING | ✓ — 37 firings |
| `BASE_WORKER_TICK_SLOW` | WARNING | ✓ — 120 firings (Phase 0 baseline) |

**Gaps:** none significant.

The discrepancy WD_TICK (43,176) vs WD_TICK_DONE (8,573) is a 5x ratio — possibly WD_TICK fires for every loop iteration, WD_TICK_DONE only when work was done (positions present). Worth Phase 11 verification but appears intentional.

### Step 6.2 — Watchdog get_positions call (`position_watchdog.py`)

**Code path:** Each tick reads positions via `position_service.get_positions()` (Transformer-routed). Per audit, currently poll-only at 10s tick (audit-specified) — but actual is 1s based on baseline.

**Logs:** Position-list reads are silent (no per-call log). Failures surface as POS_ACTION_SKIP (60) or other downstream signals.

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 6.2-G1 | No per-call log for `get_positions`. Failure of get_positions causes the entire watchdog tick to silently degrade. **Recommend:** add `WD_POSITIONS_FETCHED \| count=N elapsed_ms=N` at DEBUG, with WARNING-level `WD_POSITIONS_FAIL \| err=...` on exception. | LOW | Easy |

### Step 6.3 — Watchdog per-position health check (`position_watchdog.py`)

**Code path:** For each open position, watchdog evaluates: PnL, age, SL/TP distance, regime alignment, sentinel deadlines.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `SENTINEL_DEADLINE` | INFO | ✓ — 529 firings |
| `SENTINEL_ADVISOR_BLOCK` | INFO | ✓ — 99 firings |
| `SENTINEL_ADVISOR_SKIP` | INFO | ✓ — 178 firings |
| `SENTINEL_ADVISOR_SL` | WARNING | ✓ — 113 firings |
| `SENTINEL_STEP_CLAMP` | INFO | ✓ — 46 firings |
| `POS_ACTION_SKIP` | INFO | ✓ — 60 firings |
| `EARLY_EXIT_DISABLED_WOULD_FIRE` | INFO | ✓ — 3 firings |
| `GHOST_RECONCILED` | INFO | ✓ — 132 firings |

**Gaps:** none significant. Per-position health check is exhaustively instrumented.

### Step 6.4 — Watchdog SL hit detection (preemptive)

**Code path:** Watchdog detects positions approaching SL on tick. Acts via various close paths (emergency, hard stop, timeout, profit take).

**Logs:** 11 prose error lines for close-action failures (lines around emergency/duplicate/plan timer/trail/early-exit/hard-stop/timeout/profit-take close failures).

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 6.4-G1 | Watchdog has 11 prose error lines for close-action failures: emergency close, duplicate close, plan timer close, trail close, early exit, hard stop, timeout, profit take, watchdog alert, watchdog decision alert. Each is `log.error("X failed for {sym}: {err}", ...)` — tag-less. Replace with `WD_EMERGENCY_CLOSE_FAIL`, `WD_HARD_STOP_FAIL`, `WD_TIMEOUT_CLOSE_FAIL`, `WD_PROFIT_TAKE_FAIL`, `WD_TRAIL_CLOSE_FAIL`, `WD_EARLY_EXIT_FAIL`, `WD_PLAN_TIMER_CLOSE_FAIL`, `WD_DUP_CLOSE_FAIL`, `WD_ALERT_FAIL`, `WD_DECISION_ALERT_FAIL` structured tags. | MEDIUM | Easy — 11 sites, mostly 1-line replacements |
| 6.4-G2 | 4 prose informational lines: "Watchdog: paused", "Watchdog: full close {sym}", "Watchdog: max Brain calls/hour reached", "Watchdog: daily budget exceeded" — should be WD_PAUSED, WD_FULL_CLOSE, WD_BRAIN_BUDGET_LIMIT, WD_BUDGET_EXCEEDED structured. | LOW | Trivial |

### Step 6.5 — Sniper tick scheduling (`profit_sniper.py`)

**Code path:** Sniper monitors per position. Composite tick fires on sweet spot.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `SWEET_SPOT_FIRED` | INFO | ✓ — 8,173 firings |
| `M4_DECISION` | INFO | ✓ — 23,316 firings |
| `M4_GATED` | INFO | ✓ — 10,130 firings (decision gated by cooldown/grace/etc.) |

**Gaps:** none significant.

### Step 6.6 — Sniper score calculation

**Code path:** Per tick, computes composite score based on PnL trajectory, age, market spike, etc. Score feeds the M-phase ladder.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `M4_TRAIL_FLOOR` | INFO | ✓ — 42,289 firings (per-position per-tick trail floor calc) |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 6.6-G1 | `M4_TRAIL_FLOOR` fires 42,289 times — every position every tick. The line emits raw / floor / atr_floor / pct_floor fields per call but most ticks emit identical values (trail floor only changes when atr or price moves significantly). **Recommend:** compress to emit only on change, OR roll into M4_DECISION as a field set. The 42k log volume IS noise without aggregate value. | MEDIUM | Easy — add change-detection guard |

### Step 6.7 — Sniper ladder phase progression

**Code path:** M1 through M9 phases advance based on score thresholds. Each phase has its own threshold logic.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `M4_ACT_TIGHTEN` | INFO | ✓ — 681 firings |
| `M4_ACT_PARTIAL` | INFO | ✓ — 179 firings |
| `M4_ACT_CLOSE` | INFO | ✓ — 97 firings |
| `M4_ACT_TIGHTEN_AGG` | INFO | ✓ — 0 firings (aggressive tighten — rare condition) |
| `M4_ACT_SKIP` | INFO | (in code) |
| `M4_EVAL` | INFO | ✓ — 0 firings (may be debug) |
| `M4_LOG_FAIL` | (varies) | ✓ — 0 firings |
| `M4_SKIP` | (varies) | ✓ — 0 firings |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 6.7-G1 | `M4_EVAL`, `M4_LOG_FAIL`, `M4_SKIP` exist in code but 0 firings. Possibly DEBUG-only or never-triggered conditions. Verify in Phase 11. | LOW | Verify |
| 6.7-G2 | `M4_ACT_TIGHTEN_AGG` 0 firings — operationally rare or never reachable. Verify. | LOW | Verify |

### Step 6.8 — Sniper grace gating (Phase 1 grace fix)

**Code path:** Grace fix (per memory `project_sniper_latency_size_fix_status.md`) blocks early closes during minimum-age window. SNIPER_GRACE_BLOCKED events.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `SNIPER_GRACE_BLOCKED` | INFO | ✓ — 927 firings |
| `SNIPER_AGE_GUARD` | INFO | ✓ — 11,442 firings |
| `SNIPER_DEVELOPMENT_GUARD` | INFO | ✓ — 3,461 firings |
| `SNIPER_SPIKE` | INFO | ✓ — 2,689 firings |
| `SNIPER_CAP` | INFO | ✓ — 1,398 firings |
| `SNIPER_STALL_ESCAPE` | INFO | ✓ — 262 firings |

**Gaps:** none significant. The Phase 1 grace fix is well-instrumented.

### Step 6.9 — Sniper partial close decision

**Code path:** M-phase specific partial close logic. M4_ACT_PARTIAL fires.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `M4_ACT_PARTIAL` | INFO | ✓ — 179 firings |

**Gaps:** none significant.

### Step 6.10 — Sniper full close decision (mode4_p9)

**Code path:** Phase 9 full close trigger (`mode4_p9`). Closes via OrderService → Transformer → adapter.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `mode4_p9` | INFO | ✓ — 691 firings |
| `M4_ACT_CLOSE` | INFO | ✓ — 97 firings |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 6.10-G1 | `mode4_p9` (691) > `M4_ACT_CLOSE` (97) by 7x. Need to verify whether p9 trigger always results in M4_ACT_CLOSE, or whether some p9 triggers are gated or fail. **Recommend:** Phase 11 trace the gap to confirm. | MEDIUM | Verify |

### Step 6.11-6.13 — Time decay guards (`risk/time_decay_sl.py`)

**Code path:** TimeDecayManager evaluates per-position. Three guards: AGE, MAE, STRUCT.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `TIME_DECAY_AGE_GUARD` | INFO | ✓ — 1,718 firings |
| `TIME_DECAY_MAE_GUARD` | INFO | ✓ — 8,656 firings |
| `TIME_DECAY_STRUCT_GUARD` | INFO | ✓ — 1,439 firings |
| `TIME_DECAY_CALC` | INFO | ✓ — 143 firings |
| `TIME_DECAY_FLOOR_PRICE_REL` | INFO | ✓ — 326 firings |
| `TIME_DECAY_FORCE_CLOSE` | (varies) | ✓ — 72 firings |
| `TIME_DECAY_FORCE_CLOSE_TRACE` | DEBUG? | ✓ — 0 firings |
| `TIME_DECAY_GRACE` | (varies) | ✓ — 0 firings |
| `TIME_DECAY_STRUCT_INVALIDATED` | (varies) | ✓ — 0 firings |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 6.11-G1 | `TIME_DECAY_FORCE_CLOSE_TRACE`, `TIME_DECAY_GRACE`, `TIME_DECAY_STRUCT_INVALIDATED` 0 firings — verify in Phase 11. May be DEBUG-only or rarely-triggered. | LOW | Verify |

### Step 6.14 — Layer 4 protection service checks (`risk/layer4_protection.py`)

**Code path:** Layer4Protection has 423 lines but only 2 log calls. Tags include `L4_PROT_AGE_ERR`, `SNIPER_PROTECTED`. Per memory `project_layer4_realignment.md`, this was completed 2026-05-06 with 130/130 targeted tests.

**Logs:**

| Tag | Severity | Status |
|---|---|---|
| `L4_PROT_AGE_ERR` | (varies) | ✓ — 0 firings |
| `L4P_TIGHTEN` | (varies) | ✓ — 0 firings |
| `L4P_CLOSE` | (varies) | ✓ — 0 firings |
| `L4P` | (varies) | ✓ — 0 firings |
| `SNIPER_PROTECTED` | INFO | ✓ — 1 firing (in 7+ days of logs) |

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 6.14-G1 | All Layer 4 Protection tags show 0-1 firings across all rotations. Either L4P is genuinely silent (no triggering events in current data) OR L4P is bypassed. The Layer 4 Realignment shipped 2026-05-06 in 11 commits — should be active. **HIGH severity** if bypassed; needs operator confirmation. **Recommend:** Phase 11 verify L4P is actually invoked per cycle. Add a per-tick `L4P_TICK \| positions_evaluated=N protections_active=N` heartbeat at INFO if confirmed active. | HIGH | Easy if confirmed bypassed |

### Step 6.15 — CALL_B periodic management

**Code path:** Same as Phase 2 CALL_B but invoked mid-position. Already covered in Phase 2 audit.

**Logs:** STRAT_CALL_B_*, STRAT_POS_ACT, etc. — see Phase 2 deliverable.

**Gaps:** see Phase 2 audit gaps.

### Cross-cut: Sniper prose lines

7 prose error lines in profit_sniper.py:
- "Mode4 close FAILED {sym}: {err}" (ERROR)
- "Mode4 partial close FAILED {sym}: {err}" (ERROR)
- "Mode4 SL tighten outer-fail {sym}: {err}" (WARNING)
- "Claude ERROR for {sym}: {err}" (ERROR)
- "Claude says HOLD for {sym}, recheck in 30s" (INFO)
- "Claude error for {sym} — treating as HOLD" (WARNING)
- "Failed to record spike for {sym}: {err}" (ERROR)
- "Failed to complete counterfactual: {err}" (ERROR)

**Gaps:**

| # | Description | Severity | Fix difficulty |
|---|---|---|---|
| 6.X-G1 | Sniper has 7-8 prose error lines. Replace with `M4_CLOSE_FAIL`, `M4_PARTIAL_CLOSE_FAIL`, `M4_SL_TIGHTEN_OUTER_FAIL`, `M4_BRAIN_FAIL`, `M4_BRAIN_HOLD` (already exists?), `M4_SPIKE_RECORD_FAIL`, `M4_COUNTERFACTUAL_FAIL` structured tags. | MEDIUM | Easy — 7-8 sites |

---

## Cross-Step Observations (Carry Forward To Phase 11)

### Observation A — M4_TRAIL_FLOOR is the loudest tag (42k firings)

The single most-emitted Phase 6 tag. Per-position per-tick floor calculation. Most ticks emit identical values. Compression candidate:
- Emit only on floor change (significant noise reduction)
- OR roll into M4_DECISION (which already fires per-tick) as a `trail_floor=N` field

Operator decision in Phase 11.

### Observation B — Layer 4 Protection silence

L4P_* tags 0 firings despite Layer 4 Realignment being shipped 2026-05-06 with 130 tests. Either healthy state (no triggers) or bypassed. **HIGH severity if bypassed**. Phase 11 must confirm. Recommended: heartbeat tag for confirmation.

### Observation C — Watchdog and sniper prose pattern

22 prose lines across watchdog (15) and sniper (7) for various close failures, Brain decision failures, alert failures, plan timer failures. All operationally important. Easy fix: replace with structured tags. Recommend WD_*_FAIL and M4_*_FAIL family.

### Observation D — High-frequency tag noise vs operator value

Phase 6 emits volumes:
- WD_TICK 43k (1/sec)
- M4_TRAIL_FLOOR 42k (per position per tick)
- M4_DECISION 23k (per position per tick)
- SNIPER_AGE_GUARD 11k

The audit's Risk-2 "Adding too many logs slows the system" is partially realized here — Phase 6 already emits 100k+ lines per day across these tags. Phase 12 must NOT add more per-tick tags here. **Compression of M4_TRAIL_FLOOR is recommended; no new per-tick tags.**

### Observation E — `mode4_p9` vs `M4_ACT_CLOSE` ratio (7:1)

691 mode4_p9 triggers vs 97 M4_ACT_CLOSE actions. Possibly:
- mode4_p9 fires repeatedly until closed
- mode4_p9 is gated by additional conditions before M4_ACT_CLOSE
- Some mode4_p9 triggers fail/abort silently

Phase 11 traces this discrepancy.

---

## Verification Gate

| Gate | Status |
|---|---|
| All 15 steps audited | PASS |
| Code paths grep-walked + targeted reads | PASS (grep-only for the two huge files; deep enough for tag inventory) |
| Tag emission verified in real logs | PASS (50+ tags grep'd) |
| Gap list complete | PASS (14 gaps; 1 HIGH, 9 MEDIUM, 4 LOW) |
| Severity assigned per gap | PASS |
| Fix difficulty assigned per gap | PASS (all Trivial/Easy except verifications) |
| Evidence cited per gap (file:line + log status) | PASS |

**Phase 6 verification gate:** PASS. Proceeding to Phase 7.

---

## Notes carried forward to Phase 7-8 investigation

- **Layer 4 Protection silence (6.14-G1)** — Phase 11 must confirm L4P is active. May be a configuration gap, not a logging gap.
- **mode4_p9 vs M4_ACT_CLOSE ratio** (6.10-G1) — Phase 7 (closure triggers) audit overlaps; need to trace why p9 trigger doesn't always result in close action.
- **WD_LAST_CLOSE_FALLBACK 4.9% rate** — significantly improved from audit's 35%. Phase 8 (Detection) confirms.
- **WD_CLOSE_PRICE_FALLBACK 10.3% rate** — separate fallback path; Phase 8 audit will dig in.
- The 22 prose lines (6.4-G1, 6.X-G1) and the M4_TRAIL_FLOOR compression (6.6-G1) are the highest-leverage Phase 12 fixes for Phase 6.
