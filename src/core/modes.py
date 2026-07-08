"""Trading mode string constants — single source of truth.

The Transformer dispatches on these values; the DB persists them as
raw strings in ``transformer_state.current_mode``; the config validator
checks ``[general] mode`` against them.

Kept as ``Final[str]`` constants rather than an Enum so the DB
representation, the config-toml string, and Python comparisons share
one canonical type. An enum would force a migration of stored mode
strings for no user-visible benefit.
"""

from __future__ import annotations

from typing import Final

# Adapter modes — each has a dedicated service slot in Transformer.
MODE_SHADOW: Final[str] = "shadow"
MODE_BYBIT: Final[str] = "bybit"
MODE_BYBIT_DEMO: Final[str] = "bybit_demo"

# Legacy / future-reserved modes. ``paper`` predates the bybit_demo
# adapter (Bybit testnet via pybit); ``live`` is the explicit-confirmation
# real-money path.
MODE_PAPER: Final[str] = "paper"
MODE_LIVE: Final[str] = "live"

# Every value the config validator accepts in ``[general] mode``.
ALL_VALID_MODES: Final[tuple[str, ...]] = (
    MODE_PAPER,
    MODE_LIVE,
    MODE_SHADOW,
    MODE_BYBIT,
    MODE_BYBIT_DEMO,
)

# Modes wired through Transformer's adapter slots.
ADAPTER_MODES: Final[tuple[str, ...]] = (
    MODE_SHADOW,
    MODE_BYBIT,
    MODE_BYBIT_DEMO,
)

# Modes the restart-based ExchangeSwitcher will target. Live ``bybit``
# is intentionally excluded — that path uses Transformer.switch_to() for
# in-memory hot-swap.
RESTART_SWITCHABLE_MODES: Final[tuple[str, ...]] = (
    MODE_SHADOW,
    MODE_BYBIT_DEMO,
)
