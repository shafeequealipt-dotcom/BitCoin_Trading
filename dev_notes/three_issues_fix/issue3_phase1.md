# Issue 3 — Phase 1 — Reentry & Cooldown State Re-Verified

Date: 2026-05-18. Branch base: `fix/remove-portfolio-cap` tip `bcaae05` (stacked).
Source of design: `IMPLEMENT_THREE_ISSUES_FIX.md` Issue 3 (lines 267-311).

## A. Current Reentry Learning Gate (J6 + H4 calibration)

`src/core/trade_coordinator.py:1478-1767` — `async check_reentry_learning_gate(db, symbol, current_regime, current_setup_type, current_direction, *, lookback_hours=0.0, min_loss_usd=0.0, current_price=0.0, price_drift_pct=0.0)`.

Behaviour: queries `trade_thesis` for the most-recent CLOSED LOSING trade on the symbol (with optional H4 recency/magnitude pre-filters). Compares prior `entry_regime_at_open`, `entry_setup_type`, and `direction` to current proposal. Blocks only when ALL three match AND no H4 escape (lookback_expired, loss_below_floor, price_drift_passed) clears the path.

Log events emitted:
- `REENTRY_LEARNING_GATE_DB_FAIL` (warning, line 1637-1640) — DB error fallback.
- `REENTRY_REGIME_DRIFT_CHECK` (info, gate.py:445-452) — observability per evaluation.
- `GATE_RECALIBRATION_ALLOW` (info, gate.py:465-474) — when H4 escape clears.
- `REENTRY_LEARNING_GATE | action=block` (warning, gate.py:481-486) — block.
- `REENTRY_LEARNING_GATE | action=allow` (info, gate.py:488-491) — allow.
- `REENTRY_LEARNING_GATE_FAIL` (warning, gate.py:493-495) — defensive.

Gate invocation: `src/apex/gate.py:337-496` — CHECK 6b. Pulls regime from `regime_detector`, setup from `structure_cache` (fallback path at gate.py:355-367), current price from `market_service` (when drift escape enabled), then calls coordinator. Sets `trade["_gate_rejected"] = f"reentry_learning_gate_{_reason}"` on block.

Settings: `src/config/settings.py:2106-2139` — `reentry_learning_gate_enabled`, `_lookback_hours`, `_min_loss_usd`, `_price_drift_pct`.

## B. Current Loss Cooldown (T2-1 / F20 six-tier-fixes)

State variables: `src/core/trade_coordinator.py:152-158`.
- `_symbol_cooldowns: dict[str, float]` — symbol → expiry wall-clock.
- `_loss_cooldown_direction: dict[str, str]` — symbol → losing direction.

Setter in `on_trade_closed` at `src/core/trade_coordinator.py:1197-1221`:
- Hardcoded branch: 180s win / 600s loss / 900s hard_stop / mode4_crash.
- Records loss direction only for losses (line 1212-1213).
- Clears loss direction on a winning close (line 1220).

Helpers:
- `is_symbol_cooled_down(symbol)` at lines 1439-1451 — checks expiry; lazy-cleans on expiry by also popping the loss-direction map.
- `get_loss_cooldown_direction(symbol)` at lines 1453-1469 — returns recorded direction (None if not in cooldown OR was a win cooldown).
- `get_symbol_cooldown_remaining(symbol)` at lines 1471-1476 — int seconds remaining.

Gate invocation: `src/apex/gate.py:276-320` — CHECK 6. Hard-rejects same-direction loss cooldown (`loss_cooldown_same_direction_*s`); halves size otherwise. Log events: `GATE_REJECT | reason=loss_cooldown_same_direction` (warning), `GATE_COOL_CHECK | err=` (defensive). Coordinator setter emits `COORD_LOSS_COOLDOWN_SET` (info, line 1214-1216).

## C. Position Close Hook — Single Funnel

`src/core/trade_coordinator.on_trade_closed()` at lines 901+ is the canonical close-event hook. All watchdog close paths (deadline, SL hit, brain action, timeout, hard stop, mode4 crash, partial completion) call it. Setting the new per-direction cooldown in `on_trade_closed` therefore captures every close path with zero per-path plumbing.

## D. Brain Prompt Consumers of Cooldown State

Two duplicated read sites in `src/brain/strategist.py`:
- Lines 1597-1610 (CALL_A prompt section).
- Lines 4084-4094 (CALL_B prompt section).

Both read `coordinator._symbol_cooldowns` directly via instance attribute access. Output format: `"  {sym}: in cooldown ({n}s remaining)"`. Must be rewritten to read the new per-(symbol, direction) state and emit per-direction lines.

## E. Tests Referencing Removed APIs

Categorised:

### Tests covering the deleted features end-to-end (DELETE)

- `tests/test_j6_reentry_learning_gate.py` (316 lines, 20-ish tests) — full J6 surface.
- `tests/test_h4_reentry_gate_calibration.py` (210 lines) — H4 escapes.
- `tests/test_t2_1_loss_cooldown.py` (99 lines) — loss-direction tracking.

### Integration tests that assert on the old rejection strings (UPDATE assertions)

- `tests/test_j_series_e2e_pipeline.py:562, 629-631` — `_gate_rejected.startswith("reentry_learning_gate_")` becomes `startswith("reentry_cooldown_5min_")`.
- `tests/test_six_tier_fixes_e2e_pipeline.py:343-380` — uses `is_symbol_cooled_down` and `get_loss_cooldown_direction`. Rewrite to assert on new `is_reentry_blocked` per-direction behaviour.

### Tests with fixture-only references to `_symbol_cooldowns` (UPDATE)

- `tests/test_strategist_callb_prompt.py:42, 74, 253` — fixture init.
- `tests/test_thesis_xray_flip.py:188` — fixture init.
- `tests/test_brain_enrichment/test_direction_perf_callb.py:53` — fixture init.
- `tests/test_critical1_pnl_back_derive.py:327, 347` — asserts cooldown was set via reading `_symbol_cooldowns`. Rewrite to assert on `is_reentry_blocked` semantics.
- `tests/test_firewall_and_time_decay.py:815` — single line assertion `coord.is_symbol_cooled_down("COOLUSDT")`. Rewrite to `coord.is_reentry_blocked("COOLUSDT", "Buy")[0]` or similar.
- `tests/test_watchdog/test_strategic_action_min_hold_guardrail.py:56` — mocks `is_symbol_cooled_down` with `MagicMock(return_value=(False, 0.0))` (already tuple-shaped — interesting hint someone planned this signature). Update mock target to `is_reentry_blocked`.

### NEW

- `tests/test_reentry_cooldown_5min.py` covers the 4 trial scenarios from prompt §D Step 3.5 — surgical, not exhaustive.

## F. Settings Touchpoints

- `src/config/settings.py:809` — `loss_cooldown_seconds: int = 300` is read by `src/risk/drawdown.py:194,215` (separate drawdown cooldown concept). **OUT OF SCOPE — leave alone.**
- `src/config/settings.py:2106-2139` — the `reentry_learning_gate_*` block. **DELETE.**
- `src/config/settings.py:3587` — `loss_cooldown_seconds=data.get(...)` in the loader. **KEEP** (loader for the drawdown setting).
- Add `reentry_cooldown_seconds: int = 300` to APEXSettings, replacing the deleted block.

## G. Atomic Commit Plan (locked)

Branch: `fix/5min-reentry-cooldown` (stacked on `fix/remove-portfolio-cap` so it carries the cap removal forward).

1. `issue3/p3-1 feat(coordinator): add 5-min per-direction reentry cooldown state and API` — `_reentry_cooldown` dict, `is_reentry_blocked`, `clear_expired_reentry_cooldowns`, `get_active_reentry_cooldowns`, `REENTRY_COOLDOWN_5MIN_SET/_CLEARED` log events. Set in `on_trade_closed` (additive — doesn't touch the legacy yet). Monotonic clock, lazy cleanup.
2. `issue3/p3-2 feat(gate): replace CHECK 6 + CHECK 6b with single 5-min cooldown check` — new CHECK 6 calling `is_reentry_blocked`. Emit `REENTRY_COOLDOWN_5MIN_BLOCKED`. Remove old CHECK 6 + CHECK 6b bodies.
3. `issue3/p3-3 refactor(coordinator): remove legacy loss_cooldown + reentry_learning_gate` — delete `check_reentry_learning_gate`, `_symbol_cooldowns`, `_loss_cooldown_direction`, `is_symbol_cooled_down`, `get_symbol_cooldown_remaining`, `get_loss_cooldown_direction`, hardcoded 180/600/900 branches in `on_trade_closed`, `COORD_LOSS_COOLDOWN_SET`.
4. `issue3/p3-4 refactor(brain): per-direction reentry cooldown in strategist prompt` — both prompt sections updated.
5. `issue3/p3-5 chore(config): remove reentry_learning_gate settings, add reentry_cooldown_seconds` — settings.py changes.
6. `issue3/p3-6 test: replace J6/H4/T2-1 with surgical 5-min cooldown tests` — `git rm` the three deletions + create `test_reentry_cooldown_5min.py` (4 scenarios) + update existing tests' fixtures and assertions.
