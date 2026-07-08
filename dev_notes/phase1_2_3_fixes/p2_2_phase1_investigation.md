# P2-2 Phase 1 — Telegram Detach Investigation

## TL;DR

`alert_manager.send_custom(priority=AlertLevel.INFO)` already accepts a severity parameter — but `_send()` at line 208 unconditionally `await`s `bot.send_message` for every priority. This blocks the critical trade path for up to ~40 s on a Telegram retry storm (HTTP 429 / network blip). All 8 of the awaited callers today are TRUE INFO (post-trade notifications, summaries, switch-over alerts). Exactly ONE caller is TRUE CRITICAL: `position_watchdog.py:599` emergency_close. The cleanest fix is to gate fire-and-forget inside `_send()` itself by priority, leaving every call site unchanged.

## 1. Alert Flow Shape (verified end-to-end)

### `src/alerts/alert_manager.py`

- `send_custom(message: str, priority: AlertLevel = AlertLevel.INFO) -> bool` — line 161. Default priority is INFO.
- `_send()` at lines 171–226 is the choke point. Sequence:
  1. Throttle / dedup checks (lines 194–201) — sync, fast.
  2. `silent = (priority == AlertLevel.INFO)` (line 202).
  3. **`success = await self.bot.send_message(message, silent=silent)`** (line 208) — blocking step.
  4. Emit `ALERT_SENT` or `ALERT_FAIL` log with the level reason (lines 213–221).
- ~15 public methods (`send_trade_alert`, `send_signal_alert`, `send_brain_decision_alert`, `send_error_alert`, `send_risk_warning`, etc.) all funnel into `_send`.

### `src/alerts/telegram_bot.py`

- `send_message(text, parse_mode="HTML", silent=False) -> bool` — line 87.
- Blocking retry loop (lines 128–154): attempt 1 → 2 s → 5 s → 10 s ≈ **~17 s of waiting in the worst non-final retry case**, plus HTTP-timeout per attempt: read=15, write=15, connect=10. A truly bad network call can therefore exceed 40 s.
- Never raises — always returns bool. So the upstream `_send` sees a False (logged as ALERT_FAIL) but does not crash on Telegram outage.
- Logs `TG_SEND_RETRY` / `TG_SEND_RETRY_OK` / `TG_SEND_ABANDONED`.

### `AlertLevel` enum

- `src/core/types.py:123–127` defines `AlertLevel(str, Enum)` with members `INFO`, `WARNING`, `CRITICAL`.
- Str-enum: string literals (`"INFO"`, `"CRITICAL"`) auto-coerce in any call site that passes them.

## 2. INFO Call-Site Inventory

| File:Line | Context | Today | Classification |
|-----------|---------|-------|----------------|
| `strategy_worker.py:2558` | Trade-entry notification (dir, SL/TP, RR) | `await` + `AlertLevel.INFO` | TRUE INFO |
| `position_watchdog.py:2706` | Close summary (pnl, strategy, thesis) | `await` + `AlertLevel.INFO` | TRUE INFO |
| `position_watchdog.py:3485` | External (exchange-triggered) close | `await` + `priority="INFO"` | BOUNDARY — position already closed; pure notify |
| `profit_sniper.py:2333` | Mode4 action notify | `await` + `"INFO"` | TRUE INFO |
| `profit_sniper.py:3958` | Mode4 loss-cut / crash protection report | `await` + `"INFO"` | TRUE INFO |
| `profit_sniper.py:3986` | Sniper result (captured %, counterfactual) | `await` + `"INFO"` | TRUE INFO |
| `post_switch_verifier.py:118` | Post-restart system summary | `await` + (no priority → default INFO) | TRUE INFO |
| `exchange_switcher.py:277` | Pre-restart switchover notify | `await` + (no priority → default INFO) | TRUE INFO |

All 8 are post-decision notifications. Removing the `await` will not delay any trade-execution decision.

## 3. CRITICAL Call-Site Inventory

| File:Line | Context | Today | Classification |
|-----------|---------|-------|----------------|
| `position_watchdog.py:599` | Emergency: all positions force-closed | `await` + `AlertLevel.CRITICAL` (or `"CRITICAL"` string) | TRUE CRITICAL |

The operator's aim is aggressive opportunity exploitation — but if the system emergency-closes ALL positions, the operator MUST be alerted so they can manually restart and resume. Delivery must be retried until success or audit-logged loud.

## 4. Existing Fire-and-Forget Patterns in Repo

Found ONLY in `src/core/layer_manager.py`:

- `_send_plan_telegram()` lines 996–998 — `asyncio.create_task(alert_manager.send_custom(plan.to_telegram_text(), AlertLevel.INFO))`
- `_send_cold_start_telegram()` lines 1101–1106 — `asyncio.create_task(alert_manager.send_custom(..., AlertLevel.WARNING))`

Both wrap the entire `send_custom` call. No done-callback to log failures — silent loss is possible. Wrapped in try/except at task-creation time only.

Neither place has been duplicated elsewhere; fire-and-forget is rare in the codebase by convention.

## 5. Current Telegram Health (5-hour log + today)

- 5-hour log: 4 `ALERT_FAIL` events, 111 telegram-related lines, 61 telegram error-classified mentions.
- Today's `brain.log` (current session): 0 `ALERT_FAIL`, 0 `TG_SEND_*` — Telegram is healthy NOW.
- The fix is for the ARCHITECTURAL defect — any future Telegram slowdown will re-trigger the symptom.

## 6. Fix-Shape Options

| Option | Mechanism | LOC | Risk | Migration |
|--------|-----------|-----|------|-----------|
| **A** — Gate inside `_send` by priority (RECOMMENDED) | When `priority == AlertLevel.INFO`, wrap the actual transport call in `asyncio.create_task` + done-callback. CRITICAL keeps `await`. New tags `ALERT_FIRE_AND_FORGET` / `ALERT_AWAITED`. | 30–50 | LOW | None — all 8 call sites unchanged |
| **B** — Add `kind` kwarg to `send_custom` | Caller picks behavior; defaults to current await for safety. | 50–80 | MEDIUM | All INFO call sites must pass `kind=info` to opt in |
| **C** — Add `send_info_custom` / `send_critical_custom` methods | New explicit API; migrate all 8 call sites. | 70–100 | MEDIUM-HIGH | 8 call-site changes |
| **D** — Wrap at each call site with `asyncio.create_task` | Leave alert_manager untouched. | 10–20 | HIGH | Per-call-site wrapping; inconsistent; double-tracks fire-and-forget |

**Option A is clearly best.** Centralizes fire-and-forget in one place. Zero migration. Backward-compatible. Existing layer_manager wrappers become harmlessly double-wrapped (their `create_task(send_custom(...))` wraps a now-fire-and-forget `send_custom`; the outer task awaits the inner's optimistic True return and completes immediately).

## 7. Done-Callback Design (Option A internals)

For INFO path the helper task does:

1. `await self.bot.send_message(message, silent=True)`
2. If success → emit `ALERT_SENT` (same as today).
3. If failure → emit `ALERT_FAIL`. The done-callback (`task.add_done_callback`) traps uncaught exceptions so they don't disappear silently.

For CRITICAL path:
- `await` is preserved verbatim. Emit `ALERT_AWAITED kind=critical` once. Existing `ALERT_SENT` / `ALERT_FAIL` emits unchanged.

## 8. Throttle / Dedup Considerations

Throttle + dedup at lines 194–201 RUNS BEFORE the await. The fix only changes what happens AFTER the dedup decision. So message storms still get throttled the same way. No regression in spam prevention.

## 9. Aim Preservation Check

- No trade-frequency change.
- No defensive bias.
- All 8 INFO call sites are POST-execution notifications. Removing await does not delay any trade or risk-management decision.
- The CRITICAL path (emergency_close) keeps blocking, so the operator's halt-notification guarantee is preserved.

## 10. NOT FOUND

- No existing `kind` parameter beyond the AlertLevel enum (already plumbed through).
- No fire-and-forget pattern inside `alert_manager` itself (only in `layer_manager`).
- No `ALERT_FAIL` or `TG_SEND_*` events in today's brain.log — cannot validate against a current slowdown event. The architectural defect is the warrant for fixing.

## 11. Hard Constraints for the Fix

- INFO alerts: non-blocking. The critical trade path returns within microseconds.
- CRITICAL alerts: still awaited, still reliable delivery, still surfaces ALERT_FAIL on failure.
- Fire-and-forget tasks log delivery failures via `ALERT_FAIL` (NOT silent loss).
- `alert_manager.send_custom` signature unchanged.
- All 9 call sites unchanged.
- Shadow unaffected.

## 12. FORBIDDEN Band-Aid Choices

- Wrap all alerts in fire-and-forget (loses CRITICAL delivery guarantee).
- Remove Telegram alerts entirely.
- Add sleeps before Telegram calls.
- Catch the Telegram error and proceed silently for critical alerts.

## 13. Recommended Next Step

Option A: minimal change inside `_send`. Two parts:

1. Extract the actual transport-and-log into a coroutine `_do_send_and_log(alert)` (private).
2. In `_send`, gate by `priority == AlertLevel.INFO`:
   - INFO → `asyncio.create_task(self._do_send_and_log(...))` + done-callback that traps exceptions → return True optimistically. Emit `ALERT_FIRE_AND_FORGET kind=info bypass=Y`.
   - CRITICAL/WARNING → await `self._do_send_and_log(...)` directly. Emit `ALERT_AWAITED kind=critical` (only for CRITICAL — WARNING uses existing path).

Tests:
- INFO path returns immediately even when the bot mock takes 5 s.
- CRITICAL path waits for the bot mock.
- Failure in INFO path still logs `ALERT_FAIL` via the done-callback.
- Dedup/throttle still applied.

Verification:
- 6 CALL_A cycles minimum.
- `POST_PLACE_TIMING total_ms` P95 drops materially vs baseline (today's `STRAT_CALL_A_END` doesn't directly measure this — need to also count `ALERT_FIRE_AND_FORGET` emits to confirm fire-and-forget actually fires for entries).
- `ALERT_FAIL` count remains a meaningful signal of delivery failure (NOT zero unless Telegram is healthy).

Awaiting operator approval of Option A.
