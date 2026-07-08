# Gap 3 Phase 1 Step 3.5 — Synthesis + Recommendation

Date: 2026-05-19  
Phase: Gap 3 Phase 1 — Investigation complete. Phase 2 operator decision gate next.

## Investigation summary

Three sub-investigations produced 4 prior dev_notes. This synthesis consolidates:
- `gap3_phase1_blocker_inventory.md` — 13 rejection points + 3 informational events + 2 orphaned events
- `gap3_phase1_directive_lifecycle.md` — full flow from STRAT_DIRECTIVE to terminal outcome; 5 chokepoints in layer_manager.py
- `gap3_phase1_design_options.md` — Option A vs B vs C evaluation
- `gap3_phase1_did_propagation.md` — contextvars suffices; empirically verified

## Recommendation

**Option A: single `STRAT_DIRECTIVE_REJECTED` event emitted at orchestration entry (`src/core/layer_manager.py:_execute_new_trades`), with `rsn` field carrying the specific blocker name and `did` from contextvars.**

## Where exactly should rejection events fire? (5 emit sites)

All in `src/core/layer_manager.py:_execute_new_trades`:

1. **Pre-loop halt path** (around line 1303): if `pnl_manager.can_trade()` returns False OR enforcer halts, emit one event per pending directive in `plan.new_trades` with `rsn=halt blocker_layer=halt`. Then early-return as today.
2. **Invalid directive** (immediately before `continue` at line 1437): `rsn=invalid_directive blocker_layer=orchestration`.
3. **Position gate** (immediately before `continue` at line 1449): `rsn=pos_gate blocker_layer=orchestration detail='<open_position|executing>'`.
4. **Gate rejected** (immediately before `continue` at line 1486): `rsn=gate_rejected blocker_layer=gate detail='<_gate_rejected string from gate.py>'`.
5. **Strategy worker reject** (immediately after `_execute_claude_trade` returns `success=False` at line 1494, before the `_bump_skip` call): `rsn=<_reason_code from strategy_worker> blocker_layer=strategy_worker`.
6. **Exception** (immediately before the existing TRADE_SKIP rsn=exception at line 1509): `rsn=exception blocker_layer=orchestration detail='<truncated exception>'`.

Net: 5-6 emit additions across ~80 lines in one file.

## Which design option? Option A.

Rationale:
- Closes Gap 3 fully (every silent skip becomes a single grep-able event)
- Follows existing `TRADE_SKIP rsn=X` precedent — operator already knows this pattern
- Lowest implementation risk (one file, ~6 emits, zero behavior change)
- Lowest log volume (1 event per rejection, INFO level)
- Minimum surface for Rule 11 invariant verification

Option B (per-blocker typed events) is a cosmetic variant of A with worse grep ergonomics.
Option C (full lifecycle EMITTED/PICKED_UP/EVALUATED/ACCEPTED/REJECTED chain) is an enhancement that can be ADDED on top of A in a future iteration if timing observability becomes valuable. Not needed to close Gap 3.

## How to propagate did? Contextvars suffice.

`new_decision_id()` at `src/core/log_context.py:48-52` sets `_decision_id` contextvar when called by brain. The contextvar propagates through:
- Same coroutine (across await points): guaranteed
- Awaited subroutines: guaranteed
- Task creation: snapshot inherited

The directive lifecycle runs entirely in one async chain (brain cycle → layer_manager → gate.validate → strategy_worker → bybit_demo). Empirical trial-log evidence shows the same did flowing 16-19 seconds through the chain.

**Belt-and-suspenders recommendation**: snapshot `did` at loop entry via `_loop_did = get_did()`, include both `did={_loop_did}` and `{ctx()}` in each emit. Defensive coding with no downside.

## Aim-bias evaluation (Rule 9, all 5 questions)

1. **Preserves trade frequency?** YES — pure observability, zero behavior change. Rejection counts pre/post identical.
2. **Preserves aggression?** YES — no new gates, no new blockers.
3. **Improves decision quality?** YES — operator gains visibility into why directives are absorbed; can investigate patterns and tune accordingly.
4. **Preserves passive-close advantage?** YES — close path completely untouched.
5. **Respects structural separation of concerns?** YES — orchestration events emitted from the orchestration layer (layer_manager). No cross-layer reach.

**5/5 YES.** Fix is aim-aligned.

## Trial behavior specification (Rule 14)

After deployment, the following scenarios must produce the expected events:

### Scenario 1: pnl_manager halt
- **Trigger**: brain emits 3 directives; pnl_manager halts before iteration starts.
- **Expected events**:
  - 1× `BRAIN_TRADE_HALT | rsn=<can_trade reason>` (existing)
  - 3× `STRAT_DIRECTIVE_REJECTED | sym=<each> rsn=halt blocker_layer=halt | did=<same for all 3>` (NEW)
- **Verifier**: grep both events, count matches.

### Scenario 2: gate rejects via CHECK 6b reentry learning gate
- **Trigger**: brain emits a Buy directive for HYPEUSDT shortly after a Buy loss in same regime/setup.
- **Expected events**:
  - 1× `STRAT_DIRECTIVE | sym=HYPEUSDT dir=Buy | did=<X>` (existing)
  - 1× `TRADE_SKIP | sym=HYPEUSDT rsn=gate_rejected detail='reentry_learning_gate_...' | did=<X>` (existing)
  - 1× `STRAT_DIRECTIVE_REJECTED | sym=HYPEUSDT dir=Buy rsn=gate_rejected blocker_layer=gate detail='reentry_learning_gate_same_conditions' | did=<X>` (NEW)
- **Verifier**: all three carry same did; grep STRAT_DIRECTIVE_REJECTED tells the full story in one line.

### Scenario 3: strategy_worker xray_skip
- **Trigger**: brain emits directive; strategy_worker rejects because xray check fails.
- **Expected events**:
  - 1× existing TRADE_SKIP rsn=xray_skip
  - 1× NEW STRAT_DIRECTIVE_REJECTED rsn=xray_skip blocker_layer=strategy_worker
- **Verifier**: same did, same sym.

### Scenario 4: success path
- **Trigger**: brain emits directive; everything passes.
- **Expected events**:
  - existing STRAT_DIRECTIVE
  - existing BYBIT_DEMO_ORDER_RECEIVED
  - **NO STRAT_DIRECTIVE_REJECTED** (only rejections fire the new event)
- **Verifier**: confirm absence.

### Scenario 5: invariants preserved
- All 4 fix-series boot sentinels still fire after restart
- Phase 1A/1B config still in effect (cap disabled, flip thresholds 0.70)
- Trade frequency unchanged (count of BYBIT_DEMO_ORDER_RECEIVED per hour unchanged)
- Direction distribution unchanged (50/50 if Layer 2/3 re-enabled)
- DB cascade absence (Rule 13)

## Implementation surface

**Branch**: `fix/gap3-directive-lifecycle` (per spec Rule 8)

**Files modified (Phase 3)**:
- `src/core/layer_manager.py` — 5-6 log emit additions in `_execute_new_trades` (~10-15 lines added)
- `tests/test_gap3_directive_lifecycle.py` (NEW) — 5 scenarios × 2-3 assertions each (~80-120 lines)
- `dev_notes/gaps_fix/gap3_phase4_verification.md` — verification report (post-deploy)

**Files NOT modified (per Rule 11)**:
- `src/brain/strategist.py` (out of scope per spec line 9)
- `src/apex/gate.py` (no behavior change; CHECK 1-14 logic untouched)
- `src/apex/optimizer.py` (no behavior change)
- `src/workers/strategy_worker.py` (no behavior change; internal TRADE_SKIPs unchanged)
- `src/intelligence/signals/signal_generator.py` (SIG_DOWNGRADE correctly excluded from scope)
- `src/core/trade_coordinator.py` (COORD_LOSS_COOLDOWN_SET correctly excluded from scope)
- `src/core/log_context.py` (contextvars mechanism unchanged)

**Commit plan** (per spec line 454-462):
- `gap3/p3-1: add STRAT_DIRECTIVE_REJECTED event at orchestration entry (halt + invalid + pos_gate paths)`
- `gap3/p3-2: wire event at gate-rejected path with detail from _gate_rejected`
- `gap3/p3-3: wire event at strategy_worker reject path with reason_code`
- `gap3/p3-4: wire event at exception path`
- `gap3/p3-5: add belt-and-suspenders did snapshot at loop entry`
- `gap3/p3-6: tests + deploy + verify`

Stacked atomic commits per Rule 8. Each commit reviewable independently.

## Risks

- **Risk per spec Risk 2** (log volume increase): MITIGATED — Option A emits only on rejection, ~5-30 events/hour based on trial data. Negligible volume.
- **Risk per spec Risk 5** (cross-fix interactions): LOW — single file, no shared mutable state.
- **Risk per spec Risk 10** (hidden interactions with previously-shipped fixes): MITIGATED — all 4 fix-series boot sentinels verified continuing to fire in trial; pure additive log emits cannot break behavior.
- **Risk new (logging only at orchestration layer might miss directives consumed elsewhere)**: There are no other directive consumers — verified by grep. plan.new_trades is iterated only in `_execute_new_trades`.

## Open questions for operator (Phase 2 decision)

1. **Approve Option A** (recommended) — or choose Option B or C?
2. **Approve belt-and-suspenders did snapshot** at loop entry? (recommended, no downside)
3. **Approve emit on halt-path** (one event per pending directive when halt drops the queue)? Or skip the halt scenario (events only on per-directive rejection)?
4. **Approve commit plan** as listed (6 commits)? Or compress into 2-3 commits?

Implementation cannot begin until operator approves at this Phase 2 gate.

## What success looks like

After Gap 3 ships and verifies:
- `grep STRAT_DIRECTIVE_REJECTED data/logs/workers.log` shows every silent skip with originating did + reason
- Operator can correlate rejected directives to brain cycles using did
- The "silent absorption" pattern (e.g., HYPEUSDT batches 12-14 during the 2026-05-19 trial) becomes visible without manual log correlation
- 100% of TRADE_SKIP events have a corresponding STRAT_DIRECTIVE_REJECTED (one-to-one)
- 0 STRAT_DIRECTIVE_REJECTED events have `no_ctx` (did always present)
- Operator confidence in directive lifecycle visibility increases; future debugging is faster

## Reference paths

- This synthesis: `dev_notes/gaps_fix/gap3_phase1_synthesis.md`
- Blocker inventory: `dev_notes/gaps_fix/gap3_phase1_blocker_inventory.md`
- Lifecycle trace: `dev_notes/gaps_fix/gap3_phase1_directive_lifecycle.md`
- Design options: `dev_notes/gaps_fix/gap3_phase1_design_options.md`
- Did propagation: `dev_notes/gaps_fix/gap3_phase1_did_propagation.md`
- Baseline: `dev_notes/gaps_fix/phase0_baseline.md`
- Approved plan: `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-nifty-toast.md`
- Spec: `/home/inshadaliqbal786/IMPLEMENT_THREE_GAPS_FIX.md`

End of synthesis. Phase 2 operator decision gate is open.
