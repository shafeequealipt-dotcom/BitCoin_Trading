"""Exchange-related infrastructure: switching workflow + post-restart verifiers.

The execution adapters themselves live one level higher:
  - src/shadow/         — Shadow virtual exchange adapter
  - src/bybit_demo/     — Bybit demo adapter
  - src/trading/        — live Bybit market-data + future-live-trading client

This package houses code that orchestrates BETWEEN adapters: the
restart-based switching workflow used to flip between Shadow and
Bybit demo, and the boot-time verifier that closes the loop.
"""
