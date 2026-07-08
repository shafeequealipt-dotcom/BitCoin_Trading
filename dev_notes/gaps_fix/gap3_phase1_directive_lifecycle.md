# Gap 3 Phase 1 Step 3.2 — Directive Lifecycle Trace

Date: 2026-05-19  
Scope: the exact path a brain directive takes from emission at `strategist.py:950` to either successful `BYBIT_DEMO_ORDER_RECEIVED` or silent rejection. Identifies where a unified `STRAT_DIRECTIVE_REJECTED` event should fire.

## Stage 1 — Brain emit

**File:line**: `src/brain/strategist.py:809-984` (`create_trade_plan` CALL_A path)

1. `did = new_decision_id()` (line 809) — sets `_decision_id` contextvar
2. `log.info(f"STRAT_CALL_A_START | did={did} | {ctx()}")` (line 810)
3. Brain builds prompt, calls Claude CLI, parses response into trade dicts
4. For each parsed trade: `log.info(f"STRAT_DIRECTIVE | #{i+1} sym=X dir=Y lev=Z rsn='...' | {ctx()}")` (line 950)
5. Returns `StrategicPlan` with `new_trades: List[dict]`

**Did state**: set in contextvar, inherited by all subsequent awaited code.

**Today's events**: `STRAT_CALL_A_START`, `STRAT_DIRECTIVE` (per directive).

## Stage 2 — Layer manager orchestration

**File:line**: `src/core/layer_manager.py:1287-1512` (`_execute_new_trades(plan)`)

The directive list is iterated. At each iteration, multiple potential blockers fire IN ORDER:

```
for i, trade in enumerate(plan.new_trades):
    # B1: pnl_manager halt → return early (drops all queued)
    # B2: enforcer halt → return early
    # B3: invalid_directive (not dict) → continue
    # B4: pos_gate (symbol blocked) → continue
    # B5: APEX optimization applied → trade dict updated
    # B6: gate.validate → may set _gate_rejected → continue
    # B7: strategy_worker._execute_claude_trade → may emit internal TRADE_SKIP
    # B8: exception → TRADE_SKIP rsn=exception
    # ELSE: success → BYBIT_DEMO_ORDER_RECEIVED downstream
```

Each `continue` is a silent absorption point from the operator's perspective: a TRADE_SKIP event fires with a specific `rsn`, but no single event names "the directive that brain emitted with did=X was rejected because Y."

## Stage 3 — APEX optimization (informational)

**File:line**: `src/apex/optimizer.py:254-700` (called from `layer_manager.py:1473` via `_apply_apex_optimization`)

This is NOT a rejection stage. APEX may:
- Lock the original direction (`APEX_DIR_LOCK` at :289)
- Override the lock (`APEX_DIR_LOCK_OVERRIDE` at :398)
- Flip the direction or revert the flip (`APEX_FLIP_*` events at :524, :559, :580, :686)

The trade direction may change, but the directive proceeds to the gate.

**Observability gap**: if APEX flips Buy→Sell, the resulting trade is recorded as Sell. The originating Buy directive's transformation isn't captured in a single lifecycle event.

## Stage 4 — Gate validation

**File:line**: `src/apex/gate.py:48` (`TradeGate.validate`)

14 CHECKs run sequentially. Any can set `trade["_gate_rejected"] = <reason>`. The gate emits per-CHECK informational events (e.g., `GATE_REJECT | reason=zero_conviction`) but those are local to the CHECK that fired.

Back in layer_manager.py:1479, the post-gate check:
```
if trade.get("_gate_rejected"):
    log.warning(f"TRADE_SKIP | sym=X rsn=gate_rejected detail='<_gate_rejected>' | {ctx()}")
    continue
```

So the existing observability is:
- per-CHECK detail at gate (GATE_REJECT or no event for CHECK 6b)
- layer-manager TRADE_SKIP at the orchestration level
- `did` present via ctx()

But there is no single canonical event saying "STRAT_DIRECTIVE did=X for SYM was rejected by gate because reentry_learning_gate_same_conditions." A monitor must:
- See STRAT_DIRECTIVE for SYM at time T
- See TRADE_SKIP rsn=gate_rejected for SYM at time T+δ
- Correlate by symbol + timestamp + did

## Stage 5 — Strategy worker execution

**File:line**: `src/workers/strategy_worker.py:1526` (`_execute_claude_trade`)

Returns `(success: bool, reason_code: str)`. Internal TRADE_SKIP events fire at 12+ sites with specific `rsn`. Each is a distinct failure mode, all logged with `did` via ctx.

## Stage 6 — Bybit demo execute

**File:line**: `src/bybit_demo/bybit_demo_adapter.py` (called from strategy_worker)

Successful path emits `BYBIT_DEMO_ORDER_RECEIVED | sym=X side=Y qty=Z | did=<ctx>`. Failure path emits `TRADE_SKIP rsn=order_reject` via strategy_worker.

## End-to-end paths

For ONE STRAT_DIRECTIVE, the possible terminal events are:

| Path | Terminal event | Where |
|---|---|---|
| Success | `BYBIT_DEMO_ORDER_RECEIVED` | bybit_demo_adapter.py |
| Halted before iteration | `BRAIN_TRADE_HALT` (drops ALL queued) | layer_manager.py:1303 |
| Invalid directive format | `TRADE_SKIP rsn=invalid_directive` | layer_manager.py:1434 |
| Position blocked | `TRADE_SKIP rsn=pos_gate` | layer_manager.py:1448 |
| Gate rejected | `TRADE_SKIP rsn=gate_rejected detail='<_gate_rejected>'` | layer_manager.py:1482 |
| Strategy worker reject | `TRADE_SKIP rsn=<sanity_reject\|enforcer_block\|...>` | strategy_worker.py:1548+ |
| Exception | `TRADE_SKIP rsn=exception` | layer_manager.py:1509 |
| APEX flip (informational) | `APEX_FLIP_DECISION` | optimizer.py:686 |

## Where the unified `STRAT_DIRECTIVE_REJECTED` event should fire

**Single chokepoint candidate**: `layer_manager.py:1432-1512` per-directive iteration loop.

Every `continue` in this loop = a directive silently absorbed. Emitting `STRAT_DIRECTIVE_REJECTED` IMMEDIATELY BEFORE each `continue` would capture all rejections at a single architectural layer. The `did` from ctx() is already there. The `rsn` is already known (just constructed for TRADE_SKIP).

Specific emit sites:
- Before `continue` at line 1437 (invalid_directive)
- Before `continue` at line 1449 (pos_gate)
- Before `continue` at line 1486 (gate_rejected)
- After strategy_worker returns `success=False` at line 1494 (with `_reason_code`)
- Before `continue` after exception at line 1512

That's **5 emit sites in layer_manager.py**, all within ~80 lines. Each event:
- Includes `did` (from ctx)
- Includes `sym` (trade dict)
- Includes `rsn` (the existing rsn used for TRADE_SKIP)
- Includes `detail` (the existing detail string)
- May include `blocker_layer` (gate, strategy_worker, orchestration)

**Strategy_worker internal TRADE_SKIPs are NOT additional emit sites for STRAT_DIRECTIVE_REJECTED.** They feed into the layer_manager loop's `_reason_code` (the return value from `_execute_claude_trade`). The single emit at line 1494 captures whatever strategy_worker decided.

## Flip transformations (separate observability event)

The APEX_FLIP_* events change the direction but don't reject the directive. To complete the lifecycle picture, an optional `STRAT_DIRECTIVE_TRANSFORMED` event could fire on flip. But this is OUT OF SCOPE for Gap 3 — the gap is about REJECTIONS, not transformations.

## Conclusion

The lifecycle has 5 clean chokepoints in `layer_manager.py:_execute_new_trades`. A single new event `STRAT_DIRECTIVE_REJECTED` emitted at each chokepoint, immediately before the existing TRADE_SKIP log, would:
- Surface all silent skips into one grep-able stream
- Include `did` (already in ctx)
- Reuse the existing `rsn` codes (no new taxonomy)
- Require ZERO changes to gate.py, strategy_worker.py, optimizer.py, signal_generator.py, trade_coordinator.py

The implementation surface is fully contained in one file: `src/core/layer_manager.py`.

Optional Stage 1 enrichment: a `STRAT_DIRECTIVE_ACCEPTED` event at the success path (before BYBIT order placement) would complete the binary acceptance/rejection view per directive. But this is an enhancement, not a requirement for Gap 3.
