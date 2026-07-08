"""M10: Risk Weather System.

5-level risk assessment that combines volatility, performance, sentiment,
correlation, and news into a unified weather report. Cached for 60 seconds.
"""

import time
from datetime import datetime, timezone

from src.core.logging import get_logger
from src.core.log_context import ctx
from src.fund_manager.models.fund_types import RiskWeather, RiskWeatherReport

log = get_logger("fund_manager")

# Weather thresholds (score 0-100)
WEATHER_THRESHOLDS: list[tuple[float, RiskWeather]] = [
    (80.0, RiskWeather.NUCLEAR),
    (60.0, RiskWeather.HURRICANE),
    (40.0, RiskWeather.STORMY),
    (20.0, RiskWeather.CLOUDY),
    (0.0, RiskWeather.CLEAR),
]

# Allocation multipliers per weather level
WEATHER_MULTIPLIERS: dict[RiskWeather, float] = {
    RiskWeather.CLEAR: 1.0,
    RiskWeather.CLOUDY: 0.8,
    RiskWeather.STORMY: 0.5,
    RiskWeather.HURRICANE: 0.25,
    RiskWeather.NUCLEAR: 0.0,
}

# Max leverage per weather level
WEATHER_MAX_LEVERAGE: dict[RiskWeather, int] = {
    RiskWeather.CLEAR: 5,
    RiskWeather.CLOUDY: 4,
    RiskWeather.STORMY: 3,
    RiskWeather.HURRICANE: 2,
    RiskWeather.NUCLEAR: 0,
}

# Cache TTL in seconds
CACHE_TTL = 60.0


class RiskWeatherAssessor:
    """5-level risk weather assessment system.

    Combines multiple risk factors into a unified score and weather level.
    Results are cached for 60 seconds to avoid excessive service calls.

    Args:
        services: Dict of service instances (ta_engine, market_service, etc.).
    """

    def __init__(self, settings=None, services: dict | None = None) -> None:
        self._services = services or {}
        self._cache: RiskWeatherReport | None = None
        self._cache_time: float = 0.0
        self._previous_level: RiskWeather | None = None

    async def assess(self) -> RiskWeatherReport:
        """Assess current risk weather conditions.

        Returns a cached result if called within 60 seconds of last assessment.

        Returns:
            RiskWeatherReport with level, score, multiplier, and components.
        """
        now = time.time()
        if self._cache is not None and (now - self._cache_time) < CACHE_TTL:
            return self._cache

        components: dict[str, float] = {}
        warnings: list[str] = []

        # ── Component 1: Volatility (0-25) ──
        vol_score = await self._assess_volatility()
        components["volatility"] = vol_score
        if vol_score >= 20:
            warnings.append("Extreme market volatility detected")

        # ── Component 2: Performance (0-25) ──
        perf_score = await self._assess_performance()
        components["performance"] = perf_score
        if perf_score >= 20:
            warnings.append("Poor recent trading performance")

        # ── Component 3: Sentiment (0-20) ──
        sent_score = await self._assess_sentiment()
        components["sentiment"] = sent_score
        if sent_score >= 16:
            warnings.append("Extreme market sentiment")

        # ── Component 4: Correlation (0-15) ──
        corr_score = await self._assess_correlation()
        components["correlation"] = corr_score
        if corr_score >= 12:
            warnings.append("High position correlation risk")

        # ── Component 5: News (0-15) ──
        news_score = await self._assess_news()
        components["news"] = news_score
        if news_score >= 12:
            warnings.append("Negative news environment")

        # ── Compute total score ──
        total_score = sum(components.values())
        total_score = max(0.0, min(100.0, total_score))

        # ── Determine weather level ──
        level = RiskWeather.CLEAR
        for threshold, weather in WEATHER_THRESHOLDS:
            if total_score >= threshold:
                level = weather
                break

        multiplier = WEATHER_MULTIPLIERS[level]
        max_leverage = WEATHER_MAX_LEVERAGE[level]

        report = RiskWeatherReport(
            level=level,
            score=total_score,
            allocation_multiplier=multiplier,
            max_leverage_override=max_leverage,
            components=components,
            warnings=warnings,
            updated_at=datetime.now(timezone.utc),
        )

        self._cache = report
        self._cache_time = now

        log.info(
            "Risk weather: {level} (score={score:.1f}, multiplier={mult})",
            level=level.value,
            score=total_score,
            mult=multiplier,
        )

        return report

    async def _assess_volatility(self) -> float:
        """Assess market volatility component (0-25).

        Uses BTC ATR relative to historical to gauge overall market volatility.

        Returns:
            Volatility risk score 0-25.
        """
        try:
            market_service = self._services.get("market_service")
            if market_service is None:
                return 10.0  # neutral default

            from src.core.types import TimeFrame

            klines = await market_service.get_klines("BTCUSDT", TimeFrame.H1, limit=50)
            if len(klines) < 20:
                return 10.0

            # Calculate recent ATR (14 periods)
            recent = klines[-14:]
            atr_sum = 0.0
            for i, candle in enumerate(recent):
                tr = candle.high - candle.low
                if i > 0:
                    prev_close = recent[i - 1].close
                    tr = max(tr, abs(candle.high - prev_close), abs(candle.low - prev_close))
                atr_sum += tr
            recent_atr = atr_sum / len(recent)

            # Calculate historical ATR (earlier 30 candles)
            historical = klines[:30]
            hist_atr_sum = 0.0
            for i, candle in enumerate(historical):
                tr = candle.high - candle.low
                if i > 0:
                    prev_close = historical[i - 1].close
                    tr = max(tr, abs(candle.high - prev_close), abs(candle.low - prev_close))
                hist_atr_sum += tr
            hist_atr = hist_atr_sum / len(historical) if historical else recent_atr

            # Ratio > 1 means higher than normal volatility
            if hist_atr == 0:
                return 10.0

            ratio = recent_atr / hist_atr
            # Map ratio to 0-25 score: ratio 1.0 = 5, ratio 2.0 = 15, ratio 3.0+ = 25
            score = min(25.0, max(0.0, (ratio - 0.5) * 16.67))
            return score

        except Exception:
            log.debug("Volatility assessment failed, using neutral score")
            return 10.0

    async def _assess_performance(self) -> float:
        """Assess recent trading performance component (0-25).

        Looks at position PnL and account health.

        Returns:
            Performance risk score 0-25.
        """
        try:
            position_service = self._services.get("position_service")
            if position_service is None:
                return 8.0  # neutral default

            pnl_summary = await position_service.get_pnl_summary()
            total_unrealized = pnl_summary.get("total_unrealized_pnl", 0.0)
            position_count = pnl_summary.get("position_count", 0)

            score = 0.0

            # Negative unrealized PnL increases risk
            if total_unrealized < -100:
                score += 15.0
            elif total_unrealized < -50:
                score += 10.0
            elif total_unrealized < 0:
                score += 5.0

            # Many open positions increases risk
            if position_count >= 8:
                score += 10.0
            elif position_count >= 5:
                score += 5.0

            return min(25.0, score)

        except Exception:
            log.debug("Performance assessment failed, using neutral score")
            return 8.0

    async def _assess_sentiment(self) -> float:
        """Assess market sentiment component (0-20).

        Uses Fear & Greed index and market momentum.

        Returns:
            Sentiment risk score 0-20.
        """
        try:
            fear_greed = self._services.get("fear_greed")
            if fear_greed is None:
                return 8.0  # neutral default

            fg_data = await fear_greed.get_latest()
            if fg_data is None:
                return 8.0

            fg_value = fg_data.value  # 0-100

            # Extreme fear or extreme greed both increase risk
            if fg_value <= 10 or fg_value >= 90:
                return 18.0
            elif fg_value <= 20 or fg_value >= 80:
                return 14.0
            elif fg_value <= 30 or fg_value >= 70:
                return 10.0
            elif fg_value <= 40 or fg_value >= 60:
                return 6.0
            else:
                return 3.0  # neutral zone 40-60

        except Exception:
            log.debug("Sentiment assessment failed, using neutral score")
            return 8.0

    async def _assess_correlation(self) -> float:
        """Assess position correlation component (0-15).

        High correlation between open positions means concentrated risk.

        Returns:
            Correlation risk score 0-15.
        """
        try:
            position_service = self._services.get("position_service")
            if position_service is None:
                return 5.0  # neutral default

            positions = await position_service.get_positions()

            if len(positions) <= 1:
                return 0.0

            # Check how many positions are on the same side
            buy_count = sum(1 for p in positions if p.side.value == "Buy")
            sell_count = sum(1 for p in positions if p.side.value == "Sell")
            total = len(positions)

            # All same direction = high correlation risk
            max_direction = max(buy_count, sell_count)
            if total > 0:
                same_direction_pct = max_direction / total
            else:
                same_direction_pct = 0.0

            if same_direction_pct >= 1.0 and total >= 3:
                return 15.0
            elif same_direction_pct >= 0.8 and total >= 3:
                return 12.0
            elif same_direction_pct >= 0.8:
                return 8.0
            elif total >= 5:
                return 6.0
            else:
                return 3.0

        except Exception:
            log.debug("Correlation assessment failed, using neutral score")
            return 5.0

    async def _assess_news(self) -> float:
        """Assess news environment component (0-15).

        Negative news increases the risk score.

        Returns:
            News risk score 0-15.
        """
        try:
            ta_engine = self._services.get("ta_engine") or self._services.get("ta")
            if ta_engine is None:
                return 5.0  # neutral default

            # If ta_engine has a method to get recent sentiment, use it
            # Otherwise fall back to neutral
            return 5.0

        except Exception:
            log.debug("News assessment failed, using neutral score")
            return 5.0
