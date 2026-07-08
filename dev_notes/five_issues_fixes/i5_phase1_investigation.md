# I5 Phase 1 — F-32 Dashboard State Persistence Investigation

**Status:** Phase 1 complete. F-29 RAM upgrade is operator-confirmed
(15 GiB available). Architectural state-persistence gap addressed.

---

## TL;DR

Two in-memory state holders contributed to the operator-visible
"dashboard reset after restart" symptom captured in the audit:

1. **TradeCoordinator._trade_plans + _trades** — volatile dicts at
   `src/core/trade_coordinator.py:129-133`. Lost on every restart.
   The watchdog's existing `WD_CLOSE_THESIS_RECOVERY` path at L3304
   reconstructs reactively (when a position closes), but the
   intermediate dashboard reads (Telegram `/positions`, MCP
   `get_positions`) show 0 age / 0 PnL until the recovery catches up.
2. **DailyPnLManager** counters (`realized_pnl`, `_trades_today`,
   `_wins_today`, `_losses_today`, `_max_drawdown_today`) at
   `src/strategies/pnl_manager.py:32-67`. `_persist_daily_pnl` writes
   the row, but `initialize()` only sets starting_equity — it doesn't
   read the counters back.

DB-backed state (positions, theses, trade_log, daily_pnl row itself)
survived the SEGV cleanly. The architectural gap was purely the
absence of READ-BACK on initialize.

---

## Architectural ROOT cause

Asymmetric write/read for restart-critical state:
- WRITE side: `_persist_daily_pnl` writes the row every ~7.5 min
  AND on day rollover. `thesis_manager.save_thesis` writes
  per-trade thesis.
- READ-BACK side: missing. Initialize / boot zeros the counters and
  empties the trade-plan dict.

F-29 RAM hardware upgrade reduces SEGV likelihood, but a graceful
operator-initiated restart suffers the same state loss. The fix is
architectural; F-29 is complementary.

---

## Fix

### TradeCoordinator restart-resilient state

New method `TradeCoordinator.recover_state_from_db(db)` queries
`trade_thesis WHERE status='open'`, builds a `TradeState` per row
(carrying entry_price, side, size, opened_at, order_id, exchange_mode)
and populates `self._trades[symbol]`. Idempotent — skips symbols
already in `_trades`. Emits `DASHBOARD_STATE_RECOVERED` per restored
row + `DASHBOARD_STATE_RECOVER_SUMMARY` at end.

Wired at `src/workers/manager.py:582` (immediately after
trade_coordinator construction + transformer attach).

### DailyPnLManager restart-resilient PnL

New method `_restore_today_from_db()` reads
`daily_pnl WHERE date = today` and populates `starting_equity /
realized_pnl / _trades_today / _wins_today / _losses_today /
_max_drawdown_today / target_hit / halted`. Called from `initialize()`
AFTER the zeroing block, so:
- On a genuine new-day boot (no row for today) → zeros stand
- On a restart-during-trading-day → counters restored

Emits `DASHBOARD_STATE_RECOVERED` with scope=daily_pnl.

### Coordinator visibility on trade-plan registration

`register_trade_plan` now emits a structured `TRADEPLAN_PERSISTED`
event so operators see plan registrations in the canonical tag
inventory. The trade-thesis row that powers state-recovery is
already written via `thesis_manager.save_thesis` at the
strategy_worker site; this event confirms the coordinator's
in-memory state and the DB-side thesis stayed in sync.

---

## New structured emissions (per Rule 6)

- `DASHBOARD_STATE_RECOVERED` — per restored TradeState row +
  per restored daily_pnl row (`scope=daily_pnl`)
- `DASHBOARD_STATE_RECOVER_SUMMARY` — final count after the
  trade-coordinator restore loop
- `DASHBOARD_STATE_RECOVER_FAIL` — query / build failure (best-effort
  fallback, never blocks boot)
- `TRADEPLAN_PERSISTED` — register_trade_plan emission
- `BOOT_STATE_RECOVERED` — worker-manager-side rollup of restored count

---

## Behaviour preservation (Rule 4)

- TradeCoordinator constructor signature unchanged
- DailyPnLManager constructor signature unchanged
- `_persist_daily_pnl` schema + insert unchanged
- Watchdog's reactive `WD_CLOSE_THESIS_RECOVERY` path unchanged
- All existing tests pass
- New methods are best-effort — any failure logs and continues with
  the empty state (boot is never blocked on recovery)

---

## Verification gate (Phase 4)

- F-29 RAM upgrade confirmed (operator)
- Operator-supervised restart of workers.py
- Dashboard `/positions` reflects entry prices + ages immediately
  (within first 1-2 ticks)
- PnL counter, win/loss counts, max-drawdown reflect today's
  accumulated state instead of zero
- DASHBOARD_STATE_RECOVERED logs visible at boot
- Operator sees no "dashboard reset" — the restart is transparent
  for already-running trades
- New positions opened post-restart appear via the existing
  register_trade path with TRADEPLAN_PERSISTED visible
