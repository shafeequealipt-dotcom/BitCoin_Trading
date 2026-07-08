# Issue 1 — Phase 1 — Code Anatomy + Factor Availability + Intercept Point

Date: 2026-05-18. HEAD: tip of `fix/wd-scoring-brain-vote` (stacked on Issue 3 tip `497378c`).

Consolidates prompt steps 1.1, 1.2, 1.3 into a single concise reference. Re-verified against current code after Issue 2/3 line shifts.

## A. Files Read End-to-End

- `src/workers/position_watchdog.py` — close paths, scoring intercept, helpers.
- `src/core/trade_coordinator.py` — get_trade_plan, get_age_seconds, drain_strategic_actions.
- `src/brain/strategist.py` — CALL_B response → PositionAction wiring (lines around layer_manager call).
- `src/core/strategic_plan.py` — PositionAction dataclass (line 28 onward) defines the contract.
- `src/risk/time_decay_sl.py` — TimeDecayState (lines 48-90) carries velocity/acceleration when position is loser-lane.
- `src/risk/layer4_protection.py` — `STRUCT_GUARD_VERDICT_MAX_AGE_S = 60s` (line ~97) for XRAY staleness.
- `src/analysis/structure/models/structure_types.py` — `StructureAnalysis.trade_direction` and `structural_placement.rr_long/rr_short` (lines 528-640) for XRAY factor.

No `src/risk/sentinel.py` exists; the watchdog's sentinel surface is `_sentinel_advisor` + `_execute_sentinel_recommendations()`, both in `position_watchdog.py`. XRAY via `structure_cache` is the design's "Factor 6" — no sentinel-specific plumbing required.

## B. Intercept Point (precise location, current line numbers)

`src/workers/position_watchdog.py`:
- Method: `_execute_strategic_actions()` defined at line 2944.
- Outer loop: line 2952 iterates `for action in actions:`.
- Existing position re-verify: lines 2960-2969 (skip if position closed during brain cycle).
- Existing min-hold guardrail (Phase 1B, retained): lines 2989-3030. When `_age_sec < _min_hold and not _reason_allowed`, emits `STRAT_ACTION_CLOSE_BLOCKED` and `continue`s.
- Close branch start: line 3033 `if act in ("close", "take_profit"):`.
- Close execution: line 3034 `await self.position_service.close_position(symbol, close_trigger="wd_claude_action")`.
- Existing close log: line 3035 `STRAT_ACTION_CLOSE`.

**Scoring inserts BETWEEN line 3030 (the existing min-hold guard's `continue`) and line 3032 (the outer `try:`).** Order matters: the min-hold guard runs first (so explicit SL/TP/structure/regime/manual reasons bypass scoring as today); scoring runs only on the "discretionary brain close" remainder.

This preserves the prompt's hard rules:
- Scoring does NOT disable brain's close authority outright.
- Min-hold remains the fast-path bypass for explicit-reason closes.
- Score logs and decisions only impact the close branch (tighten_stop / set_exit / hold are untouched).

## C. Factor Availability (each of 7 factors, per Step 1.2)

### Factor 1 — PnL %
- Helper: `_calculate_pnl_pct(pos, current_price)` at `position_watchdog.py:2812-2819` (static-ish).
- Position object: re-fetched in the loop at line 2961 (`_pos_check`); reuse via another `await self.position_service.get_position(symbol)` or stash into a local before scoring.
- Current price: from the position service ticker or from the position object's `mark_price`.
- Default on missing price: 0.0% (treat as neutral → factor contribution 0).

### Factor 2 — Time remaining to deadline
- Source: `coordinator.get_trade_plan(symbol)` returns the StrategicPlan; `plan.max_hold_minutes * 60 - (time.time() - plan.created_at)` (or analogous field).
- Coordinator method exists at `trade_coordinator.py` accessible via `self.coordinator.get_trade_plan(symbol)` per existing usage (e.g. `position_watchdog.py:616`).
- Default on missing plan: 0 seconds (treat as "deadline soon" → +1 factor contribution).

### Factor 3 — Position age
- Source: `coordinator.get_age_seconds(symbol)` (already used at `position_watchdog.py:3011`).
- Returns `float` seconds. Safe default 0 (treat as "too soon" → -2 conservative HOLD).

### Factor 4 — PnL velocity
- Primary source: `self._td_states[symbol].prev_velocity` (TimeDecayState, populated when position is loser-lane).
- Fallback: a local `_brain_score_prev_pnl: dict[str, tuple[float, float]]` on the watchdog instance that stashes `(pnl, ts)` each tick; on score time compute `(pnl_now - pnl_prev) / dt`.
- Default on no history: 0.0 velocity (factor contribution 0).

### Factor 5 — SL consumption %
- Inverse of `_calculate_sl_proximity(pos, current_price)` at `position_watchdog.py:2822-2840` (returns 0-100% proximity → SL consumption = same).
- Default on missing price: 50% (factor contribution 0).

### Factor 6 — XRAY structural verdict
- Source: `self.structure_cache.get(symbol)` returns `StructureAnalysis` with `trade_direction` ("long"/"short"/"") and `structural_placement.rr_long/rr_short`.
- Staleness: apply `STRUCT_GUARD_VERDICT_MAX_AGE_S = 60s` (from `layer4_protection.py:97`) using the `StructureAnalysis.computed_at` timestamp.
- Verdict mapping (per design):
  - Structure supports position direction (XRAY direction == position direction) → −2 (strong HOLD).
  - Structure neutral / unavailable / stale → 0.
  - Structure broken / supports opposite (XRAY direction != position direction) → +2.
- Default on missing/stale: 0 (factor contribution 0); emit `WD_BRAIN_SCORE_XRAY_STALE` for observability.

### Factor 7 — Brain reasoning quality
- Source: `action["reason"]` (the free-form string from PositionAction.reason).
- Keyword set: structural anchors (`structure`, `invalidate`, `broken`, `setup`, `regime`, `reversal`, `fvg`, `ob`, `breakdown`, `breakout`) → +2 (cited structural evidence).
- Non-empty without keywords → +0.5 (vague reasoning).
- Empty / whitespace → 0.

## D. Edge Cases (Step 1.5)

- Unparseable / non-ASCII reasoning → default to +0.5 bucket (non-empty fallback).
- NaN PnL (price-feed glitch) → log `WD_BRAIN_SCORE_INPUT_NAN`, factor contribution 0, scoring continues.
- Divide-by-zero velocity (dt = 0) → return 0.0 velocity, no error.
- Negative time-remaining (deadline already passed) → clamp to 0 (treat as `<5 min` → +1).
- Stale XRAY (>60s) → factor contribution 0; log `WD_BRAIN_SCORE_XRAY_STALE`.

## E. Phase 1 Log-Only Design (Step 1.6)

Settings:
- `wd_brain_scoring_enabled: bool = True` — kill switch.
- `wd_brain_scoring_enforce: bool = False` — Phase 1 default. When False, scoring runs and logs every factor + composite + recommendation but the brain's close still fires.
- `wd_brain_scoring_threshold: float = 6.0` — close fires when composite >= threshold (only consulted in enforce mode).

Behaviour with enforce=False:
- For every brain close vote that passes the existing min-hold guard, compute the score.
- Emit `BRAIN_CLOSE_VOTE_RECEIVED` (info, on entry).
- Emit `WATCHDOG_CLOSE_SCORE_COMPUTED` (warning, with all 7 factor values + composite + would-be-action).
- Emit `WD_CLOSE_SCORE_LOG_ONLY` (info, "log-only mode, brain close proceeding as scheduled").
- Continue with the existing `await position_service.close_position(...)` call.

Operator validates by post-deploy log dump: for each historical SCORE_COMPUTED event, pair with the actual post-close PnL trajectory to confirm score correctly predicts good vs bad closes.

## F. Phase 2 Enforce Design (Step 1.7)

Settings flip to enforce=True (separate commit issue1/p3-7 after operator approval).

Behaviour matrix:
- composite >= +6 → close fires; emit `WATCHDOG_CLOSE_EXECUTED` with composite + factors.
- 0 <= composite < +6 → brain vote suppressed; emit `WATCHDOG_CLOSE_REJECTED`; `continue` (no close, no tighten).
- composite < 0 → brain vote strongly rejected; tighten SL toward break-even by 30% of remaining distance via `_push_sl_to_shadow` with `source="wd_brain_scoring"`; emit `WATCHDOG_CLOSE_OVERRIDE_TIGHTEN`; `continue`.

SL tightening helper `_tighten_sl_breakeven_30pct(pos)`:
- Computes `tightened_sl = pos.stop_loss + 0.3 * (entry_or_breakeven - pos.stop_loss)` (signed for direction).
- Calls `self._push_sl_to_shadow(symbol, new_sl=tightened_sl, plan=coord.get_trade_plan(symbol), current_shadow_sl=pos.stop_loss, direction=pos.side.value, source="wd_brain_scoring")`.
- The push helper's existing tighter-only and break-even guards (lines 848-950) act as the safety net — a malformed delta can never widen SL or push past entry.

## G. Trial Scenarios (Step 1.8)

### Scenario A — Historical BSBUSDT -$28 (would have been HELD)

Per prompt §B worked example:
- PnL -1.25% → +0.5
- Time remaining short (last ~5 min of plan) → 0
- Age 498s → 0
- Velocity unclear → 0
- SL consumption ~50% → -1
- Structure: bullish_fvg_ob_counter still active → -1
- Brain vote with structural reasoning → +1.5
- Composite: ~0. < +6 threshold → HOLD + log; no tighten (composite >= 0).

### Scenario B — Historical HYPEUSDT -$2.74 (would have been STRONGLY HELD)

- PnL -0.20% → -3
- Time remaining moderate → -1
- Age long (1899s) → 0 to +0.5
- SL consumption low → -2
- Brain vote → +0.5
- Composite: ~ -5 to -5.5. < 0 → HOLD + tighten SL toward break-even.

### Scenario C — Winning brain close at +1.2% PnL (REGRESSION SAFETY — should still fire)

- PnL +1.2% → +3
- Time remaining short → +1 (clamped)
- Age moderate → 0
- Velocity slightly positive → -1
- SL consumption ~10% → -2
- Structure neutral → 0
- Brain vote with vague reasoning → +0.5
- Composite: ~+1.5. < +6 → REJECTED in enforce mode. **This is intentional**: a winning close at low conviction is suspect; the deadline / TP path will handle it. If operator wants more closes on winners, they raise the PnL+ factor weight; default keeps the system holding.

If the operator prefers winners to fire more eagerly: raise the PnL +1.0% bucket weight from +3 to +5 in `wd_brain_scoring_factor_weights`. This is a tuning knob, not a code change. Recommend deferring tuning until log-only data is in hand.

## H. Critical Files (Issue 1 Touch List)

### New
- `src/risk/wd_brain_scoring.py` — pure scoring module (factor evaluators + composite).
- `tests/test_wd_brain_scoring.py` — surgical unit + integration tests.

### Modified
- `src/workers/position_watchdog.py` — wire scoring into `_execute_strategic_actions` (insertion at line 3031); add `_brain_score_prev_pnl` cache; add `_tighten_sl_breakeven_30pct` helper. NO modification to `_push_sl_to_shadow`, `_calculate_pnl_pct`, `_calculate_sl_proximity` — reused as-is.
- `src/config/settings.py` — add `wd_brain_scoring_*` fields to `WatchdogSettings` (after line 953); add loader entries (after line 3642).

### Reused without modification
- `coordinator.get_trade_plan` and `get_age_seconds`.
- `TimeDecayState._td_states` (read-only access).
- `structure_cache.get` and `StructureAnalysis.trade_direction`/`structural_placement`.
- `STRUCT_GUARD_VERDICT_MAX_AGE_S` constant from `layer4_protection.py`.
- Existing watchdog logging pattern with `ctx()`.
