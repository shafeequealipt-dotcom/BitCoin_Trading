# Issue 5 — Fund balance reconciliation drift (Bybit ErrCode 110007)

**Status:** PRESENT — no reconciler exists; ErrCode 110007 observed.
**Tier:** 2 (recurring trade rejections).
**Source observation:** `dev_notes/layer1_live_monitor_2026-04-27.md` lines 217-218 (Finding #7); current `FUND_POOLS` snapshot 07:39-07:58 UTC.

## A. Mechanism

`FundManager` at `src/fund_manager/manager.py` is the local capital-allocation source of truth. Its state is updated via `update_state()` (lines 136-200) which calls `account_service.get_wallet_balance()` (lines 136-150 balance, 178-197 position) on every tick, computes:

```python
state.available = max(0, state.trading_capital - state.in_use)   # line 199
log.info(f"FUND_POOLS | cap={state.trading_capital:.2f} | available={state.available:.2f} | in_use={state.in_use:.2f}")
```

Observed in workers.log: `FUND_POOLS | cap=1257.05 | available=1257.05 | in_use=0.00` every minute, unchanging.

`update_state()` is called by `fund_manager_worker` (registered in `workers/manager.py`), so a fresh wallet snapshot IS pulled — but the result is mapped to `state.trading_capital` (the cap), not compared to local in-flight position margin. There is no explicit "compare local committed margin to Bybit committed margin" step.

When OrderService places an order with insufficient available margin, Bybit returns `ErrCode 110007` ("ab not enough for new order"). `src/trading/client.py:57` maps 110007 → `PositionError("Position not exists")` — the human-friendly retMsg differs from the retCode mapping ("not enough" vs "not exists"). `OrderService` retries up to `_ORDER_PLACE_MAX_ATTEMPTS = 2` (line 58) with 0.5s delay; retry is futile because balance won't change in 500ms. Two attempts exhaust; `ORDER_RETRY_EXHAUSTED` logs.

Live evidence: 06:27:15.121 ETHUSDT and 06:27:16.040 BTCUSDT both `purpose=mcp_tool err=ab not enough for new order (ErrCode: 110007)`.

## B. Dependencies

- **State source:** `src/fund_manager/manager.py` (1000+ lines, 22 sub-modules; `update_state` at 136-200).
- **Account API:** `src/trading/services/account_service.py` `get_wallet_balance()` returns `total_equity`, `available_balance`.
- **Order placement:** `src/trading/services/order_service.py:372` calls `account_service.get_wallet_balance()` for HARD POSITION SIZE CAP (good — already pulls fresh balance). 110007 path is unhandled in retry loop.
- **Worker registry:** `src/workers/manager.py:832-1095` lists 13 workers. No fund_reconciler. Adding one extends the list.
- **Telegram alert path:** existing in `src/telegram/handlers/*` for system events.

## C. Constraints

- Must not auto-overwrite local fund state with Bybit silently (operator must opt-in via config — auditability).
- Must not reconcile faster than 60s — REST quota and CPU.
- Must preserve the existing `FUND_POOLS` log shape (downstream alerts may grep it).
- Existing failure counters (`_consecutive_balance_fails`, `_consecutive_position_fails`, `_FAIL_ALERT_THRESHOLD = 3` at lines 89-91) are tracking-only — preserve or extend; do not replace.
- Pre-flight check must NOT block legitimate trades; a small expected drift (< threshold) is normal.

## D. Fix candidates

1. **New `FundReconciler` worker + pre-flight + 110007 ad-hoc reconcile (chosen).**
   - 60s tick that calls `account_service.get_wallet_balance()`, compares to local view, emits `FUND_RECONCILE`. If `|drift_pct| > drift_alert_threshold_pct`, emit `FUND_RECONCILE_DRIFT` at WARNING + Telegram alert.
   - Pre-flight in `OrderService.place_order` before Bybit call: if local view says insufficient, emit `ORDER_PREFLIGHT_INSUFFICIENT` and abort early (no Bybit call, no retry).
   - In ErrCode 110007 path: don't retry, trigger ad-hoc reconcile, emit `FUND_DRIFT_DETECTED_VIA_ORDER_REJECT`.
   - Daily summary `FUND_DAILY_SUMMARY` at midnight UTC.
2. Subscribe to Bybit's wallet WebSocket channel for live updates. Rejected — adds a second long-lived WS, complicates teardown, redundant for 60s cadence.
3. Synchronous reconciliation inside `OrderService.place_order` on every call. Rejected — slow + chatty against the REST quota.
4. No reconciler; just fix the 110007 retry behavior. Rejected — band-aid; doesn't address the drift.

## E. Observability gap

- No periodic `FUND_RECONCILE` event today. Operators don't know when local and Bybit diverge.
- No `ORDER_PREFLIGHT_INSUFFICIENT` event. Wasted 2-attempt retry on guaranteed-fail orders.
- No `FUND_DAILY_SUMMARY` event. Daily PnL audit requires DB queries.
- Existing `FUND_POOLS` shows local view only — no Bybit comparison.

## F. Verification approach

- Unit test (reconciler): mock `account_service` returning skewed balance vs local, assert `FUND_RECONCILE` then `FUND_RECONCILE_DRIFT` events.
- Unit test (auto-correct): with `auto_correct_on_drift=true`, assert local fund_manager state is overwritten on next tick.
- Unit test (preflight): mock `fund_manager.state.available=0`, attempt place_order with notional > 0, assert `ORDER_PREFLIGHT_INSUFFICIENT` raised pre-Bybit-call.
- Unit test (110007): mock Bybit raising PositionError with message containing 110007, assert no retry + ad-hoc reconcile triggered.
- Live trial: 5-min window → 5 `FUND_RECONCILE` events (one per minute). Manual transfer of $1 in/out → drift detected within 60s with WARNING + Telegram.
- 24h trace: zero `ORDER_RETRY_EXHAUSTED` with 110007 in `purpose=brain_auto`.

## G. Rollback path

Four atomic commits:
- Revert FundReconciler worker → drops the new worker registration; system returns to previous state (fund_manager_worker still updates local view; just no reconciliation).
- Revert preflight → preflight check removed; orders flow as before, including the 2-attempt retry on 110007.
- Revert 110007 handling → ad-hoc reconcile and Telegram alert removed; existing `ORDER_RETRY_EXHAUSTED` path preserved.
- Revert tests.

No DB migration. No state mutation that needs cleanup. Worker registration is added to a list — removing the registration is a one-line revert.
