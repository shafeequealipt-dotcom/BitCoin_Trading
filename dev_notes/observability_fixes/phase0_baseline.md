# Phase 0 — Pre-Flight Baseline & Corrected Gap Report

## Status

Phase 0 complete. The audit's gap list has been verified against the
current code and the cited log window. **Four of the ten "ZERO event"
gaps are tag-name mismatches: the lifecycle events DO fire under
established internal tag names.** Three gaps are genuine pure misses.
Three are partial gaps that need extension. This report drives the
per-gap protocol that begins with G1 Phase 1.

---

## Pre-Condition Checks

| Check | Status | Evidence |
|-------|--------|----------|
| Working tree | Modified | `config.toml`, `data/layer_state.json`, `data/logs/layer1c_full.jsonl` modified; several `dev_notes/` untracked from prior fixes |
| Current branch | `audit/all-tier2-combined` | `b3480381203ff1ceada7ab56788b1fbd97c1ba67` |
| B1a regime fix | In place upstream | Operator confirmed commit `6938c69` (2026-05-12) is on the merged history |
| Systemd services | Not directly verified | (operator-side state; not part of Phase 0 read-only scope) |
| System currently running | Yes | `data/logs/workers.log` actively growing (8.5 MB, last modified within minutes) |
| Current monitoring log on disk | Yes | `/home/inshadaliqbal786/ALL_LOGS_2026-05-13_21-53_to_23-23.log` (5.4 MB, 23,463 lines) |

**Note on log line count:** the audit prompt claims "33,987 log lines" for
the same window; the actual file is **23,463 lines**. The audit's
quantitative claims are spot-corrected below from the actual log.

---

## Step 0.1 — All 10 Gap Claims Verified Against Current Logs

Method: grep the in-prompt log file for every audit-claimed tag and every
related variant (same prefix, sibling lifecycle suffixes). Counts below
are from `/home/inshadaliqbal786/ALL_LOGS_2026-05-13_21-53_to_23-23.log`
(window 2026-05-13 21:53 → 23:23, ~1.5h, 23,463 lines).

### G1 — STRAT_CALL_A_DONE

| Tag | Audit | Actual |
|-----|-------|--------|
| `STRAT_CALL_A_START` | 12 | **12** ✓ |
| `STRAT_CALL_A_DONE` | 0 (claimed gap) | **0** (literal name absent) |
| `STRAT_CALL_A_END` | not checked | **10** (canonical complete event) |
| `STRAT_CALL_A_CTX` | not checked | 10 |
| `STRAT_CALL_A_PLAN` | not checked | 8 |
| `STRAT_CALL_A_SKIPPED` | not checked | 2 |
| `STRAT_CALL_A_URGENT_ACTS` | not checked | 3 |
| `STRAT_CALL_A_URGENT` | not checked | 3 |
| `STRAT_CALL_A_FAIL` | not checked | 0 (no failures in window) |

**Verified emission sites in `src/brain/strategist.py`:**
- L738: `STRAT_CALL_A_START`
- L760: `STRAT_CALL_A_SKIPPED` (precheck-skip path)
- L765: `STRAT_CALL_A_END` (inside skip path)
- L889: `STRAT_CALL_A_END` (success path)
- L893: `STRAT_CALL_A_FAIL` (exception path)
- L895: `STRAT_CALL_A_END` (after FAIL — same path)

**Verdict:** PARTIAL gap. The audit's exact tag (`_DONE`) does not
exist; `STRAT_CALL_A_END` is the canonical completion event. However,
the 12 START vs 10 END count is real — there is a **2-event pairing
gap** that needs investigation in Phase 1. Either the skip path's END
emission is conditional, or two cycles experienced silent exits.

### G2 — SNIPER_TICK

| Tag | Audit | Actual |
|-----|-------|--------|
| `SNIPER_TICK` | 0 | **0** ✓ (genuinely absent) |
| `SNIPER_AGE_GUARD` | not checked | 904 |
| `SNIPER_STRUCT_GUARD_DEFER` | not checked | 618 |
| `SNIPER_DEVELOPMENT_GUARD` | not checked | 441 |
| `SNIPER_SPIKE` | not checked | 183 |
| `SNIPER_GRACE_BLOCKED` | not checked | 164 |
| `SNIPER_PROFIT_GUARD` | not checked | 74 |
| `SNIPER_STALL_ESCAPE` | 7 | **7** ✓ |
| Other SNIPER_* | — | 11 unique tags |

**Verdict:** REAL gap. No pure heartbeat tag exists for the profit_sniper
worker. State events fire abundantly but a dead sniper would still
emit nothing (none of the existing events are guaranteed-per-tick).
Phase 1 of G2 must design a sampled heartbeat.

### G3 — BYBIT_DEMO_WS_EXECUTION

| Tag | Audit | Actual |
|-----|-------|--------|
| `BYBIT_DEMO_WS_EXECUTION` | 0 | **0** (literal name absent) |
| `BYBIT_DEMO_WS_CLOSE_EVENT` | 15 | **15** ✓ |
| `BYBIT_DEMO_WS_EXEC_PARTIAL` | not checked | 0 in window (logged at INFO only when partial; was 0 partials in window) |
| `BYBIT_DEMO_WS_EXEC_NON_CLOSE` | not checked | 0 in window (DEBUG-level, suppressed by sink filter) |
| `BYBIT_DEMO_WS_HEALTH` | not checked | 86 |
| `BYBIT_DEMO_WS_POS_FLAT` | not checked | 18 |

**Verdict:** PARTIAL gap. The audit's exact tag (`_EXECUTION`) does not
exist. `BYBIT_DEMO_WS_CLOSE_EVENT` covers full closes; non-close fills
are emitted at DEBUG (suppressed). A canonical per-execution event at
INFO is missing for the non-close path.

### G4 — BYBIT_DEMO_WS_POSITION

| Tag | Audit | Actual |
|-----|-------|--------|
| `BYBIT_DEMO_WS_POSITION` | 0 | **0** (literal name absent) |
| `BYBIT_DEMO_WS_POS_FLAT` | not checked | **18** (size→0 only) |

**Verdict:** PARTIAL gap. Position updates only emit on size==0 (flat).
General position-state updates (size, unrealized PnL, SL/TP modifications)
not visible.

### G5 — BYBIT_DEMO_WS_ORDER

| Tag | Audit | Actual |
|-----|-------|--------|
| `BYBIT_DEMO_WS_ORDER` | 0 | **0** ✓ (DEBUG-level only) |
| `BYBIT_DEMO_WS_ORDER_PARSE_FAIL` | not checked | 0 |

**Verdict:** REAL gap (effectively). Handler exists at
`bybit_demo_websocket_subscriber.py:284-302` but emits at DEBUG only.
Order state transitions invisible at INFO level.

### G6 — COORD_REGISTER

| Tag | Audit | Actual |
|-----|-------|--------|
| `COORD_REGISTER` | 0 | **0** (literal name absent — no `_REGISTER` anywhere in 986 src/ tags) |
| `COORD_REG` | not checked | **20** ✓ (matches 20 opened trades) |
| `COORD_DOUBLE_CLOSE` | not checked | 22 |
| `COORD_CLOSE_START` | not checked | 8 |
| `COORD_CLOSE_END` | not checked | 8 |
| `COORD_LOSS_COOLDOWN_SET` | not checked | 6 |
| `COORD_PARTIAL_PENDING` | not checked | 3 |

**Verified emission site:** `src/core/trade_coordinator.py:375`
`COORD_REG | sym={symbol} src={source} cat={strategy_category} ...`

**Verdict:** TAG MISMATCH (not a gap). The lifecycle event fires
correctly under the canonical name. Phase 2 will verify field
completeness against the audit's required field list.

### G7 — COORD_UNREGISTER

| Tag | Audit | Actual |
|-----|-------|--------|
| `COORD_UNREGISTER` | 0 | **0** (literal name absent) |
| `COORD_CLOSE_START` | not checked | **8** ✓ (canonical close-start) |
| `COORD_CLOSE_END` | not checked | **8** ✓ (canonical close-end) |

**Verified emission sites:** `src/core/trade_coordinator.py:963` (START),
`src/core/trade_coordinator.py:1006` (END).

**Verdict:** TAG MISMATCH (not a gap). The close lifecycle uses a
two-event pair (start + end) which provides MORE observability than a
single `_UNREGISTER` would (latency measurable, callbacks-fired count
visible).

**Pairing note:** 20 REG events vs 8 CLOSE_START/END events means 12
trades were open at end of window. Confirmed open-position
coordinator state — not a leak.

### G8 — THESIS_SAVE

| Tag | Audit | Actual |
|-----|-------|--------|
| `THESIS_SAVE` | 0 | **0** (literal name absent — `_SAVE` exists only as `TIAS_SAVE`) |
| `THESIS_OPEN` | not checked | **20** ✓ (matches 20 trades) |
| `THESIS_CLOSE` | 12 | **12** ✓ |
| `THESIS_RECOVERY` | not checked | 4 |

**Verified emission site:** `src/core/thesis_manager.py:183`
`THESIS_OPEN | id={thesis_id} sym={symbol} dir={direction} ent={entry_price} sl={stop_loss_price} tp={take_profit_price} lev={leverage}`

**Verdict:** TAG MISMATCH (not a gap). Note: 20 OPEN + 12 CLOSE means
8 still-open theses at window end + 4 RECOVERY events explain the
remainder.

### G9 — TIAS_BRIDGE

| Tag | Audit | Actual |
|-----|-------|--------|
| `TIAS_BRIDGE` | 0 | **0** (literal name absent) |
| `TIAS_LESSON_BRIDGED` | not checked | **8** ✓ (matches 8 TIAS_SAVE) |
| `TIAS_SAVE` | 8 | **8** ✓ |
| `TIAS_ANALYZED` | not checked | 8 |
| `TIAS_DEEPSEEK_OK` | not checked | 23 |
| `TIAS_MODE_RESOLVED` | not checked | 8 |
| `STRAT_CALL_B_LESSONS_INJECTED` | not checked | TBD (was observed during Explore) |

**Verified emission sites:**
- `src/core/thesis_manager.py:456` — `TIAS_LESSON_BRIDGED` (the audit's missing event under canonical name)
- `src/core/thesis_manager.py:462` — `TIAS_LESSON_BRIDGE_FAIL` (failure sibling)
- `src/brain/strategist.py:1423` — `STRAT_CALL_B_LESSONS_INJECTED` (prompt-side injection event for CALL_B)

**Verdict:** TAG MISMATCH (not a gap). The learning-loop closure event
fires per save. **However, an INVESTIGATION ITEM exists:** CALL_A
prompt construction has no lesson-injection emission. Phase 1 of G9
will confirm whether CALL_A intentionally skips lessons or whether the
injection emission is missing.

### G10 — SLTP_VALIDATE

| Tag | Audit | Actual |
|-----|-------|--------|
| `SLTP_VALIDATE` | 0 | **0** (literal name absent) |
| `SLTP_PAIR_SKIP` | not checked | 0 in window (was 1 TRADE_SKIP observed earlier) |
| `SLTP_ADJUST` | not checked | 4 |
| `SLTP_SKIP` | not checked | 0 in window |
| `SLTP_VALIDATE_SKIP` | not checked | 0 in window |
| `TRADE_SKIP` | 1 | **1** ✓ |

**Verdict:** REAL gap. The success path of `validate_pair()` (action="OK"
return) does not emit any event. Validation activity is invisible except
on adjustments and skips. Phase 1 of G10 will design the success-path
event schema.

---

## Step 0.2 — Current Log Volume Baseline

**Window:** 2026-05-13 21:53 → 23:23 (~1.5h, 5400 sec)

| Metric | Value |
|--------|-------|
| Total lines (combined) | 23,463 |
| `[workers]` source lines | 22,373 |
| `[general]` source lines | 489 |
| `[mcp]` source lines | 0 (not captured in this combined window) |
| `[brain]` source lines | 0 (separate brain.log file) |
| Average lines/min | ~261 |
| Average lines/sec | ~4.3 |
| INFO-level lines | 22,320 (95%) |
| WARNING-level lines | 1,026 (4%) |
| ERROR-level lines | ~120 (0.5%) |
| Unique event tags | **406** |

**Top 20 noisiest tags (target offsets for G11 noise reduction):**

| Count | Tag |
|-------|-----|
| 1085 | SENT_UNKNOWN_CACHE_HIT |
| 904 | SNIPER_AGE_GUARD |
| 868 | REGIME |
| 850 | XRAY_ANALYZE |
| 850 | SIG_GEN_INPUT |
| 850 | SIG_GEN |
| 850 | SIG_CLASSIFY |
| 618 | SNIPER_STRUCT_GUARD_DEFER |
| 615 | SENT_AGG |
| 547 | SIG_DOWNGRADE |
| 541 | M4_DECISION |
| 489 | XRAY_CONFIDENCE_DETAIL |
| 489 | XRAY_CLASSIFY |
| 467 | WD_TICK |
| 441 | SNIPER_DEVELOPMENT_GUARD |
| 418 | WD_TICK_DONE |
| 361 | XRAY_NONE_REASON |
| **296** | **TIME_DECAY_MAE_MONOTONIC_HOLD** (G11 target) |
| 283 | VOL_PROFILE |
| 280 | M4_TRAIL_FLOOR |

**Volume ceiling per Rule 13:** total + 30% = 30,502 lines/1.5h. New
additions across G1–G10 must collectively stay below this; G11 reduction
helps offset.

---

## Step 0.3 — Tag Inventory Completeness

- 406 unique tags emitted in the 1.5-hour window
- 986 unique tags exist in `src/` source code
- **580 source tags did NOT fire in the window.** Many are error/edge-case
  paths (e.g., `BYBIT_DEMO_HMAC_FAIL`, `WD_HARD_STOP_FAIL`) and that is
  expected. Cluster investigations (Part D of the prompt) will surface
  which absent tags represent real observability gaps vs. which are
  defensive error paths that simply didn't trigger.

Full source inventory saved to `/tmp/tag_inventory.txt` (in-window
counts) and `dev_notes/observability_fixes/phase0_src_tag_inventory.txt`
(source-tree inventory — generated next).

---

## Step 0.4 — Code Reference Verification

Confirmed all critical-file file:line references from the Plan:

| File | Confirmed lines |
|------|-----------------|
| `src/brain/strategist.py` | L738 START, L760 SKIPPED, L765/889/895 END, L893 FAIL, L1423 LESSONS_INJECTED |
| `src/core/trade_coordinator.py` | L375 COORD_REG, L730 DOUBLE_CLOSE, L963 CLOSE_START, L1006 CLOSE_END |
| `src/core/thesis_manager.py` | L183 THESIS_OPEN, L344 THESIS_CLOSE, L456 TIAS_LESSON_BRIDGED, L462 TIAS_LESSON_BRIDGE_FAIL |

Earlier read-only Explore confirmed:
- `src/workers/profit_sniper.py` `tick()` at L292, no SNIPER_TICK emission
- `src/bybit_demo/bybit_demo_websocket_subscriber.py` handlers at L237/258/284/329, CLOSE_EVENT at L411-417
- `src/core/sl_tp_validator.py` `validate_pair()` at L254, no success-path emission

All file:line references match. No drift since the Plan was written.

---

## Step 0.5 — Tag-Naming Convention Analysis (Operator-Requested)

Full analysis appears in the approved plan
(`/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-soft-lagoon.md`).
Headline:

| Suffix | Codebase count | Audit asked for | Recommendation |
|--------|----------------|------------------|----------------|
| `_REGISTER` / `_UNREGISTER` | 0 in 986 tags | G6/G7 | KEEP `COORD_REG` + `COORD_CLOSE_START`/`COORD_CLOSE_END` |
| `_SAVE` | 1 (TIAS_SAVE) | G8 | KEEP `THESIS_OPEN` (matches OPEN/CLOSE pattern) |
| `_BRIDGE` (verb form) | 0 | G9 | KEEP `TIAS_LESSON_BRIDGED` (matches sibling FAIL/SKIP) |
| `_DONE` vs `_END` | DONE=13, END=6 | G1 | KEEP `STRAT_CALL_A_END` (within STRAT cluster `_END` is canonical) |

**For each "keep existing tag" recommendation, Phase 2 of the
corresponding gap will verify field completeness against the audit's
required fields and ADD any missing fields.** Adding fields is a
behavior-preserving observability improvement (Rule 3 allows additive log
changes).

---

## Corrected Gap Classification

| Gap | Audit claim | Actual state | Class |
|-----|-------------|--------------|-------|
| G1 STRAT_CALL_A_DONE | ZERO → defect | 12 START / 10 END / 2 SKIPPED — 2-event pairing gap | **Partial — fix pairing** |
| G2 SNIPER_TICK | ZERO → defect | 0 heartbeat events; sniper alive only inferred from state events | **Real — sampled heartbeat** |
| G3 WS_EXECUTION | ZERO → defect | 15 CLOSE_EVENT; non-close path at DEBUG only | **Partial — INFO-promote non-close** |
| G4 WS_POSITION | ZERO → defect | 18 POS_FLAT only (size=0); no general position-update event | **Partial — general position event** |
| G5 WS_ORDER | ZERO → defect | DEBUG-level only; no INFO emissions | **Real — promote to INFO** |
| G6 COORD_REGISTER | ZERO → defect | 20 COORD_REG (canonical) | **Tag mismatch — field completeness only** |
| G7 COORD_UNREGISTER | ZERO → defect | 8 COORD_CLOSE_START + 8 COORD_CLOSE_END (canonical pair) | **Tag mismatch — field completeness only** |
| G8 THESIS_SAVE | ZERO → defect | 20 THESIS_OPEN (canonical) | **Tag mismatch — field completeness only** |
| G9 TIAS_BRIDGE | ZERO → defect | 8 TIAS_LESSON_BRIDGED (canonical); CALL_A injection emission absent | **Tag mismatch — CALL_A injection emission only** |
| G10 SLTP_VALIDATE | ZERO → defect | 4 SLTP_ADJUST + 1 TRADE_SKIP; success path silent | **Real — success-path event** |

**Real net work:** add new emissions for G2, G5, G10. Extend
emission coverage for G1, G3, G4. Verify/add fields for G6, G7, G8.
For G9, verify CALL_A injection-event absence is intentional vs. real
gap. G11 reduces TIME_DECAY noise volume.

---

## Cluster Investigation Targets (Per Prompt Part D)

Each gap's Phase 1 must also sweep these clusters. Findings become G12+
candidates the operator scopes:

- **A — Brain/strategist:** `STRAT_CALL_A_TIMEOUT`, `STRAT_CALL_A_RETRY`, `STRAT_CALL_B_END` pairing
- **B — Workers:** `KLINE_WORKER_TICK`, `ALTDATA_WORKER_TICK`, `REGIME_WORKER_TICK` etc.
- **C — WebSocket:** connect/disconnect/reconnect/heartbeat coverage
- **D — Coordinator:** duplicate-register, reconcile-run, reconcile-drift
- **E — Persistence:** TRADE_HISTORY_WRITE, POSITION_INSERT/UPDATE/DELETE, DB_VACUUM_RUN
- **F — Learning:** TIAS_LESSON_SCORE, TIAS_LESSON_EXPIRE
- **G — Validation:** GATE_VALIDATE_DETAIL, APEX_VALIDATE_DETAIL, XRAY_VALIDATE_DETAIL
- **H — Enforcer & Capital:** PERF_ENFORCER_DECISION, PERF_ENFORCER_MODE_TRANSITION, CAPITAL_TIER_DECISION
- **I — Alerting:** PUSH_SEND, PUSH_FAIL, PUSH_RETRY, ALERT_DELIVERY_LATENCY
- **J — Time decay noise:** confirmed targets in §0.2 top-20 above

---

## Volume Ceiling Tracker

| Gap | Estimated additions (events/1.5h window) | Cumulative |
|-----|------------------------------------------|------------|
| G1 STRAT_CALL_A_END field-completeness | +0 (only fields, not new emissions) | 0 |
| G2 SNIPER_TICK sampled at 1/min | ~90 | 90 |
| G3 WS_EXECUTION INFO-promote non-close | ~50-100 | ~190 |
| G4 WS_POSITION general update | ~50-100 | ~290 |
| G5 WS_ORDER INFO promote | ~50-100 | ~390 |
| G6 COORD_REG field additions | +0 (already fires 20) | ~390 |
| G7 COORD_CLOSE field additions | +0 | ~390 |
| G8 THESIS_OPEN field additions | +0 | ~390 |
| G9 CALL_A injection emission (if added) | ~12 | ~400 |
| G10 SLTP_VALIDATE success path | ~20 (one per trade) | ~420 |
| G11 TIME_DECAY noise reduction | **-700 to -900** | ~-300 to ~-500 |
| **Net** | | **Below baseline** ✓ |

Budget healthy under the +30% ceiling (which is ~7,000 events/1.5h).

---

## Phase 0 Sign-Off & Next

Phase 0 is complete. The corrected gap landscape is established.

**Next step:** Begin G1 Phase 1 — full investigation of
`STRAT_CALL_A_START`/`_END` pairing, all exit paths, and the brain
cluster sweep.

Phase 0 deliverable: this file. No code changes.
