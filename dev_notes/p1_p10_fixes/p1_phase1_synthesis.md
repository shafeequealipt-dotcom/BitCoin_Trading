# P1 Phase 1 — Synthesis: Diagnosis Confirmed + Solution Candidates

## Audit's diagnosis (recap)

> CRITICAL-1 — No private WebSocket subscription for Bybit demo; close detection is poll-only.
> The system polls `position_service.get_positions()` every 10 seconds. When closes happen between ticks, the watchdog asks Bybit's REST `/v5/position/closed-pnl` for authoritative PnL; this endpoint is asynchronously indexed and 35% of those calls return None or stale data, falling back to ticker-derived exit price (excludes fees, may be 0-5s stale).

## Diagnosis confirmed against current code

| Audit claim | Verified | Evidence |
|-------------|----------|----------|
| `BybitWebSocket` exists at `src/trading/websocket.py:18` | YES | Phase 0 spot-check |
| `connect_private`, `subscribe_orders`, `subscribe_positions` are dead | YES | grep across `src/` shows zero call sites |
| `subscribe_executions` does NOT exist | YES | confirmed in `websocket.py` end-to-end read |
| Detection is poll-only for bybit_demo | YES | `position_watchdog.py:2925-3074` is sole detection path; 0 `BYBIT_DEMO_WS_*` log events |
| `WD_CLOSE_PRICE_FALLBACK` rate is significant | YES | Baseline: 15/71 = 21.1% over 24-36h (audit's 3hr window: 35%) |
| `WD_LAST_CLOSE_FALLBACK` (closed-pnl race) is observable | YES | Baseline: 6 events over 24-36h |
| Watchdog cooldown gate at `position_watchdog.py:2936` exists | YES | Phase 0 spot-check; emits `WD_SKIP_CLOSE | rsn=already_processed_by_coordinator` |
| Coordinator's `on_trade_closed` is the convergence point | YES | `trade_coordinator.py:578-725` mapped end-to-end |
| 14 close callbacks registered | YES | grep counted (lines 547, 1640, 1664, 1682, 1695, 1713, 1744, 1775, 1799, 1816, 1833, 1850, 1853, 1982) |

**Diagnosis confirmed. No refinement needed.**

## Refinement: pybit DOES support demo mode natively

Audit suggested wiring `BybitWebSocket` to demo URL would require code changes to accept a URL override. **This is not necessary** — pybit's `WebSocket(channel_type, **kwargs)` accepts `demo: bool = False` as a kwarg. Setting `demo=True` produces `wss://stream-demo.bybit.com/v5/private` for `channel_type="private"`.

This removes the largest planning risk for P1. The fix is a small wiring change, not a library replacement.

## Idempotency + reconnection strategy

### Idempotency (preventing double-processing)

Three layers, in increasing strictness:

| Layer | Where | Behaviour |
|-------|-------|-----------|
| L1 — Per-handler TTL dedup | NEW WS subscriber | Tracks `(symbol, last_close_event_ms)` for 5s. Prevents Bybit-side duplicate `execution` events for the same close from triggering two `on_trade_closed` calls. |
| L2 — Coordinator atomic pop | `trade_coordinator.py:597` | `self._trades.pop(symbol, None)` — first writer wins; subsequent calls get `None`, emit `COORD_DOUBLE_CLOSE` warning, return. |
| L3 — Watchdog cooldown gate | `position_watchdog.py:2936` | Once coordinator processes a close, `is_symbol_cooled_down(symbol) = True` for 180-900s; watchdog skips with `WD_SKIP_CLOSE`. |

L2 and L3 are existing infrastructure. L1 is the only new addition. The combination handles all four race orderings:
- WS first, poll later → L3 catches (poll skips)
- Poll first, WS later → L2 catches (WS gets None state, warns, returns)
- WS twice (Bybit duplicate) → L1 catches (handler dedups within 5s)
- WS during reconnect window → Bybit doesn't replay events; no scenario

### Reconnection strategy

| Failure | Recovery |
|---------|----------|
| Transient TCP drop | pybit auto-reconnects (`restart_on_error=True` default) and resubscribes from `self.subscriptions` registry |
| Server cluster restart | Same as above |
| Auth expiry | pybit re-signs on reconnect (handles internally) |
| Pybit retries exhausted (>10 attempts) | Project-side `BybitWebSocket.reconnect()` (lines 183-215) — exponential backoff, max 10 attempts, 5-min cap |
| All recovery exhausted | WS subscriber emits `BYBIT_DEMO_WS_DEAD` CRITICAL log; AlertManager dispatches Telegram alert (P10 surfaces it). Polling continues as fallback (uninterrupted). |

The polling path is **never disabled**. It always runs. Even with WS at 100% reliability, polling is the safety net.

## Three solution candidates for Phase 2

### Option A — Extend BybitWebSocket; new BybitDemoWebSocketSubscriber consumer

**Diff scope:**
- `src/trading/websocket.py` — extend `connect_private(self, demo: bool = False)` (1 line change to constructor call); add `subscribe_executions(self, callback)` (~10 lines); extend `reconnect()` to handle `_private_ws` (~15 lines).
- NEW `src/bybit_demo/bybit_demo_websocket_subscriber.py` (~250 lines) — class that owns the subscription lifecycle, message handlers, idempotency dedup TTL, and the bridge to `coordinator.on_trade_closed` via `asyncio.run_coroutine_threadsafe`.
- `src/workers/manager.py` — wiring after coordinator construction (line ~509); ~10 lines.
- NEW `src/bybit_demo/__init__.py` export entry.

**Pros:**
- Reuses existing `BybitWebSocket` infrastructure (callback wrapping, ping config from settings, project's reconnect pattern).
- Minimal surface change in `websocket.py`.
- Subscriber is testable in isolation (mockable `BybitWebSocket`).

**Cons:**
- `BybitWebSocket` becomes mode-dual (demo + live + testnet) — minor cognitive load.
- Reconnect logic in `websocket.py` is currently public-only; adding private-side path requires care.

**Estimated effort:** 1.5 days (code + tests + boot wiring).

### Option B — Dedicated BybitDemoWebSocket class (independent of BybitWebSocket)

**Diff scope:**
- NEW `src/bybit_demo/bybit_demo_websocket.py` (~350 lines) — owns its own pybit `WebSocket` instance + connect + subscribe + reconnect logic. No reuse of `BybitWebSocket`.
- NEW `src/bybit_demo/bybit_demo_websocket_subscriber.py` — same as Option A but uses `BybitDemoWebSocket` instead of `BybitWebSocket`.
- `src/workers/manager.py` wiring as Option A.

**Pros:**
- Cleanest separation: bybit_demo owns its own WS infrastructure end-to-end.
- No mode-dual logic in `websocket.py`.
- Pattern-aligned with the existing `bybit_demo/bybit_demo_client.py` (the HTTP client is also dedicated, not shared with `bybit/client.py`).

**Cons:**
- Duplicates connect + reconnect + callback-wrap code (~80 lines duplicated).
- Future Shadow private-WS work would need a third class.

**Estimated effort:** 1.75 days (more code; same tests + wiring).

### Option C — Hybrid: WebSocket primary + polling at unchanged 10s

**Same as Option A** but explicitly retain polling at 10s (no reduction). The watchdog's `_detect_and_record_closes` keeps running every tick. WS is the primary path; polling is the safety net.

**Pros:**
- Belt-and-braces redundancy.
- If WS subscriber has a bug, polling silently corrects.

**Cons:**
- More observable noise (`WD_SKIP_CLOSE` events on every WS-handled close).
- The actual savings from WS is detection latency, not CPU; polling continues to do the REST call regardless.

**Estimated effort:** Same as Option A.

In practice, Option A and Option C are nearly identical — Option C is just Option A with explicit affirmation that polling stays as-is. The plan already specifies polling stays. So **Option A is Option C**.

### Recommendation: Option A

Rationale:
1. Minimal new code surface (1 small `websocket.py` change + 1 new subscriber class + boot wiring).
2. Reuses existing infrastructure consistently with how `BybitWebSocket` is used today.
3. The cost of the mode-dual `connect_private(demo: bool = False)` parameter is one extra kwarg; it does not pollute live or testnet paths.
4. Future Shadow-private-WS work (out of scope) would benefit from the same shared infrastructure.

## Verification design (Phase 4 preview)

### Pre-deploy assertions
- 1 unit test: WS message parser converts pybit `execution` event JSON to `(symbol, exit_price, pnl_usd, was_win, price_source="bybit_ws_authoritative")`.
- 1 integration test: mock pybit WS, emit synthetic close event, verify `coordinator.on_trade_closed` called exactly once even when watchdog poll detects same close 5s later.
- 1 smoke test: live connect to `wss://stream-demo.bybit.com/v5/private` with operator's demo creds; verify subscribe response within 5s.

### Post-deploy verification (4-6h soak in bybit_demo mode)
| Metric | Pre-fix | Target | How |
|--------|---------|--------|-----|
| `WD_CLOSE_PRICE_FALLBACK / WD_CLOSE` rate | 21.1% (15/71) | <5% | grep both tags, ratio |
| `WD_SKIP_CLOSE | rsn=already_processed_by_coordinator` count | small | should grow proportionally with `WS_HANDLED` count | grep |
| New `BYBIT_DEMO_WS_CONN` events | 0 | ≥1 (boot) | grep |
| `BYBIT_DEMO_WS_HEALTH` per-tick log | 0 | every WS health tick | grep |
| `BYBIT_DEMO_WS_DEAD` (catastrophic failure) | 0 | 0 expected | grep |
| `COORD_DOUBLE_CLOSE` rate | 0 | 0 (idempotency holds) | grep |
| Polling continues to work (kill WS test) | n/a | yes | manual: kill WS via test hook, observe poll resumes detection within 10s |
| Shadow mode unaffected | works | works | switch to shadow, place trade, verify WD_CLOSE fires normally |

## What to ask the operator (Phase 2)

1. Approve diagnosis (no refinement needed — confirmed).
2. Approve Option A (recommended) over B / C-as-A-renamed.
3. Confirm BaseWorker pattern for the subscriber's lifecycle (vs. boot-time attach with manual coordinator wiring) — minor design detail; recommendation: BaseWorker subclass for consistency.
4. Approve "always-construct-when-enabled" mode-handling (subscriber idle when current_mode != bybit_demo, fully constructed always when `[bybit_demo].enabled = true`) over "construct-on-mode-switch".
5. Approve Tier-1 test budget: 1 unit + 1 integration + 1 smoke (≤10 min total).

## What this Phase 1 does NOT decide

- Specific severity for `BYBIT_DEMO_WS_DEAD` alert — that's P10 (severity assignment to triggers).
- Whether to also subscribe to `wallet` and `fast_execution` streams — out of P1 scope.
- Whether to consolidate `BybitWebSocket` and `BybitDemoWebSocket` infrastructure later — future refactor.
- Mode-aware label refactor (`shadow_sl_tp` → `bybit_demo_sl_tp`) — that's P2.
- Bounded retry on `get_last_close` — that's P3.

P1 stays scoped to: wire one WS connection, parse close events, route to coordinator, dedup, document.
