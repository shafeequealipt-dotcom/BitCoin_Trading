"""Smart Leverage Calculator: dynamically determines leverage based on multiple factors."""

from src.config.settings import Settings
from src.core.types import Side
from src.strategies.models.regime_types import MarketRegime, RegimeState


class SmartLeverage:
    """Calculates optimal leverage based on confidence, coin tier, volatility, and regime.

    Args:
        settings: Application settings with leverage config.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def calculate(
        self,
        symbol: str,
        direction: Side,
        confidence: float,
        regime: RegimeState,
        coin_tier: int,
        volatility_percentile: float,
        ensemble_strength: str,
    ) -> int:
        """Calculate recommended leverage.

        Args:
            symbol: Trading pair.
            direction: BUY or SELL.
            confidence: Setup confidence 0-1.
            regime: Current market regime.
            coin_tier: 1 (BTC/ETH), 2 (major alts), 3 (small alts).
            volatility_percentile: Current ATR vs normal (100 = normal).
            ensemble_strength: "STRONG", "GOOD", "WEAK".

        Returns:
            Leverage multiplier (1-5).
        """
        cfg = self.settings.leverage
        leverage = cfg.max_leverage

        # Reduce by confidence
        if confidence < cfg.min_confidence_for_4x:
            leverage = min(leverage, 2)
        elif confidence < cfg.min_confidence_for_5x:
            leverage = min(leverage, 3)
        elif confidence < 0.9:
            leverage = min(leverage, 4)

        # Reduce by coin tier
        if coin_tier == 1:
            leverage = min(leverage, cfg.tier_1_max)
        elif coin_tier == 2:
            leverage = min(leverage, cfg.tier_2_max)
        else:
            leverage = min(leverage, cfg.tier_3_max)

        # Reduce by volatility
        if volatility_percentile > 150:
            leverage = min(leverage, 2)
        elif volatility_percentile > 120:
            leverage = min(leverage, 3)

        # Reduce by regime
        if regime.regime == MarketRegime.VOLATILE:
            leverage = min(leverage, cfg.volatile_max)
        elif regime.regime == MarketRegime.DEAD:
            leverage = min(leverage, cfg.dead_max)

        # Boost for strong consensus with high confidence
        if ensemble_strength == "STRONG" and confidence > cfg.min_confidence_for_5x:
            leverage = min(leverage + 1, cfg.max_leverage)

        return max(leverage, 1)
