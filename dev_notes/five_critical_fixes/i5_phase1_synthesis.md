# Issue 5 Phase 1 — Silent Close After Partial: Root-Cause Investigation

## Conclusion First

**Issue 5 is fully caused by Issue 4.** No independent bug. Fix Issue 4 correctly → Issue 5 disappears at the same commit.

## Evidence

The directive's claim: "When the residual from a partial close eventually closes (M4 stall valve fired on FILUSDT residual at 10:11:23), no trade_log row is written. The 12-recorded-closes count today is actually 13 actual closes."

Today's FILUSDT trace confirms exactly this. Reproduced from `i4_phase1_synthesis.md`:

| Time | Event | Effect on persistence |
|------|-------|------------------------|
| 09:49:07.577 | `COORD_CLOSE_START` for FILUSDT (full notional) | Coordinator state popped; 15 callbacks fire; `trade_log` + `trade_history` rows written (inflated) |
| 09:49:13.446 | Sniper `_on_position_opened FILUSDT` | Residual re-tracked in sniper's local buffer only — no `register_trade` to coordinator |
| 10:11:23.510 | `COORD_DOUBLE_CLOSE | sym=FILUSDT by=bybit_demo_sl_tp | already closed — skipping duplicate` | Coordinator silent-skip at `trade_coordinator.py:671`; no callbacks fire |
| 10:11:23.668 | `COORD_DOUBLE_CLOSE | sym=FILUSDT by=mode4_stall_valve | already closed — skipping duplicate` | Second silent-skip; no callbacks fire |
| Subsequent | No `DL_TRADE`, no `THESIS_CLOSE`, no `Strategy perf updated`, no `TIAS_SAVE` | Confirmed by grep |

The COORD_DOUBLE_CLOSE event itself is observable (WARNING level), but the *outcome of the trade* (qty, realized pnl, win/loss, hold time, strategy attribution) is never persisted.

## Why this is Issue 4's bug, not a separate one

The silent-skip at `trade_coordinator.py:671` is the correct guard against duplicate dispatch (WS race vs watchdog race). It fires when the trade state is absent. In a healthy system, state absence at close time means a true duplicate (same close arriving twice). Issue 4 creates a false absence: the state was popped erroneously on the partial fill, leaving the eventual full close to look like a duplicate.

If Issue 4 is fixed correctly (state stays alive through partial closes, with state.size decremented), then at 10:11:23 the state still exists. The eventual close calls on_trade_closed with the residual qty and the residual close emits all the right callbacks. The trade_log/trade_history get the residual's row.

## What the system would look like with Issue 4 fixed (no separate Issue 5 fix)

For a 50% partial close + residual full close, the operator chooses the persistence shape in the Issue 4 Phase 2 discussion. Two viable shapes:

### Shape A — Two rows per partial trade
- `trade_history` row #1 at partial-close moment: `qty=2215.1, pnl=$2.66, exit_price=1.1274, trade_id=bd-{oid}-partial-1`
- `trade_history` row #2 at full-close moment: `qty=2215.1, pnl=<residual realized PnL>, exit_price=<actual>, trade_id=bd-{oid}-final`
- `trade_log` parallels the same pattern

This is the cleanest for TIAS learning (each partial outcome is its own observation). Requires aggregating scripts to update.

### Shape B — Single aggregate row
- `trade_history` only writes when the position is fully closed
- Partial closes update an in-memory accumulator (`state.realized_pnl_accumulated`, `state.cumulative_closed_qty`)
- Final close writes a single row with `qty=full_entry_qty, pnl=total_realized`

This keeps the existing 1-row-per-trade invariant. TIAS sees one outcome per trade rather than per partial — coarser learning signal.

## No new options unique to Issue 5

There is nothing for Issue 5 to add that isn't already addressed by Issue 4. The "Forbidden band-aids" list from the directive (catching the missing close at end-of-day cleanup, doubling up on trade_log writes, etc.) all describe ways to paper over the symptom while leaving Issue 4 unsolved.

## Phase 2 report

Issue 5 does not need a separate Phase 2 report; the Issue 4 Phase 2 report (`i4_phase2_report.md`, to be written) will reference Issue 5 as the second consequence and ask the operator to choose between Shape A and Shape B above. After implementation lands, Issue 5's verification metric (`trade_log row count == close-event count`) is automatically observable.

## Implementation note

Once Issue 4's chosen option is implemented:

- Phase 3 implementation also covers Issue 5 — no separate commit needed.
- Phase 4 verification adds one extra metric: confirm `COORD_DOUBLE_CLOSE` rate drops to near-zero, and partial-trade lifecycles produce exactly two `trade_log` rows (Shape A) or one row representing the final state (Shape B).
- The atomic-commit prefix should be `fix(i4-i5/phase3): ...` to document the coupling.
