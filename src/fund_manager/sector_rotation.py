"""M7 — BTC-dominance-based sector rotation.

Adjusts capital allocation across coin tiers depending on whether
BTC dominance is rising, falling, or stable:

Tiers:
  1 = BTC
  2 = Major alts (ETH, SOL, XRP)
  3 = Everything else

Allocations (% of trading capital):
  BTC dom rising:  BTC 40%, Alts 30%, Small 20%
  BTC dom falling: BTC 25%, Alts 40%, Small 25%
  Balanced:        BTC 35%, Alts 35%, Small 20%

Remaining capital (10-15%) is held unallocated as buffer.
"""

from __future__ import annotations

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import TimeHorizon  # noqa: F401 — re-export convenience

log = get_logger("fund_manager")

# ── Tier definitions ────────────────────────────────────────────────
_TIER1_SYMBOLS = {"BTCUSDT", "BTC"}
_TIER2_SYMBOLS = {"ETHUSDT", "ETH", "SOLUSDT", "SOL", "XRPUSDT", "XRP"}

# ── Allocation tables (tier -> pct of trading capital) ──────────────
_ALLOC_RISING: dict[int, float] = {1: 0.40, 2: 0.30, 3: 0.20}
_ALLOC_FALLING: dict[int, float] = {1: 0.25, 2: 0.40, 3: 0.25}
_ALLOC_BALANCED: dict[int, float] = {1: 0.35, 2: 0.35, 3: 0.20}

# BTC dominance thresholds for direction detection
_DOM_RISING_THRESHOLD = 1.5   # +1.5% over 7d → rising
_DOM_FALLING_THRESHOLD = -1.5  # -1.5% over 7d → falling


class SectorRotation:
    """BTC-dominance-based sector rotation allocator."""

    def __init__(self, settings=None, services: dict | None = None) -> None:
        self._settings = settings
        self._services = services or {}
        self._cached_direction: str = "balanced"  # "rising", "falling", "balanced"
        self._cached_dominance: float = 50.0

    # ------------------------------------------------------------------
    # Tier classification
    # ------------------------------------------------------------------

    @staticmethod
    def get_coin_tier(symbol: str) -> int:
        """Classify a coin into a tier.

        Args:
            symbol: Trading pair symbol (e.g. "BTCUSDT").

        Returns:
            Tier number: 1 (BTC), 2 (major alts), 3 (everything else).
        """
        sym = symbol.upper().replace("USDT", "").replace("USD", "")
        if sym in ("BTC",):
            return 1
        if sym in ("ETH", "SOL", "XRP"):
            return 2
        return 3

    # ------------------------------------------------------------------
    # Dominance direction
    # ------------------------------------------------------------------

    async def _fetch_dominance_direction(self) -> str:
        """Determine BTC dominance direction via onchain service.

        Returns:
            "rising", "falling", or "balanced".
        """
        try:
            onchain = self._services.get("onchain")
            if onchain is None:
                return "balanced"

            metrics = await onchain.get_global_metrics()
            btc_dom = metrics.get("btc_dominance", 50.0)
            self._cached_dominance = btc_dom

            # Use market_cap_change as a proxy for dominance trend
            dom_change = metrics.get("market_cap_change_24h_pct", 0.0)

            if dom_change >= _DOM_RISING_THRESHOLD:
                direction = "rising"
            elif dom_change <= _DOM_FALLING_THRESHOLD:
                direction = "falling"
            else:
                direction = "balanced"

            self._cached_direction = direction
            log.info(
                "SectorRotation: BTC dominance={dom:.1f}%, direction={dir}",
                dom=btc_dom, dir=direction,
            )
            return direction

        except Exception:
            log.warning("SectorRotation: failed to fetch dominance, using cached={dir}",
                        dir=self._cached_direction)
            return self._cached_direction

    def _get_alloc_table(self, direction: str) -> dict[int, float]:
        """Get allocation table for the given dominance direction."""
        if direction == "rising":
            return _ALLOC_RISING
        if direction == "falling":
            return _ALLOC_FALLING
        return _ALLOC_BALANCED

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_available(self, symbol: str, trading_capital: float) -> float:
        """Return the capital available for this symbol based on its tier.

        Args:
            symbol: Trading pair symbol.
            trading_capital: Current total trading capital.

        Returns:
            Maximum capital (USD) allocatable to this symbol's tier.
        """
        direction = await self._fetch_dominance_direction()
        tier = self.get_coin_tier(symbol)
        alloc_table = self._get_alloc_table(direction)
        pct = alloc_table.get(tier, 0.10)
        available = trading_capital * pct

        log.debug(
            "SectorRotation: symbol={sym}, tier={t}, direction={dir}, "
            "alloc_pct={pct:.0%}, available={av:.2f}",
            sym=symbol, t=tier, dir=direction, pct=pct, av=available,
        )
        return available

    def snapshot(self) -> dict:
        """Return diagnostic info."""
        return {
            "cached_direction": self._cached_direction,
            "cached_dominance": self._cached_dominance,
            "alloc_table": self._get_alloc_table(self._cached_direction),
        }
