"""M18 — Anti-fragile position detection.

Identifies strategy/weather combinations where a strategy actually
*benefits* from adverse conditions, and overrides the weather-based
size reduction:

  Anti-fragile combos:
    - mean_reversion strategies in RANGING weather (they thrive)
    - microstructure strategies in VOLATILE weather (vol = opportunity)
    - funding_arb strategies in any weather (market-neutral)

  When a strategy is anti-fragile for the current weather:
    - is_antifragile() returns True
    - get_override_multiplier() returns 1.0 instead of the weather's
      reduced multiplier (e.g. 0.5)
"""

from __future__ import annotations

from src.core.logging import get_logger
from src.fund_manager.models.fund_types import RiskWeather

log = get_logger("fund_manager")

# ── Anti-fragile mapping ───────────────────────────────────────────
# Maps (strategy_category, weather_level) → True if anti-fragile
_ANTIFRAGILE_COMBOS: set[tuple[str, str]] = {
    # Mean reversion thrives in ranging markets
    ("mean_reversion", RiskWeather.CLOUDY.value),
    ("mean_reversion", RiskWeather.STORMY.value),
    # Microstructure strategies exploit volatility
    ("microstructure", RiskWeather.STORMY.value),
    ("microstructure", RiskWeather.HURRICANE.value),
    # Funding arb is market-neutral — always anti-fragile
    ("funding_arb", RiskWeather.CLEAR.value),
    ("funding_arb", RiskWeather.CLOUDY.value),
    ("funding_arb", RiskWeather.STORMY.value),
    ("funding_arb", RiskWeather.HURRICANE.value),
    # Predatory strategies can exploit panic
    ("predatory", RiskWeather.STORMY.value),
    # Scalping can work in volatile conditions (quick in/out)
    ("scalping", RiskWeather.CLOUDY.value),
}


class AntiFrag:
    """Detects anti-fragile strategy/weather combinations."""

    def __init__(self, settings=None, services: dict | None = None) -> None:
        self._settings = settings
        self._services = services or {}

    @staticmethod
    def is_antifragile(strategy_category: str, weather_level: str | RiskWeather) -> bool:
        """Check if a strategy category is anti-fragile in the given weather.

        Args:
            strategy_category: Strategy category (e.g. "mean_reversion").
            weather_level: Current risk weather level (str or RiskWeather enum).

        Returns:
            True if this combo benefits from adverse conditions.
        """
        weather_str = weather_level.value if isinstance(weather_level, RiskWeather) else weather_level
        result = (strategy_category, weather_str) in _ANTIFRAGILE_COMBOS

        if result:
            log.info(
                "AntifragileDetector: {cat} is anti-fragile in {w} weather",
                cat=strategy_category, w=weather_str,
            )
        return result

    @staticmethod
    def get_override_multiplier(
        strategy_category: str,
        weather: str | RiskWeather,
    ) -> float:
        """Get the sizing multiplier override for anti-fragile strategies.

        If the strategy is anti-fragile in this weather, returns 1.0
        (full size) instead of the weather's reduced multiplier.

        If NOT anti-fragile, returns the weather's default multiplier
        so the caller can use this as a drop-in replacement.

        Args:
            strategy_category: Strategy category.
            weather: Current risk weather (str or RiskWeather enum).

        Returns:
            1.0 if anti-fragile, otherwise the weather's default multiplier.
        """
        weather_enum = weather if isinstance(weather, RiskWeather) else RiskWeather(weather)

        if AntiFrag.is_antifragile(strategy_category, weather_enum):
            log.debug(
                "AntifragileDetector: overriding weather multiplier to 1.0 "
                "for {cat} in {w}",
                cat=strategy_category, w=weather_enum.value,
            )
            return 1.0

        # Return the standard weather multiplier
        return _WEATHER_MULTIPLIERS.get(weather_enum, 1.0)


# ── Standard weather multipliers (for non-anti-fragile strategies) ──
_WEATHER_MULTIPLIERS: dict[RiskWeather, float] = {
    RiskWeather.CLEAR: 1.0,
    RiskWeather.CLOUDY: 0.8,
    RiskWeather.STORMY: 0.5,
    RiskWeather.HURRICANE: 0.25,
    RiskWeather.NUCLEAR: 0.0,
}
