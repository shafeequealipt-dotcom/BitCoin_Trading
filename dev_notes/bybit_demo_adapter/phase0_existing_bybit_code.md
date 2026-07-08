# Phase 0.B — Existing Bybit Code Audit

**Date:** 2026-05-08
**Purpose:** Inventory Bybit-related code in the project. Determine what's reusable for the demo adapter.

## Files Found

| Path | Purpose | Reusable for demo? |
|------|---------|--------------------|
| `src/trading/client.py` | `BybitClient` — wraps `pybit.unified_trading.HTTP` for live mainnet market data + trading | Limited — `pybit` does not allow custom base URL, so cannot route to api-demo.bybit.com |
| `src/trading/auth.py` | `BybitAuth` — HMAC-SHA256 signing logic for v5 API | Reference only — Phase 2.B implements equivalent signing inline |
| `src/trading/services/order_service.py` | `OrderService` — wraps `BybitClient.call("place_order", ...)` for live | NOT reused — Bybit demo adapter implements `place_order` directly |
| `src/trading/services/position_service.py` | `PositionService` — wraps `BybitClient.call(...)` for live | NOT reused |
| `src/trading/services/account_service.py` | `AccountService` — wraps `BybitClient.call("get_wallet_balance", ...)` for live | NOT reused |
| `src/trading/websocket_client.py` | Public market-data WebSocket | NOT relevant for execution adapter |

## Decision

**Build `src/bybit_demo/bybit_demo_client.py` from scratch using `aiohttp`** (mirror Shadow's pattern at `src/shadow/shadow_adapter.py:_shadow_get_with_retry`).

Reasons:
- `pybit` library does not accept a custom base URL parameter; switching its `testnet=True` flag points to mainnet-testnet (`api-testnet.bybit.com`), not demo (`api-demo.bybit.com`).
- Shadow's `aiohttp`-based pattern is proven, has boot grace + retry + structured logging, and is the project's house style for adapter HTTP clients.
- Re-implementing HMAC signing is ~30 lines and avoids a pybit dependency for the demo path.

## Existing Bybit Mode Branches Outside Transformer

Two locations check `settings.general.mode` directly (Phase 1 finding):

1. `src/trading/client.py:84` — `if not settings.bybit.testnet and settings.general.mode == "paper"` — guards live-mainnet trading when paper mode is set.
2. `src/trading/client.py:140` — `if self._settings.general.mode == "shadow"` — skips live credential validation when in shadow mode.

**Phase 3 does NOT need to touch these.** They check `"paper"` / `"shadow"` specifically. Adding `"bybit_demo"` doesn't intersect — the demo adapter does not flow through `BybitClient` at all.

## Reusable Functions / Patterns

| Function | Path | Use |
|----------|------|-----|
| `_shadow_get_with_retry` | `src/shadow/shadow_adapter.py:59` | Pattern for `BybitDemoClient._get_with_retry` (5 attempts, 0.2*2^n backoff, 30s boot grace) |
| `_in_boot_grace`, `_PROCESS_START_MONOTONIC` | `src/shadow/shadow_adapter.py:50-56` | Mirror these as module-level constants in `bybit_demo_client.py` |
| `ctx()` | `src/core/log_context.py` | Used in every log line for request-context binding |
| `get_logger("bybit_demo")` | `src/core/logging.py` | Returns the project's loguru-bound logger |
