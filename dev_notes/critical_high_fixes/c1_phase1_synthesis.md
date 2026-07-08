# CRITICAL-1 Phase 1 — Synthesis

## Purpose

Consolidate the 7 prior Phase 1 deliverables. Confirm or refine the audit's diagnosis. Document the exact fix point. Identify all integration points. Document the back-derive formula with direction-sign handling. This document is the input to Phase 2's report-and-options for operator decision.

## The audit's diagnosis (CRITICAL-1)

> The WebSocket subscriber passes `pnl_pct=0.0, pnl_usd=0.0` and trusts the coordinator to back-derive PnL, but the coordinator's back-derive logic is gated on `pnl_pct != 0`, so it never runs. Result: every record downstream of that seam carries pnl=0.

## My confirmation

**Confirmed in full.** Specifically:

1. The subscriber's `_call_coordinator_close` (lines 463-502) passes `pnl_pct=0.0, pnl_usd=0.0, was_win=False` with explicit "back-derived by coordinator" comments at lines 491-493. The contract is unambiguous from the subscriber's perspective. (See `c1_phase1_subscriber_anatomy.md`.)

2. The coordinator's `on_trade_closed` (lines 639-794) has back-derive branches at lines 689 (close_price; dead code given line 687-688 already sets it) and 696 (pnl_usd; the active gate). **No branch back-derives `pnl_pct` from prices.** (See `c1_phase1_coordinator_anatomy.md`.)

3. The record dict built at line 713-760 carries the corrupted `pnl_pct=0`, `pnl_usd=0`, `was_win=False` values to 14 close-callbacks. Three of those write to trade_log, trade_thesis, trade_intelligence. (See `c1_phase1_downstream_paths.md`.)

4. The system-initiated close path through `bybit_demo_adapter.close_position` computes correct PnL inline and writes to trade_history independently. The coordinator path is bypassed for trade_history but still fires (via WS event) for trade_log/intelligence/thesis — which is why trade_history is correct (30 rows) and the other three tables are wrong (116, 253, 1322 rows respectively for bybit_demo). (See `c1_phase1_systemic_close_path.md`.)

5. The direction-sign formula is well-defined: `Buy = ((exit-entry)/entry)*100`, `Sell = ((entry-exit)/entry)*100`. State.side is reliably "Buy" or "Sell" with "Short" as a defensive alias. The fix point at coordinator has all three required variables (`entry_price`, `close_price`, `_side`) resolved by line 693. (See `c1_phase1_direction_sign.md`.)

6. The `was_win` flag is consumed by 6 downstream paths (Performance Enforcer, fund_manager, registry, /pnl handler, TIAS, learning_repo). It must be flipped from the back-derived pnl_pct in the same coordinator-side fix. (See `c1_phase1_was_win.md`.)

7. Five real recent trades manually computed produce values matching `trade_history` to 1e-7 precision; trade_log shows 0 for 4 of 5 (the fifth is a flat trade with entry==exit). (See `c1_phase1_data_samples.md`.)

## Refinements to the audit

The audit is accurate but I observed three nuances worth noting:

1. **The dead-code branch at line 689.** The audit said the back-derive of close_price is gated on `pnl_pct != 0`, which is technically true. But because the WS subscriber always passes a valid `exit_price`, line 687-688 always handles close_price first, making line 689 dead code. The bug isn't that close_price isn't set — it's set correctly. The bug is purely that `pnl_pct` never gets computed and `pnl_usd` never gets back-derived (because its gate also checks `pnl_pct != 0`).

2. **Two different exit prices in flight.** The adapter's `/v5/order/realtime` poll resolves a fill price; the WS execution event reports `execPrice`. These are usually identical but can differ slightly (sample: AEROUSDT — adapter 0.5156 vs WS 0.5147). The coordinator-side fix uses the WS price (already in the record's `close_price` field) and produces a coordinator-path `pnl_pct` that may differ from trade_history's `pnl_pct` by a small fraction. This is acceptable (different price source by design); Phase 4 verification SQL uses a tolerance of 0.01%.

3. **The 14-callback fan-out is a feature.** Fixing the record dict at coordinator level fixes 6 downstream consumers of `was_win` and 3 downstream consumers of `pnl_pct`/`pnl_usd` in one place. No downstream callback needs modification.

## Integration points

Files that must be modified for CRITICAL-1 (Option A — coordinator-side fix):

- `src/core/trade_coordinator.py` (lines 685-707 area)

Files that are READ but NOT modified (must be re-checked for breakage):

- `src/bybit_demo/bybit_demo_websocket_subscriber.py` (the contract becomes truthful; subscriber unchanged but its docstrings/comments could be updated for accuracy)
- `src/core/data_lake.py` (no change; DL_TRADE_SUSPECT will simply stop firing)
- `src/core/thesis_manager.py` (no change; will start receiving correct pnl values)
- `src/tias/collector.py` and `src/tias/repository.py` (no change; will start receiving correct values)
- `src/strategies/performance_enforcer.py` (no change; will start receiving correct was_win)
- `src/strategies/registry.py`, `src/database/repositories/learning_repo.py`, `src/portfolio/analytics.py`, `src/telegram/handlers/portfolio.py`, `src/telegram/handlers/dashboard_handler.py`, `src/workers/manager.py:_pnl_close_callback` (no change; will start receiving correct values)
- `src/bybit_demo/bybit_demo_adapter.py` (no change; system-initiated path remains independent)

Test files that must be added:

- `tests/test_trade_coordinator_pnl_back_derive.py` (or extend an existing coordinator test file)
  - Sell back-derive (5 fixtures from data_samples.md)
  - Buy back-derive (3 mirror fixtures)
  - was_win flip parametrized
  - No back-derive when pnl_pct already provided (system-initiated path passthrough)
  - No back-derive when entry_price == 0 (defensive)
  - No back-derive when close_price == 0 (defensive)
  - Flat trade returns pnl_pct=0, was_win=False
  - cooldown timing flips from 600s loss-grade to 180s win-grade after a winning back-derive
- Integration test (likely in existing `tests/test_bybit_demo/test_*.py`):
  - Full close path: WS execution event → coordinator → record dict → all three downstream tables show correct pnl

Files that may benefit from comment updates (cosmetic, not required):

- `bybit_demo_websocket_subscriber.py:474-487` (the `_call_coordinator_close` docstring referencing "lines 612-638" — stale audit comment)
- `bybit_demo_websocket_subscriber.py:390-400` (the explanatory comment in `_handle_one_execution`)

## Back-derive formula

Final form ready for Phase 3 implementation:

```python
# Insert in trade_coordinator.py between line 693 (end of close_price 
# resolution) and line 696 (start of pnl_usd back-derive).
#
# CRITICAL-1 fix — back-derive pnl_pct from prices + side when caller
# passed sentinel zero (e.g., bybit_demo WS subscriber). Direction sign 
# matches close_position inline (bybit_demo_adapter.py:392-401) and the 
# existing close_price back-derive at lines 689-693.
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

The new `COORD_PNL_BACK_DERIVED` log tag gives operators visibility into which closes used the back-derive path vs. caller-provided values. This is observability for Phase 4 verification.

After this insertion, the existing `pnl_usd` back-derive at line 696 sees `pnl_pct != 0` (when the back-derive ran) and computes `pnl_usd` from `pnl_pct * notional`. No further changes needed.

The flat-trade case (`entry_price == close_price`) results in `pnl_pct = 0`, `was_win = False`, log line indicates the calc happened but resulted in zero. Downstream DL_TRADE_SUSPECT does NOT fire because its guard requires `entry_price != exit_price`.

## Three Phase 2 options (preview — operator decides)

### Option A — Coordinator-side fix only (recommended)

- One file changed (`trade_coordinator.py`).
- ~10 lines of code (formula + log line).
- Subscriber contract becomes truthful.
- Both WS-driven and any future caller benefit automatically.
- Pros: minimal blast radius, single source of truth, testable in isolation.
- Cons: changes coordinator's responsibilities slightly (gains a pnl_pct back-derive).

### Option B — Subscriber-side computation

- Subscriber pre-computes pnl_pct from `coordinator._trades[symbol]` (entry/side) + `exec_price`.
- Passes a non-zero pnl_pct to coordinator. Existing back-derive at line 696 then handles pnl_usd.
- Pros: zero changes to coordinator code path.
- Cons: Couples subscriber to coordinator's internal state; subscriber needs to handle race where state is already popped (double-close); more complex.

### Option C — Hybrid

- Coordinator-side back-derive (Option A) AS DEFAULT.
- Subscriber updates docstrings to reflect the truthful contract.
- Pros: same as A plus documentation cleanup.
- Cons: minimal additional cost.

Recommendation: Option C. Same code change as A plus 5-minute comment fix in subscriber.

## Constraints satisfied

| Constraint | Met by |
|---|---|
| System-initiated close path must continue working | Coordinator change does not touch `bybit_demo_adapter.close_position` |
| Shadow-mode close path must continue working | Coordinator change applies uniformly; shadow already provides non-zero pnl_pct via its own callbacks (when running) → coordinator's gate `pnl_pct == 0` skips the new branch |
| No double-counting | Back-derive only runs when caller explicitly passed pnl_pct=0 (sentinel) |
| Identical PnL whether adapter or coordinator | Same formula. Small differences only when WS price differs from /v5/order/realtime poll (documented seam, both authoritative) |
| DL_TRADE_SUSPECT count drops to ~0 | Yes — fix eliminates the `pnl_pct == 0 AND prices != equal` condition |
| Existing 116 corrupted rows untouched | Yes — fix is forward-only; backfill is a separate operator decision |

## Open questions for operator (carry into Phase 2)

1. **Existing 116 trade_log rows + 120 trade_thesis rows + ~1322 trade_intelligence rows have wrong pnl.** Backfill, mark untrusted, or leave? Default: leave (oldest are 78 from audit; newest accrue daily; backfill is its own scope).
2. **Subscriber comment cleanup**: include in same commit (operator-approved opportunistic fix per plan), or skip?
3. **Test coverage scope**: 8 unit tests (5 sell + 3 buy parametrized) plus 1 integration test = ~9 new tests; vs minimal 3 (positive sell + positive buy + negative passthrough). Default: 9 tests for completeness given prompt's enterprise standard.

## Phase 1 deliverables index

| File | Status |
|---|---|
| `c1_phase1_subscriber_anatomy.md` | written |
| `c1_phase1_coordinator_anatomy.md` | written |
| `c1_phase1_downstream_paths.md` | written |
| `c1_phase1_systemic_close_path.md` | written |
| `c1_phase1_direction_sign.md` | written |
| `c1_phase1_was_win.md` | written |
| `c1_phase1_data_samples.md` | written |
| `c1_phase1_synthesis.md` | this file |

Next step per the prompt's Part E: write `c1_phase2_report.md` with audit-vs-current diff, evidence, three options + trade-offs, recommendation. Present to operator. Wait for choice.
