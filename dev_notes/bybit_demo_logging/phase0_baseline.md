# Phase 0 — Baseline (Bybit Demo Logging Gap-Fill)

Date: 2026-05-08
Branch: `feature/bybit-demo-adapter`
Working tree: clean for `src/` (only `data/` runtime drift, acceptable).

## CI Routing Test

```
tests/test_logging_routing.py::test_every_get_logger_component_is_routed PASSED
tests/test_logging_routing.py::test_component_routing_targets_are_valid PASSED
tests/test_logging_routing.py::test_scan_finds_known_components PASSED
3 passed in 0.50s
```

`bybit_demo` already routed to `workers.log` at `src/core/logging.py:59`.

## Existing Bybit Demo Test Suite

`pytest tests/test_bybit_demo/ --ignore=test_adapter_integration.py` →
**30 / 30 PASSED in 0.66s**.

Test files exercised: account_service, client_retcode_translation, client_signing, order_service, position_service, transformer_dispatch.

## Tag Inventory in Live Logs (from `data/logs/workers.log`)

### BYBIT_DEMO_*

```
BYBIT_DEMO_TEST_PROBE
```

(Only the test probe is present in the live log. Production trade events would emit ORDER_RECEIVED / ORD_SEND / ORD_RESP / POSITION_CLOSE / etc., but no live bybit_demo trade has happened in the current rotation window. The adapter code paths emit those tags — confirmed by reading `src/bybit_demo/bybit_demo_adapter.py`.)

### EXCHANGE_SWITCH_*

```
EXCHANGE_SWITCH_CLOSE_BEGIN
EXCHANGE_SWITCH_CLOSE_DONE
EXCHANGE_SWITCH_DB_FLIP
EXCHANGE_SWITCH_RESTART_TRIGGER
EXCHANGE_SWITCH_VALIDATE
```

(5 tags actually fired in the live log — covering at least one prior successful switch. Code defines 14 EXCHANGE_SWITCH_* tags; the others are failure paths that have not occurred yet.)

### POST_SWITCH_*

(None present in current log rotation window. Code defines 8 POST_SWITCH_* tags.)

## Gap List (Confirmed by Code Reading)

See plan file `/home/inshadaliqbal786/.claude/plans/plan-mode-first-compeltely-warm-karp.md` "Real Gaps (Evidence-Based)" table for the 8 gaps with file:line citations.

## Verification Gate

PASSED. Proceed to Phase 1A.
