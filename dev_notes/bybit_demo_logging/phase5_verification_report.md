# Phase 5 — Verification Report (Bybit Demo Logging & Observability)

Date: 2026-05-08
Branch: `feature/bybit-demo-adapter`
Plan file: `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-warm-karp.md`

---

## Section 1 — Phase Summary

Six commits on top of the existing 14-commit Bybit demo adapter delivery, gap-filling the eight evidence-based gaps identified in plan-mode investigation. No commit touches Shadow, brain, APEX, TradeGate, Layer 1, Layer 4, or the existing live Bybit market-data client.

| Phase | Commit | Files modified | Net lines |
|---|---|---|---|
| 0 (no commit; baseline doc bundled into 1A) | — | `dev_notes/bybit_demo_logging/phase0_baseline.md` (new) | +29 |
| 1A | `c788f94` feat(bybit_demo_logging/1A): retCode-specific tags + retry visibility | `src/bybit_demo/bybit_demo_client.py`, `tests/test_bybit_demo/test_client_retcode_translation.py` | +192 / -4 |
| 1B | `f171ffa` fix(bybit_demo_logging/1B): wallet-balance failure visibility | `src/bybit_demo/bybit_demo_adapter.py`, `tests/test_bybit_demo/test_account_service.py` | +38 / -1 |
| 1C | `850cad5` feat(bybit_demo_logging/1C): partial-fill visibility | `src/bybit_demo/bybit_demo_adapter.py`, `tests/test_bybit_demo/test_order_service.py` | +89 |
| 2 | `42f8676` feat(bybit_demo_logging/2): boot validation tags | `src/bybit_demo/bybit_demo_boot.py` (new), `src/workers/manager.py`, `tests/test_bybit_demo/test_boot_validation.py` (new) | +281 |
| 3 | `75001cd` feat(bybit_demo_logging/3): switch entry tags from telegram dashboard | `src/telegram/handlers/dashboard_handler.py` | +22 |
| 4 | `9f49cf3` feat(bybit_demo_logging/4): alert relay for critical adapter + switch events | `src/observability/__init__.py` (new), `src/observability/bybit_demo_alert_relay.py` (new), `src/workers/manager.py`, `tests/test_observability/__init__.py` (new), `tests/test_observability/test_bybit_demo_alert_relay.py` (new) | +563 |
| 5 (this) | (forthcoming `docs(...)` commit) | `dev_notes/bybit_demo_logging/phase5_verification_report.md` (new) | this file |

Each Phase 1–4 commit is independently revertable.

---

## Section 2 — Tag Inventory (Final, Post-Implementation)

### `bybit_demo` component → `workers.log`

| Tag | Severity | Fields | Trigger | Source |
|---|---|---|---|---|
| `BYBIT_DEMO_BOOT_START` | INFO | url, key_len, recv_window | adapter wiring (boot) | `src/bybit_demo/bybit_demo_boot.py` |
| `BYBIT_DEMO_BOOT_VALIDATED` | INFO | url, equity | health-check + wallet probe both succeeded | `src/bybit_demo/bybit_demo_boot.py` |
| `BYBIT_DEMO_BOOT_FAIL` | ERROR | step (no_creds / health_check / wallet), err | one of the boot probes failed | `src/bybit_demo/bybit_demo_boot.py` |
| `BYBIT_DEMO_AUTH_FAIL` | ERROR | code, op, msg | retCode 10003 / 10004 / 10005 from any signed call | `src/bybit_demo/bybit_demo_client.py` |
| `BYBIT_DEMO_TIMESTAMP_FAIL` | ERROR | code, op, msg | retCode 10002 (timestamp outside recv_window) | `src/bybit_demo/bybit_demo_client.py` |
| `BYBIT_DEMO_RATE_LIMIT_HIT` | WARNING | code, op, msg | retCode 10006 / 10018 (rate limit / IP rate limit) | `src/bybit_demo/bybit_demo_client.py` |
| `BYBIT_DEMO_INSUFFICIENT_BALANCE` | WARNING | code, op, msg | retCode 110007 / 110045 | `src/bybit_demo/bybit_demo_client.py` |
| `BYBIT_DEMO_RATE_LIMIT` | WARNING | op, remaining | response header `X-Bapi-Limit-Status` < 3 | `src/bybit_demo/bybit_demo_client.py` |
| `BYBIT_DEMO_RATE_LIMIT_RECOVERED` | INFO | op, remaining | first response with remaining ≥ 3 after a prior < 3 hit | `src/bybit_demo/bybit_demo_client.py` |
| `BYBIT_DEMO_HTTP_FAIL` | WARNING | op, status, body | HTTP 4xx (excluding 429) | `src/bybit_demo/bybit_demo_client.py` |
| `BYBIT_DEMO_RETRY` | DEBUG | op, attempt, wait_ms, err | transient failure inside retry loop, before sleep | `src/bybit_demo/bybit_demo_client.py` |
| `BYBIT_DEMO_CALL_FAIL` | DEBUG (boot-grace) / ERROR | op, attempts, err, boot_grace | retry chain exhausted | `src/bybit_demo/bybit_demo_client.py` |
| `BYBIT_DEMO_WALLET_FAIL` | WARNING | err | `BybitDemoAccountService.get_wallet_balance` caught `TradingMCPError` | `src/bybit_demo/bybit_demo_adapter.py` |
| `BYBIT_DEMO_PARTIAL_FILL` | INFO | sym, oid, filled, requested, ratio | `_resolve_order_fill` saw `orderStatus=PartiallyFilled` | `src/bybit_demo/bybit_demo_adapter.py` |
| `BYBIT_DEMO_ORDER_RECEIVED` | INFO | sym, side, qty, purpose, layer_snapshot_keys, force | entry of `place_order` (audit) | `src/bybit_demo/bybit_demo_adapter.py` |
| `BYBIT_DEMO_ORD_SEND` | INFO | sym, side, qty, lev, sl, tp | about to POST `/v5/order/create` | `src/bybit_demo/bybit_demo_adapter.py` |
| `BYBIT_DEMO_ORD_RESP` | INFO | sym, oid, fill, st | resolved fill / status | `src/bybit_demo/bybit_demo_adapter.py` |
| `BYBIT_DEMO_ORDER_REJECT` | WARNING | sym, side, qty, err | order place caught `TradingMCPError` | `src/bybit_demo/bybit_demo_adapter.py` |
| `BYBIT_DEMO_LEVERAGE_FAIL` | WARNING | sym, lev, err | non-idempotent `set-leverage` failure | `src/bybit_demo/bybit_demo_adapter.py` |
| `BYBIT_DEMO_POSITION_CLOSE` | INFO | sym, purpose | `close_position` entry | `src/bybit_demo/bybit_demo_adapter.py` |
| `BYBIT_DEMO_CLOSE_NO_POSITION` | WARNING | sym | close requested with no open position | `src/bybit_demo/bybit_demo_adapter.py` |
| `BYBIT_DEMO_CLOSE_REJECT` | WARNING | sym, err | close POST caught `TradingMCPError` | `src/bybit_demo/bybit_demo_adapter.py` |
| `BYBIT_DEMO_CLOSE_ALL_ITEM_FAIL` | WARNING | sym, err | one position in `close_all_positions` failed | `src/bybit_demo/bybit_demo_adapter.py` |
| `BYBIT_DEMO_SET_SL_FAIL` | WARNING | sym, sl, err | `set_stop_loss` failed | `src/bybit_demo/bybit_demo_adapter.py` |
| `BYBIT_DEMO_SET_TP_FAIL` | WARNING | sym, tp, err | `set_take_profit` failed | `src/bybit_demo/bybit_demo_adapter.py` |
| `REDUCE_FALLBACK` | WARNING | sym, qty, reason, err | reduce → close fallback (bare tag matches Shadow's pattern) | `src/bybit_demo/bybit_demo_adapter.py` |

### `worker` component → `workers.log` (switch lifecycle)

| Tag | Severity | Source |
|---|---|---|
| `EXCHANGE_SWITCH_REQUESTED` | INFO | `src/telegram/handlers/dashboard_handler.py` (preview button) |
| `EXCHANGE_SWITCH_CONFIRMED` | INFO | `src/telegram/handlers/dashboard_handler.py` (confirm button) |
| `EXCHANGE_SWITCH_VALIDATE` | INFO | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_INVENTORY_FAIL` | ERROR | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_CLOSE_BEGIN` | INFO | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_RETRY` | WARNING | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_ABORT_OPEN_POSITIONS` | ERROR | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_CLOSE_DONE` | INFO | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_DB_FLIP` | INFO | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_DB_FLIP_FAIL` | ERROR | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_HISTORY_FAIL` | WARNING | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_SENTINEL_FAIL` | WARNING | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_ALERT_FAIL` | WARNING | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_RESTART_TRIGGER` | INFO | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_NO_SYSTEMCTL` | ERROR | `src/exchanges/switching/exchange_switcher.py` |
| `EXCHANGE_SWITCH_RESTART_FAIL` | ERROR | `src/exchanges/switching/exchange_switcher.py` |
| `POST_SWITCH_VERIFY_BEGIN` | INFO | `src/exchanges/switching/post_switch_verifier.py` |
| `POST_SWITCH_VERIFY_DONE` | INFO | `src/exchanges/switching/post_switch_verifier.py` |
| `POST_SWITCH_VERIFY_WALLET_FAIL` | ERROR | `src/exchanges/switching/post_switch_verifier.py` |
| `POST_SWITCH_VERIFY_POSITIONS_FAIL` | ERROR | `src/exchanges/switching/post_switch_verifier.py` |
| `POST_SWITCH_SENTINEL_READ_FAIL` | WARNING | `src/exchanges/switching/post_switch_verifier.py` |
| `POST_SWITCH_SENTINEL_PARSE_FAIL` | WARNING | `src/exchanges/switching/post_switch_verifier.py` |
| `POST_SWITCH_SENTINEL_UNLINK_FAIL` | WARNING | `src/exchanges/switching/post_switch_verifier.py` |
| `POST_SWITCH_VERIFY_ALERT_FAIL` | WARNING | `src/exchanges/switching/post_switch_verifier.py` |
| `BYBIT_DEMO_ALERT_RELAY_REGISTERED` | INFO | `src/observability/bybit_demo_alert_relay.py` |
| `BYBIT_DEMO_ALERT_RELAY_FAIL` | WARNING | `src/observability/bybit_demo_alert_relay.py` |

**Total tag count:** 26 BYBIT_DEMO_* + 16 EXCHANGE_SWITCH_*/POST_SWITCH_* + 2 BYBIT_DEMO_ALERT_RELAY_* = **44 distinct tags** for the bybit_demo + switch surface.

---

## Section 3 — Alert Inventory

The relay (`src/observability/bybit_demo_alert_relay.py`) translates the following tag prefixes into AlertManager calls. All other tags are log-only.

| Tag prefix | AlertManager method | Level | Dedup hash basis |
|---|---|---|---|
| `BYBIT_DEMO_AUTH_FAIL` | `send_error_alert("bybit_demo", msg, CRITICAL)` | CRITICAL | full message text (SHA256[:16]) |
| `BYBIT_DEMO_BOOT_FAIL` | `send_risk_warning("bybit_demo_boot", details)` | CRITICAL | full message text |
| `BYBIT_DEMO_TIMESTAMP_FAIL` | `send_error_alert("bybit_demo", msg, WARNING)` | WARNING | full message text |
| `BYBIT_DEMO_RATE_LIMIT_HIT` | `send_error_alert("bybit_demo_rate_limit", msg, WARNING)` | WARNING | full message text |
| `EXCHANGE_SWITCH_ABORT_OPEN_POSITIONS` | `send_risk_warning("exchange_switch_abort", details)` | CRITICAL | full message text |
| `EXCHANGE_SWITCH_DB_FLIP_FAIL` | `send_risk_warning("exchange_switch_db_flip", details)` | CRITICAL | full message text |
| `EXCHANGE_SWITCH_RESTART_FAIL` | `send_risk_warning("exchange_switch_restart", details)` | CRITICAL | full message text |
| `EXCHANGE_SWITCH_NO_SYSTEMCTL` | `send_risk_warning("exchange_switch_systemctl", details)` | CRITICAL | full message text |
| `POST_SWITCH_VERIFY_WALLET_FAIL` | `send_error_alert("post_switch_verify", msg, CRITICAL)` | CRITICAL | full message text |
| `POST_SWITCH_VERIFY_POSITIONS_FAIL` | `send_error_alert("post_switch_verify", msg, WARNING)` | WARNING | full message text |

Throttle / dedup behavior (from `AlertManager._send` + `AlertThrottle`):

- 5-minute SHA256[:16] content hash window — identical messages within 5 minutes drop to a single alert.
- Hourly rolling rate cap (default 600/hour). CRITICAL bypasses the cap unconditionally; WARNING/INFO obey it.
- Repeated retries under a single root cause (e.g., wrong API key → AUTH_FAIL on every signed call) collapse via dedup because the message content is identical.

Existing pre-restart and post-restart `send_custom` calls in `exchange_switcher.py` and `post_switch_verifier.py` continue firing on the success path — they are NOT affected by the relay.

---

## Section 4 — Coverage Parity (Shadow vs. Bybit Demo)

Concrete code-level coverage map. `Shadow` column lists the actual emissions in `src/shadow/shadow_adapter.py` (not the audit-doc legacy list).

| Lifecycle moment | Shadow tag | Bybit Demo tag | Notes |
|---|---|---|---|
| Order audit (entry) | `SHADOW_ORDER_RECEIVED` | `BYBIT_DEMO_ORDER_RECEIVED` | match |
| Order send | `SHADOW_ORD_SEND` | `BYBIT_DEMO_ORD_SEND` | match |
| Order response | `SHADOW_ORD_RESP` | `BYBIT_DEMO_ORD_RESP` | match |
| Position close entry | `SHADOW_POSITION_CLOSE` | `BYBIT_DEMO_POSITION_CLOSE` | match |
| Reduce fallback | `REDUCE_FALLBACK` | `REDUCE_FALLBACK` | bare tag in both — operator decision (cross-exchange grep continuity) |
| Retry exhaustion | `SHADOW_CALL_FAIL` | `BYBIT_DEMO_CALL_FAIL` | match (both have boot-grace gating) |
| HTTP 4xx | `SHADOW_HTTP_FAIL` | `BYBIT_DEMO_HTTP_FAIL` | match |
| Boot validation | _(none)_ | `BYBIT_DEMO_BOOT_START` / `_VALIDATED` / `_FAIL` | Bybit-only — Shadow's localhost adapter doesn't need boot validation |
| Auth failure | _(none)_ | `BYBIT_DEMO_AUTH_FAIL` | Bybit-only — Shadow doesn't sign requests |
| Timestamp failure | _(none)_ | `BYBIT_DEMO_TIMESTAMP_FAIL` | Bybit-only |
| Rate limit | _(none)_ | `BYBIT_DEMO_RATE_LIMIT_HIT` / `BYBIT_DEMO_RATE_LIMIT` / `BYBIT_DEMO_RATE_LIMIT_RECOVERED` | Bybit-only |
| Insufficient balance | _(none)_ | `BYBIT_DEMO_INSUFFICIENT_BALANCE` | Bybit-only |
| Partial fill | _(none)_ | `BYBIT_DEMO_PARTIAL_FILL` | Bybit-only |
| Per-attempt retry | _(none — silent)_ | `BYBIT_DEMO_RETRY` | Bybit-only |
| Wallet probe failure | _(none — silent)_ | `BYBIT_DEMO_WALLET_FAIL` | Bybit-only |

**Asymmetry direction:** Bybit Demo has *more* tags than Shadow because Bybit's API surface (HMAC signing, rate limits, recv_window, retCodes) introduces failure modes that don't exist in Shadow's localhost adapter. This is the correct asymmetry — both adapters are fully observable for their respective failure modes.

The audit document's 21-tag list for Shadow includes legacy tags (`SHADOW_AGG_ERR`, `SHADOW_AUTO_TRACK`, `SHADOW_CONN_OPEN`, `SHADOW_NO_EXIT_PRICE`, `SHADOW_POS_CREATED`, etc.) that are NOT present in current Shadow code. Per spec Part A, Shadow logging is out-of-scope — Bybit Demo equivalents for legacy-only Shadow tags were intentionally NOT built.

---

## Section 5 — Test Results

### Routing CI (`tests/test_logging_routing.py`)

```
test_every_get_logger_component_is_routed PASSED
test_component_routing_targets_are_valid  PASSED
test_scan_finds_known_components          PASSED
3 passed in 0.14s
```

### Bybit Demo unit tests (`tests/test_bybit_demo/`, excl. live integration)

```
30 baseline tests + 17 new tests = 47 collected
account_service: 5 PASSED (1 new — wallet-fail warning)
boot_validation: 5 PASSED (all new — Phase 2)
client_retcode_translation: 17 PASSED (9 new — _log_ret_code routing)
client_signing: 3 PASSED
order_service: 8 PASSED (2 new — partial-fill + filled-no-tag)
position_service: 5 PASSED
transformer_dispatch: 4 PASSED
Total: 47 passed in 0.49s
```

### Observability tests (`tests/test_observability/`, all new — Phase 4)

```
10 passed in 1.05s
- _extract_tag (2 cases)
- relay routes auth_fail to CRITICAL error alert
- relay routes boot_fail to risk warning
- relay routes rate_limit_hit to WARNING error alert
- relay routes EXCHANGE_SWITCH_RESTART_FAIL to risk warning
- relay ignores non-trigger tags (ORD_SEND, POSITION_CLOSE, ORDER_REJECT)
- relay ignores other components (dashboard component spoofing)
- register/unregister idempotency
- sink resilience against malformed records
```

### Regression suite (Shadow / telegram / alerts / logging keywords)

```
157 passed, 2231 deselected, 0 failures, 1 unrelated warning, 0 errors
14.71s
(3 import errors in tests/test_phase7/* are pre-existing, reference
 src.brain.prompt_builder / src.brain.scheduler which do not exist
 on this branch — unrelated to this work and correctly excluded.)
```

### Live integration test (gated)

`tests/test_bybit_demo/test_adapter_integration.py` (gated by `BYBIT_DEMO_INTEGRATION=1`) was NOT run in this verification. It hits the live api-demo.bybit.com endpoint and requires credentials. The operator should run it in the live trial window described in Section 6.

---

## Section 6 — Operator Handover

### Grep cookbook (workers.log)

- **All bybit_demo activity:** `grep BYBIT_DEMO_ data/logs/workers.log`
- **Boot validation:** `grep -E 'BYBIT_DEMO_BOOT_(START|VALIDATED|FAIL)' data/logs/workers.log`
- **Trade lifecycle for one decision:** `grep 'did=d-1778240483699' data/logs/workers.log`
- **Order lifecycle:** `grep -E 'BYBIT_DEMO_(ORDER_RECEIVED|ORD_SEND|ORD_RESP|ORDER_REJECT|PARTIAL_FILL)' data/logs/workers.log`
- **Auth or timestamp problems:** `grep -E 'BYBIT_DEMO_(AUTH_FAIL|TIMESTAMP_FAIL)' data/logs/workers.log`
- **Rate limit visibility:** `grep -E 'BYBIT_DEMO_RATE_LIMIT(_HIT|_RECOVERED)?' data/logs/workers.log`
- **Switch lifecycle (full trace from button to verify):** `grep -E '(EXCHANGE_SWITCH|POST_SWITCH)_' data/logs/workers.log`
- **Switch entry from Telegram:** `grep -E 'EXCHANGE_SWITCH_(REQUESTED|CONFIRMED)' data/logs/workers.log`
- **Alert relay confirmation:** `grep BYBIT_DEMO_ALERT_RELAY data/logs/workers.log`

### Telegram alert interpretation

Alerts now arrive on the operator's Telegram for:

- **CRITICAL:** invalid API credentials (`BYBIT_DEMO_AUTH_FAIL`), boot validation failure (`BYBIT_DEMO_BOOT_FAIL`), exchange-switch abort (positions stuck open), DB-flip failure, restart failure, no systemctl, post-switch wallet probe failure.
- **WARNING:** timestamp drift (clock skew vs. recv_window), rate-limit hit (Bybit returned 10006/10018), post-switch position-list probe failure.

Existing pre-restart and post-restart `send_custom` notifications continue firing on the success path — the relay is additive.

### Common debugging workflows

- **"Why did equity drop to zero?"** Grep `BYBIT_DEMO_WALLET_FAIL` — was there a wallet probe failure? If yes, check `BYBIT_DEMO_AUTH_FAIL` / `BYBIT_DEMO_TIMESTAMP_FAIL` immediately preceding.
- **"Why is a switch stuck?"** Grep `EXCHANGE_SWITCH_` for the most recent run. The phase tags are deterministic: REQUESTED → CONFIRMED → VALIDATE → CLOSE_BEGIN → (RETRY)? → CLOSE_DONE → DB_FLIP → RESTART_TRIGGER. Whichever is missing identifies the blocking phase.
- **"Did the order fully fill?"** After a `BYBIT_DEMO_ORD_RESP`, grep the same `oid=` for `BYBIT_DEMO_PARTIAL_FILL`. Presence indicates an under-fill (operator may want to investigate liquidity).
- **"Why did the brain decision not result in a trade?"** Grep the `did=d-...` ID across workers.log; correlate `BYBIT_DEMO_ORDER_RECEIVED` (entry) with `BYBIT_DEMO_ORDER_REJECT` (rejected) — the `err=` field carries the Bybit retCode + retMsg.

---

## Section 7 — What's NOT Addressed (Honest Limits)

- **Shadow logging is unchanged.** Per spec Part A, Shadow's bare `REDUCE_FALLBACK` and 6 SHADOW_* tags continue as-is. Audit-doc legacy Shadow tags (SHADOW_CONN_OPEN, SHADOW_AUTO_TRACK, SHADOW_NO_EXIT_PRICE, SHADOW_POS_CREATED, SHADOW_SLTP_HIT, SHADOW_SLTP_NEAR, SHADOW_SL_TIGHT, SHADOW_STATS, SHADOW_SUBS_FINAL, SHADOW_AGG_ERR, SHADOW_CLOSE_LOOKUP_FAIL, SHADOW_NOT_CONNECTED, SHADOW_POS_CLOSE, SHADOW_POS_NEW) were NOT given Bybit Demo equivalents because they are not present in current Shadow code. Building Bybit-only tags for moments Shadow does not log would create asymmetric coverage in the wrong direction.
- **No new AlertManager methods.** All alerts use existing `send_error_alert` and `send_risk_warning`. Adding new `send_*` methods was avoided per spec Component 5 hard constraints.
- **No throttle / dedup customization.** The relay relies on AlertManager's existing 5-minute SHA256[:16] content-hash dedup. If a future operational pattern emerges where dedup keys need to be coarser (ignore variable retCode in the message), the relay can be extended; not done now because there is no observed need.
- **Brain / Stage 2 / APEX / TradeGate / Layer 1 / Layer 4 / strategist** alerting is out of scope per spec Part A. Existing AlertManager call sites in those modules are unchanged.
- **Live Bybit market-data client** (`src/trading/client.py`, component `trading`) is out of scope. Its tags continue to use the `trading` component routing.
- **Live trial validation pending.** This work was tested with unit + regression tests. The operator should run a 30-60 minute live trial after restart to confirm:
  - `BYBIT_DEMO_BOOT_VALIDATED` fires with non-zero equity within seconds of boot.
  - A real Telegram-driven Shadow → Bybit Demo switch produces the full grep trace listed in Section 6 + matching Telegram messages.
  - Forced credential failure (temporarily wrong key) produces `BYBIT_DEMO_BOOT_FAIL` AND a CRITICAL Telegram alert from the relay.

---

## Verification Gate: PASSED

- All routing CI tests green.
- 60 focused tests (`test_logging_routing` + `test_bybit_demo/` excl. live + `test_observability/`) green.
- 157 regression tests (Shadow, telegram, alerts, logging keywords) green; no regressions.
- Six atomic commits on the bybit-demo branch, each independently revertable.
- Verification report covers tag inventory, alert inventory, coverage parity, test results, operator handover, and out-of-scope list.

Bybit demo logging gap-fill is complete. Awaiting operator's live-trial sign-off before merge.
