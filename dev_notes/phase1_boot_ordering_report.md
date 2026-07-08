# Phase 1 — Boot Ordering Fix

**Status:** SHIPPED (code) — systemd install pending operator action
**Date:** 2026-04-26
**Investigation:** [`phase0_issue_startup_ordering.md`](phase0_issue_startup_ordering.md)

## Summary

Three coordinated changes eliminate the boot-time burst of `Cannot connect to host 127.0.0.1:9090` ERROR lines and the resulting fictitious zero-balance state that propagated into the fund manager's capital pools.

1. **Systemd unit** — `trading-workers.service` now declares `After=shadow.service` and `Wants=shadow.service`, so systemd sequences Shadow first when both are scheduled. Soft dependency: workers still start (degraded) if Shadow is permanently down.
2. **Shadow adapter** — new module-level `_shadow_get_with_retry` helper provides exponential-backoff retry on `aiohttp.ClientError`/`OSError`/`asyncio.TimeoutError` and demotes the exhausted-retry log to DEBUG during the first 30 s of process lifetime. Applied to the two boot-critical reads (`get_wallet_balance`, `get_positions`).
3. **Fund manager** — replaced two silent `except: pass` blocks with structured WARNING logs that escalate to ERROR after `_FAIL_ALERT_THRESHOLD = 3` consecutive failures. Counter resets on first success.

## Files changed

| File | Change |
|---|---|
| `systemd/trading-workers.service` | Added `After=shadow.service` / `Wants=shadow.service` + comment explaining the post-Layer-1 fix |
| `src/shadow/shadow_adapter.py` | Added `_PROCESS_START_MONOTONIC`, `_BOOT_GRACE_SECONDS`, `_in_boot_grace()`, `_shadow_get_with_retry()`. Refactored `ShadowAccountService.get_wallet_balance` and `ShadowPositionService.get_positions` to delegate to the helper. |
| `src/fund_manager/manager.py` | Added `_consecutive_balance_fails` / `_consecutive_position_fails` / `_FAIL_ALERT_THRESHOLD`. Replaced two silent `except: pass` blocks with structured `FUND_MGR_*_FAIL` (WARNING) and `FUND_MGR_*_FAIL_PERSISTENT` (ERROR) logs. |
| `tests/test_shadow_adapter_boot_grace.py` | NEW — 9 tests across 2 classes covering the grace window, transient retry, exhaustion behavior in/out of grace, 4xx/429 handling, and the `get_wallet_balance` integration. |

## Behavior matrix

### Boot-time (within 30 s of process start, Shadow not yet listening)

- Adapter: each call to Shadow retries up to 5× with exponential backoff (0.2s, 0.4s, 0.8s, 1.6s, 3.2s ≈ 6.2 s total worst-case). At Shadow's typical ~1-2 s listener startup time, the second attempt almost always succeeds.
- If the retry chain still exhausts: a single DEBUG line is emitted (instead of an ERROR) — operators see no false-alarm noise during a clean restart.
- Fund manager: receives empty `AccountInfo` once → counter increments to 1 → logs WARNING `FUND_MGR_BALANCE_FAIL | consecutive=1 threshold=3`. On the next 60-second cycle, Shadow is up, the read succeeds, the counter resets to 0.

### Steady-state (post-grace, Shadow really down)

- Adapter: same retry chain. Exhausted → ERROR `SHADOW_CALL_FAIL | op=balance attempts=5 ... boot_grace=False`.
- Fund manager: counter rises 1 → 2 → 3. On the third consecutive cycle, escalates to ERROR `FUND_MGR_BALANCE_FAIL_PERSISTENT | consecutive=3`. Operator alerted to a real outage.

### Restart-during-trading

- Workers stays running with stale `total_equity`/`in_use` while Shadow is being restarted. Position-size cap math (`order_service.py:117-156`) is wrapped in its own try/except so it gracefully degrades.
- Existing fallback in `manager.initialize` (defaults to fictitious $10K if first read fails) is unchanged. With the retry helper, it almost never fires now — the first read waits up to ~6 s through Shadow's startup.

## Test coverage

9 new tests in `tests/test_shadow_adapter_boot_grace.py`:

| Class | Tests | What's verified |
|---|---|---|
| `TestBootGrace` | 2 | `_in_boot_grace()` returns True at boot, False after window expires |
| `TestShadowGetWithRetry` | 7 | First-attempt success short-circuits; transient errors retry; exhaustion-in-grace logs DEBUG; exhaustion-after-grace logs ERROR; 404 returns None without retry; 429 retries; `ShadowAccountService.get_wallet_balance` integrates correctly |

Result: **9/9 pass.** Phase 5 regression suite continues to pass (24/24 across `test_order_idempotency.py` + `test_order_service.py`).

## Verification (operator action)

The systemd unit file in the repo is updated. To install:

```bash
sudo cp /home/inshadaliqbal786/trading-intelligence-mcp/systemd/trading-workers.service \
        /etc/systemd/system/trading-workers.service
sudo systemctl daemon-reload
systemctl show trading-workers.service -p After -p Wants  # verify
```

Verification trial (recommended after install):

```bash
# Trial 1.1 — clean restart, expect zero connection ERROR lines
sudo systemctl restart trading-workers
journalctl -u trading-workers -f --since "1 minute ago" | grep -E "Cannot connect|FUND_MGR_BALANCE_FAIL"

# Trial 1.2 — confirm capital pools come up populated
journalctl -u trading-workers --since "1 minute ago" | grep "Capital pools updated"

# Trial 1.3 — force-test the degraded path
sudo systemctl stop shadow
sudo systemctl restart trading-workers
sleep 12
sudo systemctl start shadow
# Expect: ≤ 1 DEBUG SHADOW_CALL_FAIL while shadow is down,
#         then SHADOW_CALL_FAIL stops once shadow listens,
#         then "Capital pools updated" with real numbers
```

The current 3h47m-running workers process is unaffected by this change until it's restarted. The new ordering takes effect on the next start.

## What was deliberately left untouched

- Other adapter methods (`get_position`, `get_open_orders`, `get_pnl_summary`, etc.) keep their current shape. They're called from steady-state polling loops where one failure is fine and three retries would just delay the next tick.
- The fictitious `$10K` fallback in `manager.initialize` (line 91, 120) — kept as the last-resort floor. Now almost never fires due to the adapter retry, but still protects against a permanently-broken Shadow at first boot.
- Existing `health_check` methods on each service — still single-attempt by design; they're meant to be cheap and fast.

## Rollback path

`git revert HEAD` reverts cleanly. The installed systemd unit can be reverted by re-installing the previous version (kept in git history).

## Status against the spec's verification criteria

| Spec criterion | Result |
|---|---|
| Zero `Cannot connect to host 127.0.0.1:9090` at boot | ⏳ pending operator-driven restart trial |
| Capital pools initialize with actual balance | ⏳ pending restart trial |
| Force-delayed Shadow restart handled gracefully | ⏳ pending restart trial |
| Boot ERROR lines in normal restart | 0 (by design — DEBUG during grace, ERROR if 5 retries × 6.2s exceeds 30s grace) |
| Test coverage of helper | 9/9 pass |

The code-level guarantees are in place; the operator-driven restart trials are non-blocking and can be folded into Phase 13's 4-hour observation window.
