# P1 Phase 2 — Report and Solution Options

This is the consolidated Phase 2 report for Priority 1: Wire Private WebSocket Subscription for Bybit Demo. It is intended for the operator to read and decide. Phase 3 (implementation) does not begin until the operator chooses an option.

---

## What the audit said

CRITICAL-1 (L6-G1). When Bybit's matching engine triggers a stop-loss or take-profit on a Bybit demo position, the system does not learn through a push notification. It learns only when `position_watchdog` runs its next 10-second tick and notices the position no longer appears in `get_positions()`. The watchdog then asks Bybit's REST `/v5/position/closed-pnl` endpoint for authoritative PnL. That endpoint is asynchronously indexed; in roughly one in three external closes during the 3-hour audit window, it returned no data, and the system fell back to ticker-derived exit price (no fees, possibly stale by 0–5 seconds). Authoritative post-fee PnL was lost on those closes.

Audit recommendation: wire the existing `BybitWebSocket` primitive (which already has dead `connect_private`, `subscribe_orders`, `subscribe_positions` methods) to subscribe to the bybit_demo private cluster.

---

## What current code shows (confirmed and refined)

### Diagnosis confirmed

- `BybitWebSocket` exists at `src/trading/websocket.py:18-236`.
- `connect_private` (line 66), `subscribe_orders` (138), `subscribe_positions` (151) are dead — zero call sites across `src/`.
- `subscribe_executions` does NOT exist — would route to pybit's `execution_stream` if added.
- The single instance of `BybitWebSocket` is created at `src/workers/manager.py:106`. Only `PriceWorker` consumes it (public ticker stream).
- `_detect_and_record_closes` at `position_watchdog.py:2925-3074` is the only close-detection path today. The 120-second freshness gate at line 2963 governs the authoritative-data acceptance window.
- `TradeCoordinator.on_trade_closed` at `trade_coordinator.py:578-725` is the single fan-out point. 14 close callbacks confirmed (all 14 `register_close_callback` sites located in `manager.py`).
- Cooldown idempotency gate at `trade_coordinator.py:727-735`. Watchdog's poll path checks it at `position_watchdog.py:2936`. WS-first ordering will trigger `WD_SKIP_CLOSE | rsn=already_processed_by_coordinator` (already an emitted tag).

### Refinement: pybit supports demo natively

pybit's `WebSocket(channel_type, **kwargs)` accepts `demo: bool = False`. When `demo=True, testnet=False, channel_type="private"`, pybit produces `wss://stream-demo.bybit.com/v5/private`. This was the highest planning risk for P1; it is resolved with no new library required.

### Refinement: baseline metrics

| Metric | Audit (3hr window) | Phase 0 baseline (24-36h) | Comment |
|--------|---------------------|---------------------------|---------|
| `WD_CLOSE_PRICE_FALLBACK / WD_CLOSE` | 35% | 21.1% (15/71) | Multi-day average is lower but still significant |
| `WD_LAST_CLOSE_FALLBACK` count | 4 | 6 | Self-initiated closed-pnl race events |
| `BYBIT_DEMO_POSITION_CLOSE` | 22 | 52 | System-initiated closes |
| `WD_CLOSE` | 31 | 71 | Watchdog-detected externally-initiated closes |
| Close coverage gap | All 31 external closes degraded | All 71 external closes degraded | Same shape |

Target post-P1: `WD_CLOSE_PRICE_FALLBACK` rate drops to under 5%; close-detection latency drops from ~10s to under 100ms; `BYBIT_DEMO_WS_*` events appear in logs.

---

## Evidence

### File-line proofs

- `src/trading/websocket.py:75-80` shows pybit construction without `demo=True` (the gap):
  ```python
  self._private_ws = WebSocket(
      testnet=bybit.testnet,
      channel_type="private",
      api_key=bybit.api_key,
      api_secret=bybit.api_secret,
  )
  ```

- `src/workers/position_watchdog.py:2953` is the get_last_close call site that races against Bybit's closed-pnl indexer:
  ```python
  shadow_close = await self.position_service.get_last_close(symbol)
  ```

- `src/workers/position_watchdog.py:2974-2979` shows the ticker fallback that loses fees:
  ```python
  ticker = await self.market_service.get_ticker(symbol)
  if ticker and ticker.last_price > 0:
      exit_price = ticker.last_price
      price_source = "ticker_fallback"
  ```

- `src/core/trade_coordinator.py:597-606` shows the atomic-pop idempotency layer:
  ```python
  state = self._trades.pop(symbol, None)
  if state is None:
      log.warning(f"COORD_DOUBLE_CLOSE | sym={symbol} by={closed_by} | already closed — skipping duplicate | {ctx()}")
      return
  ```

### pybit demo support

```python
# pybit/_websocket_stream.py:135-139
if self.demo:
    if self.testnet:
        subdomain = DEMO_SUBDOMAIN_TESTNET   # 'stream-demo-testnet'
    else:
        subdomain = DEMO_SUBDOMAIN_MAINNET   # 'stream-demo'
self.endpoint = url.format(SUBDOMAIN=subdomain, DOMAIN=domain, TLD=tld)
```

Confirmed by direct module-source read.

---

## Solution options

The full options document is in `p1_phase1_synthesis.md`. Summary:

### Option A — Extend BybitWebSocket; new BybitDemoWebSocketSubscriber

Extend `BybitWebSocket.connect_private` to accept `demo: bool = False`. Add `subscribe_executions(callback)`. Extend `reconnect()` to handle `_private_ws`. New consumer class `src/bybit_demo/bybit_demo_websocket_subscriber.py` owns the lifecycle, subscriptions, idempotency dedup, and bridges pybit-thread events to the project's asyncio loop via `run_coroutine_threadsafe`. Register the subscriber as a `BaseWorker` (matching `PriceWorker`'s pattern) so its `tick()` can health-check + reconnect; the actual close events arrive through the WS callback, not via tick.

Effort estimate: 1.5 days.

Pros: minimal new code; reuses existing WebSocket infrastructure consistently; natural attach point in the boot sequence; future Shadow private-WS work would benefit from the same shared infrastructure.

Cons: `BybitWebSocket` becomes mode-dual (live + testnet + demo) — minor cognitive load.

### Option B — Dedicated BybitDemoWebSocket class

A new `src/bybit_demo/bybit_demo_websocket.py` owns its own pybit `WebSocket` instance plus connect, subscribe, reconnect logic. No reuse of `BybitWebSocket`. Subscriber class layers on top of it.

Effort estimate: 1.75 days.

Pros: cleanest end-to-end separation; aligns with the existing `bybit_demo_client.py` pattern (which is a dedicated HTTP client, not a sharing of `bybit/client.py`).

Cons: duplicates connect, reconnect, callback-wrap code (~80 lines duplicated). Future Shadow private-WS would need a third class.

### Option C — Hybrid, WebSocket primary plus polling at unchanged 10 seconds

Functionally equivalent to Option A. The plan already specifies polling stays. Option C is the same diff as Option A with explicit affirmation that polling continues unchanged. No separate option in practice.

### Recommendation

**Option A.** Smaller diff, reuses infrastructure, matches the existing PriceWorker pattern, and future-proof for Shadow private-WS work.

---

## Idempotency, reconnection, and observability

These are not options — they are how the chosen option works. Listed for completeness.

### Idempotency (three layers)

1. Per-handler 5-second TTL dedup in the new subscriber prevents Bybit-side duplicate `execution` events for the same close from triggering two `on_trade_closed` calls.
2. Existing atomic `_trades.pop(symbol, None)` in the coordinator (`trade_coordinator.py:597`) is the first writer wins; subsequent calls emit `COORD_DOUBLE_CLOSE` warning and return cleanly.
3. Existing watchdog cooldown gate (`position_watchdog.py:2936`) prevents the poll path from re-processing a WS-handled close; emits `WD_SKIP_CLOSE | rsn=already_processed_by_coordinator`.

### Reconnection

- pybit auto-reconnects on transport errors and re-subscribes from its own subscription registry (covers transient TCP drops, server restarts, auth expiry).
- Project-side `BybitWebSocket.reconnect()` (extended for private) is the safety net when pybit gives up; bounded exponential backoff, max 10 attempts, 5-min cap.
- If all recovery exhausted, emit `BYBIT_DEMO_WS_DEAD` CRITICAL event. AlertManager dispatches Telegram alert; polling continues uninterrupted.

### Observability (new tags)

- `BYBIT_DEMO_WS_CONN | demo_url=wss://stream-demo.bybit.com/v5/private | {ctx}` — connection established
- `BYBIT_DEMO_WS_SUBSCRIBED | topics=position,execution,order | {ctx}` — subscriptions confirmed
- `BYBIT_DEMO_WS_HEALTH | status=connected msgs_per_min=N topic_msgs={...} subscribed=3 | {ctx}` — per-tick health (mirrors `PRICE_WS_HEALTH`)
- `BYBIT_DEMO_WS_DISC | rsn=... | {ctx}` — connection lost
- `BYBIT_DEMO_WS_RECONNECT | attempt=N delay=Ds | {ctx}` — reconnect attempts
- `BYBIT_DEMO_WS_DEAD | err='...' | {ctx}` — all recovery exhausted
- `BYBIT_DEMO_WS_CLOSE_EVENT | sym=... orderId=... execPrice=... closedSize=... pnl_usd=... | {ctx}` — close-event detected
- `BYBIT_DEMO_WS_DEDUP | sym=... reason=duplicate_within_5s | {ctx}` — TTL dedup hit (informational)

---

## Hard constraints and FORBIDDEN choices the implementation will respect

Per the spec, the following are non-negotiable:

- Polling MUST remain as fallback. No change to `_detect_and_record_closes`. No reduction of `WD_TICK` cadence.
- Both detection paths converge on `coordinator.on_trade_closed`. Same close cannot be processed twice (idempotency required — three-layer strategy above).
- Fix MUST work for fresh connections AND reconnections.
- Fix MUST integrate cleanly with the boot sequence (Candidate A: BaseWorker registration after coordinator construction).
- Fix MUST NOT affect Shadow's behaviour. Shadow path untouched.
- No removing polling under the assumption WebSocket is reliable. FORBIDDEN.
- No skipping reconnection logic. FORBIDDEN.
- No treating WebSocket failures as "should never happen." FORBIDDEN.
- No hardcoding subscription parameters that should come from config. FORBIDDEN. (Subscription topic list will be a settings constant.)
- No broad try/except around WebSocket message handlers. FORBIDDEN. (Per-line failure handling with structured logging.)

---

## Test budget (Tier 1, ~10 minutes)

- 1 unit test: WS message parser converts pybit `execution` event JSON to `(symbol, exit_price, pnl_usd, was_win, price_source="bybit_ws_authoritative")`.
- 1 integration test: mock pybit WS, emit synthetic close event, verify `coordinator.on_trade_closed` called exactly once even when watchdog poll detects same close 5 seconds later.
- 1 smoke test: live connect to `wss://stream-demo.bybit.com/v5/private` with operator's demo creds; verify subscribe response within 5 seconds.

All test invocations prefixed with `timeout N` per the standing test rule.

---

## Phase 4 verification design

Run for 4-6 hours after deployment in `bybit_demo` mode.

| Metric | Pre-fix baseline | Target |
|--------|--------------------|--------|
| `WD_CLOSE_PRICE_FALLBACK / WD_CLOSE` rate | 21.1% | under 5% |
| New `BYBIT_DEMO_WS_CONN` events | 0 | at least 1 (boot) |
| `BYBIT_DEMO_WS_HEALTH` per-tick | 0 | every WS health tick |
| `BYBIT_DEMO_WS_DEAD` (catastrophic) | 0 | 0 expected |
| `COORD_DOUBLE_CLOSE` | 0 | 0 (idempotency holds) |
| Polling continues to work (kill-WS test) | n/a | yes; poll resumes detection within 10s |
| Shadow mode unaffected | works | works (manual switch test) |

---

## Open questions for the operator

1. Approve diagnosis as-is (no refinement needed).
2. Choose Option A (recommended) or Option B.
3. Approve `BaseWorker` lifecycle pattern (vs. boot-time manual attach).
4. Approve "always-construct-when-enabled" mode-handling (subscriber idle when `current_mode != bybit_demo`, fully constructed always when `[bybit_demo].enabled = true` in config).
5. Approve Tier-1 test budget (1 unit + 1 integration + 1 smoke).
6. Confirm operator will restart `trading-workers` after P1 lands so Phase 4 verification can run.

After your decisions, Phase 3 (implementation) begins.
