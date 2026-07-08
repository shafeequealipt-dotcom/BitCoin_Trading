# Gap 3 Phase 1 Step 3.4 — Did Propagation Analysis

Date: 2026-05-19  
Scope: verify that `did` is reliably accessible at every emit site where `STRAT_DIRECTIVE_REJECTED` would fire. Determine whether contextvars propagation suffices or explicit attachment to trade dict is needed.

## Current propagation mechanism

**File**: `src/core/log_context.py`

Key components:
- `_decision_id: ContextVar[str]` at line 40 — Python contextvar, default `""`
- `new_decision_id()` at line 48-52 — generates `did = f"d-{epoch_ms}"` AND calls `_decision_id.set(did)`. Returns the did. **This is the only call that sets `_decision_id`** (verified via grep).
- `get_did()` at line 78 — returns current did or `""`
- `set_did(value)` at line 100 — manual setter; no other src/ file calls this (verified via grep)
- `ctx()` at line 160 — formats compact context suffix; emits `did=X` if `_decision_id.get("")` is non-empty, else `no_ctx`

**Discipline**: `new_decision_id()` is the single source of truth. Calling it at the start of a brain cycle sets the contextvar for the duration of that async chain.

## How Python contextvars propagate

- Within a single coroutine: contextvar value persists across `await` points (this is the design intent of contextvars vs threadlocals)
- Across `asyncio.create_task(coro)`: the new task **inherits a snapshot** of the parent context at the moment `create_task` is called
- Across `asyncio.gather(*coros)`: same — each gathered coro inherits the parent's snapshot
- Across thread boundaries: contextvars are NOT inherited (each thread has its own context)
- Across process boundaries: NOT inherited

## Propagation across the directive lifecycle

Trace from brain to gate to execute:

| Step | Same task? | did propagated? | Verified |
|---|---|---|---|
| `strategist.create_trade_plan` calls `new_decision_id()` | n/a (sets context) | YES | source code |
| Returns `StrategicPlan` | same task | YES | source code |
| `layer_manager._execute_trades_background` awaits `_execute_new_trades(plan)` | same task | YES | source code |
| `_execute_new_trades` iterates plan.new_trades | same task | YES | source code |
| `_apply_apex_optimization(trade, ...)` awaited inside iteration | same task | YES | source code |
| `gate.validate(trade)` awaited inside iteration | same task | YES | source code (plan-mode agent saw `did=d-1779194759952` in TRADE_SKIP gate_rejected) |
| `strategy_worker._execute_claude_trade(trade, ...)` awaited inside iteration | same task | YES | source code (plan-mode agent confirmed TRADE_SKIP carries did) |
| `bybit_demo_adapter.place_order` awaited inside above | same task | YES | source code (BYBIT_DEMO_ORDER_RECEIVED log includes `did=...`) |

**All 5 emit sites identified in Step 3.3 are in the same async task chain as the originating brain cycle.** contextvars propagation is reliable for the Option A design.

## Empirical confirmation from trial logs

Plan-mode agent traced these from `data/logs/workers.2026-05-19_11-26-15_574407.log`:

- `2026-05-19 12:48:25.641 STRAT_DIRECTIVE | #2 sym=HYPEUSDT dir=Buy lev=2 | did=d-1779194759952`
- `2026-05-19 12:48:41.055 TRADE_SKIP | sym=HYPEUSDT rsn=gate_rejected detail='reentry_learning_gate_same_conditions' | did=d-1779194759952`

**Same did**: confirms the directive's did flowed through 16 seconds and through gate.validate to the TRADE_SKIP emission. Contextvars working as expected.

Similarly:
- `2026-05-19 13:04:33.486 STRAT_DIRECTIVE | #2 sym=HYPEUSDT dir=Buy lev=2 | did=d-1779195718917`
- `2026-05-19 13:04:52.543 TRADE_SKIP | sym=HYPEUSDT rsn=gate_rejected detail='portfolio_direction_cap_Buy_80pct_aim_conditional' | did=d-1779195718917`

Same did, ~19 second flow. Confirmed.

## Edge cases (where contextvars could fail)

1. **Background tasks spawned without context** (e.g., `asyncio.create_task` inside a bare event loop with empty context). Verified: layer_manager runs in the main asyncio loop; the brain cycle is awaited from there; no fire-and-forget tasks in the directive iteration loop.
2. **Thread pool offload** (e.g., `loop.run_in_executor`). Grep `src/` for `run_in_executor` calls inside the lifecycle path: found uses are in unrelated workers (analysis engine, ta_worker), NOT in the directive iteration. SAFE.
3. **Cross-process** (e.g., subprocess). The Claude CLI subprocess is invoked from the brain BEFORE STRAT_DIRECTIVE is emitted; the directive iteration runs entirely in the parent process. SAFE.
4. **Long-lived workers reading a queue** (e.g., signal_generator runs in its own task). signal_generator does NOT receive directives; it produces signals consumed via cache. NOT IN PATH.

## Orphaned events (excluded from STRAT_DIRECTIVE_REJECTED scope)

Two events run outside the directive lifecycle task chain:

- `SIG_DOWNGRADE` at `signal_generator.py:220` — emitted in signal_generator's own worker task, `no_ctx`. NOT a directive rejection event. The downgrade INFLUENCES future brain proposals but doesn't reject a current directive.
- `COORD_LOSS_COOLDOWN_SET` at `trade_coordinator.py:1254` — emitted in trade-close handler task, `no_ctx`. Marks state; the eventual rejection happens later in gate.py CHECK 6/6b which IS in the directive task chain.

These orphaned events are CORRECTLY excluded from STRAT_DIRECTIVE_REJECTED — they are state changes, not directive rejections. The gap report's claim that "SIG_DOWNGRADE silently absorbed a directive" was misattributed (per Phase 0 timeline correction).

## Recommendation

**Contextvars-based propagation is sufficient for all 5 emit sites in Option A.** No explicit attachment of `did` to the trade dict is required.

Implementation pattern:

```python
# Inside layer_manager.py iteration loop, before each `continue`:
log.info(
    f"STRAT_DIRECTIVE_REJECTED | sym={symbol} dir={trade.get('direction', '?')} "
    f"rsn={rsn_code} detail='{detail[:120]}' blocker_layer={layer} | {ctx()}"
)
continue
```

`ctx()` automatically appends `did=<value>` (from contextvars) or `no_ctx` if somehow lost. If `no_ctx` is observed in any STRAT_DIRECTIVE_REJECTED event during verification, that flags a propagation bug — useful diagnostic.

## Edge-case insurance

Optional belt-and-suspenders: explicitly capture `did` at the top of `_execute_new_trades` via `get_did()`, store as local var, and include in every emit. This protects against any unexpected context reset within the loop. Recommended for robustness:

```python
async def _execute_new_trades(self, plan) -> None:
    _loop_did = get_did()  # snapshot at loop entry
    ...
    # At each emit site:
    log.info(f"STRAT_DIRECTIVE_REJECTED | ... did={_loop_did} | {ctx()}")
```

This is OPTIONAL. The empirical evidence shows contextvars work in practice. The belt-and-suspenders pattern adds a few lines of robustness with no downside.

## Conclusion

- **did is available at all 5 emit sites** via contextvars
- **Mechanism is empirically verified** in trial-window logs (same did flows from STRAT_DIRECTIVE to TRADE_SKIP)
- **No explicit trade dict attachment needed**
- **Optional belt-and-suspenders snapshot at loop entry** adds robustness with no downside; recommend for defensive coding
- **SIG_DOWNGRADE and COORD_LOSS_COOLDOWN_SET correctly excluded** from this scope (they are state events, not directive rejections)
