# Phase 0 — Issue Investigation: Duplicate ORDER_START (Issue #4) [SAFETY-CRITICAL]

**Issue:** At and around 12:47:28 on 2026-04-26, the workers log emitted two `ORDER_START` lines per trade ~500ms apart with identical params. Same pattern observed across multiple historical days. **No `order_link_id` is sent to Bybit.**

## Section A — The mechanism

### A.1 Single emit site

**File:** `src/trading/services/order_service.py:85`

```python
log.info(f"ORDER_START | sym={symbol} side={side.value} type={order_type.value} qty={qty} lev={leverage} sl={stop_loss} tp={take_profit} | {ctx()}")
```

Every `ORDER_START` log line in the system flows through this one emit point. Confirmed via grep: zero other emit sites in `src/`.

### A.2 The retry decorator

**File:** `src/trading/services/order_service.py:51-52`

```python
@retry(max_attempts=2, delay=0.5)
@timed
async def place_order(self, symbol, side, order_type, qty, ...) -> Order:
    ...
    log.info(f"ORDER_START | sym={symbol} ... | {ctx()}")  # line 85
    ...
    result = await self._client.call("place_order", **order_params)  # line 175
    ...
```

**Retry decorator implementation** (`src/core/decorators.py:17-65`):

```python
@functools.wraps(func)
async def async_wrapper(*args, **kwargs):
    current_delay = delay        # 0.5
    for attempt in range(1, max_attempts + 1):  # max_attempts=2
        try:
            return await func(*args, **kwargs)
        except exceptions as e:                  # catches Exception (default)
            ...
            await asyncio.sleep(current_delay)   # sleep 0.5s
            current_delay *= backoff             # backoff=2.0 default
    raise last_exc
```

**The 500ms gap matches `delay=0.5` exactly.** When attempt 1 raises any `Exception`, the decorator sleeps 0.5s and re-invokes the wrapped function — re-running every line of `place_order` including line 85's ORDER_START log AND line 175's Bybit `place_order` call.

### A.3 Where attempt 1 fails

The retry catches `Exception` (default tuple). Inside `place_order` (lines 53-229), several blocks can raise:
- `_validate_symbol` / `_validate_stop_loss` / `_validate_leverage` (lines 86-88) — raise on bad inputs.
- `instrument_svc.get_instrument_info(symbol)` (line 91) — network-bound, raises on failure.
- `validate_order_params` (line 99) raises `InvalidOrderError`.
- The position-cap math (lines 117-156) is wrapped in its own try/except (line 155), so it does NOT bubble.
- `_set_leverage(symbol, leverage)` (line 115) — Bybit API call, can raise.
- **`self._client.call("place_order", **order_params)`** (line 175) — Bybit place-order; can raise on transient timeout, 5xx, network blip.
- `save_order` to DB (line 193) — can raise on lock contention.
- The post-place SL verification block (lines 207-227) is wrapped in its own try/except (line 226), so it does NOT bubble out of the function.

**Most likely cause of attempt-1 raise**: transient network conditions on the Bybit `place_order` call (line 175), since the live evidence shows the duplicates occurring at moments of high system activity (Stop Trading click flushing many orders at once).

### A.4 What Bybit sees

**File:** `src/trading/services/order_service.py:159-173`

```python
order_params: dict = {
    "category": "linear",
    "symbol": symbol,
    "side": side.value,
    "orderType": order_type.value,
    "qty": str(qty),
}
if price is not None:
    order_params["price"] = str(price)
if stop_loss is not None:
    order_params["stopLoss"] = str(stop_loss)
if take_profit is not None:
    order_params["takeProfit"] = str(take_profit)
```

**No `orderLinkId`.** Without an idempotency key, Bybit cannot recognize attempt 2 as a duplicate of attempt 1. If attempt 1 actually reached Bybit and was accepted (then a downstream timeout returned a transient error to our client), the retry places a **second real order**.

### A.5 Live evidence (verified)

**Source:** `data/logs/workers.log:5-15` (2026-04-26 12:47:28-12:49:03):

```
12:47:28.355 ORDER_START | sym=LDOUSDT side=Buy type=Market qty=1795 lev=3 ...
12:47:28.858 ORDER_START | sym=LDOUSDT side=Buy type=Market qty=1795 lev=3 ...   ← +503ms
12:47:28.981 ORDER_START | sym=INJUSDT side=Buy type=Market qty=138 lev=2 ...
12:47:29.483 ORDER_START | sym=INJUSDT side=Buy type=Market qty=138 lev=2 ...   ← +502ms
12:49:01.221 ORDER_START | sym=BTCUSDT side=Buy type=Market qty=0.007 lev=3 ...
12:49:02.074 ORDER_START | sym=BTCUSDT side=Buy type=Market qty=0.007 lev=3 ...  ← +853ms (drift)
12:49:02.375 ORDER_START | sym=ETHUSDT side=Buy type=Market qty=0.22 lev=3 ...
12:49:03.184 ORDER_START | sym=ETHUSDT side=Buy type=Market qty=0.22 lev=3 ...   ← +809ms
```

**Source:** `data/logs/workers.2026-04-24_12-13-43_359149.log` (4 confirmed historical pairs at 16:41:48-51 and 21:56:48-50, 21:58:04-05).

Pattern is **active and ongoing**, not a one-off. The +500ms is the most common gap; +800-850ms gaps suggest either the inner Bybit call took longer to return-fail, or the decorator's exponential backoff (2.0×) widened on a second attempt of a different retry stack (less likely given `max_attempts=2`).

## Section B — The dependencies

Six callers of `place_order` (all six are at risk of producing a duplicate):

| Caller | File:Line |
|---|---|
| Brain | `src/brain/brain_v2.py:487` |
| Strategy worker | `src/workers/strategy_worker.py:1033` |
| Telegram bot | `src/telegram/bot.py:689` |
| Telegram handler | `src/telegram/handlers/trading.py:88` |
| Force-trade script | `scripts/force_trade.py:62` |
| MCP tool | `src/mcp/tools/trading_tools.py:170` |

The fix must be inside `place_order` itself — fixing at the caller layer leaves five other doors open.

Downstream consumers of `ORDER_START`:
- Operator dashboards / log greps
- The audit trail in `data/logs/workers.log` for trade reconstruction

If a fix changes the log format (e.g., adding `link_id={...}`), downstream parsers may need updates. Confirm none break — current parsers grep for `ORDER_START | sym=` so adding fields after the existing structure is backwards-compatible.

## Section C — The constraints

- **Cannot drop the retry behavior entirely without replacement** — the original intent (handle transient Bybit hiccups) is legitimate.
- **Must not change `ORDER_START`'s prefix** — operator tooling depends on grep'able `ORDER_START | sym=`.
- **Must not introduce a new race** — generating `order_link_id` before lock-bound work is fine; generating it after is not.
- **Bybit's `orderLinkId` constraint**: alphanumeric + dash/underscore, max 36 chars. UUID4 hex (32 chars) plus a 3-char prefix fits comfortably.
- **Bybit duplicate response code**: Bybit returns `retCode=10005` (or similar) for "duplicate orderLinkId". Must verify the exact code in `BybitClient._handle_response` (`src/trading/client.py:178-212`).

## Section D — The fix (per user decision)

User selected **"Idempotency-key + scoped retry"** — the recommended option.

### D.1 Restructure `place_order`

1. **Remove `@retry(max_attempts=2, delay=0.5)` from line 51.**

2. **Generate `order_link_id` ONCE at the top of `place_order`** before any logging or validation:
   ```python
   import uuid
   order_link_id = f"ti-{uuid.uuid4().hex[:24]}"
   ```

3. **Update line 85 ORDER_START log** to include `link_id={order_link_id}`:
   ```python
   log.info(
       f"ORDER_START | link_id={order_link_id} sym={symbol} side={side.value} "
       f"type={order_type.value} qty={qty} lev={leverage} sl={stop_loss} tp={take_profit} | {ctx()}"
   )
   ```

4. **Inject `orderLinkId` into `order_params`** at line 159-173:
   ```python
   order_params: dict = {
       "category": "linear",
       "symbol": symbol,
       "side": side.value,
       "orderType": order_type.value,
       "qty": str(qty),
       "orderLinkId": order_link_id,
   }
   ```

5. **Wrap ONLY the Bybit call** at line 175 in an inline scoped retry:
   ```python
   for attempt in range(2):
       try:
           result = await self._client.call("place_order", **order_params)
           if attempt > 0:
               log.info(
                   f"ORDER_RETRY_OK | link_id={order_link_id} attempt={attempt+1} | {ctx()}"
               )
           break
       except DuplicateOrderLinkIdError:                  # (new sentinel; see D.2)
           log.warning(
               f"ORDER_DEDUPED | link_id={order_link_id} attempt={attempt+1} | {ctx()}"
           )
           # Recover the canonical order from Bybit by orderLinkId
           result = await self._client.call(
               "get_open_orders", category="linear", orderLinkId=order_link_id,
           )
           # ... extract the matching order; treat as success
           break
       except Exception as e:
           if attempt == 1:
               log.error(
                   f"ORDER_RETRY_EXHAUSTED | link_id={order_link_id} err={str(e)[:120]} | {ctx()}"
               )
               raise
           log.warning(
               f"ORDER_RETRY | link_id={order_link_id} attempt={attempt+1} err={str(e)[:80]} | {ctx()}"
           )
           await asyncio.sleep(0.5)
   ```

6. **Update ORDER_OK** (line 195) and the post-SL log lines to include `link_id={order_link_id}` for trace correlation.

### D.2 Bybit duplicate handling

In `src/trading/client.py:178-212` (`_handle_response`), recognize Bybit's duplicate-orderLinkId retCode and raise a typed `DuplicateOrderLinkIdError` (new exception in `src/trading/exceptions.py` or wherever `OrderRejectedError` lives). The retry block in D.1 catches this and recovers the canonical order rather than raising.

### D.3 Validation/SL stay non-retried

Pre-Bybit validation (lines 86-104) and post-Bybit SL verification (lines 207-227) remain outside the retry boundary. If validation fails, fail fast with no retry. If SL verification fails, log loudly but the order itself is already placed.

## Section E — Forensic audit (Phase 5 deliverable)

In addition to the code fix, run a forensic audit on Bybit account history:
1. Pull all closed orders for the past 48h via the Bybit API.
2. Group by `(symbol, side, qty, orderType)` and timestamp clustered within 1 second.
3. Count clusters with size > 1.
4. Cross-reference with `ORDER_START` log lines.
5. Determine: **were real duplicate fills placed?** This may require reconciliation with the user — refunds/closes may have already neutralized them, but the safety implication is on the record.

## Verified citations

| Claim | File:Line |
|---|---|
| Single ORDER_START emit site | `src/trading/services/order_service.py:85` |
| `@retry(max_attempts=2, delay=0.5)` on place_order | `src/trading/services/order_service.py:51` |
| Retry decorator catches all `Exception` | `src/core/decorators.py:21,45` |
| Retry sleeps `delay` (0.5s) on attempt 1 | `src/core/decorators.py:63` |
| `orderLinkId` not in current params | `src/trading/services/order_service.py:159-173` |
| Bybit place_order call site | `src/trading/services/order_service.py:175` |
| Bybit response handler | `src/trading/client.py:178-212` |
| Live evidence — every order doubles | `data/logs/workers.log:5-15` |
| Six callers of `place_order` | brain_v2.py:487, strategy_worker.py:1033, telegram/bot.py:689, telegram/handlers/trading.py:88, scripts/force_trade.py:62, mcp/tools/trading_tools.py:170 |
