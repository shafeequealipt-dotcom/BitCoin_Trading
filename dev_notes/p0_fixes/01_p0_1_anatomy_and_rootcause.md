# P0-1 Anatomy and Root Cause — Execution Blackout

Date: 2026-05-22. Defect does not reproduce in current behaviour per Phase 0 baseline. Operator requested deep investigation anyway (silent-drop-class defensive hardening).

## H1 — Spec Claim vs Actual Logs

Spec: "Fifteen new-trade directives across five cycles between 15:19 and 15:53 ... none executed ... first execution event of any kind at or after 16:02:27 ... fifteen directives simply vanished with no skip event and no rejection event."

Actual logs (Phase 0 evidence): all three layers active by 15:07:20.565. Brain cycles ran at 15:07 (empty plan, brain returned no ideas), 15:12 (empty plan), 15:19 (3 trades emitted, BRAIN_DO_START fired at 15:19:57.303, first STRAT_EXEC + BYBIT_DEMO_ORD_SEND at 15:20:59 on NEARUSDT). 25 STRAT_EXEC events in the strict window vs 30 BRAIN_DO_TRADE attempts; the 5-item gap is accounted for by 3 STRAT_DIRECTIVE_REJECTED events and 2 other SKIP/BLOCKED events. Zero BRAIN_TRADES_DROPPED events. No silent vanishing.

So P0-1 is not a live defect in current code. The investigation below is defensive — does the silent-drop class of bug have residual surfaces in current code that should be hardened anyway?

## H1 — Anatomy of the Directive → Order Path

Single execution authority lives in `src/core/layer_manager.py`:

1. `_brain_review_loop` at line 676 spawns the brain loop as an asyncio task at Layer 2 activation.
2. `_run_brain_cycle` (708–991) alternates between CALL_A (find new trades) and CALL_B (manage positions).
3. CALL_A branch (753–910): calls `strategist.create_trade_plan()` which returns a `StrategicPlan` with `new_trades` list. Logs `BRAIN_CYCLE_A_DONE`.
4. **Layer 3 gate at ~807**: `if self._layer_active[3]:` — only fork the execution path when Layer 3 is active.
5. Background concurrency gate (818–823): `if self._background_exec_task and not self._background_exec_task.done():` → log `BRAIN_DO_SKIP | prev_still_running` and skip this batch. This prevents two execution batches running in parallel.
6. Background task spawn (824 onward): `asyncio.create_task(self._execute_trades_background(plan))` and store on `_background_exec_task`.
7. `_execute_trades_background` (1293–1327): `asyncio.wait_for(_execute_new_trades(plan), timeout=300)`. On timeout, logs `BRAIN_DO_TIMEOUT` and aborts. Always emits `BRAIN_DO_START` at entry (1308) and `BRAIN_DO_DONE` at exit (1311).
8. `_execute_new_trades` (1379–1721): iterates `plan.new_trades`, applies APEX optimization, checks gate, calls `strategy_worker._execute_claude_trade` for each trade. Per-trade emits `BRAIN_DO_TRADE` (1708) on completion regardless of success/failure.
9. `strategy_worker._execute_claude_trade` (1660–2800+): enforces, X-RAY-gates, validates SL/TP, places order via `order_service.place_order`. Returns `(success, reason_code)` — caller logs `TRADE_SKIP` on failure.

**No alternate emitter exists.** Grep for `_execute_trades_background` shows one caller (line 824, inside the layer_active[3] branch). Grep for `place_order` from a worker outside layer_manager → none in the brain-cycle path.

The strategist (`src/brain/strategist.py`) is purely a producer of `StrategicPlan` objects. It does not execute, does not call `place_order`, does not spawn execution tasks. The only consumer of the plan is the layer_manager. So the spec's "two orchestration paths exist" claim is incorrect for current code — there is only one orchestration path, and it gates on `layer_active[3]`.

## H1 — Directive Accounting Today

For each brain emission of `trades = N`, the system emits one or more of these terminal events per directive:

- `STRAT_EXEC` — order successfully sent (placed).
- `STRAT_EXEC_BLOCKED` — enforcer/X-RAY/gate blocked, reason emitted alongside.
- `STRAT_EXEC_SKIP` — pre-execution skip (e.g., dup_position, sltp_skip).
- `STRAT_DIRECTIVE_REJECTED` — Phase 10 Gap C4 unified rejection event with `blocker_layer` field.
- `TRADE_SKIP` — per-trade skip-with-reason emitted by `_execute_claude_trade` before returning.
- `BRAIN_DO_TRADE` — per-trade summary regardless of outcome, includes `rsn=ok|skip_reason`.

The 2026-05-22 strict window arithmetic: 25 `STRAT_EXEC` + 3 `STRAT_DIRECTIVE_REJECTED` + 2 `TRADE_SKIP` (sltp_skip class) = 30. Matches `BRAIN_DO_TRADE` count exactly. Zero unaccounted.

## H1 — Residual Silent-Drop Surfaces (Defensive Hardening Opportunities)

Even with no current live drops, the operator asked for deep investigation. The defensive surfaces:

### H2 — Surface 1: `BRAIN_TRADES_DROPPED` is a WARNING-level log, not a structural failure

At layer_manager.py:807 the `if self._layer_active[3]:` gate's `else` branch logs `BRAIN_TRADES_DROPPED | layer=3_inactive trades_count=N sample_syms=...`. This is a warning, not an exception, not a counter. An operator dashboard that filters out warnings would miss it. The directives are not recovered; the next brain cycle generates fresh ideas.

**Hardening proposal:** convert the drop into a structured "deferred queue" — directives dropped due to layer-inactive are appended to an in-memory queue and drained atomically the moment Layer 3 toggles active. If the queue exceeds a safety cap (default 50) the oldest are evicted with an ERROR-level event `BRAIN_DIRECTIVE_EVICTED`. The structural guarantee is "no silent loss when Layer 3 toggles late". Today's behaviour is "drop with warning"; the proposal is "queue with capped capacity and explicit eviction event".

### H2 — Surface 2: `BRAIN_DO_TIMEOUT` aborts the entire batch with no per-directive accounting

If `_execute_trades_background` exceeds the 300-second timeout, the wait_for raises and the whole batch is abandoned. Per-trade `BRAIN_DO_TRADE` events fire only for trades that completed before the abort. Trades that were mid-flight at abort have no terminal event.

**Hardening proposal:** wrap each per-trade execution in its own short timeout (e.g., 30 seconds per trade) within `_execute_new_trades`. Each per-trade timeout emits its own `STRAT_EXEC_TIMEOUT` event. The outer 300s timeout becomes a structural ceiling, not the primary control. This makes per-directive accounting hold even under timeout.

### H2 — Surface 3: Exceptions in `_execute_claude_trade` may not emit `BRAIN_DO_TRADE`

Looking at the code: `_execute_claude_trade` returns `(success, reason_code)`. The caller (`_execute_new_trades`) emits `BRAIN_DO_TRADE` after the return. If `_execute_claude_trade` raises an unhandled exception, the caller's loop continues to the next trade but the exception-trade has no `BRAIN_DO_TRADE` event.

**Hardening proposal:** wrap each per-trade call in `try/except Exception`, log `STRAT_EXEC_EXCEPTION | rsn=<exception_class>:<message>` on raise, and emit `BRAIN_DO_TRADE | rsn=exception` so the per-trade accounting holds. This is a one-line wrapper around the existing call.

### H2 — Surface 4: No structural enforce of "single execution authority"

Today the single-path nature is by code design, not by structural enforcement. A future regression that adds a second emitter (e.g., a worker that directly calls `place_order` in response to a different signal) would not be caught by current code.

**Hardening proposal:** wrap `order_service.place_order` such that the first parameter is a `caller_id: Literal["layer_manager", "watchdog", "emergency_close"]`. The wrapper records which caller invoked each order and asserts that the brain-cycle-driven path always uses `caller_id="layer_manager"`. Any other source produces a structured log line for audit. Optional: the wrapper could refuse `caller_id` values outside an allowed set (truly structural enforce).

This is the heaviest of the four hardenings; it touches the order service.

## H1 — Proposed Root Fix (Subject to Operator Sequencing)

The operator already noted P0-1 does not reproduce as written. Two paths forward:

### H2 — Path A: Skip P0-1 fix entirely

Per the spec's Rule 14 ("if a defect no longer reproduces, document that and escalate before fixing something that is no longer broken"), do not apply a fix that exists primarily to make a non-reproducing defect "safer". The current code is structurally sound on this surface. The investigation report stays as a future reference.

This is consistent with the spec's anti-pattern 11: "Declaring a phase passed on impression rather than the specified pass values; or hiding a failure." Phase 0's pass value for P0-1 ("the first executable brain cycle and its execution phase occur within one configured cycle period of the brain becoming live") was met by current behaviour: first execution at 15:20:59, brain became live at 15:07:18 with first non-empty plan at 15:19:57. So the pass value is held.

### H2 — Path B: Apply Surfaces 1-3 as defensive hardening

If the operator wants defensive hardening for the silent-drop class:

- Surface 1 (deferred queue for Layer 3 toggle): MEDIUM effort, structurally meaningful.
- Surface 2 (per-trade timeout): MEDIUM effort, structurally meaningful.
- Surface 3 (per-trade exception wrap): LOW effort, structurally meaningful.

Surface 4 (caller_id wrapper around place_order) is HIGH effort and touches the order service. Recommended NO — it adds complexity to a hot path for a defect that does not reproduce.

### H2 — Recommendation

Adopt **Path A** (skip P0-1 fix). The structural soundness of the current code is confirmed by Phase 0; there is no live defect to fix. Surfaces 1-3 are defensive options the operator can request later if they become important.

If the operator chooses Path B (defensive hardening), I would apply Surfaces 1, 2, and 3 as one atomic commit on main (`p0-1: directive accounting hardening — deferred queue, per-trade timeout, exception wrapper`), with `P0_1_SENTINEL` boot log naming the active hardening.

## H1 — Decision Gate (P0-1)

I will ask the operator:

1. P0-1 outcome — Path A (skip, no fix needed) or Path B (defensive hardening of Surfaces 1-3)?
2. If Path B, whether Surface 4 (caller_id wrapper) should also be included.

No code change applied until operator decides at the gate.
