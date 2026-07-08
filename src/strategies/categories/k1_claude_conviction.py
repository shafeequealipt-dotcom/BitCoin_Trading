"""Strategy K1: Claude Conviction Trade — Deep Claude API analysis for high-quality setups.

UNIQUE: This is the ONLY strategy that makes an external API call in scan().
Only triggered when another strategy produces a score > 80 setup with STRONG consensus.
"""

from src.core.types import OHLCV, Side, Ticker, TimeFrame
from src.core.utils import now_utc
from src.strategies.base_strategy import BaseStrategy
from src.strategies.categories._helpers import safe_get
from src.strategies.models.regime_types import MarketRegime
from src.strategies.models.signal_types import RawSignal


class ClaudeConviction(BaseStrategy):

    @property
    def name(self) -> str: return "K1_claude_conviction"
    @property
    def category(self) -> str: return "ai_enhanced"
    @property
    def applicable_regimes(self) -> list[MarketRegime]:
        return list(MarketRegime)
    @property
    def timeframe(self) -> TimeFrame: return TimeFrame.M15
    @property
    def risk_level(self) -> str: return "medium"
    @property
    def expected_hold_minutes(self) -> int: return 360

    async def scan(self, symbol, candles, ticker, ta_data, sentiment_data, altdata) -> RawSignal | None:
        """K1 does not independently scan. It is triggered by the StrategyWorker
        when a high-quality setup (score > 80, STRONG consensus) needs deep analysis.

        The altdata dict may contain a trigger:
          altdata["k1_trigger"] = {"symbol": str, "direction": str, "score": float, "consensus": str}
        """
        if not altdata or "k1_trigger" not in altdata:
            return None
        if not candles or len(candles) < 20:
            return None

        trigger = altdata["k1_trigger"]
        if trigger.get("symbol") != symbol:
            return None
        if trigger.get("score", 0) < 80 or trigger.get("consensus") != "STRONG":
            return None

        rsi = safe_get(ta_data, "momentum", "rsi_14")
        trend = safe_get(ta_data, "trend", "trend_summary", default="NEUTRAL")
        if rsi is None:
            return None

        price = ticker.last_price if ticker else candles[-1].close
        direction = Side.BUY if trigger.get("direction") == "Buy" else Side.SELL
        score = trigger["score"]

        # Deep conviction signal with high confidence
        if direction == Side.BUY:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.BUY, entry_price=price,
                suggested_stop_loss=price * 0.985,
                suggested_take_profit=price * 1.03,
                timeframe=self.timeframe.value,
                conditions_met={"claude_triggered": True, "base_score": score, "consensus": "STRONG", "rsi": rsi, "trend": trend},
                conditions_strength={"conviction": min(score / 100, 1.0), "consensus": 0.9, "trend_confirm": 0.8 if trend == "BULLISH" else 0.5},
                created_at=now_utc(),
            )
        else:
            return RawSignal(
                strategy_name=self.name, strategy_category=self.category,
                symbol=symbol, direction=Side.SELL, entry_price=price,
                suggested_stop_loss=price * 1.015,
                suggested_take_profit=price * 0.97,
                timeframe=self.timeframe.value,
                conditions_met={"claude_triggered": True, "base_score": score, "consensus": "STRONG", "rsi": rsi, "trend": trend},
                conditions_strength={"conviction": min(score / 100, 1.0), "consensus": 0.9, "trend_confirm": 0.8 if trend == "BEARISH" else 0.5},
                created_at=now_utc(),
            )

    def vote(self, symbol, direction, candles, ta_data, sentiment_data, altdata) -> tuple[str, float, str]:
        # K1 does not vote on other strategies
        return ("NEUTRAL", 0.0, "K1 does not vote — deep analysis only")
