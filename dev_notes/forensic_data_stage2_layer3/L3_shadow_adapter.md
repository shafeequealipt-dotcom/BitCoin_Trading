# L3 — Shadow Adapter Forensic Data

Refreshed: 2026-05-02 ~11:45 UTC. Replaces 2026-04-28 baseline.

Source path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/shadow/shadow_adapter.py`
Measured line count: **774** lines.
Shadow root: `/home/inshadaliqbal786/shadow/`
Shadow HTTP server: `/home/inshadaliqbal786/shadow/src/api/shadow_client.py` (aiohttp `web` app).

---

## 1. Classes & Public Methods

The adapter file defines THREE service-mirror classes plus helpers.

### `ShadowPositionService` (file:135)

| Method | File:Line | Signature |
|---|---|---|
| `get_positions` | 150 | `(self, symbol: str | None = None) -> list[Position]` |
| `get_position` | 173 | `(self, symbol: str) -> Position | None` |
| `get_last_close` | 192 | `(self, symbol: str) -> dict | None` |
| `close_position` | 227 | `(self, symbol: str, *, purpose: str = "layer4_close") -> Order` |
| `reduce_position` | 273 | `(self, symbol: str, qty: float) -> Order` |
| `close_all_positions` | 332 | `(self) -> list[Order]` |
| `set_leverage` | 341 | `(self, symbol: str, leverage: int) -> bool` |
| `set_stop_loss` | 345 | `(self, symbol: str, stop_loss: float) -> bool` |
| `set_take_profit` | 358 | `(self, symbol: str, take_profit: float) -> bool` |
| `get_pnl_summary` | 371 | `(self) -> dict` |
| `health_check` | 392 | `(self) -> bool` |

### `ShadowOrderService` (file:409)

| Method | File:Line | Signature |
|---|---|---|
| `place_order` | 424 | see section 2 |
| `modify_order` | 549 | `(self, symbol, order_id, qty=None, price=None) -> Order` (returns rejected — Shadow is market-only) |
| `cancel_order` | 560 | `(self, symbol, order_id) -> bool` (no-op, returns True) |
| `cancel_all_orders` | 564 | `(self, symbol=None) -> int` (returns 0) |
| `get_open_orders` | 568 | `(self, symbol=None) -> list[Order]` (returns []) |
| `get_order_history` | 574 | `(self, symbol=None, limit=50) -> list[Order]` (returns []) |
| `health_check` | 580 | `(self) -> bool` |

### `ShadowAccountService` (file:597)

| Method | File:Line | Signature |
|---|---|---|
| `get_wallet_balance` | 611 | `(self) -> AccountInfo` |
| `get_available_balance` | 628 | `(self) -> float` |
| `get_equity` | 633 | `(self) -> float` |
| `get_margin_usage` | 638 | `(self) -> dict[str, float]` |
| `health_check` | 649 | `(self) -> bool` |

Module-level helpers: `_in_boot_grace` (54), `_shadow_get_with_retry` (59), `_parse_side` (666), `_build_position` (673), `_build_close_order` (703), `_build_account_info` (719), `_empty_account_info` (738), `_rejected_order` (748), `_optional_float` (767).

Boot-grace window constant: `_BOOT_GRACE_SECONDS = 30.0` (file:51). Inside the window, exhausted retries log at DEBUG; after the window, at ERROR (file:119-124).

---

## 2. `ShadowOrderService.place_order` — Signature Parity with Live

File:424-437:
```python
async def place_order(
    self,
    symbol: str,
    side: Side,
    order_type: OrderType,
    qty: float,
    price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    leverage: int | None = None,
    *,
    purpose: str = "other",
    layer_snapshot: "LayerSnapshot | None" = None,
    force: bool = False,
) -> Order
```

This signature matches `OrderService.place_order` (live, file:400-414) parameter-by-parameter (name, kind, default). Shadow accepts but does NOT enforce `purpose`/`force`/`layer_snapshot` — values are recorded in the `SHADOW_ORDER_RECEIVED` audit log only (file:482-494).

### Parity Test

`tests/test_shadow_signature_parity.py` (205 lines). Three test functions:

1. `test_shadow_implements_every_public_live_method(live_cls, shadow_cls)` (test_file:144-158) — parametrized over `(OrderService, ShadowOrderService)`, `(PositionService, ShadowPositionService)`, `(AccountService, ShadowAccountService)`. Asserts `live_methods - shadow_methods == set()`.

2. `test_shadow_method_signatures_match_live(live_cls, shadow_cls)` (test_file:162-178) — for every shared method, calls `_assert_signature_match` which compares each parameter as `(name, kind.name, default)` tuples (test_file:79-86, `_normalize_param`).

3. `test_place_order_accepts_phase2_kwargs()` (test_file:181-205) — direct regression test that `inspect.signature(ShadowOrderService.place_order).bind(self=None, symbol=..., purpose="layer3_entry", layer_snapshot=None, force=False, ...)` does not raise `TypeError`.

The test exists because of a 2026-04-27 incident: live `OrderService.place_order` had `purpose`/`layer_snapshot`/`force` added in Phase 2 of the Layer 1 restructure, but the Shadow mirror was NOT updated, causing every brain-driven paper trade to crash with `TypeError: ShadowOrderService.place_order() got an unexpected keyword argument 'purpose'` (test_file docstring lines 17-25; references `dev_notes/phase0_post_layer1_fixes/issue_1_shadow_signature.md`).

The annotation is intentionally not compared (test_file:81-86): "What matters for runtime call compatibility is the name, kind, and default."

---

## 3. Shadow HTTP Endpoint Mapping

URL config: `general.shadow_api_url` default `"http://127.0.0.1:9090"` (`src/config/settings.py:41`, also at line 2336 in the loader). Wired into the adapters at `src/workers/manager.py:289-298`:

```python
shadow_url = getattr(settings.general, "shadow_api_url", "http://127.0.0.1:9090")
self._shadow_session = aiohttp.ClientSession()
shadow_position = ShadowPositionService(self._shadow_session, shadow_url)
shadow_order = ShadowOrderService(self._shadow_session, shadow_url)
shadow_account = ShadowAccountService(self._shadow_session, shadow_url)
log.info("Shadow adapters: created (API: {url})", url=shadow_url)
```

A single shared `aiohttp.ClientSession` is used by all three adapters.

### Endpoint table

Routes from the Shadow side (`/home/inshadaliqbal786/shadow/src/api/shadow_client.py:84-97`):
| Method | URL | Adapter caller (file:line) | Purpose |
|---|---|---|---|
| POST | `/api/order` | shadow_adapter.py:509 | Place order |
| POST | `/api/close` | shadow_adapter.py:257 | Full close |
| POST | `/api/reduce` | shadow_adapter.py:292 | Partial close |
| POST | `/api/set-sl` | shadow_adapter.py:350 | Set SL |
| POST | `/api/set-tp` | shadow_adapter.py:362 | Set TP |
| GET | `/api/positions` | shadow_adapter.py:159 | All positions |
| GET | `/api/position/{symbol}` | shadow_adapter.py:177 | Single position |
| GET | `/api/position/{symbol}/last_close` | shadow_adapter.py:210 | Most recent close |
| GET | `/api/balance` | shadow_adapter.py:620 | Wallet balance |
| GET | `/api/ticker/{symbol}` | (not used by adapter) | — |
| GET | `/api/health` | shadow_adapter.py:396, 583, 652 | Health probe |

### POST /api/order request format

Built at shadow_adapter.py:496-503:
```python
payload = {
    "symbol": symbol,
    "side": side_str,
    "qty": qty,
    "leverage": leverage or 1,
    "sl": stop_loss,
    "tp": take_profit,
}
```

Response shape (used at file:516-547):
- `data["status"]` may be `"Rejected"` -> returns `Order(status=REJECTED)` with `data.get("reason", "unknown")` logged.
- Else expects `data["order_id"]`, `data["price"]`, `data.get("qty", qty)`. Returns `Order(status=FILLED)` with `filled_qty = qty`, `avg_fill_price = price`.

Timeout: GAP — `place_order` does NOT set a per-request timeout. The default `aiohttp.ClientSession()` timeout (5 min) applies. Only `health_check` sets `aiohttp.ClientTimeout(total=5)` (file:397, 586, 655).

Retry: `place_order` has NO retry layer; one `aiohttp` POST. On `aiohttp.ClientError` it logs error and returns `_rejected_order(symbol, side=side)` (file:512-514). The shared retry helper `_shadow_get_with_retry` (file:59-127) is GET-only and used for `get_positions` (file:157), `get_wallet_balance` (file:619).

`_shadow_get_with_retry` parameters: `attempts=5`, `base_delay=0.2`, exponential backoff (`base_delay * 2**(attempt-1)`). Worst-case sleep ~3.0s before final exhaust (file:71-73). HTTP 4xx (except 429) abandoned without retry (file:99-104). On full exhaustion, returns `None` and logs `SHADOW_CALL_FAIL`.

---

## 4. Paper-Trade Simulation

The adapter delegates ALL fill simulation to the Shadow service itself (separate process at `127.0.0.1:9090`). Shadow's order engine lives at `/home/inshadaliqbal786/shadow/src/exchange/order_engine.py` (out of scope for the adapter).

The adapter does NOT perform local slippage/latency simulation. Side enum -> string conversion (`side.value`) at file:475, then JSON POST.

Audit logging in the adapter (file:482-494, 505, 533):
- `SHADOW_ORDER_RECEIVED` (line 490): logs symbol, side, qty, purpose, `layer_snapshot_keys=[...]`, force. Phase 1 of post-Layer-1 fix added this for directive→execution audit reconciliation.
- `SHADOW_ORD_SEND` (line 505): logs sl, tp, lev BEFORE the POST.
- `SHADOW_ORD_RESP` (line 533): logs `oid`, `fill` price, `st=FILLED` AFTER the POST.

Note: the snapshot class fields at audit time (from `layer_snapshot_keys`) are `[captured_at_monotonic, captured_at_wall, layer_active]` — confirmed in every recent log line.

---

## 5. Last 20 SHADOW_ORDER_RECEIVED Events (last 24h)

Total counts in last 24h (2026-05-01..2026-05-02 from `data/logs/workers.*.log`):
- `SHADOW_ORDER_RECEIVED`: **35**
- `SHADOW_ORD_RESP`: **35**
- `SHADOW_ORD_RESP` with `st=FILLED`: **35**
- Fill rate: **35/35 = 100.0%** (no rejections observed in last 24h)

Last 20 RECEIVED→RESP pairs (timestamp delta = end-to-end latency):

| # | Time | Symbol | Side | Qty | Latency (ms) | Result |
|---|---|---|---|---|---|---|
| 1 | 2026-05-02 02:35:42.743 | DYDXUSDT | Buy | 17103.6 | 14 | FILLED |
| 2 | 2026-05-02 02:35:43.635 | ORCAUSDT | Buy | 937.5 | 17 | FILLED |
| 3 | 2026-05-02 02:35:44.133 | INJUSDT | Sell | 301.6 | 9 | FILLED |
| 4 | 2026-05-02 02:44:14.063 | EGLDUSDT | Sell | 164.99 | 13 | FILLED |
| 5 | 2026-05-02 02:44:15.058 | AXSUSDT | Buy | 489.7 | 15 | FILLED |
| 6 | 2026-05-02 03:00:16.781 | RENDERUSDT | Sell | 1083.3 | 13 | FILLED |
| 7 | 2026-05-02 03:07:58.682 | AXSUSDT | Buy | 218.5 | 11 | FILLED |
| 8 | 2026-05-02 03:07:59.664 | AEROUSDT | Buy | 668.0 | 10 | FILLED |
| 9 | 2026-05-02 03:16:50.091 | ALGOUSDT | Sell | 2819.5 | 9 | FILLED |
| 10 | 2026-05-02 03:16:51.126 | AXSUSDT | Buy | 218.4 | 9 | FILLED |
| 11 | 2026-05-02 03:16:51.804 | NEARUSDT | Buy | 386.8 | 11 | FILLED |
| 12 | 2026-05-02 03:26:22.216 | BLURUSDT | Buy | 11108.0 | 19 | FILLED |
| 13 | 2026-05-02 03:42:13.855 | RENDERUSDT | Buy | 214.2 | 12 | FILLED |
| 14 | 2026-05-02 03:50:43.307 | INJUSDT | Buy | 99.8 | 9 | FILLED |
| 15 | 2026-05-02 03:50:44.287 | BLURUSDT | Buy | 41244.0 | 10 | FILLED |
| 16 | 2026-05-02 03:59:15.601 | RENDERUSDT | Sell | 175.2 | 10 | FILLED |
| 17 | 2026-05-02 04:07:52.484 | AXSUSDT | Buy | 218.1 | 10 | FILLED |
| 18 | 2026-05-02 04:16:29.144 | RENDERUSDT | Sell | 542.5 | 8 | FILLED |
| 19 | 2026-05-02 04:16:34.259 | HYPEUSDT | Buy | 26.8 | 15 | FILLED |
| 20 | 2026-05-02 04:25:16.987 | AXSUSDT | Buy | 220.9 | 10 | FILLED |

**Average response latency: ~11.7 ms** (sum 224, /20). All purpose=layer3_entry. All carrying `layer_snapshot_keys=[captured_at_monotonic,captured_at_wall,layer_active]`. Symbols repeated across cycles (AXSUSDT 5×, RENDERUSDT 4×, AEROUSDT 1× etc.) reflecting the strategist re-firing on the same setups.

---

## 6. Failure Handling Summary

| Failure path | File:Line | Behaviour |
|---|---|---|
| Shadow listener boot race (GET `/api/balance`/`/api/positions`) | 59-127 | 5 attempts with exponential backoff; in 30s boot grace logs at DEBUG, after at ERROR |
| `aiohttp.ClientError` on POST `/api/order` | 512-514 | logs ERROR `Shadow order error`, returns `_rejected_order(symbol, side=side)` (status=REJECTED, qty=0) |
| `data["status"] == "Rejected"` | 516-529 | logs WARNING `Shadow order rejected: {reason}`, returns `Order(status=REJECTED, ...)` preserving qty |
| `aiohttp.ClientError` on `/api/close` | 260-262 | logs ERROR `Shadow close error`, returns `_rejected_order(symbol)` |
| `aiohttp.ClientError` on `/api/reduce` | 296-301 | logs WARNING `REDUCE_FALLBACK reason=http_error`, falls back to full close |
| Shadow rejects partial reduce (`http != 200` or `status != "Reduced"`) | 320-330 | logs WARNING `REDUCE_FALLBACK reason=shadow_reject http={status} err='...'`, falls back to full close |
| `aiohttp.ClientError` on `/api/set-sl` | 354-356 | logs ERROR `Shadow set_sl error`, returns False |
| Network failure on `/api/health` | 400-401, 588-589, 657-658 | swallows Exception, returns False |

---

## 7. Notes & Gaps

- The `SHADOW_POSITION_CLOSE` audit log (file:250-252) carries `purpose` so close events reconcile with directive→execution traces.
- `purpose`/`layer_snapshot`/`force` parity with live exists on `place_order` only. `close_position` accepts `purpose` (kw-only, default `"layer4_close"`); the live `PositionService.close_position` has the same parameter (verified by parity test).
- Shadow has no Layer 3 gate (file:444-449 docstring): "Shadow has no Layer 3 gate, so the values are ACCEPTED but not enforced here".
- `did=d-<timestamp>` field at the end of each log line is the directive-id from `ctx()` — every order in a cycle shares the same `did` (visible in the table above: did=d-1777689214603 covers 3 orders within a few seconds).
- GAP — no per-request `timeout` on `place_order` POST.
- GAP — no retry on `place_order` POST. Live `OrderService` has a scoped 1-retry; Shadow has 0.
