"""Restart-based exchange switching for Shadow ↔ Bybit Demo.

Phase 4 of the bybit_demo_adapter project. The switcher is intentionally
a SEPARATE path from ``Transformer.switch_to()`` (which does in-memory
hot-swap and stays for the live-bybit flow). This module's
:class:`ExchangeSwitcher` closes positions, persists the target mode to
the database, writes a post-switch sentinel, and triggers
``systemctl restart trading-workers trading-mcp-sse``. On the next
boot, ``Transformer.initialize()`` reads the new mode from the DB and
the post-switch verifier delivers a Telegram notification once
services are up.
"""

from src.exchanges.switching.exchange_switcher import ExchangeSwitcher
from src.exchanges.switching.post_switch_verifier import (
    POST_SWITCH_SENTINEL_PATH,
    verify_post_switch,
)

__all__ = [
    "ExchangeSwitcher",
    "POST_SWITCH_SENTINEL_PATH",
    "verify_post_switch",
]
