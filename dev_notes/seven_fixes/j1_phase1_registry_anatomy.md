# J1 Phase 1 Step 1.1.3 — Registry Anatomy

Captured 2026-05-14 22:40 UTC. Read-only.

## The Real Registry

`TradeCoordinator._trades: dict[str, TradeState]` at `src/core/trade_coordinator.py:146`, keyed by **symbol** (not by `trade_id`). The audit's `_open_positions` does not exist in production code; the only occurrence is `src/factory/simulator.py` (offline backtesting).

Companion structures on the coordinator (`src/core/trade_coordinator.py:147-181`):

- `_closed_trades: list[dict]` — recent close records (for analytics).
- `_callbacks_on_close: list` — close callbacks registered at boot.
- `_last_brain_context: dict[str, str]` — per-symbol last brain reasoning.
- `_trade_plans: dict` — per-symbol TradePlan.
- `_trade_info: dict[str, dict]` — extended trade info (Telegram).
- `_symbol_cooldowns: dict[str, float]` — symbol → expiry timestamp (revenge-trade defence).
- `_loss_cooldown_direction: dict[str, str]` — direction of the LOSING close that triggered each cooldown (T2-1 / F20 fix).
- `_strategic_actions: list[dict]` — queued position actions.
- `_close_reasons: dict[str, str]` — symbol → close reason.
- `_transformer` — late-bound transformer reference (attach via `attach_transformer`).
- `_partial_close_pending: dict[str, dict]` — partial-fill tracking (Issue 4 fix).
- `_callbacks_on_partial_close: list` — partial-close callbacks.

The registry's source-of-truth status: `_trades` is the brain-side and strategy-side view of "what is open." The watchdog reads it (`coordinator._trades.get(pos.symbol)` at `position_watchdog.py:1359, 3324`) to enrich live-API positions with brain-side metadata (entry price, decision id, side). The watchdog itself operates on the live API result, not on `_trades`.

## Boot Path — `recover_state_from_db` (Issue I5)

`src/core/trade_coordinator.py:183-288`, shipped as Issue I5 (F-32, 2026-05-14) to address SEGV-induced state loss. Contract:

```
SQL: SELECT symbol, direction, entry_price, size_usd, leverage,
            opened_at, order_id, exchange_mode
     FROM trade_thesis WHERE status = 'open'
```

Per row, builds a minimal `TradeState`, derives `qty = (size_usd * leverage) / entry_price`, and inserts into `_trades` with `source="state_recovery"`. **Idempotent: skips symbols already in `_trades` (line 225-226).**

Emits `DASHBOARD_STATE_RECOVERED` per restored row and `DASHBOARD_STATE_RECOVER_SUMMARY` at the end.

Called once from `src/workers/manager.py:579`:

```python
_restored = await trade_coordinator.recover_state_from_db(self.db)
```

after the coordinator is constructed (line 558-560) and before close callbacks are wired.

### The H2 hypothesis surface

`recover_state_from_db` reads only `trade_thesis WHERE status='open'`. If a position is open on Bybit but its `trade_thesis` row has `status='closed'` (e.g., closed by `zombie_reconciler` while Bybit still has the position), the brain wakes up unaware of that position. The position will still appear in WD_TICK because the watchdog reads live API truth, but the brain's `_trades` map is missing it. Any brain code path that consults `_trades` (e.g., `_check_for_existing_position` style guards in the entry validator) will incorrectly conclude there is no open position on that symbol.

The H2 forward-race scenario:
1. Position open on Bybit, registered in `_trades`, `trade_thesis` row has `status='open'`.
2. Bybit closes the position (SL fill). Watchdog tick T₀ catches it via `_detect_and_record_closes`; cleanup callback fires; `_trades` entry popped; `trade_thesis` marked closed.
3. Worker restarts. `recover_state_from_db` reads `status='open'` and finds nothing for the symbol — correct, the position is closed.

The H2 case where this goes wrong:
1. Position open on Bybit, registered in `_trades`, `trade_thesis` row has `status='open'`.
2. Watchdog tick at time T₀ — Bybit reports the position as open. `_last_known_symbols` includes the symbol.
3. Bybit closes the position at T₀ + 100s, **but** the next watchdog tick T₁ at T₀ + 10s comes BEFORE the close — Bybit still reports the position open.
4. The 300s zombie_reconciler timer expires AT T₀ + 305s. `_shadow_syms = {symbol for p in current_positions}` — but the position may or may not still be in the response depending on Bybit's API state. If the position closed on Bybit between T₀ + 100s and T₀ + 305s, zombie_reconciler sees it as missing and closes the `trade_thesis` row at pnl=0.
5. The next watchdog tick T₂ catches the close authoritatively; cleanup callback fires; `_trades` popped; close record is written. The commit `0a1d825` ensures the authoritative close overwrites the zombie close in `trade_thesis`.

This is the **reverse race**, which `0a1d825` addresses. The **forward race** is when (4) happens but (5) does not happen in time (worker restarts before T₂). In that case the new worker reads `trade_thesis WHERE status='open'` and finds no row, never registers the position in `_trades`, but the watchdog will see it open on Bybit and start ticking it.

The watchdog will then enrich the position with empty brain-side metadata. The cleanup chain still works (when the position eventually closes on Bybit, `vanished` set catches it and the cleanup callback fires; the row is removed). But the brain has no context for the position — it doesn't know the entry rationale, the original SL/TP, the conviction.

### Practical impact of H2

The four current stale rows are NOT examples of H2 because they are not actually open on Bybit any more (Bybit's WD_TICK shows n=0 right now). H2 is a logical possibility, not an observed failure in the audit window. The Phase 1 zombie reconciler audit (Step J1.1.4) will determine whether the race is realistic enough to warrant a guard.

## `register_trade` Contract

`src/core/trade_coordinator.py:403-560`. Called from:

- `src/brain/brain_v2.py:526` — primary entry, brain-initiated trades.
- `src/workers/strategy_worker.py:2420` — secondary entry, strategy-initiated trades.

Behaviour:
- Checks `symbol in self._trades` and logs `COORD_DUPLICATE_REGISTER` warning if found, then **overwrites** the prior state (line 491-505).
- Captures `self._current_mode()` and stores it on the new `TradeState.exchange_mode` (per c4eef5c).
- Sets `source` field (e.g., `claude_direct`, `state_recovery`, `strategy_worker`).
- Fires no open-time callbacks — there is no `_callbacks_on_open` list. Open-side notifications happen via direct calls in the caller (e.g., `fund_manager.on_trade_opened` is called separately from `brain_v2.py`).

## `on_trade_closed` Contract

`src/core/trade_coordinator.py:901-1140`. The end of the lifecycle:

1. Pops `_trades[symbol]` and captures the state.
2. Builds a close record with `pnl_pct`, `pnl_usd`, `was_win`, `close_reason`, `closed_by`, `exchange_mode` (from `state.exchange_mode` or fallback to `_current_mode()`).
3. Fires 17 registered callbacks via `for cb in self._callbacks_on_close: cb(record)`.
4. Logs `COORD_CLOSE_END | sym=... cbs_fired=17`.

The 17 callbacks include `_thesis_close_callback`, `_data_lake_close_callback`, `_positions_table_cleanup_on_close`, `_trade_history_close_callback` (CRITICAL-3 fix), enforcer, fund manager, pnl manager, etc.

`is_symbol_cooled_down(symbol)` returns True if symbol is in `_symbol_cooldowns` and the timestamp has not yet passed. Used by the watchdog's `_detect_and_record_closes` to skip already-processed closes (the `WD_SKIP_CLOSE | rsn=already_processed_by_coordinator` log line).

## Sniper's View

`src/workers/profit_sniper.py:182` stores `self.trade_coordinator = trade_coordinator`. Sniper reads coordinator state indirectly through the `position_service` it shares with the watchdog. Sniper does not maintain its own registry; it tracks per-symbol profit state in `_profit_states: dict[str, PositionProfitState]` (line 198) but that is for in-flight TP/SL adjustment, not for "is this position open?" awareness.

## Compliance With Master Prompt Rules

- **Rule 3 (no band-aids)**: The boot recovery extension (H2 fix) candidate must NOT bypass the `trade_thesis` source of truth. The recommended approach is to query `trade_thesis` first, then cross-check `positions` for the active mode, and emit `POSITION_REGISTRY_BACKFILL` per symbol that is in `positions` but not in `trade_thesis WHERE status='open'`. Each backfill must include enough context (entry price from `positions`, side, qty) to be useful to the watchdog and dashboard.
- **Rule 6 (observability)**: `POSITION_REGISTRY_BACKFILL` per backfilled row, `POSITION_REGISTRY_BACKFILL_SUMMARY` with total count, exposed through `DASHBOARD_STATE_RECOVER_SUMMARY` companion line.
- **Rule 10 (do not break Shadow)**: H2 fix must be mode-aware. Shadow does not need this because Shadow does not persist to the `positions` table.

## Open Question For Operator

Should the H2 boot-recovery extension be **mandatory** (the brain learns about every open Bybit position even without a thesis row) or **diagnostic only** (emit `POSITION_REGISTRY_DRIFT` warning at boot, do not auto-register)? Auto-register is simpler and matches the operator's aggressive-exploitation philosophy (the watchdog is already managing those positions; the brain might as well know). Diagnostic-only is conservative and preserves the principle that `trade_thesis` is the operator-blessed source of truth.

My current recommendation: **auto-register** with strong observability so the operator can audit the backfill.
