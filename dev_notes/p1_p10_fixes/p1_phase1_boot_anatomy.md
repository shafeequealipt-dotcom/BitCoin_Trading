# P1 Phase 1 — manager.py boot block + WS subscriber attach point

## File scope

`src/workers/manager.py:300-411` — Bybit demo adapter construction + boot validation + Transformer service wiring.

## Current bybit_demo wiring (lines 300-378)

| Line | What |
|------|------|
| 308 | `bd_settings = getattr(settings, "bybit_demo", None)` |
| 309 | `if bd_settings is not None and bd_settings.enabled:` (gate) |
| 310-322 | aiohttp session reuse (Shadow's `_shadow_session` shared with bybit_demo); separate session created if Shadow not enabled |
| 324-329 | Credential warning (`bd_settings.api_key`, `bd_settings.api_secret`) — adapter still constructs even with missing creds; every call returns sentinel |
| 331-340 | `BybitDemoClient(...)` — HTTP client with HMAC auth + retry |
| 341-343 | Service classes: `BybitDemoOrderService(bd_client)`, `BybitDemoPositionService(bd_client)`, `BybitDemoAccountService(bd_client)` |
| 344-347 | `log.info("Bybit demo adapters: created (API: {url})", url=bd_settings.base_url)` |
| 356-376 | `validate_boot(bd_client, base_url, api_key_len, recv_window)` — emits `BYBIT_DEMO_BOOT_START / _VALIDATED / _FAIL`; result stashed in `self._services["bybit_demo_boot_result"]` |
| 379-383 | else branch: log "not enabled" |

Then at lines 386-405:
| Line | What |
|------|------|
| 386 | `transformer = self._services.get("transformer")` |
| 388-398 | `transformer.set_services(shadow_*, bybit_*, bybit_demo_*)` |
| 400 | `await transformer.initialize()` (re-init to set active services from DB mode) |
| 402-405 | `proxies = transformer.create_proxies()` → `pos_svc, ord_svc, acc_svc` |

## Where the WS subscriber attaches

P1's WS subscriber needs:
1. `bd_settings` (for credentials + boot URL — informational only, the WS uses `wss://stream-demo.bybit.com/v5/private` not the REST base_url)
2. `bd_client` reference (optional — only needed if the WS handler needs to issue REST follow-ups)
3. `coordinator` reference (mandatory — from `self._services["transformer"].coordinator` after step 400) — wait, that's wrong. Coordinator lives separately. Let me check.

Actually the coordinator is constructed elsewhere — find it:
- The coordinator is created and attached after line 411 (where `self._services["position"]` etc. are set). It's around line 547 (first `register_close_callback` call). Construction must precede that.

For Phase 2 design discussion, the WS subscriber needs to attach AFTER the coordinator is created and the transformer's bybit_demo services are wired (so it can read the demo creds from settings + reference the coordinator). Natural attach point: between coordinator creation and the first `register_close_callback` call (line 547).

But this complicates the lifecycle. A cleaner design: subscriber is a `BaseWorker` registered into the worker manager's worker list, so its `tick()` runs after boot-init completes and the coordinator is available via `self._services["coordinator"]`. The first `tick()` (after init) does the connect + subscribe. Subsequent ticks just health-check.

Two design candidates for Phase 2:

### Candidate A — boot-time attach (in init_services)

After line 376 (`bybit_demo_boot_result` set):
```python
if bd_settings.enabled and bd_settings.api_key and bd_settings.api_secret:
    from src.bybit_demo.bybit_demo_websocket_subscriber import BybitDemoWebSocketSubscriber
    bd_ws_subscriber = BybitDemoWebSocketSubscriber(
        settings=settings,
        coordinator=None,  # late-wired after coordinator is available
        loop=asyncio.get_event_loop(),
    )
    self._services["bybit_demo_ws_subscriber"] = bd_ws_subscriber
    # Connect deferred until coordinator is available
```

Then after coordinator is constructed:
```python
bd_ws_subscriber.coordinator = coordinator
await bd_ws_subscriber.connect()
```

### Candidate B — BaseWorker pattern (preferred, simpler)

Add `BybitDemoWebSocketSubscriber` as a `BaseWorker` subclass in `src/workers/bybit_demo_ws_worker.py`. Worker manager constructs it like every other worker. First `tick()` connects + subscribes; subsequent ticks check health + reconnect if needed.

Pros: matches existing `BaseWorker` pattern (PriceWorker is the model), automatic lifecycle management, no late-wiring of coordinator (coordinator already in `self._services` by the time workers start ticking).
Cons: tick-loop overhead is wasted (the WS itself is push-driven; ticks only do health check). Could use a long interval (e.g., 60s) since WS push handles real-time.

## What changes in manager.py for P1

In Phase 2 we choose between Candidate A and B. Either way, the diff is small:
- Candidate A: ~10 lines after line 376 + ~3 lines after coordinator construction.
- Candidate B: ~5 lines in worker registration block (find an existing pattern around `PriceWorker` registration).

Either way: zero changes to existing bybit_demo construction (lines 300-378), zero changes to Transformer wiring (lines 386-411). The new subscriber is purely additive.

## Mode awareness in boot

Critical: the WS subscriber should ONLY connect when current_mode == "bybit_demo". Two approaches:

1. **Connect always when bybit_demo enabled in config**, leave WS idle when not the active mode. The WS push handler calls `coordinator.on_trade_closed` only if `transformer.current_mode == "bybit_demo"`. Wasteful (extra connection) but simpler.

2. **Connect on demand** when `transformer.switch_to("bybit_demo")` fires. Requires a switch hook + tear-down on switch-out. More complex, tighter resource usage.

Phase 2 picks. Recommendation: option 1 (matches Shadow — Shadow services are always constructed regardless of active mode).

## Restart-based switching consideration

Per `project_bybit_demo_adapter_status.md`, mode switching is operator-driven via Telegram with a process restart between exchanges (`ExchangeSwitcher.execute_switch_with_restart`). This means:
- The WS subscriber is constructed fresh on every restart.
- After restart, the new mode is read from `transformer_state` SQLite (line 95 boot path).
- If new mode == "bybit_demo", the subscriber connects.
- If new mode == "shadow", the subscriber stays idle (or skipped construction — option 1 vs 2 above).

P1 design must respect this restart-based model. No mid-process mode switching is needed.

## Summary

| Question | Answer |
|----------|--------|
| Where does WS subscriber attach? | Boot-time, after bybit_demo adapters constructed (line 376) OR registered as a `BaseWorker` |
| Does it need coordinator reference? | Yes — to call `on_trade_closed`. Available via `self._services["coordinator"]` after coordinator construction. |
| Does it need credentials? | Yes — `settings.bybit_demo.api_key/api_secret` |
| Does it need event-loop reference? | Yes — for `asyncio.run_coroutine_threadsafe` from pybit thread |
| Can it stay idle when mode != bybit_demo? | Yes (option 1) or only construct when mode == bybit_demo (option 2). Phase 2 chooses. |
| Restart-based switching? | Respected — subscriber is constructed fresh on every restart |
