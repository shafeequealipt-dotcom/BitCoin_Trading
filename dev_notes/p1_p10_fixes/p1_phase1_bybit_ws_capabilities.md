# P1 Phase 1 — Bybit demo WebSocket capability finding

## TL;DR

Bybit demo offers **wss://stream-demo.bybit.com/v5/private** for private-stream account events. pybit's `WebSocket(channel_type="private", api_key=..., api_secret=..., demo=True, testnet=False)` builds this URL natively. No subclassing, no raw `websockets` library, no monkey-patch.

The Bybit-demo cluster supports the same 5 private topics as live: `position`, `order`, `execution`, `fast_execution`, `wallet`. Authentication is per-cluster — main-account keys do NOT work; bybit_demo keys must be used.

## How pybit builds the URL

From `pybit/_websocket_stream.py:114-145` (paraphrased):

```python
def _connect(self, url):
    # url is the format string PRIVATE_WSS or PUBLIC_WSS
    subdomain = SUBDOMAIN_TESTNET if self.testnet else SUBDOMAIN_MAINNET
    domain = DOMAIN_MAIN if not self.domain else self.domain
    tld = TLD_MAIN if not self.tld else self.tld
    if self.demo:
        if self.testnet:
            subdomain = DEMO_SUBDOMAIN_TESTNET     # 'stream-demo-testnet'
        else:
            subdomain = DEMO_SUBDOMAIN_MAINNET     # 'stream-demo'
    self.endpoint = url.format(SUBDOMAIN=subdomain, DOMAIN=domain, TLD=tld)
```

Constants from `pybit/_websocket_stream.py:14-18`:
- `SUBDOMAIN_MAINNET = "stream"`
- `SUBDOMAIN_TESTNET = "stream-testnet"`
- `DEMO_SUBDOMAIN_MAINNET = "stream-demo"`
- `DEMO_SUBDOMAIN_TESTNET = "stream-demo-testnet"`

URL templates from `pybit.unified_trading`:
- `PRIVATE_WSS = 'wss://{SUBDOMAIN}.{DOMAIN}.{TLD}/v5/private'` → with `DOMAIN=bybit, TLD=com` → `wss://stream-demo.bybit.com/v5/private`
- `PUBLIC_WSS = 'wss://{SUBDOMAIN}.{DOMAIN}.com/v5/public/{CHANNEL_TYPE}'` → not relevant for P1

## Subscription topics (private cluster)

Per `pybit/unified_trading.py:122-185`:

| Topic | Method | When emitted | Use for P1 |
|-------|--------|--------------|------------|
| `position` | `position_stream(cb)` | Position changes (open, modify, close, leverage change). `size=0` indicates flat. | YES — close detection (size→0) |
| `order` | `order_stream(cb)` | Order lifecycle (New → PartiallyFilled → Filled / Cancelled / Rejected) | OPTIONAL — useful for orderId tracking |
| `execution` | `execution_stream(cb)` | Per-fill: `execPrice, execQty, execFee, closedSize, orderId, symbol, side, execTime` | YES — authoritative post-fee fill data |
| `fast_execution` | `fast_execution_stream(cb)` | Same as execution but fewer fields, lower latency | NO — execution is sufficient |
| `wallet` | `wallet_stream(cb)` | Wallet balance changes | OPTIONAL — useful for capital tracking |

P1 should subscribe to **execution** (primary — authoritative fill data) + **position** (confirmation — flat-state detection) + **order** (status correlation). Wallet/fast_execution can be added in a later phase (out of P1 scope).

## Authentication

pybit handles auth internally:
1. Constructor accepts `api_key`, `api_secret`.
2. On `_connect`, pybit sends `{"op": "auth", "args": [api_key, expires_ts, signature]}` within `private_auth_expire` seconds (default 1).
3. Signature: `HMAC-SHA256(api_secret, f"GET/realtime{expires_ts}")`.
4. Pybit auto-reauths on reconnect.

For P1 demo wiring, the credentials must come from `settings.bybit_demo.api_key` / `settings.bybit_demo.api_secret`. The Bybit-demo cluster validates against the demo-account key set; main-account keys produce auth failure.

## Subscription rate limits

Bybit V5 private streams have very generous limits — single-account, single-stream subscriptions are not throttled at typical project volumes. Pybit handles `op: subscribe` framing internally. The hard upper bound is `(args)` length per single subscribe message (10 topics per request); the project subscribes 3 topics so this is non-binding.

## Heartbeat / connection durability

| Aspect | Behaviour |
|--------|-----------|
| Ping cadence | Pybit sends `{"op": "ping"}` every `ping_interval=20s` (default) |
| Ping timeout | `ping_timeout=10s` (default) — server pong expected within 10s |
| Auto-reconnect | `restart_on_error=True` (default) — pybit reconnects + auto-resubscribes from `self.subscriptions` registry |
| Reconnect retries | `retries=10` (default), 0 means infinite |
| Project-side reconnect | `BybitWebSocket.reconnect()` (lines 183-215) — bounded, exponential backoff capped at 5 min, 10 attempts. Currently only handles `_public_ws`. P1 must extend for `_private_ws`. |

Pybit's auto-reconnect is the primary durability mechanism. The project-side reconnect is the safety net when pybit gives up after 10 retries. P1 must verify both layers cover the failure modes:
- Transient TCP drop → pybit handles
- Server-side cluster restart → pybit handles (subscriptions resume)
- Auth-token expired → pybit re-signs on reconnect
- Sustained outage > 10 retries → project-side `reconnect()` kicks in

## Empirical verification (deferred to scratch test)

Before P1 implementation, a 30-min scratch test must verify:
1. `WebSocket(testnet=False, demo=True, channel_type="private", api_key=BD_KEY, api_secret=BD_SECRET)` connects to `wss://stream-demo.bybit.com/v5/private` and authenticates.
2. `position_stream(cb)` callback fires when an existing demo position is modified (e.g., manually setting SL via UI).
3. `execution_stream(cb)` callback fires when a manual close is executed via UI; confirm payload shape matches `{"topic":"execution","data":[{"execPrice":..., "execQty":..., "execFee":..., "closedSize":..., "orderId":..., "symbol":...}], "ts":...}`.
4. Ping/pong + auto-reconnect both work over 5-min observation.

The scratch test runs against operator's live demo creds in `.env`; results recorded in `dev_notes/p1_p10_fixes/p1_phase1_scratch_test.md` if any surprise is found. If pybit's behaviour matches the docs (high confidence based on source-code reading), no surprise expected.
