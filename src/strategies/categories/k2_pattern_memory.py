"""Strategy K2: Pattern Memory Trade — Match current market state to historical patterns."""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


def _classify_fg(value: int) -> str:
    if value < 20: return "extreme_fear"
    if value < 40: return "fear"
    if value < 60: return "neutral"
    if value < 80: return "greed"
    return "extreme_greed"


class PatternMemory(BaseStrategy):

    @property
    def name(self) -> str: return "K2_pattern_memory"
    @property
    def category(self) -> str: return "ai_enhanced"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.H1
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 360

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        """Match current market state to historical patterns from pattern_log.

        The altdata dict may contain historical pattern matches:
          altdata["pattern_matches"] = [{"outcome": "up"/"down", "pnl_pct": float}, ...]
        If not populated (no DB query in scan), returns None.
        """
        if not altdata or not candles or len(candles) < 20:
            return None

        matches = altdata.get("pattern_matches")
        if not matches or len(matches) < 5:
            return None  # Need at least 5 historical matches

        rsi = safe_get(ta_data, "momentum", "rsi_14")
        if rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close

        up_count = sum(1 for m in matches if m.get("outcome") == "up")
        down_count = sum(1 for m in matches if m.get("outcome") == "down")
        total = len(matches)
        up_rate = up_count / total

        # Confidence based on match count and win rate
        if total >= 20 and up_rate > 0.85:
            confidence = 0.85
        elif total >= 10 and up_rate > 0.8:
            confidence = 0.7
        elif total >= 5 and up_rate > 0.7:
            confidence = 0.5
        else:
            confidence = 0.0

        if up_rate >= 0.7 and confidence >= 0.5:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.988, suggested_take_profit=price * 1.02,
                timeframe=self.timeframe.value,
                conditions_met={"pattern_matches": total, "up_rate": up_rate, "confidence": confidence},
                conditions_strength={"historical_accuracy": up_rate, "sample_size": min(total / 20, 1.0)},
                created_at=now_utc(),
            )

        down_rate = down_count / total
        if down_rate >= 0.7 and confidence >= 0.5:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.012, suggested_take_profit=price * 0.98,
                timeframe=self.timeframe.value,
                conditions_met={"pattern_matches": total, "down_rate": down_rate, "confidence": confidence},
                conditions_strength={"historical_accuracy": down_rate, "sample_size": min(total / 20, 1.0)},
                created_at=now_utc(),
            )
        return None

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        matches = (altdata or {}).get("pattern_matches")
        if not matches or len(matches) < 5:
            return ("NEUTRAL", 0.2, "No pattern history")
        total = len(matches)
        up_count = sum(1 for m in matches if m.get("outcome") == "up")
        up_rate = up_count / total
        if direction == Side.BUY and up_rate > 0.6:
            return ("BUY", min(up_rate, 0.8), f"Pattern history {up_rate:.0%} bullish")
        if direction == Side.SELL and up_rate < 0.4:
            return ("SELL", min(1 - up_rate, 0.8), f"Pattern history {1-up_rate:.0%} bearish")
        return ("NEUTRAL", 0.3, f"Pattern history mixed ({up_rate:.0%} up)")
