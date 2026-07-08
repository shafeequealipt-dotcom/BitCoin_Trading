# Phase 1.8 — Exception Hierarchy

Investigation content lives in **[`phase1_synthesis.md`](phase1_synthesis.md) Section 8**.

## What's covered there

- Full hierarchy at `src/core/exceptions.py` (all inherit from `TradingMCPError`)
- Exception types raised by the bybit_demo HTTP client:
  - `InvalidOrderError` (110001, 110003, 110009, 110013, 110017, 110026, 110043)
  - `InsufficientBalanceError` (110007, 110045)
  - `OrderRejectedError` (any other 110xxx)
  - `RateLimitError` (10006, 10018)
  - `BybitAPIError` (any other ret_code, transport failure)
- Critical contract rule mirrored from Shadow: **adapters never raise**. Client raises typed exceptions; adapter catches and returns sentinels.

See `src/core/exceptions.py` and `src/bybit_demo/bybit_demo_client.py:_translate_ret_code`.
