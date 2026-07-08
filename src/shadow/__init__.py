"""Shadow exchange adapters — drop-in replacements for Bybit services.

These adapters implement the EXACT same interface as OrderService,
PositionService, and AccountService but route calls to Shadow's
HTTP API at localhost:9090 instead of to Bybit.

Built in Transformer Phase T2. Wired into the system in Phase T3.
"""

from src.shadow.shadow_adapter import (
    ShadowOrderService,
    ShadowPositionService,
    ShadowAccountService,
)

__all__ = [
    "ShadowOrderService",
    "ShadowPositionService",
    "ShadowAccountService",
]
