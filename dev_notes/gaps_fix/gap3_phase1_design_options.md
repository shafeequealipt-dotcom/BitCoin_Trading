# Gap 3 Phase 1 Step 3.3 — Design Options

Date: 2026-05-19  
Scope: evaluate the three design options for the directive lifecycle event chain per spec line 406-425. Each option is rated on implementation complexity, observability gain, log volume cost, and the five aim-bias questions (Rule 9).

## Option A — Single `STRAT_DIRECTIVE_REJECTED` event at orchestration entry

Centralized event emission at `src/core/layer_manager.py:_execute_new_trades` per-directive loop. One event type. Reason field carries the specific blocker name.

### Event shape

```
STRAT_DIRECTIVE_REJECTED | sym=<sym> dir=<dir> rsn=<rsn_code> detail='<brief detail>' \
                          blocker_layer=<orchestration|gate|strategy_worker|halt> | did=<ctx>
```

`rsn_code` matches existing TRADE_SKIP codes: `halt`, `invalid_directive`, `pos_gate`, `gate_rejected`, `sanity_reject`, `enforcer_block`, `survival_block`, `xray_skip`, `xray_conflict`, `dup_position`, `service_missing`, `price_fetch_fail`, `order_reject`, `exception`.

### Emit sites (5 total, all in layer_manager.py)

1. Pre-loop halt path (line 1303 `BRAIN_TRADE_HALT`) — also emits STRAT_DIRECTIVE_REJECTED for each pending trade with `rsn=halt`
2. `continue` at line 1437 (`rsn=invalid_directive`)
3. `continue` at line 1449 (`rsn=pos_gate`)
4. `continue` at line 1486 (`rsn=gate_rejected` + detail = the full `_gate_rejected` string from gate.py CHECK)
5. After `_execute_claude_trade` returns `success=False` at line 1494 (`rsn=<_reason_code>` from strategy_worker)
6. Exception catch at line 1509 (`rsn=exception`)

### Implementation complexity

- **LOW**: 5-6 log emit additions, all in one file, all within ~80 lines
- No new infrastructure (contextvars already propagate did)
- No new modules
- No changes to gate.py / strategy_worker.py / optimizer.py / signal_generator.py / trade_coordinator.py
- No test mocks need updating beyond a new test file for the events

### Observability gain

- All silent rejections surface as a single event name
- Operator can `grep STRAT_DIRECTIVE_REJECTED` in real-time monitor and see every rejected directive
- Reason field gives the specific blocker
- did ties every event to the originating brain cycle

### Log volume cost

- 1 event per rejected directive
- Estimated rate: <100/hour based on historical TRADE_SKIP counts
- Log size impact: negligible (one log line ~150 bytes)
- INFO level appropriate (not WARNING — rejections are normal operational outcomes, not errors)

### Aim-bias five-question check

1. **Preserve trade frequency?** YES — no behavior change, only logging.
2. **Preserve aggression?** YES — does not add any new blocking.
3. **Improve decision quality?** YES — operator gains visibility, can investigate patterns.
4. **Preserve passive-close advantage?** YES — close path untouched.
5. **Respect structural separation of concerns?** YES — single file (layer_manager) is the orchestration layer; emitting orchestration events from it is correct architecture.

**All 5 questions: YES.**

## Option B — Per-blocker typed REJECTED events

Each blocker emits its own typed rejection event: `STRAT_DIRECTIVE_REJECTED_HALT`, `STRAT_DIRECTIVE_REJECTED_GATE`, `STRAT_DIRECTIVE_REJECTED_STRATEGY_WORKER`, etc.

### Emit sites

Same 5-6 sites as Option A, but with different event names per site.

### Implementation complexity

- **LOW-MEDIUM**: same code changes as Option A, but more event-name proliferation
- Monitor patterns become more complex: `STRAT_DIRECTIVE_REJECTED_HALT|_GATE|_STRATEGY_WORKER|...` instead of single name

### Observability gain

- More granular at first glance, but the same data is in Option A's `rsn=` field
- Pattern-matching becomes more typing for grep users
- Alerts and dashboards need to enumerate all event variants

### Log volume cost

- Same as Option A (same number of emits, different names)

### Aim-bias five-question check

Same as Option A — all 5 YES (observability-only).

### Trade-off vs Option A

Option B's only advantage is event-name clarity at first glance ("REJECTED_GATE" is more readable than "REJECTED rsn=gate_rejected"). The disadvantage is event-name proliferation: 5-6 distinct event names that must be enumerated in monitor patterns.

**Verdict**: Option B is a minor cosmetic variant. Option A's single-name + reason-field pattern is more grep-friendly and follows precedent: existing events like `TRADE_SKIP rsn=X` follow the same single-name + reason-field convention.

## Option C — Full lifecycle event chain

Multiple events per directive: `STRAT_DIRECTIVE_EMITTED`, `STRAT_DIRECTIVE_PICKED_UP`, `STRAT_DIRECTIVE_EVALUATED`, `STRAT_DIRECTIVE_ACCEPTED` or `STRAT_DIRECTIVE_REJECTED`.

### Emit sites

- `STRAT_DIRECTIVE_EMITTED` — at strategist.py:950 (already there as STRAT_DIRECTIVE; would be renamed or duplicated)
- `STRAT_DIRECTIVE_PICKED_UP` — at layer_manager.py:1432 (start of each trade iteration)
- `STRAT_DIRECTIVE_EVALUATED` — after APEX optimization at line 1473
- `STRAT_DIRECTIVE_ACCEPTED` — at line 1494 on success path
- `STRAT_DIRECTIVE_REJECTED` — same 5-6 sites as Option A

### Implementation complexity

- **MEDIUM**: 5 new event types, ~10-15 emit sites
- Requires deciding whether to RENAME existing STRAT_DIRECTIVE to STRAT_DIRECTIVE_EMITTED (breaks existing monitors) or duplicate it (log volume increase + ambiguity)
- More test coverage needed
- More documentation needed

### Observability gain

- Highest detail: every state transition is logged
- Operator can build precise timing measurements (emitted → picked_up → evaluated → terminal)
- Most flexible for future debugging

### Log volume cost

- 4 events per ACCEPTED directive (emitted + picked_up + evaluated + accepted)
- 3-4 events per REJECTED directive (depending on where rejection fires)
- Per Phase 1 trial baseline: ~30 trades/hour × 4 events = ~120/hour extra. Volume manageable but ~4x current STRAT_DIRECTIVE-related events.
- Risk per spec Risk 2 (line 654): "Adding lifecycle events causes log volume increase."

### Aim-bias five-question check

Same as Options A/B — all 5 YES (still observability-only).

### Trade-off vs Option A

Option C's advantage: full state transitions captured. Useful for performance debugging (e.g., "how long between EMITTED and EVALUATED?").

Disadvantage: 4-5x more events for the same information. Risk 2 (log volume) is real. Per spec Rule 12 priority (HIGH = lowest risk), Option A starts simpler.

**Hybrid path**: ship Option A first (closes Gap 3), gather operator feedback, optionally upgrade to Option C in a future iteration if timing data becomes valuable.

## Comparison summary

| Dimension | Option A | Option B | Option C |
|---|---|---|---|
| Implementation complexity | LOW | LOW-MEDIUM | MEDIUM |
| Number of new event types | 1 | 5-6 | 4-5 |
| Number of emit sites | 5-6 | 5-6 | 10-15 |
| Log volume per directive | 1 (on rejection) | 1 (on rejection) | 4-5 (always) |
| Grep-friendly | YES (single name) | mixed | YES (named events) |
| Closes Gap 3 | YES | YES | YES |
| Adds timing observability | NO | NO | YES |
| Aim-bias 5/5 YES | YES | YES | YES |
| Files modified | 1 | 1 | 2 (strategist + layer_manager) |
| Risk per spec Part E | LOW | LOW | MEDIUM (Risk 2) |

## Recommendation

**Option A (single `STRAT_DIRECTIVE_REJECTED` event at orchestration entry, with `rsn` field)** is recommended. It:

- Closes Gap 3 fully (every silent skip becomes visible)
- Adds 1 event type to grep for (follows TRADE_SKIP precedent)
- Touches 1 file (layer_manager.py)
- Has ZERO behavior change
- Minimum log-volume cost
- Lowest implementation risk
- Best aligned with spec Rule 12 ("HIGH = lowest risk")

If the operator later wants timing observability (Option C's advantage), it can be added in a follow-up iteration on top of Option A without rework — Option A's events become the REJECTED arm of a future ACCEPTED/REJECTED pair.

## Open question for operator

This investigation recommends Option A. Operator may choose B or C. The synthesis report (Step 3.5) will request operator approval before any implementation.
