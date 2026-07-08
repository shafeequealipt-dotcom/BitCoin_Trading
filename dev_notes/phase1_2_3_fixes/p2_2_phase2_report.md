# P2-2 Phase 2 — Operator Decision Report (Telegram Detach)

Captured retrospectively for audit completeness. The Phase 2 operator decision was made on 2026-05-13 ~20:21 UTC via `AskUserQuestion` after Phase 1 investigation concluded; this file documents the decision and the rationale for the audit trail.

## Diagnosis Summary (from Phase 1)

- `alert_manager.send_custom(priority=AlertLevel.INFO)` already accepts a severity parameter (`AlertLevel.INFO`/`WARNING`/`CRITICAL` from `src/core/types.py:123-127`).
- But `_send()` at `alert_manager.py:208` unconditionally awaits `bot.send_message` for every priority — blocking the critical trade path for up to ~40 s on Telegram retry storms (HTTP 429 / network blips).
- 8 awaited INFO call sites identified (post-trade notifications across strategy_worker, position_watchdog, profit_sniper, post_switch_verifier, exchange_switcher).
- 1 CRITICAL call site identified: `position_watchdog.py:599` (emergency_close).
- Existing fire-and-forget patterns in `layer_manager.py:996` and `:1101` document the project's convention.

## Three Fix-Shape Options Presented

| Option | Mechanism | LOC | Risk | Migration |
|--------|-----------|-----|------|-----------|
| **A** (Recommended) | Gate fire-and-forget inside `_send` by priority. INFO → `asyncio.create_task`, CRITICAL/WARNING → awaited. Done-callback logs delivery failures. New tags `ALERT_FIRE_AND_FORGET`/`ALERT_AWAITED`. | 30–50 LOC | LOW | None — 9 call sites unchanged |
| **B** | Add `kind` kwarg to `send_custom`. Callers opt in. Default = current await for backward safety. | 50–80 LOC | MEDIUM | All 8 INFO call sites must pass `kind=info` |
| **C** | Add `send_info_custom` + `send_critical_custom` methods. Migrate each call site to the right method. | 70–100 LOC | MEDIUM-HIGH | 8 call-site changes |

## Operator Decision

**Option A — Gate inside `_send` by priority.** Selected on 2026-05-13 (operator answered "Gate inside _send by priority (Recommended)" in the Phase 2 AskUserQuestion).

### Rationale

- Smallest surface area; one file change covers all call sites.
- Zero call-site migration eliminates the risk of inconsistent migration leaving some INFO callers still blocking.
- Centralizes fire-and-forget logic in the alert pipeline where it belongs.
- Backward-compatible: `send_custom` signature unchanged; existing callers continue to work.
- `_pending_info_tasks` set + `flush_pending_info` coroutine preserves graceful-shutdown semantics and lets tests assert on the bot mock after the task fires.

### Constraints / Aim Preservation

- All 8 INFO callers are post-decision notifications. Removing the await does NOT delay any trade-execution decision.
- The 1 CRITICAL caller (`position_watchdog.py:599` emergency_close) keeps the awaited path — operator's halt-notification guarantee is preserved.
- Delivery failures still surface via `ALERT_FAIL` from the done-callback. Fire-and-forget is NOT silent.
- Dedup race window closed by pre-recording `throttle.record_content(h)` + `throttle.record_send()` BEFORE `asyncio.create_task` schedules the actual send.

## Implementation

Branched as `fix/p2-2-telegram-detach`. Single atomic commit `57b7484 fix(p2-2): fire-and-forget INFO alerts; keep CRITICAL awaited`. Merged into `audit/all-tier2-combined` via `333f7e1` on 2026-05-13 ~20:29 UTC. Services restarted to pick up.

## Test Coverage

`tests/test_p2_2_telegram_detach.py` (9 cases): INFO returns in microseconds despite slow bot, CRITICAL blocks, new tags fire, INFO failure still surfaces ALERT_FAIL, dedup preserved under fire-and-forget, flush_pending_info no-op when empty, disabled alert manager skips task scheduling, done-callback captures unexpected exceptions.

5 existing tests in `tests/test_phase8/test_alert_manager.py` and 1 in `tests/test_audit_fixes_e2e/test_audit_fixes_pipeline.py` updated to `await am.flush_pending_info()` before bot-mock assertions (no behavioral change, just test-timing adjustment).

## Verification Status

Tags will fire on the next INFO alert (entries/closes/summaries) and the next CRITICAL alert (emergency_close). Since deploy at 20:29 UTC, no trade-driven alerts have triggered (Claude API has been slow today, see P2-1 correction notes); the architectural fix is in place and will exercise as normal traffic resumes.
