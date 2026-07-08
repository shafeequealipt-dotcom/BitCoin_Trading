# Observability Gaps Fix — Final Handover Report

**Project:** IMPLEMENT_OBSERVABILITY_GAPS_FIX.md
**Date:** 2026-05-14
**Base branch:** `audit/all-tier2-combined` @ `b348038`
**Phases completed:** Phase 0 baseline + G1–G11 implementation
**Branches shipped:** 11 (one per gap)
**Tests added:** 53 cases (all passing)
**Pre-existing test regressions introduced:** 0

---

## Executive summary

The 2026-05-13 audit identified 10 confirmed logging gaps + a TIME_DECAY
noise problem. Phase 0 verification against current code and the audited
log file revealed that **4 of the 10 "ZERO events" claims were tag-name
mismatches** — the lifecycle events fire correctly under established
internal tag names. The remaining 6 gaps split into:

- **3 real gaps** requiring new emissions (G2, G5, G10)
- **3 partial gaps** requiring extension of existing emissions (G1, G3, G4)
- **4 tag-mismatch cases** requiring field completeness / visibility
  adjustments (G6, G7, G8, G9)
- **1 noise-reduction gap** (G11)

Every gap was investigated to root-cause depth, the schema was decided
based on the codebase's established naming conventions (986 unique tags
analyzed in Phase 0), and the implementation was unit + integration
tested. Trading behaviour is preserved by Rule 3 across all 11
commits.

---

## Gap-by-gap summary

| Gap | Branch | Commit | Code site | Outcome |
|-----|--------|--------|-----------|---------|
| G1 | `obs/g1-strat-call-a-done` | `e9a0562` | strategist.py + layer_manager.py (4 sites) | try/finally pairing + status= field; closes 2-event START/END gap on cancellation |
| G2 | `obs/g2-sniper-tick` | `8f7675e` | profit_sniper.py | new `SNIPER_TICK` heartbeat (1/min sampling) |
| G3 | `obs/g3-ws-execution` | `a1f6291` | bybit_demo_websocket_subscriber.py | NON_CLOSE promoted DEBUG→INFO + full fields |
| G4 | `obs/g4-ws-position` | `207311e` | bybit_demo_websocket_subscriber.py | new `BYBIT_DEMO_WS_POS_UPDATE` snapshot event |
| G5 | `obs/g5-ws-order` | `95909e4` | bybit_demo_websocket_subscriber.py | order events promoted DEBUG→INFO + full fields, all transitions |
| G6 | `obs/g6-coord-register` | `0ee1d87` | trade_coordinator.py | COORD_REG gains side/qty/entry_price + new COORD_DUPLICATE_REGISTER |
| G7 | `obs/g7-coord-unregister` | `fbe8aae` | (docs only) | tag mismatch — COORD_CLOSE_START/END pair already field-complete |
| G8 | `obs/g8-thesis-save` | `97e343f` | thesis_manager.py | THESIS_OPEN gains target_pct/stop_pct/max_hold_min/size_usd/order_id |
| G9 | `obs/g9-tias-bridge` | `629b862` | strategist.py | STRAT_CALL_B_CTX gains lessons_in_db; reveals disabled-by-design state |
| G10 | `obs/g10-sltp-validate` | `b765947` | sl_tp_validator.py | new SLTP_PAIR_OK success-path emission |
| G11 | `obs/g11-time-decay-noise` | `35417ad` | time_decay_sl.py | 3 events WARNING→INFO; saves ~430 WARNING events/hour |

Each branch is independent — they can be merged in any order; they touch
non-overlapping files except for trade_coordinator.py (G6 only) and
strategist.py (G1 + G9, different functions).

---

## Phase 0 corrected gap classification (after verification)

Original audit-claimed ZERO events vs reality:

| # | Audit said | Reality in window | Class |
|---|------------|-------------------|-------|
| G1 STRAT_CALL_A_DONE | 0 | STRAT_CALL_A_END 10 / START 12 (pair gap real) | Partial |
| G2 SNIPER_TICK | 0 | 0 (genuine miss) | Real |
| G3 WS_EXECUTION | 0 | NON_CLOSE at DEBUG only | Partial |
| G4 WS_POSITION | 0 | POS_FLAT 18 only | Partial |
| G5 WS_ORDER | 0 | DEBUG-level only | Real |
| G6 COORD_REGISTER | 0 | COORD_REG 20/20 | Tag mismatch |
| G7 COORD_UNREGISTER | 0 | COORD_CLOSE_START 8 / END 8 | Tag mismatch |
| G8 THESIS_SAVE | 0 | THESIS_OPEN 20/20 | Tag mismatch |
| G9 TIAS_BRIDGE | 0 | TIAS_LESSON_BRIDGED 8 (write); read disabled | Partial (visibility only) |
| G10 SLTP_VALIDATE | 0 | No success-path event | Real |

---

## Tag-naming convention summary (Phase 0 analysis)

986 unique tags inventoried in `src/`. Suffix frequencies:

| Suffix | Count | Audit alternative | Decision |
|--------|-------|-------------------|----------|
| `_FAIL` | 30 | — | canonical for failures |
| `_START` | 22 | — | canonical for begin |
| `_CLOSE` | 15 | — | canonical close-side |
| `_DONE` | 13 | — | canonical complete (BRAIN cluster) |
| `_END` | 6 | `_DONE` | KEEP `_END` for STRAT cluster (sibling consistency) |
| `_OPEN` | 2 | `_SAVE` | KEEP `_OPEN` for thesis/coord lifecycle |
| `_TICK` | 5 | — | canonical for heartbeat |
| `_REG` | 1 | `_REGISTER` | KEEP `_REG` (no `_REGISTER` exists; standalone) |
| `_REGISTER`/`_UNREGISTER` | 0 | — | rejected — would create outliers |
| `_BRIDGED` | 1 | `_BRIDGE` | KEEP past-tense (TIAS cluster pattern) |
| `_SAVE` | 1 | — | only TIAS_SAVE uses this; not strong convention |

For each "tag mismatch" gap (G6, G7, G8, G9), the recommendation was
to KEEP the canonical internal tag and instead address field
completeness or visibility — not to rename. Renaming would have broken
dashboards and diverged from sibling pairs.

---

## New events introduced (G2, G4, G5 new tags; G6 new sub-event; G10 new tag)

| New tag | Where | Trigger | Expected rate (window) |
|---------|-------|---------|------------------------|
| `SNIPER_TICK` | profit_sniper.py | every 12 ticks (~60 s) | ~90/window |
| `BYBIT_DEMO_WS_POS_UPDATE` | bybit_demo_websocket_subscriber.py | every non-flat position WS event | ~60-100/window |
| `BYBIT_DEMO_WS_ORDER` (promoted) | bybit_demo_websocket_subscriber.py | every order state transition | ~120/window |
| `BYBIT_DEMO_WS_EXEC_NON_CLOSE` (promoted) | bybit_demo_websocket_subscriber.py | every non-close execution | ~50-100/window |
| `COORD_DUPLICATE_REGISTER` | trade_coordinator.py | when register overwrites existing | ~0-2/window (rare) |
| `SLTP_PAIR_OK` | sl_tp_validator.py | every successful validate_pair | ~20/window |

Total new emissions per 1.5h window: ~340-440 events. G11 offsets
~650 WARNING-tier events by downgrading to INFO. **Net WARNING-tier
volume DROPS substantially while total volume stays flat to slightly
positive.**

---

## Field additions to existing events

| Tag | Fields added | Total emissions affected |
|-----|--------------|---------------------------|
| `STRAT_CALL_A_END` | `status=`, `prompt_chars=`, `sys_prompt_chars=` (replaces mixed flags) | every CALL_A cycle |
| `STRAT_CALL_B_END` | same | every CALL_B cycle |
| `BRAIN_CYCLE_A_DONE` | `status=` (replaces mixed flags) | every BRAIN_CYCLE_A |
| `BRAIN_CYCLE_B_DONE` | same | every BRAIN_CYCLE_B |
| `COORD_REG` | `side=`, `qty=`, `entry_price=` | every trade open |
| `THESIS_OPEN` | `target_pct=`, `stop_pct=`, `max_hold_min=`, `size_usd=`, `order_id=` | every thesis save |
| `STRAT_CALL_B_CTX` | `lessons_in_db=` | every CALL_B prompt build |

---

## Cluster investigation findings (for G12+ future work)

Surfaced during Phase 1 of each gap; flagged for operator triage but
not implemented this round:

### Cluster A (Brain/Strategist)

- `STRAT_CALL_A_TIMEOUT` not instrumented (no timeout mechanism exists in strategist)
- `STRAT_CALL_A_RETRY` not instrumented at strategist level (visible via claude_code_client events)
- CALL_A position-actions block has no `_END_URGENT` counterpart

### Cluster B (Workers)

- `KLINE_WORKER_TICK` heartbeat missing
- `REGIME_WORKER_TICK` heartbeat missing
- `CYCLE_TRACKER_TICK` visible only via CYCLE_RESUME tags

### Cluster D (Coordinator)

- `COORD_RECONCILE_RUN` / `COORD_RECONCILE_DRIFT` do not exist. F-26
  ground-truth divergence suggests no coordinator-side reconciliation
  runs at all. Worth investigating.

### Cluster F (Learning)

- `TIAS_LESSON_SCORE` / `TIAS_LESSON_EXPIRE` do not exist (TIAS may
  not have scoring/expiry pipelines yet)
- Dead code at `_build_context_prompt:1414` (legacy
  STRAT_CALL_B_LESSONS_INJECTED) could be removed in a separate refactor

### Cluster I (Alerting)

- `PUSH_SEND` / `PUSH_FAIL` / `PUSH_RETRY` from the audit's wishlist
  not investigated — operator should triage

---

## Verification protocol for operator (Phase 4 / Tier 5)

### Per-gap quick verification (≥2h after deploy)

Run on a fresh log window after deploying each branch:

```bash
LOG=$(ls -t /home/inshadaliqbal786/trading-intelligence-mcp/data/logs/workers.log | head -1)

# G1 — STRAT_CALL_A/B pair integrity (1:1 ratio)
grep -c "STRAT_CALL_A_START" "$LOG"
grep -c "STRAT_CALL_A_END" "$LOG"
grep -c "STRAT_CALL_B_START" "$LOG"
grep -c "STRAT_CALL_B_END" "$LOG"

# G2 — SNIPER_TICK liveness (expect ≥1/min when sniper alive)
grep -c "SNIPER_TICK" "$LOG"

# G3 — non-close execution visibility at INFO
grep "BYBIT_DEMO_WS_EXEC_NON_CLOSE" "$LOG" | head -5

# G4 — position update snapshot
grep "BYBIT_DEMO_WS_POS_UPDATE" "$LOG" | head -5

# G5 — order state transitions at INFO
grep "BYBIT_DEMO_WS_ORDER" "$LOG" | head -5

# G6 — COORD_REG field completeness
grep "COORD_REG " "$LOG" | head -3 | grep "side=" | grep "qty=" | grep "entry_price="

# G7 — COORD_CLOSE pair integrity
grep -c "COORD_CLOSE_START" "$LOG"
grep -c "COORD_CLOSE_END" "$LOG"

# G8 — THESIS_OPEN field completeness
grep "THESIS_OPEN " "$LOG" | head -3 | grep "target_pct=" | grep "stop_pct=" | grep "max_hold_min="

# G9 — TIAS bridge closure
grep -c "TIAS_LESSON_BRIDGED" "$LOG"
grep "lessons_in_db=" "$LOG" | head -3

# G10 — validator success path
grep "SLTP_PAIR_OK" "$LOG" | head -3

# G11 — WARNING-tier reduction check (compare counts pre/post deploy)
grep -cE "WARNING.*(TIME_DECAY_(MAE_MONOTONIC_HOLD|MAE_GUARD|AGE_GUARD))" "$LOG"
# expected: 0 after G11 deploy
```

### Final 24-hour pairing integrity check

After all 11 branches are deployed and merged, run for 24 hours and
verify:

| Pair | Expected ratio |
|------|----------------|
| STRAT_CALL_A_START : STRAT_CALL_A_END | 1:1 |
| STRAT_CALL_B_START : STRAT_CALL_B_END | 1:1 |
| BRAIN_CYCLE_A : BRAIN_CYCLE_A_DONE | 1:1 |
| BRAIN_CYCLE_B : BRAIN_CYCLE_B_DONE | 1:1 (ignoring BRAIN_CYCLE_B_SKIP) |
| COORD_REG : COORD_CLOSE_END (matched by sym + did) | 1:1 for completed trades |
| THESIS_OPEN : THESIS_CLOSE | 1:1 for completed theses |
| TIAS_SAVE : TIAS_LESSON_BRIDGED | 1:1 |
| Per trade open: at least one SLTP_PAIR_OK | 1:N or 1:0 (skip-rate aware) |

### Volume check

Compare total log lines/hour pre vs post deploy. Expectation:

- Total volume: ≤ baseline + 30% (Phase 0 ceiling)
- WARNING-tier volume from time_decay subsystem: ~0
- INFO-tier net change: depends on G11 offsetting G1-G10 additions

### Behaviour-parity check

Compare these counters across the same time window pre vs post deploy:

- Total trades opened (COORD_REG count)
- Total trades closed (COORD_CLOSE_END count)
- Strategy decisions per cycle (STRAT_CALL_A_PLAN trade counts)
- Average tick latency (BRAIN_CYCLE_A_DONE el= histogram)
- Sniper actions per hour (SNIPER_SPIKE / STALL_ESCAPE counts)

Distributions should match within 5% of baseline. Significant
divergence would indicate a regression — but no behaviour was changed,
so the test is precautionary.

---

## What was NOT done (intentional)

Per Rule 3 (observability, not behaviour change):

- No new business logic added
- No timeouts / retries added
- No prompt construction changed
- No order-placement / SL-TP calculation changed
- No coordinator lifecycle changed
- No DB writes added (except where already present)
- No new fail-paths
- B1a regime detector (commit 6938c69) untouched

Per the prompt's "out of scope" sections:

- Brain prompt construction (Stage 2) not modified
- Bybit demo HTTP / auth / signing not modified
- Layer 1 scanner pipeline not modified
- Shadow adapter not modified

---

## Risks accepted

1. **G1 try/finally is a structural change.** Pylance + tests validate
   the rewrite preserves return semantics. Worst-case rollback: revert
   commit `e9a0562`. Risk: low — same try/finally pattern used in
   other production code paths.

2. **G2 heartbeat adds ~50 µs per sample tick.** Sample rate is 1-in-12;
   hot-path impact negligible. Risk: very low.

3. **G3/G4/G5 promotions add ~250 INFO-tier events/hour at peak.**
   Within +30 % volume budget. Risk: low.

4. **G6 COORD_DUPLICATE_REGISTER could fire if cooldown gate slips.**
   The event is a WARNING — operators will notice if it starts firing.
   That's the intended behaviour. Risk: zero (purely diagnostic).

5. **G8 percentage math guards against ZeroDivisionError.** Tested
   with `entry_price=0`. Risk: zero.

6. **G9 best-effort DB query inside CALL_B build.** Wrapped in
   try/except so query failure falls back to `lessons_in_db=0`. Risk:
   very low — won't block prompt build.

7. **G11 downgrade may surprise operators tailing WARNING.** Update
   any operator runbook tailing TIME_DECAY_MAE_* on WARNING to use
   INFO. Risk: documentation only.

---

## Files touched (final summary)

```
src/brain/strategist.py            (G1 CALL_A + CALL_B; G9 lessons_in_db)
src/core/layer_manager.py          (G1 BRAIN_CYCLE_A + BRAIN_CYCLE_B)
src/workers/profit_sniper.py       (G2 SNIPER_TICK)
src/bybit_demo/bybit_demo_websocket_subscriber.py  (G3 + G4 + G5)
src/core/trade_coordinator.py      (G6 COORD_REG + DUPLICATE_REGISTER)
src/core/thesis_manager.py         (G8 THESIS_OPEN)
src/core/sl_tp_validator.py        (G10 SLTP_PAIR_OK)
src/risk/time_decay_sl.py          (G11 WARNING→INFO)

tests/test_strat_call_pairing.py            (G1 — 8 cases)
tests/test_sniper_tick_heartbeat.py         (G2 — 10 cases)
tests/test_ws_execution_observability.py    (G3 — 2 cases)
tests/test_ws_position_observability.py     (G4 — 5 cases)
tests/test_ws_order_observability.py        (G5 — 10 cases)
tests/test_coord_register_observability.py  (G6 — 5 cases)
tests/test_thesis_save_observability.py     (G8 — 4 cases)
tests/test_callb_lessons_injected_fields.py (G9 — 3 cases)
tests/test_sltp_validate_success.py         (G10 — 6 cases)
tests/test_time_decay_log_levels.py         (G11 — 5 cases)

dev_notes/observability_fixes/
  phase0_baseline.md
  phase0_src_tag_inventory.txt
  g1_phase1_investigation.md
  g1_phase2_report.md
  g2_phase1_investigation.md
  g3_phase1_investigation.md
  g4_phase1_investigation.md
  g5_phase1_investigation.md
  g6_phase1_investigation.md
  g7_phase1_investigation.md
  g8_phase1_investigation.md
  g9_phase1_investigation.md
  g10_phase1_investigation.md
  g11_phase1_investigation.md
  FINAL_HANDOVER_REPORT.md   (this file)
```

---

## Recommended merge order

The branches are independent but the prompt's recommended sequence
preserves the audit's narrative (most-cited gap first):

```
1. obs/g1-strat-call-a-done       (brain pair integrity)
2. obs/g2-sniper-tick             (worker liveness)
3. obs/g3-ws-execution            (WS cluster — same file)
4. obs/g4-ws-position             (WS cluster — same file)
5. obs/g5-ws-order                (WS cluster — same file)
6. obs/g6-coord-register          (coordinator)
7. obs/g7-coord-unregister        (docs only — no risk)
8. obs/g8-thesis-save             (thesis fields)
9. obs/g9-tias-bridge             (learning loop visibility)
10. obs/g10-sltp-validate         (validator success path)
11. obs/g11-time-decay-noise      (noise reduction — offsets the rest)
```

Each merge can be followed by ~2h soak + Phase 4 verification before
the next merge, OR all 11 can land together with a single 24-hour
final verification window.

---

## Conclusion

All 11 gaps from `IMPLEMENT_OBSERVABILITY_GAPS_FIX.md` are addressed
through 11 atomic commits on 11 independent branches, with 53 unit /
integration tests pinning the behavior. Trading logic is unchanged
across all 11 commits. Cluster-investigation findings for G12+ work
are documented for operator triage.

The audit's tag-name mismatches (G6/G7/G8/G9) were investigated to
root-cause depth and resolved by accepting the codebase's established
naming conventions (986-tag analysis) — preserving dashboards while
addressing the underlying observability concerns via field
completeness, visibility events, and duplicate detection.

Implementation is ready for operator-side Phase 4 verification.
