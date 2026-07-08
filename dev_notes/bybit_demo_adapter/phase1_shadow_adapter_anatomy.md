# Phase 1.2 — Shadow Adapter Anatomy

Investigation content lives in **[`phase1_synthesis.md`](phase1_synthesis.md) Section 2**.

## What's covered there

- `ShadowOrderService` / `ShadowPositionService` / `ShadowAccountService` constructors at `src/shadow/shadow_adapter.py:409 / 135 / 597`
- Boot grace + retry: `_shadow_get_with_retry` (5 attempts, `0.2 * 2^(n-1)` backoff, 30 s grace)
- Every method's signature + return shape (Order / Position / AccountInfo dataclass keys)
- Every log tag (`SHADOW_HTTP_FAIL`, `SHADOW_CALL_FAIL`, `SHADOW_POSITION_CLOSE`, `SHADOW_ORDER_RECEIVED`, `SHADOW_ORD_SEND`, `SHADOW_ORD_RESP`, `REDUCE_FALLBACK`)
- Critical contract rule: Shadow returns sentinels, never raises — Bybit demo must mirror

See `src/shadow/shadow_adapter.py` and `src/core/types.py:250-354`.
