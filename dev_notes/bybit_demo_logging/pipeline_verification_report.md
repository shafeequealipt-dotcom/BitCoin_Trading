# Pipeline Verification Report — Bybit Demo Logging & Observability

Date: 2026-05-08
Branch: `feature/bybit-demo-adapter`
Scope: end-to-end pipeline check through the real project — DI wiring, data flow, runtime verification against api-demo.bybit.com, integration with real `AlertManager`, naming, dependencies.
Verdict: **PASS** with two issues caught and fixed during this verification (commits `6646ebf` and `fb74d4a`).

---

## Issues caught by the pipeline pass + fixes

### Issue P1 — Boot-fail not alerted on the very same boot (caught earlier in audit)

**Symptom:** `validate_boot` runs at `WorkerManager.initialize` line 358; `AlertManager` only exists from line 474 onward; relay only registers at line 859. A `BYBIT_DEMO_BOOT_FAIL` emitted at boot logged to workers.log but produced no Telegram alert.

**Fix (commit `6646ebf`):** stash `validate_boot`'s result on `self._services["bybit_demo_boot_result"]`; immediately after the relay registers, if the stashed result is `{"ok": False}`, dispatch one direct `alert_manager.send_risk_warning("bybit_demo_boot", details)` call. Subsequent runtime BOOT_FAIL events flow through the regular relay sink as designed.

### Issue P2 — Runtime auth failures missing the relay (caught in this pipeline pass)

**Symptom found via real network:** running `validate_boot` against api-demo.bybit.com with random hex creds produced `HTTP 401` with no JSON body. The retCode-specific `BYBIT_DEMO_AUTH_FAIL` only fires inside the 2xx-with-non-zero-retCode branch (at `_log_ret_code`). On HTTP 401, only `BYBIT_DEMO_HTTP_FAIL` fired — and `BYBIT_DEMO_HTTP_FAIL` is NOT in the relay's trigger table. Result: a real-world key-revocation during operation would log to workers.log but produce zero Telegram alert.

Boot-time was indirectly covered (BOOT_FAIL replay at the wiring level). Ongoing-operation key revocation slipped through.

**Fix (commit `fb74d4a`):** at the source, when `resp.status in (401, 403)`, emit `BYBIT_DEMO_AUTH_FAIL | code=http_401 ...` in addition to `BYBIT_DEMO_HTTP_FAIL`. The relay's existing AUTH_FAIL trigger now catches both retCode-bearing and HTTP-layer auth failures uniformly. Generic 400-class errors (validation, bad request) deliberately do NOT emit AUTH_FAIL — that would produce false-positive CRITICAL alerts on routine bad-input cases.

Live-verified end-to-end after fix.

---

## A. Tag inventory cross-check (static)

Source-of-truth grep across `src/`:

| Tag family | Code-emitted (unique) | Documented in audit report |
|---|---|---|
| `BYBIT_DEMO_*` (excl. ALERT_RELAY) | 25 | 25 ✓ |
| `BYBIT_DEMO_ALERT_RELAY_*` | 2 | 2 ✓ |
| Bare `REDUCE_FALLBACK` | 1 | 1 ✓ |
| `EXCHANGE_SWITCH_*` | 16 | 16 ✓ |
| `POST_SWITCH_*` | 8 | 8 ✓ |
| **Total unique structured tags** | **52** | (audit summary line said 44 — corrected here) |

Every emitted tag is documented; every documented tag is emitted. Earlier "44" summary in `phase5_verification_report.md` is a count typo (the table itself lists 52). Content correctness was unaffected.

## B. DI / wiring chronology in `WorkerManager.initialize`

| Line | Step | New service-key |
|---|---|---|
| 92 | Transformer constructed | `transformer` |
| 331 | `BybitDemoClient` constructed | (local) |
| 341-343 | `BybitDemo{Order,Position,Account}Service` constructed | (locals) |
| **358** | **`validate_boot` runs** → emits BOOT_START / VALIDATED / FAIL | `bybit_demo_boot_result` (stashed) |
| 388 | `transformer.set_services(...)` (proxies created) | — |
| 413-418 | proxies stored | `position`, `order`, `account`, `_service` aliases |
| 468-474 | **`AlertManager` constructed + stored** | `alert_manager` |
| 504-815 | risk, freshness, trade coord, etc. | many |
| 838-840 | `verify_post_switch` runs (if sentinel present) | (uses alert_manager) |
| **852-860** | **`BybitDemoAlertRelay` registered** | `bybit_demo_alert_relay` |
| **860+** | **Boot-fail replay** if stash indicates failure | (one-shot dispatch) |

Confirmed: no service-key collisions, no out-of-order dependencies, no orphan services.

## C. Real-pipeline boot path — live api-demo.bybit.com

Three scenarios run end-to-end:

### Scenario 1 — Good creds (real .env values)
```
BYBIT_DEMO_BOOT_START      url=... key_len=18 recv_window=5000
BYBIT_DEMO_BOOT_VALIDATED  url=... equity=182572.31
result: {'ok': True, 'equity': 182572.31...}
```

### Scenario 2 — No creds (key_len=0 short-circuit, no network)
```
BYBIT_DEMO_BOOT_START      url=... key_len=0 recv_window=5000
BYBIT_DEMO_BOOT_FAIL       step=no_creds err='BYBIT_DEMO_API_KEY/SECRET unset'
result: {'ok': False, 'step': 'no_creds', ...}
```
Verified `client.health_check` and `client.get` were NOT invoked (short-circuit works).

### Scenario 3 — Bad creds (random hex 18/40 chars), live network
```
BYBIT_DEMO_BOOT_START      url=... key_len=18 recv_window=5000
BYBIT_DEMO_HTTP_FAIL       op=boot_validate status=401 body=
BYBIT_DEMO_AUTH_FAIL       code=http_401 op=boot_validate msg=''   ← (P2 fix)
BYBIT_DEMO_BOOT_FAIL       step=wallet err=...HTTP 401 on boot_validate...
result: {'ok': False, 'step': 'wallet', 'err': '...HTTP 401...'}
```

## D. Real-pipeline relay path — through real `AlertManager`

Setup: real `Settings.load()`, real `DatabaseManager`, real `AlertManager(settings, db)` with `bot.send_message` stubbed to capture dispatches.

10 sequential bad-creds requests against live api-demo.bybit.com:

```
BYBIT_DEMO_ALERT_RELAY_REGISTERED | triggers=10 sink_id=1
BYBIT_DEMO_HTTP_FAIL ... BYBIT_DEMO_AUTH_FAIL    (call 1)
ALERT_SENT | level=critical len=142              ← Telegram dispatch
BYBIT_DEMO_HTTP_FAIL ... BYBIT_DEMO_AUTH_FAIL    (call 2)
ALERT_THROTTLE | type=dedup                      ← collapsed
... (8 more dedup events)
```

Final tally:
- 10 client errors → 10 AUTH_FAIL log lines → 10 sink invocations
- AlertManager content-hash dedup (5-min window, SHA256[:16]) → **1 dispatch**
- 9 collapsed via `ALERT_THROTTLE | type=dedup`

Dispatched message body:
```
🔴 ERROR

Component: bybit_demo
Severity: CRITICAL

BYBIT_DEMO_AUTH_FAIL | code=http_401 op=balance msg='' | no_ctx
```

This is the **exact production behavior** under a sustained auth-failure storm: one alert, then silence until 5 minutes pass.

## E. Full E2E adapter trade lifecycle — real BTCUSDT trade

Setup: same as D (real AlertManager + relay + bot stubbed) + real `BybitDemoOrderService` + `BybitDemoPositionService`.

Trade executed against api-demo.bybit.com:

```
[E2E] placing BUY 0.001 BTCUSDT...
BYBIT_DEMO_ORDER_RECEIVED | sym=BTCUSDT side=Buy qty=0.001 purpose=pipeline_verification ...
BYBIT_DEMO_ORD_SEND       | sym=BTCUSDT side=Buy qty=0.001 ...
BYBIT_DEMO_ORD_RESP       | sym=BTCUSDT oid=c8c9c1ad-... fill=80199.9 st=Filled
[E2E] order: status=Filled oid=c8c9c1ad-... fill=$80199.9

[E2E] closing position...
BYBIT_DEMO_POSITION_CLOSE | sym=BTCUSDT purpose=pipeline_verification_close
[E2E] close: status=Filled qty=0.001
```

**Telegram dispatches during normal flow: 0** ← critical correctness check.

The relay correctly distinguishes operational lifecycle events (INFO-level, normal flow) from alert triggers (ERROR/WARNING with specific tags). Adding BYBIT_DEMO_ORDER_REJECT or BYBIT_DEMO_INSUFFICIENT_BALANCE to the trigger table later would only require a `_TRIGGERS` dict update — no code-path changes.

## F. Component routing + AlertManager signature parity

```
COMPONENT_ROUTING['bybit_demo'] -> 'workers.log'  ✓
COMPONENT_ROUTING['worker']     -> 'workers.log'  ✓
```

```
AlertManager.send_error_alert(component: str, error_message: str,
                              severity: AlertLevel = AlertLevel.WARNING) -> None
AlertManager.send_risk_warning(warning_type: str, details: dict) -> None
```

Relay calls match (verified with `inspect.signature`).

## G. Module dependency graph (no cycles)

```
src.bybit_demo.bybit_demo_boot
  → src.bybit_demo.bybit_demo_client
  → src.core.exceptions, log_context, logging

src.observability.bybit_demo_alert_relay
  → src.core.log_context, logging, types
  → loguru (third-party)

src.workers.manager
  → src.bybit_demo (lazy import inside the bd_settings.enabled branch)
  → src.observability (lazy import inside the alert_manager branch)
  → src.exchanges.switching (lazy import in post-switch verify)
```

All lazy imports inside try-except — no boot-time failure if optional deps are absent. No circular imports.

## H. Test sweep summary (all run during this verification)

| Suite | Result |
|---|---|
| `tests/test_logging_routing.py` | **3 / 3** |
| `tests/test_bybit_demo/` (excl. live) | **50 / 50** (was 47, +3 new HTTP-layer tests) |
| `tests/test_observability/` | **10 / 10** |
| `tests/test_exchange_switching/` | **10 / 10** |
| **Focused total** | **73 / 73** ✓ |
| Live `tests/test_bybit_demo/test_adapter_integration.py` (`BYBIT_DEMO_INTEGRATION=1`) | **8 / 8** ✓ (against real Bybit demo) |
| Live boot-validation pipeline (this report) | **3 / 3 scenarios** ✓ |
| Live relay E2E + dedup (this report) | **PASS** — 10→1 dispatch |
| Live trade-lifecycle pipeline (this report) | **PASS** — 4 expected tags, 0 false alerts |
| Wide regression (`tests/`) excl. stale + live | **2387 / 2388** (1 pre-existing unrelated) |

## I. Naming consistency checklist

- All BYBIT_DEMO_* tags follow `BYBIT_DEMO_{EVENT}` convention with uppercase + underscore separators ✓
- All EXCHANGE_SWITCH_* tags follow `EXCHANGE_SWITCH_{PHASE}_{STATE?}` convention ✓
- All POST_SWITCH_* tags follow `POST_SWITCH_{ACTION}_{OUTCOME?}` convention ✓
- Bare `REDUCE_FALLBACK` retained per operator decision (cross-exchange grep continuity with Shadow) ✓
- Component name `bybit_demo` matches the directory name `src/bybit_demo/` ✓
- Module file names: `bybit_demo_client.py`, `bybit_demo_adapter.py`, `bybit_demo_boot.py` — consistent prefix ✓
- Service-key names: `bybit_demo_order`, `bybit_demo_position`, `bybit_demo_account`, `bybit_demo_boot_result`, `bybit_demo_alert_relay` — consistent prefix ✓
- Relay class `BybitDemoAlertRelay` matches feature scope ✓
- Trigger-table component_or_warning_type strings (`bybit_demo`, `bybit_demo_boot`, `bybit_demo_rate_limit`, `exchange_switch_*`, `post_switch_verify`) — consistent snake_case ✓

## J. Final verdict

The Bybit demo logging gap-fill has been verified end-to-end through real production code paths against the live demo exchange. Two real-world pipeline gaps were identified during this verification (boot-fail replay ordering; HTTP-401 auth coverage) and fixed at the source — both fixes are structural, not band-aids.

**Branch state:** 34 commits ahead of `main` (22 adapter + 7 logging gap-fill + 3 verification + 2 audit fixes).

**Production behavior verified:**
- Boot validation surfaces adapter readiness via three structured tags within ~150 ms of construction.
- Auth failures (both retCode-based AND HTTP-layer 401/403) trigger CRITICAL Telegram alerts.
- Boot-time auth failures get a one-shot replay alert via the wiring layer (relay isn't yet registered when boot validation runs).
- AlertManager content-hash dedup absorbs retry storms — 10 identical events collapse to 1 Telegram dispatch.
- Normal trade lifecycle (ORDER_RECEIVED → ORD_SEND → ORD_RESP → POSITION_CLOSE) produces zero Telegram noise.
- 73/73 focused unit + integration tests + 2387/2388 broader regression + 8/8 live integration + 3 in-pipeline live smokes all green. The single regression failure is pre-existing and unrelated.

Ready for operator's live trial. Next live restart will exercise boot validation → relay → trade lifecycle → switch workflow → post-restart verification end-to-end.
