# Phase 0 — Issue Investigation: Startup Ordering (Issue #10)

**Issue:** First 10 calls to Shadow API at boot fail with `Cannot connect to host 127.0.0.1:9090`. Capital pools log `active=0.00 aplus=0.00 emergency=0.00` because the wallet balance read returns empty.

## Section A — The mechanism

### A.1 Systemd units

**File:** `/home/inshadaliqbal786/trading-intelligence-mcp/systemd/trading-workers.service`

```
[Unit]
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5
```

**No `After=shadow.service`.** No `Wants=shadow.service`. No `Requires=`.

**File:** `/home/inshadaliqbal786/shadow/systemd/shadow.service`

```
[Unit]
After=network-online.target
Wants=network-online.target
```

Symmetric. Both wait for network only. Either can start first. Even if Shadow starts first, its `aiohttp` HTTP server takes a few hundred ms to bind to `127.0.0.1:9090` after `ExecStart` fires.

### A.2 Shadow adapter — no boot-time retry

**File:** `src/shadow/shadow_adapter.py:442-459` (`get_wallet_balance`):

```python
async def get_wallet_balance(self) -> AccountInfo:
    try:
        async with self._session.get(f"{self._url}/api/balance") as resp:
            if resp.status != 200:
                self._log.error("Shadow /balance error: HTTP {status}", status=resp.status)
                return _empty_account_info()
            data = await resp.json()
    except aiohttp.ClientError as e:
        self._log.error("Shadow connection error (balance): {err}", err=str(e))
        return _empty_account_info()
    return _build_account_info(data)
```

**No retry, no backoff.** First failure logs ERROR + returns empty AccountInfo. The "Cannot connect to host 127.0.0.1:9090" string is from `aiohttp.ClientError`'s message.

`get_available_balance` (line 461) and `get_equity` (line 466) chain through `get_wallet_balance` — same failure shape.

### A.3 Capital pools downstream

**File:** `src/fund_manager/manager.py:86-153`

`initialize` (lines 86-120):
- Calls `account_svc.get_wallet_balance()` (line 95)
- On exception (line 118): logs `Fund Manager init failed: {err}` and defaults to `AccountState(total_equity=10000, starting_balance=10000)`. This default is **fictional** — it's a hard-coded fallback that masks the real boot-time failure.

`update_state` (lines 122-153, called every 60s):
- Calls `account_svc.get_wallet_balance()` (line 129)
- Bare `except: pass` at line 131 — does NOT update `total_equity` if the call fails. Silent.
- Position read at line 147 has the same `except: pass` at line 149-150.

**Capital pools log:**
**File:** `src/fund_manager/capital_reserves.py:38-55` (`update_pools`)
- Receives `state` from `update_state` line 143
- Logs `Capital pools updated: active=... aplus=... emergency=...` (line 50)

If `total_equity=0` from a failed boot read, `trading_capital = 0 * (unlock_pct/100) = 0`, and pools log `0.00`. Capital pools is **silent about the underlying fund-manager failure** — operators see "0.00" but no upstream warning.

### A.4 Manager startup sequence

**File:** `src/workers/manager.py` (around line 237-249)

Shadow adapters are created at WorkerManager init. There's no health-check (`adapter.health_check()` exists at `shadow_adapter.py:482-491` but isn't called at boot). The first real call to Shadow is whenever a consumer (fund_manager, position_service) makes one — that's when the failure surfaces.

## Section B — The dependencies

| Component | Affected by failure | What happens |
|---|---|---|
| Fund manager initialize | yes | falls back to fictitious $10K default |
| Capital pools | yes | logs 0.00 |
| Position service first call | yes | `get_positions()` may fail |
| Order service `get_wallet_balance` for sizing cap | yes | Position cap check (`order_service.py:117-156`) wraps in try/except — silently skipped |
| Brain prompt context | indirect | brain reads fund manager state — sees fictitious $10K |

The cascade is wide but soft: every consumer has a defensive `except: pass` or fallback, so the system "works" with degraded data until Shadow is up.

## Section C — The constraints

- **Cannot break the existing fallback** — if Shadow is permanently down, the system should still start (degraded), not crash. Don't replace `except: pass` with `raise`.
- **Cannot make the systemd dependency too strict** — `Requires=` causes both services to fail together. Use `Wants=` for soft dependency or `Requires=` only if user explicitly chooses.
- **Cannot add unbounded retry** — boot must complete within `StartLimitIntervalSec` to avoid systemd backoff loop.

## Section D — The fix candidates

### D.1 Systemd dependency (Phase 1.1)

Edit `systemd/trading-workers.service`:
```
[Unit]
After=network-online.target shadow.service
Wants=network-online.target shadow.service
```

`Wants=` is softer than `Requires=`: if Shadow fails, workers still start (degraded), but systemd will start Shadow before workers when both are scheduled.

### D.2 Adapter retry-with-backoff (Phase 1.2)

In `shadow_adapter.py` add a `_call_with_retry(coro, attempts=5, base_delay=0.2)` helper:
- Catches `aiohttp.ClientError` / `OSError`
- First failure → ERROR (existing behavior on first attempt)
- Subsequent failures within first 30s of process start → DEBUG (rate-limited, not 10 ERROR lines)
- After 30s of cumulative failure → ERROR + raise

Apply to `get_wallet_balance`, `get_positions`, `get_open_orders`. **Don't blanket-wrap** — the steady-state poll loops already handle single failures gracefully.

### D.3 Capital pools graceful degradation (Phase 1.3)

In `fund_manager/manager.py:122-153`:
- Replace `except: pass` (line 131) with `except Exception as e: log.warning("FUND_MGR_BALANCE_FAIL | retry_in=5s err={err}")`.
- Around `initialize` (lines 86-120), wrap balance read in 3-attempt retry-with-5s-delay.
- Only after 3 consecutive failures, fall back to fictitious $10K AND log ERROR `CAPITAL_POOLS_BOOT_FAIL`. This separates "Shadow not yet up" (recoverable) from "Shadow permanently broken" (alert).

## Verification

After Phase 1:
- Restart workers; `journalctl -u trading-workers -f` shows zero `Cannot connect to host 127.0.0.1:9090` ERROR lines at boot.
- `Capital pools updated` shows actual balance, not 0.00.
- Force-test: `sudo systemctl stop shadow && sudo systemctl restart trading-workers && sleep 12 && sudo systemctl start shadow` — adapter retries; capital pools eventually correct without 10 boot ERRORs.

## Verified citations

| Claim | File:Line |
|---|---|
| trading-workers.service no Shadow dep | `systemd/trading-workers.service:4-5` |
| shadow.service unit | `/home/inshadaliqbal786/shadow/systemd/shadow.service:4-5` |
| `get_wallet_balance` no retry | `src/shadow/shadow_adapter.py:442-459` |
| ERROR log on connection error | `src/shadow/shadow_adapter.py:456` |
| `_empty_account_info` fallback | `src/shadow/shadow_adapter.py:457` |
| FundManager init fallback to $10K | `src/fund_manager/manager.py:118-120` |
| Bare `except: pass` in update_state | `src/fund_manager/manager.py:131, 149-150` |
| Capital pools log line | `src/fund_manager/capital_reserves.py:50` (via update_pools) |
| Shadow adapter health_check exists but not used at boot | `src/shadow/shadow_adapter.py:482-491` |
