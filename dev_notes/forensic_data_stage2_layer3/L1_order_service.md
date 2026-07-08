# L1 — OrderService Forensic Data

Refreshed: 2026-05-02 ~11:45 UTC. Replaces 2026-04-28 baseline.

Source path: `/home/inshadaliqbal786/trading-intelligence-mcp/src/trading/services/order_service.py`
Measured line count: **1156** lines (truth doc said ~620; measurement larger).

---

## 1. Class & Public Method Signatures

Class: `OrderService` (file:91)

Constructor (file:102-128):
```
def __init__(self, client: BybitClient, db: DatabaseManager, settings: Settings) -> None
```
Stored attrs: `_client`, `_db`, `_settings`, `_trading_repo` (TradingRepository), `_instrument_svc` (InstrumentService), `_layer_manager` (None until attach), `_init_monotonic` (boot deadline tracker).

Public methods:
| Method | File:Line | Signature (abridged) |
|---|---|---|
| `attach_layer_manager` | 130 | `(self, layer_manager: LayerManager) -> None` |
| `place_order` | 400 | see section 2 |
| `modify_order` | 855 | `(self, symbol, order_id, qty=None, price=None) -> Order` (decorated `@retry(max_attempts=2, delay=0.5, exceptions=(BybitAPIError,OSError,RuntimeError))`, line 850) |
| `cancel_order` | 911 | `(self, symbol, order_id) -> bool` (`@retry(max_attempts=2, delay=0.5)`, line 909) |
| `cancel_all_orders` | 940 | `(self, symbol=None) -> int` (`@retry(max_attempts=2, delay=0.5)`, line 938) |
| `get_open_orders` | 967 | `(self, symbol=None) -> list[Order]` (`@retry(max_attempts=3, delay=1.0)`, line 965) |
| `get_order_history` | 992 | `(self, symbol=None, limit=50) -> list[Order]` (`@retry(max_attempts=3, delay=1.0)`, line 990) |

Private helpers: `_emit_order_blocked` (142), `_enforce_layer3_gate` (199), `_place_order_with_idempotent_retry` (678), `_recover_order_by_link_id` (773), `_validate_symbol` (1022), `_validate_stop_loss` (1030), `_validate_leverage` (1038), `_set_leverage` (1049), `_get_order_from_exchange` (1067).

Module-level helpers: `_new_order_link_id` (79), `_parse_order` (1098), `_map_order_type` (1123), `_map_order_status` (1135), `_parse_optional_float` (1149).

---

## 2. `place_order` Full Signature

File:399-414, decorated `@timed` only (no `@retry` — comment file:60-72 explains the retry was deliberately narrowed to the inner RPC after duplicate-order incidents).

```python
@timed
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

Closed-set kwargs (file:49-58):
- `_VALID_PURPOSES` = `{layer3_entry, layer4_close, layer4_sl, telegram_manual, mcp_tool, test, other}` — ValueError on misspelling at line 473-477.
- `_GATED_PURPOSES` = `{layer3_entry, telegram_manual, mcp_tool}` — gated at line 498.

---

## 3. Callers of `place_order`

| Caller File | Line | Argument pattern |
|---|---|---|
| `src/workers/strategy_worker.py` | 1521 | `purpose="layer3_entry", layer_snapshot=_layer_snapshot` (full kw set + qty/sl/tp/lev) |
| `src/brain/brain_v2.py` | 487 | `purpose="layer3_entry"` (no `layer_snapshot`, no `force`) |
| `src/telegram/bot.py` | 691 | `purpose="telegram_manual"` (qty, leverage, sl, tp) |
| `src/telegram/handlers/trading.py` | 88 | `purpose="telegram_manual"` |
| `src/mcp/tools/trading_tools.py` | 170 | `purpose="mcp_tool"` (price, sl, tp, leverage) |
| `src/core/transformer.py` | 958 | proxy `*args, **kwargs` to `active_order_service.place_order` |
| `src/brain/executor.py.deprecated` | 112 | (deprecated path, file extension `.deprecated`) |
| `src/core/layer_manager.py` | 1666 | docstring reference only |
| `src/core/trade_recorder.py` | 71 | docstring reference only |

`src/workers/strategy_worker.py:1521-1531`:
```python
order = await order_svc.place_order(
    symbol=symbol,
    side=side_enum,
    order_type=OrderType.MARKET,
    qty=qty,
    stop_loss=sl,
    take_profit=tp,
    leverage=leverage,
    purpose="layer3_entry",
    layer_snapshot=_layer_snapshot,
)
```

`src/brain/brain_v2.py:487-496`:
```python
order = await self.order_service.place_order(
    symbol=sig.symbol,
    side=sig.direction,
    order_type=OrderType.MARKET,
    qty=qty,
    stop_loss=decision.stop_loss,
    take_profit=decision.take_profit_1,
    leverage=decision.leverage,
    purpose="layer3_entry",
)
```

---

## 4. Pre-order Validation

Validation order in `place_order`:

1. Purpose closed-set check (file:473-477): `ValueError` on bad purpose.
2. Idempotency key generation (file:481): `_new_order_link_id()` produces `ti-<24-hex>` once per call.
3. `ORDER_ATTEMPT` audit log (file:488-492).
4. Layer 3 gate for gated purposes (file:498-506) — see section 6.
5. `ORDER_START` audit log (file:509-514).
6. `_validate_symbol` (file:515 -> 1022-1028) — `InvalidOrderError` if symbol not in `SUPPORTED_SYMBOLS`.
7. `_validate_stop_loss` (file:516 -> 1030-1036) — `InvalidOrderError("Stop-loss is mandatory…")` if `settings.risk.mandatory_stop_loss` and `stop_loss is None`. Default `mandatory_stop_loss=True` (config/settings.py:516).
8. `_validate_leverage` (file:517 -> 1038-1047) — `RiskLimitExceededError` if `leverage > settings.risk.max_leverage` (default `max_leverage=3`, settings.py:515).
9. Instrument lookup (file:520) — `InstrumentService.get_instrument_info` fetches `qty_step` and `price_tick`.
10. Round qty/price (file:523-525) via `round_qty` / `round_price`.
11. `InstrumentService.validate_order_params` (file:528-533) — concatenates issues into `InvalidOrderError`.
12. LIMIT-without-price check (file:536-540) — `InvalidOrderError`.
13. `_set_leverage` (file:543-544 -> 1049-1065) — RPC `set_leverage`. "leverage not modified" / `110043` is swallowed as success (file:1062-1063).
14. HARD POSITION SIZE CAP ("FIX 2", file:546-585): reads wallet via `AccountService.get_wallet_balance()`, computes `notional_value = qty * price`, caps to `equity * max_position_size_pct/100` (default `max_position_size_pct=10.0`, settings.py:519). Then computes per-trade-loss cap of `2% of equity` using `(stop_loss_distance * qty * leverage)`. Note: this whole block is wrapped in a bare `try/except: log.warning("Position size cap check failed: ...")` (file:584-585) so any failure here silently lets the order through with un-capped size.

### `ORDER_PREFLIGHT_INSUFFICIENT`

GAP — searched: `grep -rn "ORDER_PREFLIGHT_INSUFFICIENT|preflight" src/` returned **0 matches** in production code. There is no early-abort log tag matching `ORDER_PREFLIGHT_INSUFFICIENT`. Insufficient-balance is detected at exchange level via Bybit retCodes 110012/110043/110044 -> `InsufficientBalanceError` (client.py:58-59) raised from `_handle_response`.

---

## 5. Order Placement to Bybit

URL/transport: there is no direct HTTP — `BybitClient.call("place_order", **kwargs)` calls `pybit.unified_trading.HTTP.place_order` via `asyncio.to_thread` (client.py:190). pybit uses Bybit's mainnet/testnet REST endpoints internally based on `bybit.testnet` flag (client.py:124-129).

Order params built at order_service.py:590-604:
```python
order_params: dict = {
    "category": "linear",
    "symbol": symbol,
    "side": side.value,
    "orderType": order_type.value,
    "qty": str(qty),
    "orderLinkId": order_link_id,
}
if price is not None: order_params["price"] = str(price)
if stop_loss is not None: order_params["stopLoss"] = str(stop_loss)
if take_profit is not None: order_params["takeProfit"] = str(take_profit)
```

Auth: `BybitAuth(api_key, api_secret)` set up at `BybitClient.connect()` (client.py:122). pybit signs requests via `recv_window` from `bybit.recv_window` (client.py:128).

Rate limit: `@rate_limit(calls_per_second=10.0)` at `BybitClient.call` (client.py:161). See L2.

Retry policy: place_order has its own scoped retry — `_place_order_with_idempotent_retry` (file:678-771). At-most-one transient retry of the inner RPC; `_ORDER_PLACE_MAX_ATTEMPTS = 2` (file:76); `_ORDER_PLACE_RETRY_DELAY_S = 0.5` (file:75). Bybit-mapped errors (`InvalidOrderError, RateLimitError, OrderRejectedError, BybitAPIError`) re-raise immediately at file:740-752; non-Bybit exceptions retry once at file:753-766.

Outer retry on `BybitClient.call` itself: `@retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(BybitAPIError,))` — client.py:160.

### Routing to Shadow vs Bybit

OrderService is the LIVE-trading service. Routing occurs at the `Transformer` level:
- `src/core/transformer.py:958` — proxy: `return await self._t.active_order_service.place_order(*args, **kwargs)`.
- `src/workers/manager.py:289-298` — both `BybitClient` and `ShadowOrderService` are constructed; `transformer.set_services(...)` feeds both; `transformer.initialize()` reads DB mode and selects `active_order_service`.

When `general.mode == "shadow"`, the proxy resolves to `ShadowOrderService` (see L3); when `mode == "live"`, to the live `OrderService`. There is no per-call branch inside `OrderService` itself.

---

## 6. Order ID Generation, Idempotency, RC_DUPLICATE_ORDER_LINK_ID = 110072

`_new_order_link_id` (file:79-86):
```python
_ORDER_LINK_ID_PREFIX = "ti"
_ORDER_LINK_ID_LEN = 24
def _new_order_link_id() -> str:
    return f"{_ORDER_LINK_ID_PREFIX}-{uuid.uuid4().hex[:_ORDER_LINK_ID_LEN]}"
```
Format: `ti-<24-hex>` = 27 chars (Bybit V5 limit 36).

110072 mapping: `client.py:62 -> DuplicateOrderLinkIdError`. Constant defined at `client.py:35: RC_DUPLICATE_ORDER_LINK_ID = 110072`.

Handling in OrderService (file:730-739):
```python
except DuplicateOrderLinkIdError:
    log.warning(f"ORDER_DEDUPED | link_id={...} | ...")
    return await self._recover_order_by_link_id(order_link_id=order_link_id, symbol=symbol)
```

Recovery sequence (`_recover_order_by_link_id` file:773-839):
1. Try `get_open_orders(category="linear", symbol=symbol, orderLinkId=order_link_id)` (file:790-802); if found, `ORDER_RECOVERED | src=open` log and return `{orderId, orderLinkId}`.
2. Else try `get_order_history(...)` (file:811-825); `ORDER_RECOVERED | src=history`.
3. Else synthesize `{"orderId": f"DEDUP-{order_link_id}"}` and emit `ORDER_RECOVERY_SYNTH` (file:835-839).

---

## 7. Position Tracking After Fill

After a successful place_order:

- `Order` object built at file:622-634 with `OrderStatus.NEW`.
- Persisted via `await self._trading_repo.save_order(order)` (file:636).
- `ORDER_OK` logged (file:638-642).
- FIX 3 — VERIFY STOP-LOSS ON EXCHANGE (file:654-674): sleeps 1.5s, instantiates `PositionService` and calls `get_position(symbol)`. If position has no SL or `stop_loss == 0`, logs `SL NOT on exchange` and calls `set_stop_loss`. Re-checks after 0.5s, logs `SL VERIFIED` or `SL FAILED TO SET`. Wrapped in `try/except`.

Cache vs DB:
- DB write: `TradingRepository.save_order` writes to `orders` table.
- No in-memory cache update by `OrderService` itself — position cache is owned by `PositionService` (separate file). Position reconciliation comes from periodic `PositionService.get_positions` polling and FIX 3's explicit re-fetch.

---

## 8. Failure Modes

Bybit retCode -> exception map (`client.py:51-63`):
| retCode | Exception | Note |
|---|---|---|
| 10003 | `AuthenticationError` | Invalid API key |
| 10004 | `AuthenticationError` | Invalid signature |
| 10006 | `RateLimitError` | Rate limited (`RC_RATE_LIMIT`) |
| 110001 | `InvalidOrderError` | Order not found |
| 110003 | `InvalidOrderError` | Quantity not valid |
| 110007 | `PositionError` | Position not exists |
| 110012 | `InsufficientBalanceError` | Insufficient balance for order |
| 110043 | `InsufficientBalanceError` | Insufficient available balance |
| 110044 | `InvalidOrderError` | Insufficient balance after SL (mapped to `InvalidOrderError`, NOT `InsufficientBalanceError`) |
| 110045 | `InvalidOrderError` | Leverage not modified |
| 110072 | `DuplicateOrderLinkIdError` | Idempotency hit |

Comment at client.py:39-50 documents that 10001 (parameter error) deliberately falls through to `BybitAPIError` because the pre-2026-04 mapping (10001 -> `InsufficientBalanceError`) was wrong.

### Counts in last 24h (2026-05-01 to 2026-05-02 from `data/logs/workers.*.log`)

| Pattern | Count | Notes |
|---|---|---|
| `ORDER_FAIL\|ORDER_REJECT_\|ORDER_BLOCKED` | 5 | All 5 are `ORDER_BLOCKED reason=lm_deadline_exceeded actor=system_auto` for purpose=mcp_tool |
| `10006 \| RateLimitError` | 1 false-positive | Single match is a `WORKER_LIVENESS_HEARTBEAT` line happening to contain "10006" — no actual rate-limit event |
| `110012 \| 110043 \| 110044 \| InsufficientBalance` | 0 | None — system is in shadow mode (paper trades route through Shadow, see L3) |
| `10003 \| 10004 \| InvalidAPIKey \| InvalidSign` | 0 | None |
| `110007` | 0 | None |
| `110072 \| ORDER_DEDUPED \| DuplicateOrderLinkId` | 0 | None |
| `ORDER_RETRY` | 0 | None |

5 verbatim `ORDER_BLOCKED` events (last 24h):
```
2026-05-01 00:27:19.777 ORDER_BLOCKED | link_id=ti-60665526ec054cc5b4c1282f sym=OPUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=1045.1
2026-05-01 00:27:20.556 ORDER_BLOCKED | link_id=ti-f7619483a44b4031ac05c12e sym=AEROUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=1045.9
2026-05-01 00:48:44.283 ORDER_BLOCKED | link_id=ti-9bdd2a8b5d0a4835993cd58c sym=AEROUSDT side=Sell purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=2329.6
2026-05-01 01:02:34.509 ORDER_BLOCKED | link_id=ti-831e5d767be5436e82214a22 sym=AXSUSDT side=Sell purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=3159.8
2026-05-01 01:02:35.460 ORDER_BLOCKED | link_id=ti-a100e0bffef3450f80f8f0e7 sym=AEROUSDT side=Buy purpose=mcp_tool reason=lm_deadline_exceeded actor=system_auto force=False deadline_s=60.0 elapsed_s=3160.8
```

All 5 have `elapsed_s` between 1045 and 3161 seconds — well past the 60s LM-attach deadline. The reason path is `_enforce_layer3_gate` Path 4a (file:248-278): when `lm is None and elapsed > deadline`, fail-close ALL purposes including layer4. These came from MCP tools targeting symbols not in any active workers session, confirming LayerManager attachment failed in those processes.

---

## 9. Order Audit (DB)

`orders` table schema (`/tmp/trading_snapshot_1777722335.db`):
```sql
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    price REAL NOT NULL DEFAULT 0,
    qty REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'New',
    filled_qty REAL NOT NULL DEFAULT 0,
    avg_fill_price REAL NOT NULL DEFAULT 0,
    stop_loss REAL,
    take_profit REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_orders_symbol_status ON orders(symbol, status);
```

Sample rows: GAP — `SELECT COUNT(*) FROM orders` returned **0** in the snapshot. The system is in shadow mode and paper trades are NOT persisted to this `orders` table; live `OrderService.place_order` -> `TradingRepository.save_order` is the only writer.

`order_history` table: GAP — does not exist in the snapshot DB. Order history is fetched ad-hoc from Bybit via `OrderService.get_order_history` (file:992-1018) and saved into the `orders` table (file:1015).

`trade_history` table also empty: `SELECT COUNT(*) FROM trade_history` returned **0** (schema present). Confirms no live trades persisted recently.

Shadow/paper trade audit lives in Shadow's own SQLite — see L3.
