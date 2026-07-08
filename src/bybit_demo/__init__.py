"""Bybit demo adapter — paper-money execution against api-demo.bybit.com.

Mirrors the Shadow adapter contract: three service classes returning
``Order`` / ``Position`` / ``AccountInfo`` dataclasses (never raises;
returns REJECTED / empty / zero sentinels on error). Wired into the
Transformer alongside Shadow and the live-bybit slot via Phase 3.

The adapter exists to validate strategy edge against real Bybit
microstructure (real spreads, real partial fills, real fill latency)
without real-money risk. Toggled at runtime via Telegram-driven
restart-based switching (Phase 4 — ExchangeSwitcher).

Built in Phase 2 of the bybit_demo_adapter project (see
``dev_notes/bybit_demo_adapter/phase1_synthesis.md`` for the contract
the adapter must mirror).
"""

from src.bybit_demo.bybit_demo_adapter import (
    BybitDemoAccountService,
    BybitDemoOrderService,
    BybitDemoPositionService,
)
from src.bybit_demo.bybit_demo_client import BybitDemoClient

__all__ = [
    "BybitDemoAccountService",
    "BybitDemoClient",
    "BybitDemoOrderService",
    "BybitDemoPositionService",
]
