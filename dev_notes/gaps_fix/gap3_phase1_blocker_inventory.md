# Gap 3 Phase 1 Step 3.1 — Blocker Inventory

Date: 2026-05-19  
Scope: every silent-skip blocker in the brain→execute path. For each: file:line, check predicate, current log event, rejection reason string, `did` accessibility.

## Lifecycle entry point

Brain emits `STRAT_DIRECTIVE` from `src/brain/strategist.py:743` (CALL_A urgent path) or `:950` (CALL_A main path). Each directive flows into `src/core/layer_manager.py:1287:_execute_new_trades(plan)`. Inside that loop, every blocker that could silently absorb the directive is enumerated below.

## Blocker A — Manual pause / Enforcer halt (`layer_manager.py:1297-1310`)

- **Check**: `pnl_manager.can_trade()` returns `(allowed, reason)`. Also `enforcer.check_and_enforce()` may halt.
- **Log today**: `BRAIN_TRADE_HALT | rsn=<reason> | did=<from-ctx>` (line 1303)
- **Outcome**: early `return` from `_execute_new_trades`. ALL queued directives in this plan are dropped.
- **did availability**: YES via `ctx()`.
- **Reason string**: whatever `can_trade()` returns.

## Blocker B — Invalid directive format (`layer_manager.py:1434-1437`)

- **Check**: `if not isinstance(trade, dict)`
- **Log today**: `TRADE_SKIP | sym=? rsn=invalid_directive detail='type=...' idx=<i> | did=<ctx>`
- **Outcome**: `_bump_skip("invalid_directive")` + `continue`
- **did availability**: YES via `ctx()`.

## Blocker C — Position gate / executing lock (`layer_manager.py:1444-1454`)

- **Check**: `if symbol in blocked_symbols` (already has open position OR currently executing)
- **Log today**: `POS_GATE_BLOCK | sym=X rsn='open_position|executing' | did=<ctx>` then `TRADE_SKIP | sym=X rsn=pos_gate detail='<rsn>' | did=<ctx>`
- **Outcome**: `_bump_skip("pos_gate")` + `continue`
- **did availability**: YES via `ctx()`.

## Blocker D — APEX gate validate (`layer_manager.py:1477-1486` → `apex/gate.py` CHECKs 0-14)

Gate has 15 CHECKs (CHECK 15 is `portfolio_direction_cap_*`, disabled by Phase 1A — short-circuits at `gate.py:666`). All other CHECKs can set `trade["_gate_rejected"] = <reason>`.

| Sub-blocker | File:line | Reason string set | Local log event |
|---|---|---|---|
| CHECK 0: Claude size cap | gate.py:65 | (no rejection — caps instead) | — |
| CHECK 1: Max position size | gate.py:99 | sets reject | GATE_REJECT |
| CHECK 2: Max leverage | gate.py:106 | sets reject | GATE_REJECT |
| CHECK 3: Max concurrent positions | gate.py:113 | sets reject | GATE_REJECT |
| CHECK 4: Capital availability (conviction-weighted) | gate.py:131-174 | `_gate_rejected="zero_conviction..."` at :171 | `GATE_REJECT | layer=gate sym=X reason=zero_conviction` at :174 |
| CHECK 5: Duplicate position | gate.py:263 | sets reject | GATE_REJECT |
| CHECK 6: Recent cooldown (revenge-trade) | gate.py:276-310 | `_gate_rejected=<reason>` at :307 | `GATE_REJECT | layer=gate sym=X` at :310 |
| CHECK 6b: J6 re-entry learning gate | gate.py:322-477 | `_gate_rejected=f"reentry_learning_gate_{reason}"` at :477 | (no local emit — relies on layer_manager TRADE_SKIP) |
| CHECK 7: Min position size | gate.py:498 | (caps to min) | — |
| CHECK 8-12: Floors / Mode / Size scaling | gate.py:531-596 | (adjustments, not rejections) | — |
| CHECK 13: R:R validation | gate.py:613 | may set reject | — |
| CHECK 14: TP/SL sanity | gate.py:632 | may set reject | — |
| CHECK 15: Portfolio cap (DISABLED post-Phase-1A) | gate.py:649-810 | short-circuits at :666 | — |

After gate returns, layer_manager.py:1479 checks `_gate_rejected`. If set:
- **Outcome**: `TRADE_SKIP | sym=X rsn=gate_rejected detail='<_gate_rejected>'` at :1482 + `continue`
- **did availability**: YES via `ctx()`.
- **Coverage**: layer_manager catches every CHECK rejection via the `_gate_rejected` flag.

## Blocker E — strategy_worker._execute_claude_trade internal returns (`strategy_worker.py:1526`)

Method returns `(success: bool, reason_code: str)`. Multiple internal rejection sites:

| Sub-blocker | File:line | rsn |
|---|---|---|
| sanity_reject | strategy_worker.py:1548 | TRADE_SKIP rsn=sanity_reject |
| enforcer_block | strategy_worker.py:1601 | TRADE_SKIP rsn=enforcer_block |
| survival_block | strategy_worker.py:1656 | TRADE_SKIP rsn=survival_block |
| xray_skip | strategy_worker.py:1674 | TRADE_SKIP rsn=xray_skip |
| xray_conflict | strategy_worker.py:1693 | TRADE_SKIP rsn=xray_conflict |
| sltp_skip | strategy_worker.py:1883 | TRADE_SKIP rsn=sltp_skip (caller logs) |
| qty_zero | strategy_worker.py:1914 | TRADE_SKIP rsn=qty_zero |
| unsupported_symbol | strategy_worker.py:1985 | TRADE_SKIP rsn=unsupported_symbol |
| dup_position | strategy_worker.py:1994 | TRADE_SKIP rsn=dup_position |
| service_missing | strategy_worker.py:2007 | TRADE_SKIP rsn=service_missing |
| price_fetch_fail | strategy_worker.py:2019 | TRADE_SKIP rsn=price_fetch_fail |
| order_reject | (bybit_demo level) | TRADE_SKIP rsn=order_reject |

- **did availability**: YES via `ctx()` (strategy_worker runs in same async chain).
- **Coverage**: 12+ distinct internal failure modes. Each emits its own TRADE_SKIP with specific `rsn`.

Also: layer_manager.py:1503-1512 catches exceptions from `_execute_claude_trade` and emits `TRADE_SKIP rsn=exception`.

## Blocker F — APEX optimizer flip rejections (`apex/optimizer.py`)

Note: these run BEFORE gate.validate, during apex optimization. Direction-lock may keep the original direction (no rejection) or override via composite scoring. Flip rejections revert to original direction (not a directive rejection per se):

| Sub-event | File:line | Outcome |
|---|---|---|
| APEX_DIR_LOCK | optimizer.py:289 | informational; may still allow flip via R2/R3 override |
| APEX_DIR_LOCK_OVERRIDE | optimizer.py:398 | lock relaxed, flip allowed |
| APEX_FLIP_COUNTER_PROTECTED | optimizer.py:524 | flip reverted to original direction |
| APEX_FLIP_INSUFFICIENT_DATA | optimizer.py:559 | flip reverted |
| APEX_FLIP_BLOCKED | optimizer.py:580 | flip below threshold, reverted |
| APEX_FLIP_DECISION | optimizer.py:686 | unified decision log |

**These are flip BLOCKERS, not directive REJECTORS.** They keep the directive alive in the brain's original direction. So they belong to the directive's lifecycle as transformations, not rejections. Should still surface as `STRAT_DIRECTIVE_FLIP_BLOCKED` events tied to did for full observability.

## Blocker G — Signal-confidence downgrade (`signal_generator.py:220`)

- **Event**: `SIG_DOWNGRADE | sym=X from=buy to=neutral conf=0.28 strong_min=0.60 buy_min=0.40 | no_ctx`
- **CRITICAL**: emitted with `no_ctx` — no `did` tied to it.
- **Mechanism**: signal_generator runs in its own worker task. It produces signals that get cached. The brain reads cached signals. There is NO direct rejection path tying a STRAT_DIRECTIVE to a SIG_DOWNGRADE — the downgrade simply influences future brain decisions.
- **Per plan-mode timeline correction**: spec's claim "Batch 13 SIG_DOWNGRADE silently absorbed" is misattributed. SIG_DOWNGRADE is NOT a silent absorber of directives; it's an upstream signal-mutation that may influence what the brain proposes next cycle. The actual batch 13 rejection mechanism is unconfirmed in logs.

## Blocker H — Loss cooldown set on close (`trade_coordinator.py:1254`)

- **Event**: `COORD_LOSS_COOLDOWN_SET | sym=X dir=Buy cooldown_sec=600 | no_ctx`
- **This is a STATE-SETTING event, not a rejection event.** It marks the symbol as cool-down-locked.
- **The rejection happens later**, at gate.py CHECK 6 (recent cooldown) and CHECK 6b (J6 reentry learning).
- **did availability**: NO at the set site (different async task: trade close handler). YES at the rejection site (gate.py via ctx()).

## Did propagation summary

| Site | did available? | Mechanism |
|---|---|---|
| Brain emit STRAT_DIRECTIVE | YES | `new_decision_id()` sets contextvar |
| Manual pause / Enforcer halt | YES | inherited via ctx() |
| Invalid directive | YES | inherited via ctx() |
| Position gate | YES | inherited via ctx() |
| Gate CHECK 1-14 rejections | YES | inherited via ctx() (layer_manager logs TRADE_SKIP with ctx) |
| strategy_worker internal skips | YES | inherited via ctx() |
| Flip blocker events | YES | inherited via ctx() |
| SIG_DOWNGRADE | NO | runs in own worker task, no_ctx |
| COORD_LOSS_COOLDOWN_SET | NO | trade close handler, no_ctx (but rejection IS tied to did at gate.py) |

## Conclusion

**13 active rejection points** (1 pre-gate halt, 1 invalid format, 1 pos-gate, 8 gate CHECKs, ~10 strategy_worker internal modes, 1 exception). All 13 have `did` available through contextvars propagation.

**3 informational events** (APEX_DIR_LOCK_OVERRIDE, APEX_FLIP_BLOCKED variants) that are transformations, not rejections.

**2 orphaned events** (SIG_DOWNGRADE, COORD_LOSS_COOLDOWN_SET) emitted outside the directive task chain. The rejection these eventually cause IS tied to did at the gate level; the upstream events are NOT.

**Recommendation prerequisite**: a unified `STRAT_DIRECTIVE_REJECTED` event emitted at the `layer_manager.py:1432-1512` loop level (post-rejection-detection) would capture all 13 rejection points using `did` from contextvars. Strategy_worker internal TRADE_SKIP events already carry `rsn` codes; the new event would be a COMPLEMENT at the orchestration layer naming the directive that got rejected and the canonical reason.
