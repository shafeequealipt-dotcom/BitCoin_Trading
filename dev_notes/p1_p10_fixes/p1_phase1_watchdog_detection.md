# P1 Phase 1 — position_watchdog detection + how WS path joins it

## File scope

`src/workers/position_watchdog.py:2925-3121` — `_detect_and_record_closes(open_symbols)` and the surrounding tick loop.

## Tick-driven poll

The watchdog's `tick()` calls `position_service.get_positions()` (line 464 per audit) and aggregates `open_symbols`. Then `_detect_and_record_closes(open_symbols)` runs the set-difference against `_last_known_symbols` and processes each vanished symbol.

| Stage | Line | What |
|-------|------|------|
| Set-diff | 2933 | `vanished = self._last_known_symbols - open_symbols` |
| Cooldown gate | 2936-2940 | `if coordinator.is_symbol_cooled_down(symbol): continue` (emits `WD_SKIP_CLOSE`) |
| Authoritative lookup | 2953 | `shadow_close = await self.position_service.get_last_close(symbol)` |
| 120s freshness gate | 2963 | `if age_s is not None and age_s <= 120:` — accepts authoritative data |
| Authoritative path | 2964-2971 | `exit_price = shadow_close.exit_price`, `price_source = "shadow_authoritative"` |
| Fallback 1 — current ticker | 2974-2981 | `MarketService.get_ticker` → `price_source = "ticker_fallback"` |
| Fallback 2 — last_tick_cache | (not shown in excerpt; further down) | `price_source = "last_tick_cache"` |
| PnL resolution | 3000-3053 | back-derived from entry/exit + state (audit U-3 / P3 race when shadow_close has wrong field) |
| `was_win` | 3055 | `pnl_usd > 0 if shadow_authoritative else pnl_pct > 0` |
| `close_reason` | 3057 | `coordinator.pop_close_reason(symbol) if coordinator else "shadow_sl_tp"` (P2 — fallback string) |
| `WD_CLOSE` log | 3062-3067 | structured close-detected event |
| Coordinator fan-out | 3075-3091 | `coordinator.on_trade_closed(...)` |
| Telegram alert | 3110-3118 | `alert_manager.send_custom("Closed by: Shadow SL/TP")` (P2 — Telegram literal) |
| Cleanup | 3120-3121 | `self._last_known_symbols = open_symbols.copy()` |

## Convergence point with WS path

P1 introduces a second path that arrives at the **same convergence point** — `coordinator.on_trade_closed`. Two arrival orders are possible:

### Order A — WS arrives first (expected ~99% of the time)

| t (ms) | Event |
|--------|-------|
| 0 | Bybit matching engine fills the close |
| <100 | Bybit `execution` stream pushes event over WS |
| <100 | Project-side WS handler calls `coordinator.on_trade_closed(...)` |
| <100 | Coordinator pops state, sets cooldown for `symbol` |
| <10000 | Watchdog's next tick fires, `get_positions` reports `symbol` missing |
| <10000 | `_detect_and_record_closes` checks `is_symbol_cooled_down(symbol)` → True → emits `WD_SKIP_CLOSE | rsn=already_processed_by_coordinator | continue` |

The cooldown gate at `position_watchdog.py:2936` is the synchronisation primitive that prevents the watchdog from re-processing.

### Order B — Poll arrives first (rare; possible during WS outage)

| t (ms) | Event |
|--------|-------|
| 0 | Bybit matching engine fills the close |
| 0 | WS connection is down (transient) — no push |
| <10000 | Watchdog tick fires, processes via `get_last_close` (REST) |
| <10000 | Coordinator processes, sets cooldown |
| WS reconnect | Eventually pybit auto-reconnects; subscription resumes |
| variable | If WS replays the close event after reconnect, `coordinator.on_trade_closed` will hit the COORD_DOUBLE_CLOSE warning (state already None); fan-out skipped. |

Bybit's WS does NOT replay events on reconnect (event-time replay is opt-in via `replay` topics, not used here). So Order B simply degrades to the existing poll path with no double-processing.

### Order C — WS arrives twice (Bybit re-emits)

Possible if Bybit's WS sends two events for the same close (e.g., two `execution` events for partial fills that together flatten the position). Each `execution` event is a discrete fill; we only want to call `on_trade_closed` ONCE per position close.

**Idempotency strategy for P1:**
1. WS handler tracks `(symbol, last_processed_close_time_ms)` in a small TTL dict.
2. Only the first execution with `closedSize > 0` for a position triggers `on_trade_closed`. Subsequent fills for the same position (within 5s window or until a new entry) are merged into the close PnL calculation OR ignored if the position is already flat.
3. The coordinator's `state.pop` is the last line of defense — even if the WS handler botches dedup, COORD_DOUBLE_CLOSE catches it.

## What P1 changes in this file

**Nothing.** P1's WS subscriber is a NEW component (proposed `src/bybit_demo/bybit_demo_websocket_subscriber.py`). The watchdog's logic stays as-is — it remains the fallback path. The cooldown gate at line 2936 already does the right thing for the WS-first scenario.

The watchdog's `WD_CLOSE_PRICE_FALLBACK` (line ~2980-2986; not in excerpt above) drops naturally as a function of how many closes the WS handles before the watchdog gets to them.

## Watchdog instrumentation that P1 should respect

- `WD_SKIP_CLOSE | rsn=already_processed_by_coordinator` — already emits on every cooldown-gate hit. P1 should NOT modify this. We can compute the "WS-handled fraction" from this tag's count: `WS_HANDLED_FRACTION = WD_SKIP_CLOSE_count / (WD_CLOSE_count + WD_SKIP_CLOSE_count)` (target: 95%+ post-P1).
- `WD_CLOSE_PRICE_FALLBACK` (lines ~2982-2986) — emits when `price_source = "ticker_fallback"`. Target post-P1: <5% (currently 21.1%).
- `WD_LAST_CLOSE_FALLBACK` — emits when `get_last_close` returns None. Will drop indirectly via P3 (bounded retry).

## Boundaries clarified

- The **120s freshness gate at line 2963** is intentionally NOT P1's concern. It governs the watchdog's REST authoritative path. P3 will replace this with a matching-time-keyed gate.
- The **`pop_close_reason` fallback "shadow_sl_tp" at line 3057** is P2's concern. P1 must not introduce new hardcoded mode-string literals; the new WS handler should derive `closed_by` from execution event metadata + coordinator state, not from a literal.
- The **Telegram literal "Closed by: Shadow SL/TP" at line 3114** is P2's concern. P1 leaves this alone.

## What this file confirms for P1 design

| Design choice | Backed by |
|---------------|-----------|
| WS handler calls `coordinator.on_trade_closed` directly (no project-side dedup needed) | Coordinator's `_trades.pop` + cooldown gate are sufficient |
| Polling stays as fallback | The `_detect_and_record_closes` loop is unchanged; cooldown gate handles WS-first ordering |
| Idempotency dedup is a thin per-handler TTL dict | Belt-and-braces; `pop` + cooldown are the load-bearing layers |
| WS handler must dispatch via `asyncio.run_coroutine_threadsafe` | pybit thread can't call sync coordinator that triggers async downstream work |
| New `BYBIT_DEMO_WS_*` event tags do NOT collide with existing `WD_*` tags | grep across `src/` confirms no `BYBIT_DEMO_WS_` prefix today |
