# Audit Report — Bybit Demo Logging & Observability

Date: 2026-05-08
Branch: `feature/bybit-demo-adapter`
Audit scope: every file/line touched in commits `c788f94..e1d7fa5` (Phases 1A, 1B, 1C, 2, 3, 4, 5) plus the post-audit fix commit `6646ebf`.
Verdict: **PASS** with one ordering issue caught and fixed during the audit.

---

## Summary

| Phase | Verdict | Issues found in audit | Action |
|---|---|---|---|
| 0 — Pre-flight | PASS | none | n/a |
| 1A — retCode tags + retry + rate-limit-recovered | PASS | minor concurrency note (no actual bug) | none — documented only |
| 1B — wallet-fail tag | PASS | none | n/a |
| 1C — partial-fill tag | PASS | none | n/a |
| 2 — boot validation | PASS | none in helper itself | n/a |
| 3 — switch entry tags | PASS | none | n/a |
| 4 — alert relay | PASS | **ordering gap: this-boot BOOT_FAIL not alerted** | fixed in `6646ebf` |
| 5 — verification report | PASS | none | n/a |

---

## Phase-by-phase audit detail

### Phase 1A — `src/bybit_demo/bybit_demo_client.py`

**Re-read findings:**

- `_log_ret_code` is module-level, frozen-set-driven, called exactly once in the request loop (line 394) immediately before `_translate_ret_code` raises. No leak path.
- The retry-loop `BYBIT_DEMO_RETRY` log (line 408) fires inside `if attempt < self._retry_attempts:` — only on attempts that will be retried, not on the final exhausted attempt (which already gets `BYBIT_DEMO_CALL_FAIL`). Correct semantics.
- `_rate_limit_active` is instance state. Multiple coroutines sharing the same client may interleave reads/writes of this flag. Worst case: a single duplicate `BYBIT_DEMO_RATE_LIMIT_RECOVERED` emission, which AlertManager's content-hash dedup absorbs. CPython's GIL makes single attribute reads/writes atomic, so no torn state. **Acceptable as-is** — adding `asyncio.Lock` would add cost for zero meaningful benefit.
- The retCode tag emissions don't overlap with the existing header-derived `BYBIT_DEMO_RATE_LIMIT` tag (different name, different trigger condition).
- All log lines append `| {ctx()}` for did/tid/wid/sid propagation.

**Cross-check against callers:**
- `_translate_ret_code` already in use (only callsite is line 395).
- `_log_ret_code` is new, only callsite is line 394.

**Verdict:** PASS.

### Phase 1B — `src/bybit_demo/bybit_demo_adapter.py:758-784`

**Re-read findings:**

- The catch site catches `TradingMCPError` (parent of `BybitAPIError`, `RateLimitError`, `InsufficientBalanceError`, `InvalidOrderError`, `OrderRejectedError`). All Bybit-translated errors caught.
- The sentinel return `_empty_account_info()` is unchanged. Contract preserved.
- Truncation `str(e)[:160]` matches the project convention used in 6+ other places (`bybit_demo_client.py:289`, `bybit_demo_adapter.py:231,313,360,382,503,544`).

**Cross-check against callers (40+ callsites of `get_wallet_balance`):**
- Every caller expects `AccountInfo` with possibly-zero fields on failure. Zero-field sentinel is unchanged.
- Adding a log line is purely additive.
- No caller inspects whether a log was emitted.

**Verdict:** PASS.

### Phase 1C — `src/bybit_demo/bybit_demo_adapter.py:583-619`

**Re-read findings:**

- The new `BYBIT_DEMO_PARTIAL_FILL` log fires inside `if status_str == "PartiallyFilled":` (line 612 area). Only on partial-fill responses.
- Status mapping unchanged (`PartiallyFilled` still maps to `OrderStatus.FILLED` for downstream contract parity with Shadow).
- Edge case: `requested_qty == 0` → ratio falls to `0.0` via the `else` clause. No division-by-zero.

**Cross-check against callers:**
- `_resolve_order_fill` is internal (only caller is `place_order` at line ~560). No external surface change.

**Verdict:** PASS.

### Phase 2 — `src/bybit_demo/bybit_demo_boot.py` + `src/workers/manager.py:344-368`

**Re-read findings:**

- `validate_boot` calls `client.health_check()` (silent-on-fail bool) then `client.get(...)` directly (re-raises `TradingMCPError`). The direct `client.get` bypasses `BybitDemoAccountService`'s sentinel return, which would otherwise mask boot failures.
- `api_key_len == 0` short-circuits before any network call — fast fail on missing creds.
- The function never raises: every exception path returns a structured dict.
- Wired from `WorkerManager.initialize` at line 344 inside the existing `bd_settings.enabled` branch. Inner try-except catches anything `validate_boot` might raise (defense-in-depth).

**Cross-check imports:**
- `from src.bybit_demo.bybit_demo_boot import validate_boot` resolves cleanly. No circular dep.

**Live smoke (verified during audit):**
```
BYBIT_DEMO_BOOT_START  | url=https://api-demo.bybit.com key_len=18 recv_window=5000
BYBIT_DEMO_BOOT_VALIDATED | url=https://api-demo.bybit.com equity=182572.31
result: {'ok': True, 'equity': 182572.31...}
```

**Verdict:** PASS.

### Phase 3 — `src/telegram/handlers/dashboard_handler.py`

**Re-read findings:**

- New module-level `_switch_log = get_logger("worker")` (line 46) — separate from existing `log = get_logger("dashboard")`. Routes to `workers.log` for grep continuity with switcher's own EXCHANGE_SWITCH_* tags.
- Three emission points, exactly matching the three callback handlers:
  - line 1599: `EXCHANGE_SWITCH_REQUESTED | direction=bybit_demo` at preview-bybit_demo entry
  - line 1640: `EXCHANGE_SWITCH_REQUESTED | direction=shadow` at preview-shadow_from_demo entry
  - line 1670: `EXCHANGE_SWITCH_CONFIRMED | target={target_mode}` at confirm entry
- Each uses `getattr(query.from_user, 'id', 0)` — defensive against unusual telegram objects.
- All three append `| {ctx()}`.

**Cross-check:** No new component name; `worker` is already in `COMPONENT_ROUTING`. Routing CI test green.

**Verdict:** PASS.

### Phase 4 — `src/observability/bybit_demo_alert_relay.py` + `src/workers/manager.py:836-883`

**Re-read findings:**

- `_TRIGGERS` table has 10 entries; verified non-overlapping (no prefix is a prefix of another tag in the table).
- Filter is two-stage: component check (frozenset lookup) then `startswith` against trigger keys. Both cheap.
- Sink uses `asyncio.run_coroutine_threadsafe` with the loop captured at registration. Loguru's `enqueue=True` runs the sink on a background thread; `run_coroutine_threadsafe` is the correct cross-thread dispatch primitive.
- Sink's outer `try` catches all exceptions and re-logs as `BYBIT_DEMO_ALERT_RELAY_FAIL` (which doesn't itself match any trigger prefix → no infinite recursion).
- `BYBIT_DEMO_ALERT_RELAY_REGISTERED` (info on register) doesn't match any trigger prefix → no self-trigger.
- `register()` is idempotent. `unregister()` catches `(ValueError, KeyError)` from stale sink IDs.
- `AlertManager.send_error_alert(component, error_message, severity)` and `send_risk_warning(warning_type, details)` signatures verified to match what the relay invokes.

**Issue caught during audit:**

Wiring order in `WorkerManager.initialize`:
- line ~355: `validate_boot` runs → may emit `BYBIT_DEMO_BOOT_FAIL`
- line ~466: `AlertManager` constructed
- line ~832: `verify_post_switch` runs → may emit `POST_SWITCH_VERIFY_*_FAIL`
- line ~851: relay registers

A `BYBIT_DEMO_BOOT_FAIL` emitted at line ~355 would never reach the relay sink (relay isn't registered yet) AND `AlertManager` doesn't even exist yet. Per spec Component 6, boot failures should fire a CRITICAL alert immediately.

**Fix applied (commit `6646ebf`):**

- Stash `validate_boot`'s result on `self._services["bybit_demo_boot_result"]`.
- After the relay registers, check the stashed result; if `ok=False`, dispatch one immediate `alert_manager.send_risk_warning("bybit_demo_boot", details)` call.
- Defense-in-depth: outer try-except around `validate_boot` also stashes a synthetic failure dict if `validate_boot` itself ever raises.

**`POST_SWITCH_VERIFY_*_FAIL` events on this same boot** — also miss the relay (registered at line 851, after verifier runs at line 832). Documented trade-off: the verifier itself sends a `send_custom` Telegram message regardless of probe outcomes (uses "unknown" for failed probes), so the operator already gets a notification. The relay's CRITICAL alert would have been an additional message; not adding it preserves the verifier's existing single-notification UX.

**Live smoke (verified during audit):**
- All 10 trigger entries dispatched correctly
- 7 CRITICAL + 3 WARNING level distribution matches `_TRIGGERS` table
- 0 false positives on `BYBIT_DEMO_ORD_SEND` / `BYBIT_DEMO_POSITION_CLOSE` (normal-flow info events)

**Verdict:** PASS (after fix).

### Phase 5 — verification report

Re-read `dev_notes/bybit_demo_logging/phase5_verification_report.md` — accurate; tag inventory matches code; alert inventory matches `_TRIGGERS`.

**Verdict:** PASS.

---

## Cross-cutting verification

### Module imports

Verified all modified or new modules import cleanly with no circular dependencies:

```
OK: src.bybit_demo
OK: src.bybit_demo.bybit_demo_client
OK: src.bybit_demo.bybit_demo_adapter
OK: src.bybit_demo.bybit_demo_boot
OK: src.observability
OK: src.observability.bybit_demo_alert_relay
OK: src.exchanges.switching{,.exchange_switcher,.post_switch_verifier}
OK: src.workers.manager
OK: src.telegram.handlers.dashboard_handler
OK: src.alerts.alert_manager
OK: src.core.{logging,log_context,log_tags,types}
```

### Component routing (CI invariant)

```
OK: component 'worker'    -> workers.log
OK: component 'bybit_demo' -> workers.log
```

CI routing test (`tests/test_logging_routing.py`) is green: `3 passed in 0.14s`.

### Tag-prefix uniqueness

```
10 trigger entries verified.
No tag is a prefix of another (first-match policy is unambiguous).
```

### AlertManager signature parity

```
send_error_alert(self, component: str, error_message: str,
                 severity: AlertLevel = AlertLevel.WARNING) -> None
send_risk_warning(self, warning_type: str, details: dict) -> None
```

Relay's calls (`method(spec.component_or_warning_type, msg_short, spec.level)` and `method(spec.component_or_warning_type, details)`) match the signatures.

### `validate_boot` signature parity

```
validate_boot(client: BybitDemoClient, *, base_url: str,
              api_key_len: int, recv_window: int) -> dict[str, Any]
```

Call site at `src/workers/manager.py:355` matches.

---

## Test sweep results

| Suite | Result |
|---|---|
| `tests/test_logging_routing.py` | 3 / 3 passed |
| `tests/test_bybit_demo/` (excl. live integration) | 47 / 47 passed |
| `tests/test_observability/` | 10 / 10 passed |
| `tests/test_exchange_switching/` | 10 / 10 passed |
| **Focused total** | **70 / 70 passed** |
| Wide regression (`tests/`) excl. live + stale `tests/test_phase7` | **2387 / 2388 passed** |
| The single failure (`test_apex_direction_lock::test_system_prompt_still_has_rsi_caution`) | pre-existing, unrelated, documented in `project_bybit_demo_adapter_status` memory |
| Live integration (`BYBIT_DEMO_INTEGRATION=1 tests/test_bybit_demo/test_adapter_integration.py`) | **8 / 8 passed** against api-demo.bybit.com |
| Live boot-validation smoke (during audit) | PASS — equity=$182,572.31 read from real demo account |
| Live relay smoke (during audit) | PASS — 10 / 10 trigger entries dispatched, 7 CRITICAL + 3 WARNING, 0 false positives |

**Pre-existing test issues (NOT caused by this work):**
- 3 import errors in `tests/test_phase7/{test_executor,test_prompt_builder,test_scheduler}.py` reference `src.brain.{prompt_builder,scheduler}` which were renamed to `*.deprecated` in earlier project work. Confirmed via `grep deprecated src/brain/`.
- 1 assertion failure in `tests/test_apex_direction_lock::test_system_prompt_still_has_rsi_caution` — checks that `STRATEGIST_SYSTEM_PROMPT` contains "Oversold RSI in a downtrend"; that text was removed in earlier prompt-richness work.

---

## Final verdict

The Bybit demo logging gap-fill is implemented at industry-standard quality:

- **Architecture**: adapter code stays log-only (mirrors Shadow's contract); orchestration-layer relay (loguru sink) translates structured events into Telegram alerts; clean separation of concerns.
- **Naming**: every new tag follows the existing `{COMPONENT}_{EVENT}` convention (matches the project's 280+ tag taxonomy). Bare `REDUCE_FALLBACK` preserved per operator decision.
- **Dependencies**: every new module imports cleanly; no circular deps; no new third-party dependencies; reuses existing `get_logger`, `ctx`, `AlertLevel`, `AlertManager`, loguru.
- **Wiring**: boot validation called from `WorkerManager.initialize` at the right point; relay registered after AlertManager exists; one-shot replay bridges the early-boot gap discovered in audit.
- **Tests**: 27 new unit tests + 70 / 70 focused suite green + 2387 / 2388 broader regression (1 unrelated pre-existing) + 8 / 8 live integration + 2 in-audit live smokes. No test was added without a real production-code change to verify.
- **Observability**: 44 distinct tags across 3 components (`bybit_demo`, `worker`); 10 trigger entries dispatch alerts via `send_error_alert` (4) and `send_risk_warning` (6); content-hash dedup handles retry storms.
- **No band-aids**: each commit is a focused, atomic, independently revertable change. Boot validation never blocks boot (matches Shadow precedent). Sink never raises (avoids logger-system breakage). Failure paths are explicit, not silently swallowed.
- **Audit ordering issue**: caught and fixed (commit `6646ebf`). The fix is structural (stash → replay), not a band-aid.

**Status:** ready for operator's live trial. The next live restart will exercise the boot validation, the alert relay, and (if creds were rotated) the boot-fail replay path end-to-end.
