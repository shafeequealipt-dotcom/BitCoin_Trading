# CRITICAL-1 Phase 2 — Operator Discussion Report

## Summary

This report presents Phase 1 findings for CRITICAL-1 (universal pnl=0 corruption in trade_log, trade_intelligence, trade_thesis), confirms the audit's diagnosis against current code, and offers three implementation options for the operator's decision. No code is changed yet.

---

## Section 1 — What the audit said

The audit (`AUDIT_BYBIT_COMPLETE_DATA_FLOW_FINDINGS.md`, Section 2, CRITICAL-1) stated:

The bybit_demo WebSocket subscriber (`bybit_demo_websocket_subscriber.py`) calls `coordinator.on_trade_closed(...)` with `pnl_pct=0.0, pnl_usd=0.0, was_win=False, exit_price=exec_price` and an explicit comment: "back-derived by coordinator from state + exit_price". The trade_coordinator's `on_trade_closed` has back-derive branches at lines 685-696, but both branches require `pnl_pct != 0`. Since the subscriber passes 0, the back-derive never runs. Result: every record carries `pnl_pct=0`, `pnl_usd=0`, `was_win=False`, propagating to trade_log, trade_intelligence, trade_thesis. The system-initiated close path through `bybit_demo_adapter.close_position` (lines 248-438) writes correct PnL inline to trade_history, which is why trade_history is the only correct table.

---

## Section 2 — What current code shows

The audit is correct in full. Refinements identified during Phase 1:

### Subsection 2.1 — Confirmed file:line references

- `bybit_demo_websocket_subscriber.py:489-497` passes `pnl_pct=0.0, pnl_usd=0.0, was_win=False, exit_price=exec_price, price_source="bybit_ws_authoritative"`. Comments at lines 491-493 explicitly claim coordinator back-derives. Confirmed.
- `trade_coordinator.py:639` defines `on_trade_closed`. Function uses `_trades.pop(symbol, None)` for atomic state retrieval. Confirmed.
- `trade_coordinator.py:687-688` sets `close_price = float(exit_price)` when caller supplies it (always true from WS subscriber). Confirmed.
- `trade_coordinator.py:689-693` is back-derive of `close_price` from `pnl_pct`, gated on `pnl_pct != 0`. Dead code given lines 687-688 always set close_price first. Confirmed.
- `trade_coordinator.py:696` is back-derive of `pnl_usd`, gated on `pnl_usd == 0 AND pnl_pct != 0 AND entry_price > 0`. The middle term blocks the back-derive when subscriber passes pnl_pct=0. Confirmed.
- `trade_coordinator.py:713-760` builds the record dict. Stores `pnl_pct` and `pnl_usd` from function parameters (both 0). Includes `closed_at` (ISO string) but not `opened_at` (the source of CRITICAL-2). Confirmed.
- `workers/manager.py:1860-1898` defines `_data_lake_close_callback`. Reads `record["pnl_pct"]` and `record["pnl_usd"]`. Calls `data_lake.write_trade(...)` without passing `opened_at`. Confirmed.
- `data_lake.py:93-110` defines the DL_TRADE_SUSPECT guard. Fires `alert_manager.send_risk_warning("DL_TRADE_SUSPECT", ...)` when `pnl_pct == 0 AND entry_price > 0 AND exit_price > 0 AND entry_price != exit_price`. `send_risk_warning` is hardwired to `AlertLevel.CRITICAL` per `alert_manager.py:98`. Confirmed.
- `bybit_demo_adapter.py:248-438` defines `close_position`. Computes PnL inline at lines 392-401 (with `Side.SELL` direction handling). Calls `_trading_repo.save_trade(trade)` at line 413, writing to trade_history. Does NOT call `coordinator.on_trade_closed` directly. Confirmed.

### Subsection 2.2 — Refinements

Three nuances not explicit in the audit but found during Phase 1 investigation:

1. The dead-code branch at line 689 means the close_price back-derive is unreachable given the subscriber always supplies `exit_price`. The bug is purely in the missing pnl_pct back-derive and the consequent failure of the pnl_usd back-derive.

2. Two different exit prices are in flight. The adapter polls `/v5/order/realtime` for the actual fill price; the WS execution event independently reports `execPrice`. These are usually identical. Sample observation in the data: AEROUSDT shows trade_log exit = 0.5147 (WS price) vs trade_history exit = 0.5156 (adapter poll). The coordinator-side fix uses the WS price (already in `record["close_price"]`); trade_history keeps its own value. Both paths remain authoritative; small differences are by design.

3. The 14-callback fan-out is a feature, not a complication. Fixing the record dict at coordinator level fixes 6 downstream consumers of `was_win` (Performance Enforcer, fund_manager, registry, /pnl handler, TIAS, learning_repo) and 3 downstream consumers of `pnl_pct`/`pnl_usd` (trade_log via data_lake, trade_thesis via thesis_manager, trade_intelligence via TIAS) in one place.

### Subsection 2.3 — Database evidence

- Total bybit_demo trade_log rows with pnl_usd=0: 116 of 116 (100 percent).
- Total closed bybit_demo trade_thesis rows with actual_pnl_usd=0: 120 of 253 (47 percent). The remainder (133 of 253) had pnl backfilled by the watchdog overwrite race; 120 missed that backfill (HIGH-6, auto-resolves with CRITICAL-1).
- Trade_intelligence: most recent 10 rows all show pnl_pct=0, pnl_usd=0, win=0.
- DL_TRADE_SUSPECT alerts in 2.85h window: 49 (one per non-flat close).
- trade_history bd-* rows: 30, all with correct PnL via the adapter inline path.

### Subsection 2.4 — Five real samples (test oracle)

| Symbol | Direction | Entry | Exit | trade_log pnl_pct | trade_history pnl_pct (correct) |
|---|---|---|---|---|---|
| ADAUSDT | Sell | 0.272 | 0.2721 | 0.0 (corrupt) | -0.0367647 |
| IMXUSDT | Sell | 0.18976 | 0.18974 | 0.0 (corrupt) | +0.0105396 |
| ARBUSDT | Sell | 0.14207 | 0.14208 | 0.0 (corrupt) | -0.00703878 |
| NEARUSDT | Sell | 1.5585 | 1.5582 | 0.0 (corrupt) | +0.0192493 |
| KATUSDT | Sell | 0.01031 | 0.01031 | 0.0 (correct flat) | 0.0 (correct flat) |

Manual computation: Sell pnl_pct = ((entry - exit) / entry) * 100. Matches trade_history to 1e-7 precision on all five samples.

### Subsection 2.5 — Direction-sign formula

From `bybit_demo_adapter.py:392-401` (the trusted inline computation):
- Buy / Long: `pnl_pct = ((exit - entry) / entry) * 100`
- Sell / Short: `pnl_pct = -((exit - entry) / entry) * 100` (equivalent to `((entry - exit) / entry) * 100`)

The Side enum (`src/core/types.py:17-21`) has only "Buy" and "Sell". The coordinator's existing close_price back-derive at line 690 uses membership test `_side in ("Sell", "Short")`, defensively including the legacy "Short" alias. The CRITICAL-1 fix mirrors this convention.

---

## Section 3 — Three solution options

All three options share the same goal: when the WS subscriber passes pnl_pct=0 with valid entry/exit prices, the coordinator's downstream record dict carries the correctly-derived pnl_pct, pnl_usd, and was_win.

### Option A — Coordinator-side back-derive

**What changes**: Insert ~10 lines in `trade_coordinator.on_trade_closed` between line 693 (after close_price resolution) and line 696 (before pnl_usd back-derive):

```python
if pnl_pct == 0 and entry_price > 0 and close_price > 0:
    if _side in ("Sell", "Short"):
        pnl_pct = ((entry_price - close_price) / entry_price) * 100
    else:
        pnl_pct = ((close_price - entry_price) / entry_price) * 100
    was_win = pnl_pct > 0
    log.info(
        f"COORD_PNL_BACK_DERIVED | sym={symbol} ent={entry_price} "
        f"ext={close_price} side={_side} pnl_pct={pnl_pct:+.4f}% "
        f"win={'Y' if was_win else 'N'} | {ctx()}"
    )
```

The existing `pnl_usd` back-derive at line 696 then runs because its gate is satisfied.

**Pros**:
- Single file changed (`trade_coordinator.py`).
- Smallest blast radius; ~10 lines of new code.
- Centralised — one source of truth for back-derive.
- Subscriber's contract becomes truthful as written.
- Future callers (e.g., a hypothetical second exchange WS) get the back-derive automatically.
- New COORD_PNL_BACK_DERIVED log tag adds Phase 4 verification observability.
- All 6 downstream `was_win` consumers fix in one operation; all 3 downstream `pnl_pct` consumers fix in one operation.

**Cons**:
- Marginally expands coordinator's responsibilities (gains a price→pnl back-derive in addition to its existing pnl→price back-derive).
- The `_side in ("Sell", "Short")` membership test inherits the existing legacy alias — not new technical debt, but worth noting.

**Test cost**: 9 new tests (5 Sell parametrized, 3 Buy parametrized, plus negative-control passthrough), 1 integration test on the full close path.

### Option B — Subscriber-side computation

**What changes**: In `bybit_demo_websocket_subscriber._call_coordinator_close`, look up state via `coordinator._trades.get(symbol)` (read-only access to the entry price + side), compute pnl_pct, then pass it. Coordinator's existing pnl_usd back-derive then runs because pnl_pct != 0.

```python
# In _call_coordinator_close, before the on_trade_closed call:
state = self._coordinator._trades.get(symbol)
pnl_pct = 0.0
was_win = False
if state and state.entry_price > 0 and exit_price > 0:
    if state.side in ("Sell", "Short"):
        pnl_pct = ((state.entry_price - exit_price) / state.entry_price) * 100
    else:
        pnl_pct = ((exit_price - state.entry_price) / state.entry_price) * 100
    was_win = pnl_pct > 0

self._coordinator.on_trade_closed(
    symbol=symbol,
    pnl_pct=pnl_pct,
    pnl_usd=0.0,    # coordinator back-derives this from pnl_pct
    was_win=was_win,
    closed_by=closed_by,
    exit_price=exit_price,
    price_source="bybit_ws_authoritative",
)
```

**Pros**:
- Zero changes to coordinator code path.
- Subscriber's "back-derived by coordinator" contract is rewritten to "computed in subscriber" — accurate after the change.

**Cons**:
- Couples subscriber to coordinator's INTERNAL state (`_trades` dict access). Breaks the existing design separation that the prompt explicitly preserves ("subscriber is the consumer side of the BybitWebSocket demo extension"; coordinator's internals are not part of its public API).
- Race exposure: between the subscriber's `_trades.get()` and `on_trade_closed`'s `_trades.pop()`, another close could pop the state. Subscriber gets stale or null state. Coordinator handles the null case but not the stale case.
- Duplicates the formula — coordinator already has the close_price back-derive formula at lines 690-693; subscriber would have the inverse formula. Two-place truth.
- Future callers (any non-WS path that wants to call on_trade_closed with pnl=0 sentinel) would not get the same back-derive; each new caller needs its own copy.
- Cannot use `coordinator.pop_close_reason(symbol)` AND `coordinator._trades.get(symbol)` reliably in same call without race risk.

**Test cost**: same as A, but tests live in subscriber test file; needs additional fixtures for state mocking.

### Option C — Hybrid (Option A + subscriber comment cleanup)

**What changes**:
1. Same coordinator-side back-derive as Option A.
2. Update comments at `bybit_demo_websocket_subscriber.py:474-487` and `lines 390-400` to reflect the truthful contract (the audit reference at line 478 to "lines 612-638" is stale; correct it to "lines 685-707"; tighten the language to "back-derived by coordinator when caller passes pnl_pct=0 sentinel").

**Pros**:
- Same as Option A.
- Documentation matches code.
- Operator-approved opportunistic small fix bundled with the same atomic commit (per plan's MEDIUM/LOW handling decision).

**Cons**:
- Same as Option A.
- Marginally larger commit (5 extra comment-edit lines).

**Test cost**: same as A.

---

## Section 4 — Recommendation

**Option C** (coordinator-side back-derive plus subscriber comment cleanup).

Reasoning:
1. The coordinator is the architectural seam for "things that touch every close". The 14-callback fan-out makes it the optimal place to fix the upstream dict once and have all downstream tables benefit. This is the same pattern the existing close_price back-derive at lines 689-693 follows. Option A fits the existing pattern.
2. Option B adds coupling and race risk that Option A avoids.
3. The comment cleanup is a 5-minute add that prevents future maintainers from hitting the same stale-reference confusion the audit noted.
4. The new `COORD_PNL_BACK_DERIVED` log tag becomes the Phase 4 verification signal — operators can grep for it to confirm the fix is firing on every WS close.
5. Risk Register Risk 8 (Performance Enforcer behaviour change after CRITICAL-1) applies regardless of option chosen — operator should expect mode-transition events post-deploy.

---

## Section 5 — Phase 3 implementation plan (contingent on Option C selection)

Single atomic commit on `feature/bybit-demo-adapter`:

1. `src/core/trade_coordinator.py`: insert back-derive block (~10 lines) between line 693 and line 696. Update the function docstring to mention the new `pnl_pct == 0` sentinel contract.
2. `src/bybit_demo/bybit_demo_websocket_subscriber.py`: update comments at lines 390-400 and lines 474-487 to reflect truthful contract. No code change.
3. `tests/test_trade_coordinator.py` (or new file): 9 unit tests + 1 integration test as enumerated in `c1_phase1_data_samples.md`.
4. Run `make test` to confirm full suite green.
5. Commit message:
   ```
   fix(c1/phase3): back-derive pnl_pct from prices in coordinator
   
   Closes CRITICAL-1 from /home/inshadaliqbal786/IMPLEMENT_CRITICAL_HIGH_FIXES.md.
   
   The bybit_demo WS subscriber passes pnl_pct=0 as a sentinel and trusts
   the coordinator to back-derive. Previously the coordinator only had
   back-derive branches gated on pnl_pct != 0 — so the back-derive never
   ran and every WS-driven close wrote pnl=0 to trade_log,
   trade_intelligence, trade_thesis. trade_history was correct because
   bybit_demo_adapter.close_position computes inline.
   
   Fix adds a single back-derive branch in trade_coordinator.on_trade_closed
   between the existing close_price and pnl_usd handlers. Computes pnl_pct
   from entry/exit/side; flips was_win from the result. Direction sign
   matches the canonical formula at bybit_demo_adapter.py:392-401.
   
   Also updates two stale comments in bybit_demo_websocket_subscriber.py
   that referenced lines 612-638 (now 685-707) and claimed "back-derived
   by coordinator" without qualifying the sentinel-zero contract.
   
   Verified by 9 new unit tests + 1 integration test. Existing 281 tests
   still pass. New observability via COORD_PNL_BACK_DERIVED log tag.
   
   Existing 116 corrupted trade_log rows + 120 trade_thesis rows + ~1322
   trade_intelligence rows are NOT backfilled by this commit; operator
   decides per Rule 12.
   ```

Phase 4 verification:
- Operator restarts `trading-workers`, `trading-brain`, `trading-mcp-sse`.
- Run for 4-6 hours; capture log + DB.
- Re-run Phase 0 baseline queries; expect:
  - DL_TRADE_SUSPECT count drops from ~17/h to ~0/h.
  - New trade_log bybit_demo rows have non-zero pnl_pct on non-flat closes.
  - New trade_thesis bybit_demo closed rows have non-zero actual_pnl_usd.
  - New trade_intelligence rows have non-zero pnl_pct and correct win value.
  - COORD_PNL_BACK_DERIVED log tag appears once per close.
- Sample 5 fresh trades; manually compute pnl; compare.
- Document in `c1_phase4_verification.md`.
- Operator signs off.

---

## Section 6 — Open questions

These need operator answers before Phase 3 begins:

1. **Option choice**: A, B, or C? (Recommendation is C.)
2. **Backfill of existing 116 trade_log rows + 120 trade_thesis rows + recent trade_intelligence rows**: backfill, mark-untrusted (add a column flag), or leave? (Default per plan: leave; backfill is a separate scoped task if operator wants it.)
3. **Test scope**: 9 unit tests + 1 integration test, or minimal 3 tests? (Default: 9 — enterprise standard per prompt.)
4. **Subscriber comment cleanup**: include in same commit per Option C, or separate? (Recommendation: same commit per the plan's opportunistic-fix decision.)
