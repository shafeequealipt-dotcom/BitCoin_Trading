"""Strategy F4: Grid/DCA Recovery — Add to losing positions in ranging markets only.

SPECIAL: This strategy doesn't scan for new setups. It activates only when an
existing position is losing between -0.5% and -2% in a RANGING market.

HARD RULES:
- Max 3 grid additions per position
- Choppiness must be > 45 (ranging confirmed)
- Total position must not exceed max_position_size_pct
- If loss exceeds 3% even with grid → hard stop
"""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class GridRecovery(BaseStrategy):

    @property
    def name(self) -> str: return "F4_grid_recovery"
    @property
    def category(self) -> str: return "advanced"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return [MarketRegime.RANGING]
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M15
    @property
    def risk_level(self) -> str: return "high"
    @property
    def expected_hold_minutes(self) -> int: return 240

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        """Grid Recovery only activates for losing positions.

        The altdata dict may contain position context:
          altdata["open_position"] = {"symbol": str, "side": str, "pnl_pct": float, "grid_count": int}
        If not present, this strategy does nothing.
        """
        if not altdata or not candles or len(candles) < 20:
            return None

        position_info = altdata.get("open_position")
        if not position_info:
            return None

        pos_symbol = position_info.get("symbol", "")
        if pos_symbol != symbol:
            return None

        pnl_pct = position_info.get("pnl_pct", 0)
        grid_count = position_info.get("grid_count", 0)
        pos_side = position_info.get("side", "")

        # Only activate for moderate losses
        if pnl_pct > -0.5 or pnl_pct < -2.0:
            return None

        # Hard rule: max 3 grid additions
        if grid_count >= 3:
            return None

        # Must be ranging market
        chop = safe_get(ta_data, "volatility", "choppiness_index")
        if chop is None or chop < 45:
            return None  # Trending → DO NOT grid

        price = ticker.last_price if ticker else candles[-1].close
        supports = safe_get(ta_data, "support_resistance", "support_levels", default=[])
        resistances = safe_get(ta_data, "support_resistance", "resistance_levels", default=[])

        # Check price is at support (for longs) or resistance (for shorts)
        direction = Side.BUY if pos_side == "Buy" else Side.SELL
        if direction == Side.BUY:
            near_support = any(abs(price - s) / price < 0.005 for s in supports) if supports else False
            if not near_support:
                return None
            next_support = min(supports) if supports else price * 0.97
            sl = next_support * 0.995
            tp = position_info.get("entry_price", price * 1.01)  # Target breakeven
        else:
            near_resistance = any(abs(r - price) / price < 0.005 for r in resistances) if resistances else False
            if not near_resistance:
                return None
            next_resistance = max(resistances) if resistances else price * 1.03
            sl = next_resistance * 1.005
            tp = position_info.get("entry_price", price * 0.99)

        return RawSignal(
            strategy_name=self.name, strategy_category=self.category,
            symbol=symbol, direction=direction, entry_price=price,
            suggested_stop_loss=sl,
            suggested_take_profit=tp,
            timeframe=self.timeframe.value,
            conditions_met={"losing_position": pnl_pct, "grid_count": grid_count, "ranging_market": chop, "at_level": True},
            conditions_strength={"loss_moderate": min(abs(pnl_pct) / 2, 1.0), "choppiness": min(chop / 80, 1.0)},
            created_at=now_utc(),
        )

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        # Grid Recovery does NOT vote on other strategies
        return ("NEUTRAL", 0.0, "Grid Recovery does not vote")
