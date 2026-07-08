# Phase 6 — End-to-End Test Plan

**Status:** Ready to execute once Bybit demo credentials are configured and trading-workers / trading-mcp-sse services are running.

This document is the runbook for the 11 E2E scenarios from the IMPLEMENT prompt (`/home/inshadaliqbal786/IMPLEMENT_BYBIT_DEMO_ADAPTER_INDEPTH.md` Section "Phase 6 — End-To-End Testing"). For each test: setup, action, expected, then space to record actual outcome.

## Pre-conditions

- [ ] `BYBIT_DEMO_API_KEY` and `BYBIT_DEMO_API_SECRET` set in `/home/inshadaliqbal786/trading-intelligence-mcp/.env`
- [ ] `[bybit_demo] enabled = true` in `config.toml` (or env override)
- [ ] All branches merged to a deployable branch (`feature/bybit-demo-adapter` ↦ verify before final merge)
- [ ] Services running: `systemctl is-active trading-workers trading-mcp-sse shadow.service`
- [ ] Telegram bot up: send `/control` and verify dashboard renders

## Tests

### Test 6.1 — Switch with 0 positions (Shadow → Bybit Demo)
**Setup:** mode=shadow in DB, 0 open positions, system idle.
**Action:** Press "Switch to Bybit Demo (Paper)" button → confirm.
**Expected:**
- Telegram shows "Closing positions complete. Restart triggered."
- After ~60s, post-switch verifier sends "Restart complete. Now trading on bybit_demo. Equity: $X. Open positions: 0."
- `/control` dashboard shows "Exchange: Bybit Demo (paper)" and the new mode_label
- `data/post_switch_sentinel.json` is gone (verifier deletes it)
- DB `transformer_state.current_mode = 'bybit_demo'`
- DB `switch_history` has a new row with `reason LIKE 'telegram_restart_switch:%'`
- Logs (`workers.log`) show: `EXCHANGE_SWITCH_VALIDATE`, `EXCHANGE_SWITCH_CLOSE_BEGIN`, `EXCHANGE_SWITCH_DB_FLIP`, `EXCHANGE_SWITCH_RESTART_TRIGGER`, then on the next boot: `XFORM_INIT mode=bybit_demo`, `Bybit demo API: reachable`, `POST_SWITCH_VERIFY_BEGIN`, `POST_SWITCH_VERIFY_DONE`
**Actual:** _____

### Test 6.2 — Switch with 0 positions (Bybit Demo → Shadow)
**Setup:** mode=bybit_demo, 0 open positions.
**Action:** Press "Switch to Shadow (from Demo)" button → confirm.
**Expected:** Mirror of 6.1 in the reverse direction.
**Actual:** _____

### Test 6.3 — Switch with multiple positions
**Setup:** On Shadow with 3+ open positions (let the brain open them naturally).
**Action:** Press switch button → confirm.
**Expected:**
- All positions close at market via Shadow (visible in workers.log as `SHADOW_POSITION_CLOSE` events)
- After 2s settle window, switcher re-queries — list is empty
- Sentinel + restart fire as in 6.1
- After restart: `transformer_state.current_mode = 'bybit_demo'`, position list on Bybit Demo is empty
**Actual:** _____

### Test 6.4 — Switch back-to-back
**Action:** Switch shadow → bybit_demo, wait for verifier confirmation. Switch bybit_demo → shadow, wait again.
**Expected:** Both switches succeed. Two new rows in `switch_history`. Dashboard reflects state at each step.
**Actual:** _____

### Test 6.5 — First trade on Bybit Demo after switch
**Setup:** Just switched to bybit_demo. System idle, brain Layer 2 active.
**Action:** Wait for next CALL_A cycle (≤ Stage 2 brain interval).
**Expected:**
- A trade is opened. workers.log emits `BYBIT_DEMO_ORDER_RECEIVED`, `BYBIT_DEMO_ORD_SEND`, `BYBIT_DEMO_ORD_RESP` (NOT `SHADOW_*`).
- Position appears in `transformer.active_position_service.get_positions()` output
- `/positions` dashboard shows the new position with realistic Bybit fill price
**Actual:** _____

### Test 6.6 — Trade lifecycle on Bybit Demo
**Setup:** ≥ 1 trade open on bybit_demo.
**Action:** Allow the trade to develop until SL or TP triggers naturally.
**Expected:**
- Bybit's matching engine triggers SL/TP at the exchange level
- Position disappears from `get_positions()` on next poll
- Layer 4 detects the close (via `get_last_close()` returning Bybit's `closedPnl` data)
- TIAS records the trade outcome with the correct PnL
**Actual:** _____

### Test 6.7 — Layer 4 protections on Bybit Demo
**Setup:** ≥ 2 trades open on bybit_demo.
**Action:** Wait until a trade ages past sniper minimum-age threshold (~5 min).
**Expected:** `SNIPER_AGE_GUARD`, `TIME_DECAY_AGE_GUARD`, CALL_B management events fire identically to Shadow. Same business logic; only the exchange differs.
**Actual:** _____

### Test 6.8 — MCP tools on Bybit Demo
**Setup:** mode=bybit_demo with at least 1 position.
**Action:** Call MCP tools: `get_current_exchange`, `validate_switch("shadow")`, `get_positions`, `get_account_info`.
**Expected:** All return Bybit Demo data (positions match what's open on demo, equity matches Bybit Demo wallet). NO Shadow data leaks.
**Actual:** _____

### Test 6.9 — Failure modes
- **6.9.A — Bybit Demo unreachable:**
  Setup: temporarily set wrong API key in `.env`, restart workers, attempt switch.
  Expected: `validate_switch` reports `target_reachable=False`. Switch fails at the existing reachability check inside `Transformer.switch_to`'s validator (or the equivalent gate ExchangeSwitcher runs via Phase A) with clear error.
  **Actual:** _____

- **6.9.B — Close-all partial failure:**
  Setup: simulate (test-only patch) one of three positions failing to close.
  Expected: ExchangeSwitcher retries once, then aborts with `EXCHANGE_SWITCH_ABORT_OPEN_POSITIONS` and reverts `is_switching=False`. System remains on current exchange.
  **Actual:** _____

- **6.9.C — systemctl missing:**
  Setup: temporarily revoke systemctl access (e.g., move binary, or simulate `FileNotFoundError` via test-only patch).
  Expected: `EXCHANGE_SWITCH_NO_SYSTEMCTL` log + clear error returned to operator. Mode unchanged.
  **Actual:** _____

### Test 6.10 — Capital tier and Performance Enforcer post-switch
**Setup:** Recently switched to bybit_demo with a different equity than Shadow.
**Action:** Verify capital tier recomputes from new equity.
**Expected:** Capital tier reflects new equity. Performance Enforcer at NORMAL (no accumulated PnL on new exchange yet).
**Actual:** _____

### Test 6.11 — Cross-cycle switch
**Setup:** Active brain cycle (mid-CALL_A).
**Action:** Trigger switch.
**Expected:** Switch sets `is_switching=True` in DB so any in-flight order placement is blocked by the existing Transformer guard. Worst case: the in-flight CALL_A finishes against the OLD exchange before the close-all phase, but no new orders go through. Restart cleans up everything.
**Actual:** _____

## Failure Recovery Notes

If a switch is interrupted (process crash mid-way):
- DB has `is_switching=True` and `switching_to=<target>` persisted
- On next boot, `Transformer.initialize()` runs the existing crash-recovery branch (lines 124-176): if positions still open on old exchange → cancel switch, mark `is_switching=False`, log `startup_recovery`. If no positions → complete the switch by flipping `current_mode` to `switching_to`.
- The post-switch sentinel may also be present without a corresponding DB flip; the verifier handles missing/partial sentinel data without aborting boot.

## Sign-off

When all 11 scenarios pass without workarounds:
- [ ] Update `phase6_test_results.md` with each Actual outcome
- [ ] Commit: `test(bybit_demo/6): end-to-end test suite passing`
- [ ] Proceed to Phase 7 trial.
