# Phase 3 — Emergency-path audit (operator-confirmed: investigate both)

Spec: `IMPLEMENT_LAYER4_REALIGNMENT_INDEPTH.md` Phase 3 + Issues 4 + 5
Plan: `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-breezy-ember.md`
Date: 2026-05-06
Parent commit: `14cbd7b` (Phase 2)

The audit framed `emergency_manual` as "8-25 events per window — not rare,"
suggesting routine triggers misclassified as emergency. The Phase 0
investigation showed this is a different mechanism than the audit assumed:
- `emergency_manual` is the close-reason on `trade_log` for OPERATOR-initiated
  closes via Telegram emergency-close buttons.
- The watchdog's separate `emergency_close` event tag is SYSTEM-initiated mass
  close (session_pnl ≤ -5 %, hard_stops ≥ 3/h, margin < 10 %).

Per operator decision (AskUserQuestion: "Both paths"), Phase 3 of the realignment
splits into two sub-phases:

- Phase 3.1 (this document) — `emergency_manual` audit + regression guard.
- Phase 3.2 (next commit) — recalibrate watchdog `emergency_close` triggers.

## Phase 3.1 — `emergency_manual` is operator-only

### Code paths verified (file:line)

The literal string `emergency_manual` appears in exactly one source location:
`src/core/layer_manager.py:625` inside `LayerManager.emergency_close_all`:

```python
async def emergency_close_all(
    self, *, reason: str = "manual_emergency", actor: str = "operator",
) -> str:
    ...
    for pos in positions:
        try:
            if coordinator:
                coordinator.set_close_reason(pos.symbol, "emergency_manual")  # line 625
            await position_service.close_position(pos.symbol)
            ...
```

The method is called from exactly two places in the source tree, both on the
operator-authenticated Telegram interface:

1. `src/telegram/handlers/dashboard_handler.py:1429-1438` — handler for callback
   `dash_emergency_close` (the dashboard's "🚨 CLOSE ALL" button at line 586/592).
   Calls `layer_manager.emergency_close_all(reason="telegram_dash_emergency",
   actor=f"telegram_user:{chat_id}")`.

2. `src/telegram/handlers/control_handler.py:300-307` — handler for callback
   `emergency_close` (the control menu's "EMERGENCY CLOSE ALL" button at
   line 212). Calls `layer_manager.emergency_close_all(reason=
   "telegram_control_emergency", actor=f"telegram_user:{_user}")`.

Both callers thread the operator's Telegram user-id into the `actor` field for
audit logging. Both callers fall back to a position-service-only emergency-close
if `layer_manager` is unavailable; **the fallback paths do NOT set the
`emergency_manual` close-reason** (the close proceeds without one). The
fallback is therefore not a hidden source of `emergency_manual` events.

### Telegram authentication

The Telegram bot enforces operator authentication at the bot-init level — only
the operator's whitelisted chat-id can issue callbacks. This is the standard
authentication pattern across all Telegram handlers and is out of scope for
this audit. No regression-prone path was found that bypasses this layer.

### Recent log evidence (Phase 0 baseline window)

Pre-restart 24 h: 4 distinct `emergency_manual` trade-closures (17 cascading
log lines).
Post-restart: 1 distinct trade-closure (8 cascading log lines).

In both windows, the operator had pressed the Telegram emergency button.
This is the expected behaviour — operator-initiated, not routine.

### Conclusion

`emergency_manual` is correctly scoped to operator action. **No code change
required.**

A regression guard test is added at
`tests/test_layer4_emergency/test_emergency_manual_scope.py` to fail loudly
if a future commit introduces an `emergency_manual` setter outside
`src/core/layer_manager.py`. The grep-style test runs as part of the regular
suite so the regression is caught at PR review time, not in production.

## Verification gate

| Item | Status |
|---|---|
| Source-grep for `emergency_manual` shows one setter | PASS (layer_manager.py:625) |
| Both Telegram callers use operator-authenticated `actor` field | PASS |
| Fallback paths do not set `emergency_manual` | PASS (control_handler.py:308-321 closes without close_reason) |
| Regression test asserts the scope | PASS (added) |

Phase 3.1 verification gate is GREEN. Proceeding to Phase 3.2 (watchdog
`emergency_close` recalibration).
