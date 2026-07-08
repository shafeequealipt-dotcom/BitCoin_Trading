# I1 Phase 1 — F-26 TIMESTAMP_FAIL Investigation

**Status:** Phase 1 complete. Awaiting operator review of Phase 2 report
(separate file) before any code change.

**Branch:** `audit/all-tier2-combined` @ `b348038` (read-only investigation).

---

## TL;DR (root cause)

Bybit returns `retCode=10002` ("Request expired") when the gap between
**client-side signing** (`bybit_demo_client.py:344`) and **server-side
receipt** exceeds `recv_window=5000ms`. The client raises
`BybitAPIError`; the **adapter swallows it to an empty list**
(`bybit_demo_adapter.py:181-182`); the **watchdog interprets the empty
list as "every tracked position is closed on Bybit"**
(`position_watchdog.py:503-506`). Result: phantom close events written
to `trade_log` for live positions.

The proximate cause is `recv_window=5000ms` being too tight for network
jitter under VM load. **The architectural ROOT CAUSE** is that the
adapter contract collapses three distinct states into the same
response — `"truly zero positions" == "API error" == "API timeout"` —
all return `[]`. Without a semantic distinction at the adapter
boundary, no downstream consumer can avoid the phantom-close
interpretation.

The same pattern affects `get_wallet_balance` (→ `_empty_account_info()`),
`close_all_positions` (→ `[]`), and several other adapter methods.

---

## Anatomy of the call chain

### Step 1 — Client signs with stale timestamp

`src/bybit_demo/bybit_demo_client.py:342-351` (the retry loop):

```python
for attempt in range(1, self._retry_attempts + 1):
    try:
        timestamp_ms = int(time.time() * 1000)        # L344  ← sign timestamp
        if signed:
            signature = self._sign(timestamp_ms, sig_payload)  # L346
            headers = self._signed_headers(timestamp_ms, signature)
        else:
            headers = {"Content-Type": "application/json"}

        async with self._session.request(             # L351  ← actual send
            method,
            request_url,
            headers=headers,
            ...
        ) as resp:
```

Between L344 (timestamp captured) and L351 (HTTP request sent), the
following can elapse:

- aiohttp connection establishment (TCP + TLS)
- DNS resolution if cached entry expired
- TCP send-buffer queueing
- Network jitter to Bybit's edge
- VM CPU steal / GIL contention

Under nominal load this is <100ms. Under the F-27 DB cascade load
(I4) or F-29 memory pressure (separately addressed by RAM upgrade),
the delta can exceed 5000ms.

### Step 2 — Bybit rejects, client raises

`src/bybit_demo/bybit_demo_client.py:413-431` (non-zero retCode path):

```python
# 2xx — parse envelope.
envelope = await resp.json()
ret_code = int(envelope.get("retCode", -1))
ret_msg = str(envelope.get("retMsg", ""))

if ret_code == 0:
    return envelope

# Non-zero retCode → emit a specific structured tag (auth /
# timestamp / rate-limit / balance) then translate and raise.
_log_ret_code(self._log, ret_code, ret_msg, op=op)            # L427  ← emits BYBIT_DEMO_TIMESTAMP_FAIL
raise _translate_ret_code(ret_code, ret_msg, op=op)            # L428  ← raises BybitAPIError

except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:   # L430-431
    last_err = e
```

**Critical observation: line 430-431 catches ONLY** `aiohttp.ClientError`,
`OSError`, `asyncio.TimeoutError`. `BybitAPIError` is NOT in this set.
The exception raised at L428 propagates immediately out of the retry
loop without any retry attempts.

The `_TIMESTAMP_FAIL_CODES = frozenset({10002})` at L150-152 routes
through `_log_ret_code()` at L171-175 emitting:

```
BYBIT_DEMO_TIMESTAMP_FAIL | code=10002 op=positions msg='invalid request, please check your server timestamp or recv_window param: req_timestamp[N],server_timestamp['
```

Then `_translate_ret_code()` at L94-132 returns a `BybitAPIError`
because 10002 is NOT in any of the specialized codepoint sets
(`_INSUFFICIENT_BALANCE_CODES`, `_INVALID_ORDER_CODES`, `_RATE_LIMIT_CODES`,
`_AUTH_FAIL_CODES`, or the 110000-119999 range).

### Step 3 — Adapter collapses error to empty result

`src/bybit_demo/bybit_demo_adapter.py:177-182`:

```python
try:
    envelope = await self._client.get(
        "/v5/position/list", params, op="positions"
    )
except TradingMCPError:
    return []                                                  # ← SEMANTIC LOSS HERE
```

`TradingMCPError` is the parent of `BybitAPIError` (and every other
project exception). The catch is intentionally broad (sub-classes
catch the same way) but the **`return []` is byte-for-byte identical
to "Bybit confirmed zero positions."**

The same pattern repeats across the adapter:

| Method | Conversion site | Sentinel |
|--------|------------------|----------|
| `get_positions` | L181-182 | `return []` |
| `get_wallet_balance` | L1493-1500 | `return _empty_account_info()` (all zeros) |
| `close_all_positions` | L818 | `return []` (after get_positions return []) |
| `close_position` | L439-440 | `return _rejected_order(symbol, side)` |
| `get_last_close` | L760, L853 | `return None` |
| `modify_order` etc. | various | typed sentinels |

**Most of these are reasonable** — a rejected order is correctly
expressed as a sentinel order. But `get_positions → []` and
`get_wallet_balance → _empty_account_info()` are problematic because
the downstream consumer cannot distinguish "API failed" from "truly
empty."

### Step 4 — Watchdog drives phantom closes from empty result

`src/workers/position_watchdog.py:498-520`:

```python
positions = await self.position_service.get_positions()                # L478
sym_list = ",".join(p.symbol for p in positions[:10]) if positions else "none"
log.info(f"WD_TICK | mode={self._watchdog_mode} n={len(positions)} syms=[{sym_list}] | {ctx()}")

# ... thesis reconciler ...

if not positions:
    # Phase 2 (P0-1) — empty Shadow set is the strongest possible
    # signal that everything we still track is a ghost. Run BOTH
    # the fast reconcile and the full close-detect pass: the fast
    # path emits GHOST_RECONCILED for any tracked symbol still in
    # our dicts; the full path drives the coordinator close-record.
    await self._reconcile_with_shadow_fast(positions)
    await self._detect_and_record_closes(set())                        # L505  ← EMPTY SET = ALL VANISHED
    if self.coordinator:
        self.coordinator.cleanup_stale()
    return
```

`_detect_and_record_closes(set())` at L3168-3490 computes:

```python
vanished = self._last_known_symbols - open_symbols                     # L3176
```

When `open_symbols = set()`, `vanished` equals **every symbol the
watchdog was tracking**. Each vanished symbol then goes through the
close-reconstruction logic (L3168-3490) — and lands at L3463 which is
exactly where I3 (`WD_PNL_MISMATCH`) fires for the corrupted reconstructed
data with `pnl=0, ent=ext`.

The comment at L501 ("empty Shadow set is the strongest possible
signal that everything we still track is a ghost") is correct IF the
empty set genuinely reflects exchange state. **When the empty set
comes from an API error**, the comment is wrong.

---

## Evidence — audit window (6 events)

```
22:10:25.102 | ERROR | BYBIT_DEMO_TIMESTAMP_FAIL | code=10002 op=positions msg='...' | wid=w-1778710217525
22:19:26.156 | ERROR | BYBIT_DEMO_TIMESTAMP_FAIL | code=10002 op=positions msg='...' | wid=w-1778710748006
22:40:22.002 | ERROR | BYBIT_DEMO_TIMESTAMP_FAIL | code=10002 op=balance   msg='...' | no_ctx
22:40:22.004 | ERROR | BYBIT_DEMO_TIMESTAMP_FAIL | code=10002 op=positions msg='...' | no_ctx
22:40:27.796 | ERROR | BYBIT_DEMO_TIMESTAMP_FAIL | code=10002 op=positions msg='...' | no_ctx
22:40:33.376 | ERROR | BYBIT_DEMO_TIMESTAMP_FAIL | code=10002 op=balance   msg='...' | no_ctx
```

The 22:40 cluster (4 events in 11 seconds) is correlated in time with
the SEGV at 22:42:34 (~80 seconds later). Pattern: as VM load spiked
(presumably toward the OOM), the request-send latency exceeded
recv_window, repeatedly. **TIMESTAMP_FAIL is therefore a leading
indicator of broader system-pressure problems.**

The audit's I1↔I3 connection is verified: the WD_PNL_MISMATCH events
at 22:37 (ORCAUSDT) and 23:06 (AEROUSDT) BOTH involve positions that
were marked phantom-closed during the TIMESTAMP_FAIL cluster window.
**Fixing I1 eliminates the primary source of corrupted reconstruct
inputs to I3.**

---

## Cluster sweep — same pattern, other endpoints

| Endpoint | Method | Failure mode | Risk |
|----------|--------|--------------|------|
| `/v5/position/list` (positions) | `get_positions → []` | phantom closes via watchdog L503 | **CRITICAL** |
| `/v5/account/wallet-balance` | `get_wallet_balance → _empty_account_info()` | downstream sizing thinks balance is zero; capital tier misreads; trade gates block | **HIGH** (silently halts trading) |
| `/v5/position/closed-pnl` | `get_last_close → None` | watchdog falls back to ticker; tolerable | LOW |
| `/v5/order/create` etc. | `_rejected_order` sentinel | correct behavior — operator sees rejection | OK |

**The wallet path is a separate but cluster-related risk**: 2 of the
6 TIMESTAMP_FAIL events in the audit window were `op=balance`. The
adapter logs `BYBIT_DEMO_WALLET_FAIL` (already structured — see
`bybit_demo_adapter.py:1494-1500`) but returns zero-equity, which can
cause the brain / enforcer / capital tier to misread funding state.
**Phase 2 must propose a fix that scales to both endpoints.**

---

## Recv-window analysis

### Bybit's documented recommendation

Per Bybit V5 docs (https://bybit-exchange.github.io/docs/v5/intro):
- Default recv_window in their SDK examples: **5000ms**
- Maximum allowed: **300000ms** (5 minutes)
- "The longer the recv_window, the higher the risk of replay attack;
  the shorter, the more sensitive to network jitter."

The 5000ms default is balanced for typical co-located clients. A
crypto futures trading VM in a different region (the operator's GCP
VM appears to be remote from Bybit's datacenter) routinely exceeds
this under load.

### Observed latency

The TIMESTAMP_FAIL events sample doesn't directly include latency
measurements (msg field shows `req_timestamp[N],server_timestamp[`
truncated at 120 chars — server timestamp is cut off). However:

- 4 events fired within 11 seconds at 22:40 — coinciding with the
  pre-SEGV memory-pressure window
- After the 22:42 SEGV+restart, NO further TIMESTAMP_FAIL events in
  ~12 hours of live operation
- Conclusion: TIMESTAMP_FAIL is correlated with VM pressure, not
  steady-state network — the steady-state RTT is well under 5000ms

A modest recv_window bump to 10000ms or 15000ms would absorb most
pressure-correlated spikes without compromising security
(replay-attack risk on a 15-second window vs 5-second is functionally
identical).

---

## Shadow parity check

`src/shadow/shadow_adapter.py:150-171` — Shadow `get_positions`:

```python
async def get_positions(self, symbol: str | None = None) -> list[Position]:
    data = await _shadow_get_with_retry(
        self._session,
        f"{self._url}/api/positions",
        log=self._log,
        op="positions",
    )
    if data is None:                                                   # L163
        return []
    ...
```

Shadow's `_shadow_get_with_retry` returns `None` on transport failure.
The adapter converts to `[]`. **Same semantic-loss pattern as
bybit_demo, with the same vulnerability** — but Shadow runs on
localhost (`127.0.0.1:9090`) and has no signing, so the 10002 trigger
doesn't apply.

However, the same `_detect_and_record_closes(set())` phantom-close
path would fire if Shadow's HTTP server hangs or restarts. Operator
should consider whether the I1 fix's architectural change applies
symmetrically to Shadow. Recommendation: yes — the watchdog's "empty
result = all closed" interpretation is the real architectural gap,
shared across both exchanges.

---

## Root cause analysis

### Proximate cause

`recv_window=5000ms` is too tight for the observed latency
distribution under VM load.

### Deeper cause (semantic)

The adapter contract `except TradingMCPError: return []` collapses
three states into one response:

| State | Adapter returns | Downstream interpretation |
|-------|-----------------|----------------------------|
| Truly 0 positions on Bybit | `[]` | "0 open positions" — CORRECT |
| HTTP 401 (auth fail) | `[]` | "0 open positions" — WRONG (auth broken) |
| 10002 TIMESTAMP_FAIL | `[]` | "0 open positions" — WRONG (state unknown) |

The watchdog cannot recover the distinction. Even if I1 bumps
recv_window, the same vulnerability exists for ANY error code that
reaches the adapter's catch.

### Architectural ROOT cause

The system has no notion of "ground truth UNKNOWN" — only "ground
truth confirmed" (success) or "treat as empty" (any failure). For a
trading system, **"unknown state" must be DISTINCT from "empty state"**
because the safe response to unknown is "do nothing / preserve last
known," while the safe response to empty is "close everything." These
are opposite actions.

Fixing this architectural gap is the right root-cause fix.

---

## Fix Options (for operator selection in Phase 2)

### Option A — Bump recv_window (DEFENSIVE, lowest risk)

Change `recv_window: int = 5000` → `recv_window: int = 15000` (or
20000) at `bybit_demo_client.py:222`. Optionally make it configurable
via `Settings.bybit_demo.recv_window_ms`.

- **Pros:** One-line change. Eliminates the 10002 trigger for typical
  pressure events. No behavioural change beyond fewer error spikes.
- **Cons:** Band-aid. Doesn't address the SEMANTIC root cause. Future
  pressure events that exceed 15-20s will hit the same bug. Doesn't
  cover other endpoints with the same `return []` pattern.
- **Effort:** 1 hour code + 1 test + 6h soak.

### Option B — Retry on 10002 with fresh timestamp (TARGETED)

Modify `bybit_demo_client.py:430-431` to ALSO catch `BybitAPIError`
when `ret_code == 10002` AND attempt < max. The retry loop already
re-signs with a fresh timestamp at the top of each iteration (L344),
so the retry naturally re-signs.

Add new structured emission `BYBIT_DEMO_TIMESTAMP_RETRY` per the
prompt's Rule 6.

- **Pros:** Addresses the specific symptom. Preserves recv_window=5000.
  No watchdog change needed.
- **Cons:** When retries are exhausted, same propagation as today.
  Doesn't address the SEMANTIC root cause.
- **Effort:** 2 hours code + 2 tests + 6h soak.

### Option C — Distinguish "unknown" from "empty" at adapter (ARCHITECTURAL — ADDRESSES ROOT)

Change adapter contract for `get_positions` (and analogously
`get_wallet_balance`) to raise a typed exception OR return a
discriminated union. The watchdog and other consumers check for the
"unknown" state and preserve prior position state.

Concrete shape (the discriminated-union flavour, cleaner than typed
exception propagation given the existing `never raises` contract):

```python
# src/bybit_demo/bybit_demo_adapter.py
class PositionsQueryResult:
    """Discriminated result for get_positions.
    KNOWN = exchange-confirmed open set; UNKNOWN = API error, state unverified.
    """
    KNOWN: bool  # True if the list is exchange-confirmed; False if API error
    positions: list[Position]  # populated only when KNOWN=True

async def get_positions(self, ...) -> PositionsQueryResult:
    try:
        envelope = await self._client.get(...)
    except TradingMCPError as e:
        self._log.warning(
            f"BYBIT_DEMO_POSITIONS_UNKNOWN_STATE | err={str(e)[:120]} "
            f"| {ctx()}"
        )
        return PositionsQueryResult(KNOWN=False, positions=[])
    ...
    return PositionsQueryResult(KNOWN=True, positions=positions)

# src/workers/position_watchdog.py:498-520 (the phantom-close site)
result = await self.position_service.get_positions_with_state()  # new method
if not result.KNOWN:
    log.warning(
        f"WD_GROUND_TRUTH_UNKNOWN | preserving prior state n={len(self._last_known_symbols)} | {ctx()}"
    )
    return  # Do NOT close anything. Skip _detect_and_record_closes.
positions = result.positions
# ... existing flow continues ...
```

Plus a structured `BYBIT_DEMO_TIMESTAMP_UNKNOWN_STATE` event per the
prompt's Rule 6.

- **Pros:** Addresses the SEMANTIC ROOT. Eliminates phantom closes for
  EVERY error code, not just 10002. Scales naturally to other
  endpoints (wallet, last_close). Preserves "aggressive opportunity
  exploitation" (positions never disappear silently). Operator gets
  explicit visibility into "ground truth unknown" via log.
- **Cons:** Larger surface: adapter contract change, watchdog change,
  callers of `get_positions` need to handle the new type (or use a
  backwards-compat shim). More tests required.
- **Effort:** 1 day code + 4 tests (unit + integration) + 24h soak.

### Option D — Combination A+B+C (RECOMMENDED, defense-in-depth)

1. **A:** Bump `recv_window` from 5000 → 10000 ms (modest, still tight).
2. **B:** Add retry-on-10002 in the client's retry loop with re-sign.
3. **C:** Adapter returns a `PositionsQueryResult` (or sentinel) when
   ALL retries exhausted; watchdog preserves prior state.

This addresses the root at three layers:
- Network jitter handled by larger window (A)
- Transient hits handled by retry (B)
- Ground-truth-unknown semantics preserved when nothing works (C)

The new emissions: `BYBIT_DEMO_TIMESTAMP_RETRY` (B-side per-attempt),
`BYBIT_DEMO_TIMESTAMP_UNKNOWN_STATE` (C-side after retries exhausted),
`WD_GROUND_TRUTH_UNKNOWN` (watchdog-side state preservation).

- **Pros:** Eliminates phantom closes at every layer. Provides the
  operator's required `BYBIT_DEMO_TIMESTAMP_RETRY` and
  `BYBIT_DEMO_TIMESTAMP_UNKNOWN_STATE` emissions (Rule 6). Aligns
  with operator's "aggressive exploitation" philosophy.
- **Cons:** Largest surface. More work + careful test coverage.
- **Effort:** 1.5-2 days code + 6-8 tests + 6h soak.

---

## Recommendation

**Option D (combination)** is the operator-aligned choice because:

1. The prompt's Rule 3 forbids band-aid fixes. Option A alone is a
   band-aid; D includes it but goes further.
2. The prompt's Rule 6 prescribes both `BYBIT_DEMO_TIMESTAMP_RETRY`
   AND `BYBIT_DEMO_TIMESTAMP_UNKNOWN_STATE` emissions — neither A nor
   B alone satisfies the unknown-state emission.
3. The architectural ROOT (semantic-loss at adapter) is only
   addressed by C, but C alone leaves the recv_window default which
   means UNKNOWN_STATE fires too often. D mitigates both.
4. The wallet-balance endpoint has the same vulnerability; D's C
   component naturally extends to it.
5. The aggressive-exploitation philosophy demands that positions
   never silently disappear. C is the only option that GUARANTEES
   this; A and B reduce frequency but don't eliminate.

---

## Decision needed from operator (Phase 2 gate)

Phase 2 will write a 1-page operator-facing report summarizing this
investigation and asking the operator to pick A / B / C / D. The
report awaits operator review before any code change to
`bybit_demo_client.py`, `bybit_demo_adapter.py`, or
`position_watchdog.py`.

**No code touched.** Phase 1 deliverable complete.
